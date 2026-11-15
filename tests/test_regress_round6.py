"""Regression tests for the round-6 review.

* A: the four ``SHEDDING_L*_SOC`` values are resolved by ONE rule, by the same code, in
  ``config.py``, the sentinel and every copy of the power exporter: any value that is not a
  number, out of range or out of order discards all four.
* B: ``restore.sh`` picks the newest COMPLETE snapshot; ``backup.sh`` classifies snapshots
  and applies retention to complete ones only.
* C: ``restore.sh`` finds out which repository the default path means and never guesses
  between two.
* D: the Helm chart runs the power exporter from a ConfigMap in a public image by default;
  the chart's copy of the script is under the mirror gate.
* E: the bootstrap provisioner reads power telemetry with the HAL's strict reader; the
  sentinel honours ``sovereign_telemetry_source_up``.
* F: ``smdc app verify`` is the (only, unenforced) verification entry point; the compliance
  inventory no longer says packages are verified on install.
"""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from sovereign_dc import cli, telemetry
from sovereign_dc import config as config_module
from sovereign_dc.agents import sentinel_copilot
from sovereign_dc.agents.bootstrap_provisioner import BootstrapProvisioner
from sovereign_dc.agents.technician_notifier import BaseNotifier, TechnicianMessage, TechnicianNotifierChain
from sovereign_dc.config import SovereignConfig
from sovereign_dc.enterprise.manager import EnterpriseManager, package_sidecar_path
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.hal import power as hal_power
from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner, get_node_pqc_keypair

ROOT = Path(__file__).resolve().parent.parent
ENV_NAMES = ("SHEDDING_L1_SOC", "SHEDDING_L2_SOC", "SHEDDING_L3_SOC", "SHEDDING_L4_SOC")
DEFAULTS = (50.0, 30.0, 20.0, 10.0)
RULE_BEGIN = "# --- shedding thresholds (one rule): begin"
RULE_END = "# --- shedding thresholds (one rule): end"
CHART = ROOT / "kubernetes" / "helm" / "sovereign-stack"
STANDALONE_EXPORTER = ROOT / "software" / "telemetry" / "power_exporter.py"
CHART_EXPORTER = CHART / "files" / "power_exporter.py"
BACKUP_SH = ROOT / "software" / "backup" / "backup.sh"
RESTORE_SH = ROOT / "software" / "backup" / "restore.sh"


@pytest.fixture(autouse=True)
def _no_shedding_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*ENV_NAMES, "SHEDDING_HYSTERESIS_SOC"):
        monkeypatch.delenv(name, raising=False)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # No bytecode next to the file: a __pycache__ inside the chart's files/ would be packaged with it.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


# --- A: one shedding rule -------------------------------------------------------------------------

RULE_FILES = (
    ROOT / "src" / "sovereign_dc" / "config.py",
    ROOT / "src" / "sovereign_dc" / "agents" / "sentinel_copilot.py",
    ROOT / "software" / "agents" / "sentinel_copilot.py",
    ROOT / "src" / "sovereign_dc" / "telemetry.py",
    STANDALONE_EXPORTER,
    CHART_EXPORTER,
)


def _rule_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(RULE_BEGIN) == 1 and text.count(RULE_END) == 1, path
    return text[text.index(RULE_BEGIN) : text.index(RULE_END)]


def test_threshold_rule_is_byte_identical_in_config_sentinel_and_every_exporter_copy() -> None:
    reference = _rule_block(RULE_FILES[0])
    assert "def checked_shedding_thresholds(" in reference and "def shedding_policy_problem(" in reference
    for path in RULE_FILES[1:]:
        assert _rule_block(path) == reference, path
    # No reader parses the variables on its own any more: each hands the raw settings to the rule.
    call = "checked_shedding_thresholds(tuple(os.getenv(name) for name in SHEDDING_ENV_NAMES))"
    for path in RULE_FILES[1:]:
        assert call in path.read_text(encoding="utf-8"), path


# (settings of L1..L4 as the environment would hold them; None = unset) -> the four thresholds
POLICY_MATRIX: list[tuple[tuple[str | None, str | None, str | None, str | None], tuple[float, float, float, float]]] = [
    ((None, None, None, None), DEFAULTS),
    (("60", "40", "25", "15"), (60.0, 40.0, 25.0, 15.0)),  # valid custom policy
    ((None, None, "25", None), (50.0, 30.0, 25.0, 10.0)),  # one value set, still ordered
    (("60", "abc", "25", "15"), DEFAULTS),  # the review's reproduction: was 60/30/25/15 vs L3 20
    (("abc", None, "25", None), DEFAULTS),  # Fable's reproduction: was 50/30/25/10 vs L3 20
    ((None, None, "abc", None), DEFAULTS),
    (("60", "40", "25", "nan"), DEFAULTS),
    (("inf", "40", "25", "15"), DEFAULTS),
    (("150", "40", "25", "15"), DEFAULTS),  # out of range
    (("60", "40", "25", "-1"), DEFAULTS),
    (("60", "40", "45", "15"), DEFAULTS),  # L2 not above L3
    (("30", "30", "20", "10"), DEFAULTS),  # L1 not above L2
    ((None, None, None, "30"), DEFAULTS),  # L4 above the default L3
    (("", "  ", "25", ""), (50.0, 30.0, 25.0, 10.0)),  # blank = not set
    ((" 60 ", "4e1", "+25", "15.0"), (60.0, 40.0, 25.0, 15.0)),  # whatever float() accepts
    (("60", "40", "25", "15%"), DEFAULTS),
    (("True", "40", "25", "15"), DEFAULTS),
]


@pytest.mark.parametrize(("settings", "expected"), POLICY_MATRIX)
def test_every_reader_of_the_policy_reaches_the_same_four_thresholds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    settings: tuple[str | None, str | None, str | None, str | None],
    expected: tuple[float, float, float, float],
) -> None:
    for name, value in zip(ENV_NAMES, settings, strict=True):
        if value is not None:
            monkeypatch.setenv(name, value)
    caplog.set_level(logging.WARNING)

    cfg = SovereignConfig.from_env()
    assert cfg.shedding_thresholds == expected
    assert SovereignConfig.from_yaml_and_env(str(ROOT / "does-not-exist.yaml")).shedding_thresholds == expected
    assert sentinel_copilot.shedding_thresholds() == expected
    assert _load_module(ROOT / "software" / "agents" / "sentinel_copilot.py", "r6_sentinel").SHEDDING_THRESHOLDS == (
        expected
    )
    assert telemetry.load_shedding_soc_percent() == expected[2]
    for index, path in enumerate((STANDALONE_EXPORTER, CHART_EXPORTER)):
        exporter = _load_module(path, f"r6_exporter_{index}")
        assert exporter.load_shedding_soc_percent() == expected[2], path
        assert exporter.LOAD_SHEDDING_SOC_PERCENT == expected[2], path
        assert exporter.checked_shedding_thresholds(tuple(os.getenv(n) for n in ENV_NAMES)) == expected

    # The flag and the level agree for every SoC, which is what the split broke at 22 %.
    for step in range(0, 1001):
        soc = step / 10.0
        assert (soc < telemetry.load_shedding_soc_percent()) == (cfg.get_shedding_level(soc) >= 3), soc
        assert cfg.get_shedding_level(soc) == sentinel_copilot.compute_shedding_level(
            soc, sentinel_copilot.shedding_thresholds()
        )

    rejected = expected == DEFAULTS and any(value is not None and value.strip() for value in settings)
    warnings = [r for r in caplog.records if "load-shedding thresholds" in r.getMessage()]
    assert bool(warnings) == rejected
    if rejected:
        message = warnings[0].getMessage()
        assert all(name in message for name in ENV_NAMES)
        assert "for all four" in message
    # Never a second, per-variable message about a threshold.
    assert not [r for r in caplog.records if "need a number" in r.getMessage() or "not a valid float" in r.getMessage()]


def test_the_reviews_case_at_22_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    """60 / invalid / 25 / 15: config and sentinel said level 3 at 22 %, the exporter flag said 0."""
    for name, value in zip(ENV_NAMES, ("60", "invalid", "25", "15"), strict=True):
        monkeypatch.setenv(name, value)
    cfg = SovereignConfig.from_env()
    level = cfg.get_shedding_level(22.0)
    assert level == sentinel_copilot.compute_shedding_level(22.0, sentinel_copilot.shedding_thresholds()) == 2
    flag_threshold = _load_module(STANDALONE_EXPORTER, "r6_exporter_22").load_shedding_soc_percent()
    assert flag_threshold == telemetry.load_shedding_soc_percent() == cfg.shedding_l3_soc == 20.0
    assert (22.0 < flag_threshold) is (level >= 3)


def test_one_unparsable_threshold_in_yaml_discards_all_four(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "sovereign.yaml"
    config_file.write_text(
        "shedding_l1_soc: 60\nshedding_l2_soc: forty\nshedding_l3_soc: 25\nshedding_l4_soc: 15\nexporter_port: 9200\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.WARNING)
    cfg = SovereignConfig.from_yaml_and_env(str(config_file))
    assert cfg.shedding_thresholds == DEFAULTS  # was (60, 30, 25, 15)
    assert cfg.exporter_port == 9200  # other settings are still coerced one by one
    assert all(isinstance(value, float) for value in cfg.shedding_thresholds)
    assert sum("load-shedding thresholds" in r.getMessage() for r in caplog.records) == 1

    config_file.write_text("shedding_l1_soc: 60\nshedding_l3_soc: 25\n", encoding="utf-8")
    assert SovereignConfig.from_yaml_and_env(str(config_file)).shedding_thresholds == (60.0, 30.0, 25.0, 10.0)
    # A blank environment variable is "not set": it does not undo the file's value.
    monkeypatch.setenv("SHEDDING_L1_SOC", " ")
    monkeypatch.setenv("SHEDDING_L3_SOC", "22")
    assert SovereignConfig.from_yaml_and_env(str(config_file)).shedding_thresholds == (60.0, 30.0, 22.0, 10.0)


def test_thresholds_passed_to_the_constructor_follow_the_same_rule() -> None:
    assert SovereignConfig(shedding_l1_soc=60, shedding_l3_soc=25.5).shedding_thresholds == (60.0, 30.0, 25.5, 10.0)
    assert SovereignConfig(shedding_l2_soc="abc").shedding_thresholds == DEFAULTS  # type: ignore[arg-type]
    assert SovereignConfig(shedding_l2_soc=True).shedding_thresholds == DEFAULTS
    assert config_module.checked_shedding_thresholds((60, 40)) == DEFAULTS
    assert config_module.checked_shedding_thresholds((60, 40, 25, 15, 5)) == DEFAULTS


def test_documents_state_the_all_four_rule() -> None:
    for name in ("README.md", "ENTERPRISE_ONBOARDING.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "all four defaults" in text, name
    env_example = (ROOT / "software" / "env.example").read_text(encoding="utf-8")
    assert "ALL FOUR defaults" in env_example and "exits 2 without deciding" in env_example
    shedder = (ROOT / "software" / "telemetry" / "load_shedder.sh").read_text(encoding="utf-8")
    assert "use all four defaults instead" in shedder and "plain decimals only" in shedder


# --- E: strict telemetry in bootstrap and the sentinel -----------------------------------------------


class _RecordingNotifier(BaseNotifier):
    def __init__(self) -> None:
        self.sent: list[TechnicianMessage] = []

    def send(self, message: TechnicianMessage) -> bool:
        self.sent.append(message)
        return True


@pytest.fixture
def exporter_url() -> Iterator[Any]:
    """A loopback HTTP server whose /metrics body the test sets with ``serve(body)``."""
    state = {"body": b""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def serve(body: str) -> str:
        state["body"] = body.encode("utf-8")
        return f"http://127.0.0.1:{server.server_port}/metrics"

    yield serve
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


UNAVAILABLE_BODIES = {
    "source down with NaN SoC (the review's input)": "sovereign_telemetry_source_up 0\nsovereign_battery_soc_percent NaN\n",
    "source down with a plausible SoC": "sovereign_telemetry_source_up 0\nsovereign_battery_soc_percent 88.0\n",
    "NaN SoC": "sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent NaN\n",
    "infinite SoC": "sovereign_battery_soc_percent +Inf\n",
    "no SoC series": "sovereign_telemetry_source_up 1\nsovereign_solar_pv_power_watts 900\n",
    "NaN source flag": "sovereign_telemetry_source_up NaN\nsovereign_battery_soc_percent 88.0\n",
    "NaN simulated flag": "sovereign_telemetry_simulated NaN\nsovereign_battery_soc_percent 88.0\n",
}


@pytest.mark.parametrize("body", list(UNAVAILABLE_BODIES.values()), ids=list(UNAVAILABLE_BODIES))
def test_bootstrap_does_not_call_unusable_telemetry_healthy(
    exporter_url: Any, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    url = exporter_url(body)
    power = BootstrapProvisioner._read_power_telemetry(url)
    assert power["telemetry_available"] is False  # was True for the NaN inputs
    assert "battery_soc" not in power and "solar_w" not in power
    assert power["error"]
    # Bootstrap and the HAL agree: the HAL has no reading either.
    with pytest.raises(Exception):  # noqa: B017, PT011 - TelemetryUnavailable or a subclass of it
        hal_power.read_power(url, simulation=False)

    monkeypatch.setenv("POWER_EXPORTER_URL", url)
    recorder = _RecordingNotifier()
    provisioner = BootstrapProvisioner(
        node_id="r6", notifier_chain=TechnicianNotifierChain(notifiers=[recorder], node_id="r6")
    )
    with patch("shutil.which", return_value=None):
        info = provisioner.phase_1_discovery()
    assert info["power"]["telemetry_available"] is False
    assert any(item.startswith("power:") for item in provisioner.state.unverified)
    event_types = [message.event_type for message in recorder.sent]
    assert "HARDWARE_OK" not in event_types and "HARDWARE_WARNING" not in event_types
    assert event_types == ["HARDWARE_UNVERIFIED"]


def test_bootstrap_reads_a_healthy_exporter_like_the_hal(exporter_url: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    url = exporter_url(
        "# HELP x y\nsovereign_telemetry_source_up 1\nsovereign_telemetry_simulated 1\n"
        "sovereign_battery_soc_percent 81.5\nsovereign_solar_pv_power_watts NaN\n"
    )
    power = BootstrapProvisioner._read_power_telemetry(url)
    reading = hal_power.read_power(url, simulation=False)
    assert power["telemetry_available"] is True and power["battery_soc"] == reading.battery_soc == 81.5
    assert power["simulated"] is True and reading.is_simulated is True
    assert "solar_w" not in power and reading.solar_watts is None  # NaN is not a measurement

    monkeypatch.setenv("POWER_EXPORTER_URL", url)
    recorder = _RecordingNotifier()
    provisioner = BootstrapProvisioner(
        node_id="r6", notifier_chain=TechnicianNotifierChain(notifiers=[recorder], node_id="r6")
    )
    with patch("shutil.which", return_value=None):
        provisioner.phase_1_discovery()
    assert [m.event_type for m in recorder.sent] == ["HARDWARE_OK"]
    assert "SIMULATED" in recorder.sent[0].message
    assert not any(item.startswith("power:") for item in provisioner.state.unverified)


def test_bootstrap_has_no_metric_parser_of_its_own() -> None:
    source = (ROOT / "src" / "sovereign_dc" / "agents" / "bootstrap_provisioner.py").read_text(encoding="utf-8")
    assert "fetch_exporter_metrics(url)" in source
    assert "float(parts[1])" not in source and "line.split()" not in source


def test_sentinel_holds_its_mode_when_the_exporter_reports_its_source_down() -> None:
    healthy = {"sovereign_battery_soc_percent": 80.0, "sovereign_solar_pv_power_watts": 100.0}
    assert sentinel_copilot.evaluate_state(healthy, 0.0) is not None
    assert sentinel_copilot.evaluate_state({**healthy, "sovereign_telemetry_source_up": 1.0}, 0.0) is not None
    for source_up in (0.0, -1.0, math.nan, math.inf):
        metrics = {**healthy, "sovereign_telemetry_source_up": source_up}
        assert sentinel_copilot.telemetry_source_up(metrics) is False
        assert sentinel_copilot.evaluate_state(metrics, 0.0) is None  # was NORMAL at 80 % for 0
        assert sentinel_copilot.evaluate_mode(metrics, 0.0) is None
    # A low SoC from a source that says it is down is not a reading either: hold, do not shed.
    assert (
        sentinel_copilot.evaluate_state(
            {"sovereign_battery_soc_percent": 5.0, "sovereign_telemetry_source_up": 0.0}, 0.0
        )
        is None
    )
    assert sentinel_copilot.evaluate_state({"sovereign_battery_soc_percent": math.nan}, 0.0) is None


def test_bootstrap_cli_prints_no_invented_solar_value(
    exporter_url: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POWER_EXPORTER_URL", exporter_url("sovereign_battery_soc_percent 81.5\n"))
    monkeypatch.setattr(sys, "argv", ["smdc", "bootstrap", "--dry-run"])
    with (
        patch("shutil.which", return_value=None),
        patch("sovereign_dc.mesh.mesh_sync.check_peer_health", return_value=False),
        patch.object(BootstrapProvisioner, "_http_ok", return_value=False),
    ):
        try:
            cli.main()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    assert "81.5% SoC | solar n/a" in out and "0W Solar" not in out


# --- F: package verification entry point ----------------------------------------------------------------


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["smdc", *argv])
    try:
        cli.main()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


@pytest.fixture
def signed_package(tmp_path: Path) -> Path:
    app_dir = EnterpriseRegistry.create_project_scaffold(
        tmp_path / "pkg-app", EnterpriseRegistry.scaffold_manifest(name="Demo App", app_id="pkg-app")
    )
    out = tmp_path / "out" / "pkg.smdc-app"
    manager = EnterpriseManager(registry=EnterpriseRegistry(registry_file=tmp_path / "registry.json"))
    ok, message, _ = manager.package_app(app_dir, output_path=out)
    assert ok, message
    return out


def test_app_verify_accepts_the_signer_key_and_nothing_else(
    signed_package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    node_key = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key.hex()
    assert _run_cli(monkeypatch, "app", "verify", str(signed_package), "--public-key", node_key) == 0
    assert "signature is valid" in capsys.readouterr().out

    key_file = tmp_path / "signer.hex"
    key_file.write_text("\n".join(node_key[i : i + 64] for i in range(0, len(node_key), 64)) + "\n", encoding="utf-8")
    assert _run_cli(monkeypatch, "app", "verify", str(signed_package), "--public-key", str(key_file)) == 0
    capsys.readouterr()

    other_key = PQCSigner(PQCAlgorithm.ML_DSA_87).generate_keypair().public_key.hex()
    assert _run_cli(monkeypatch, "app", "verify", str(signed_package), "--public-key", other_key) == 1
    assert "not signed by the trusted public key" in capsys.readouterr().out

    assert _run_cli(monkeypatch, "app", "verify", str(signed_package), "--public-key", "not-hex") == 1
    assert "NOT verified" in capsys.readouterr().out

    # The key that travels inside the sidecar is not a trust anchor: a re-signed sidecar fails.
    sidecar = package_sidecar_path(signed_package)
    forged = json.loads(sidecar.read_text(encoding="utf-8"))
    forged["pqc_signature"]["public_key_hex"] = other_key
    sidecar.write_text(json.dumps(forged), encoding="utf-8")
    assert _run_cli(monkeypatch, "app", "verify", str(signed_package), "--public-key", node_key) == 1
    capsys.readouterr()


def test_app_verify_rejects_a_changed_archive_and_an_unsigned_package(
    signed_package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    node_key = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key.hex()
    with open(signed_package, "ab") as handle:
        handle.write(b"tampered")
    assert _run_cli(monkeypatch, "app", "verify", str(signed_package), "--public-key", node_key) == 1
    assert "SHA-256 does not match" in capsys.readouterr().out

    assert _run_cli(monkeypatch, "app", "verify", str(tmp_path / "missing.smdc-app"), "--public-key", node_key) == 1
    assert "package not found" in capsys.readouterr().out

    app_dir = tmp_path / "pkg-app"
    unsigned = tmp_path / "out" / "unsigned.smdc-app"
    assert _run_cli(monkeypatch, "app", "package", str(app_dir), "--output", str(unsigned), "--no-pqc") == 0
    assert "unsigned" in capsys.readouterr().out
    assert _run_cli(monkeypatch, "app", "verify", str(unsigned), "--public-key", node_key) == 1
    assert "package is not signed" in capsys.readouterr().out


def test_app_verify_needs_a_key_and_package_output_says_nothing_is_verified_for_you(
    signed_package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run_cli(monkeypatch, "app", "verify", str(signed_package)) == 2  # argparse: --public-key is required
    capsys.readouterr()
    out = tmp_path / "out" / "again.smdc-app"
    assert _run_cli(monkeypatch, "app", "package", str(tmp_path / "pkg-app"), "--output", str(out)) == 0
    text = capsys.readouterr().out
    assert "Nothing verifies this signature automatically" in text and "smdc app verify" in text
    assert "Signer Key ID" in text and "ready for air-gapped" not in text
    assert (
        cli.cmd_app_verify(
            argparse.Namespace(
                package=str(out), public_key=get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key.hex(), sidecar=None
            )
        )
        == 0
    )


def test_compliance_inventory_does_not_claim_install_time_verification() -> None:
    compliance = (ROOT / "COMPLIANCE.md").read_text(encoding="utf-8")
    assert "verified on install" not in compliance
    row = next(line for line in compliance.split("\n") if line.startswith("| **Application package signatures**"))
    assert "**Partial.**" in row and "smdc app verify" in row
    assert "available, not enforced" in row and "no package installer" in row
    # Nothing but the CLI (self-test and `app verify`) calls verify_package: there is no installer.
    callers = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / "sovereign_dc").rglob("*.py")
        if "verify_package(" in path.read_text(encoding="utf-8")
    ]
    assert sorted(callers) == ["src/sovereign_dc/cli.py", "src/sovereign_dc/enterprise/manager.py"]


# --- D: Helm chart ------------------------------------------------------------------------------------------


def test_chart_copy_of_the_exporter_is_identical_and_small_enough_for_a_configmap() -> None:
    assert CHART_EXPORTER.read_bytes() == STANDALONE_EXPORTER.read_bytes()
    assert CHART_EXPORTER.stat().st_size < 1024 * 1024 // 2  # a ConfigMap holds at most 1 MiB


def test_chart_default_values_need_no_project_built_image() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    exporter = values["telemetry"]["powerExporter"]
    assert values["telemetry"]["enabled"] is True
    assert exporter["image"]["repository"] == ""  # was ghcr.io/iliachry/sovereign-power-exporter (pull denied)
    assert exporter["runtimeImage"] == {"repository": "python", "tag": "3.11-alpine", "pullPolicy": "IfNotPresent"}
    compose = (ROOT / "software" / "telemetry" / "docker-compose.telemetry.yml").read_text(encoding="utf-8")
    assert "image: python:3.11-alpine" in compose  # the chart runs what the Compose overlay runs
    assert values["space"]["enabled"] is False  # its image is not published either

    def images(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            if isinstance(node.get("repository"), str) and "tag" in node:
                yield node["repository"]
            for child in node.values():
                yield from images(child)

    enabled_by_default = {key: values[key] for key in ("ai", "core", "telemetry")}
    assert not [name for name in images(enabled_by_default) if "iliachry" in name or "sovereign-" in name]

    deployment = (CHART / "templates" / "deployment-telemetry.yaml").read_text(encoding="utf-8")
    assert 'command: ["python", "/app/power_exporter.py"]' in deployment
    assert "$exporter.runtimeImage.repository" in deployment and "$exporter.image.repository" in deployment
    configmap = (CHART / "templates" / "configmap-telemetry.yaml").read_text(encoding="utf-8")
    assert '.Files.Glob "files/power_exporter.py"' in configmap
    assert "not .Values.telemetry.powerExporter.image.repository" in configmap


def _helm_template(*extra: str) -> list[dict[str, Any]]:
    result = subprocess.run(  # noqa: S603 - helm on the developer's PATH, rendering the repository's chart
        ["helm", "template", "t", str(CHART), *extra],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_rendered_chart_mounts_the_script_or_uses_the_prebuilt_image() -> None:
    docs = _helm_template()
    configmaps = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-power-exporter")]
    assert len(configmaps) == 1
    assert configmaps[0]["data"]["power_exporter.py"] == STANDALONE_EXPORTER.read_text(encoding="utf-8")
    deployment = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-telemetry"))
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["image"] == "python:3.11-alpine"
    assert container["command"] == ["python", "/app/power_exporter.py"]
    assert container["volumeMounts"] == [{"name": "power-exporter-script", "mountPath": "/app", "readOnly": True}]
    assert pod["volumes"][0]["configMap"]["name"] == configmaps[0]["metadata"]["name"]
    images = {
        c["image"] for d in docs if d["kind"] == "Deployment" for c in d["spec"]["template"]["spec"]["containers"]
    }
    assert not [image for image in images if "iliachry" in image]

    prebuilt = _helm_template(
        "--set", "telemetry.powerExporter.image.repository=registry.example/exporter",
        "--set", "telemetry.powerExporter.image.tag=1.2.3",
    )  # fmt: skip
    assert not [d for d in prebuilt if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-power-exporter")]
    deployment = next(d for d in prebuilt if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-telemetry"))
    pod = deployment["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"] == "registry.example/exporter:1.2.3"
    assert "command" not in pod["containers"][0] and "volumes" not in pod


needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("awk") is None, reason="bash and awk are needed"
)


@needs_bash
def test_mirror_gate_fails_when_the_chart_copy_drifts(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "src" / "sovereign_dc", checkout / "src" / "sovereign_dc")
    for name in ("agents", "mesh", "space", "telemetry"):
        shutil.copytree(ROOT / "software" / name, checkout / "software" / name)
    shutil.copytree(ROOT / "kubernetes" / "helm", checkout / "kubernetes" / "helm")
    gate = ["bash", str(ROOT / "scripts" / "check_mirrors.sh"), str(checkout)]

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(gate, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603

    clean = run()
    assert clean.returncode == 0, clean.stderr
    assert "1 chart copy is byte-identical" in clean.stdout

    copy = checkout / "kubernetes" / "helm" / "sovereign-stack" / "files" / "power_exporter.py"
    copy.write_text(copy.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    drifted = run()
    assert drifted.returncode == 1
    assert "DRIFT    kubernetes/helm/sovereign-stack/files/power_exporter.py" in drifted.stderr

    copy.unlink()
    missing = run()
    assert (
        missing.returncode == 1 and "MISSING  kubernetes/helm/sovereign-stack/files/power_exporter.py" in missing.stderr
    )

    shutil.copyfile(STANDALONE_EXPORTER, copy)
    (copy.parent / "other.py").write_text("x = 1\n", encoding="utf-8")
    unlisted = run()
    assert unlisted.returncode == 1 and "UNLISTED kubernetes/helm/sovereign-stack/files/other.py" in unlisted.stderr


# --- B: complete and incomplete snapshots ------------------------------------------------------------------------


def _function(script: Path, name: str) -> str:
    text = script.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start : text.index("\n}\n", start) + 3]


SNAPSHOTS_JSON = (
    '[{"time":"2026-11-13T02:00:00.5+02:00","parent":"p","tree":"t","paths":["/a}b",'
    '"/c,\\"tags\\":[\\"sovereign-complete\\"],\\"short_id\\":\\"dead\\""],"hostname":"h","excludes":["x"],'
    '"tags":["sovereign-snapshot","mode-host","dumps-1-ab","sovereign-complete"],"program_version":"restic 0.19.1",'
    '"summary":{"files_new":1,"backup_end":"2026"},"id":"11112222aaaa","short_id":"11112222"},'
    '{"time":"2026-11-14T02:00:00Z","tree":"t","paths":["/x"],"hostname":"h","id":"33334444bbbb","short_id":"33334444"},'
    '{"time":"2026-11-15T02:00:00Z","tree":"t","paths":["/x"],"tags":["sovereign-snapshot","sovereign-incomplete"],'
    '"summary":{"files_new":1},"id":"55556666cccc","short_id":"55556666"}]\n'
)


@needs_bash
def test_snapshot_rows_is_one_function_in_both_scripts_and_reads_restic_json(tmp_path: Path) -> None:
    function = _function(BACKUP_SH, "snapshot_rows")
    assert function == _function(RESTORE_SH, "snapshot_rows")
    script = tmp_path / "rows.sh"
    script.write_text(function + "snapshot_rows\n", encoding="utf-8")
    for shell in ("sh", "bash"):
        result = subprocess.run(  # noqa: S603
            [shell, str(script)], input=SNAPSHOTS_JSON, capture_output=True, text=True, timeout=30, check=False
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.split("\n")[:-1] == [
            "11112222|2026-11-13T02:00:00.5+02:00|sovereign-snapshot,mode-host,dumps-1-ab,sovereign-complete",
            "33334444|2026-11-14T02:00:00Z|",
            "55556666|2026-11-15T02:00:00Z|sovereign-snapshot,sovereign-incomplete",
        ]
        empty = subprocess.run([shell, str(script)], input="[]\n", capture_output=True, text=True, check=False)  # noqa: S603
        assert empty.stdout == ""


def test_backup_classifies_snapshots_after_the_run_and_retains_complete_ones_only() -> None:
    script = BACKUP_SH.read_text(encoding="utf-8")
    code = "\n".join(line for line in script.split("\n") if not line.lstrip().startswith("#"))
    # The completeness tag is never given to `restic backup` itself.
    backup_line = next(line for line in code.split("\n") if line.strip().startswith("set -- backup "))
    assert '--tag "$SNAPSHOT_TAG"' in backup_line and "COMPLETE_TAG" not in backup_line
    assert 'restic tag --tag "${TAG},mode-${MODE}" --add "$fp_dumps_tag" --add "$COMPLETE_TAG"' in code
    assert code.count('mark_incomplete "$TAG"') == 2  # restic exit != 0; MANIFEST changed under it
    assert code.index('restic "$@" || fp_rc=$?') < code.index('--add "$COMPLETE_TAG" >/dev/null')
    # Retention: two commands, neither on every snapshot.
    forgets = [block.split("--prune")[0] for block in code.split("restic forget")[1:]]
    assert len(forgets) == 2
    assert '--tag "$INCOMPLETE_TAG"' in forgets[0] and '--keep-last "$INCOMPLETE_KEEP_LAST"' in forgets[0]
    assert '--tag "$COMPLETE_TAG"' in forgets[1] and "--keep-daily 7" in forgets[1]
    assert not [block for block in forgets if "SNAPSHOT_TAG" in block or "sovereign-snapshot" in block]
    assert code.count("--prune") == 1 and "--prune" in code.split("restic forget")[2]
    assert 'COMPLETE_TAG="sovereign-complete"' in code and 'INCOMPLETE_TAG="sovereign-incomplete"' in code
    restore = RESTORE_SH.read_text(encoding="utf-8")
    assert 'COMPLETE_TAG="sovereign-complete"' in restore and 'INCOMPLETE_TAG="sovereign-incomplete"' in restore
    assert "restore latest" not in restore and "ls latest" not in restore  # restic's own "latest" is never used


FAKE_RESTIC = r"""#!/bin/sh
# snapshots --json lists $FAKE/snapshots (lines `<id>|<tags>`, oldest first); restore <id> copies
# $FAKE/snap/<id> into --target; every call is logged with the repository it was made on.
printf '%s|%s\n' "${RESTIC_REPOSITORY:-}" "$*" >>"$FAKE/restic.log"
case "$1" in
  snapshots)
    case " $* " in *" --json "*) ;; *) echo "(table)"; exit 0 ;; esac
    out=""; n=0
    while IFS='|' read -r id tags; do
      n=$((n + 1)); t=""
      [ -n "$tags" ] && t=",\"tags\":[\"$(printf '%s' "$tags" | sed 's/,/","/g')\"]"
      out="${out:+$out,}{\"time\":\"2026-11-0${n}T02:00:00Z\",\"tree\":\"t\",\"paths\":[\"/x\"]$t,\"summary\":{\"files_new\":1},\"id\":\"$id$id\",\"short_id\":\"$id\"}"
    done <"$FAKE/snapshots"
    printf '[%s]\n' "$out" ;;
  restore)
    id="$2"; while [ "$#" -gt 0 ]; do [ "$1" = "--target" ] && tgt="$2"; shift; done
    cp -R "$FAKE/snap/$id/." "$tgt/" ;;
esac
"""

FAKE_DOCKER = r"""#!/bin/sh
printf '%s\n' "$*" >>"$FAKE/docker.log"
[ -e "$FAKE/docker_down" ] && { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
for last in "$@"; do :; done
case "$1" in
  inspect) echo "Error: No such object: $last" >&2; exit 1 ;;
  volume)
    while IFS='=' read -r name mountpoint; do
      [ "$name" = "$last" ] && { printf '%s\n' "$mountpoint"; exit 0; }
    done <"$FAKE/volumes"
    echo "Error: No such volume: $last" >&2; exit 1 ;;
esac
exit 97
"""


class _Restore:
    """Runs a copy of software/backup/restore.sh against fake ``restic`` and ``docker`` binaries.

    The copy differs from the script in ONE line: ``DEFAULT_REPOSITORY`` points into the
    sandbox instead of at /var/backups/sovereign, so the detection can be exercised.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "restore"
        self.fake = self.root / "fake"
        self.bin = self.root / "bin"
        self.host_repo = self.root / "hostrepo"
        self.volume_repo = self.root / "volumes" / "myproj_backup_storage" / "_data"
        script_dir = self.root / "software" / "backup"
        for directory in (self.fake / "snap", self.bin, script_dir, self.root / "work"):
            directory.mkdir(parents=True)
        # sqlite3 is only looked up (`command -v`) on the paths tested here; hosts without the CLI must not differ.
        for name, body in (("restic", FAKE_RESTIC), ("docker", FAKE_DOCKER), ("sqlite3", "#!/bin/sh\necho ok\n")):
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        original = RESTORE_SH.read_text(encoding="utf-8")
        line = 'DEFAULT_REPOSITORY="/var/backups/sovereign"\n'
        assert original.count(line) == 1
        self.script = script_dir / "restore.sh"
        self.script.write_text(original.replace(line, f'DEFAULT_REPOSITORY="{self.host_repo}"\n'), encoding="utf-8")
        self.env_file = self.root / "software" / ".env"
        self.env_file.write_text(
            f"RESTIC_REPOSITORY={self.host_repo}\nRESTIC_PASSWORD=test\nCOMPOSE_PROJECT_NAME=myproj\n", encoding="utf-8"
        )
        for name in ("snapshots", "volumes", "restic.log", "docker.log"):
            (self.fake / name).write_text("", encoding="utf-8")

    def snapshots(self, *rows: tuple[str, str]) -> None:
        (self.fake / "snapshots").write_text("".join(f"{sid}|{tags}\n" for sid, tags in rows), encoding="utf-8")
        for sid, _ in rows:
            (self.fake / "snap" / sid).mkdir(exist_ok=True)
            (self.fake / "snap" / sid / "marker").write_text(sid, encoding="utf-8")

    @staticmethod
    def make_repository(path: Path) -> None:
        (path / "keys").mkdir(parents=True)
        (path / "config").write_text("", encoding="utf-8")

    def volume(self, mountpoint: Path | None) -> None:
        (self.fake / "volumes").write_text(
            f"myproj_backup_storage={mountpoint}\n" if mountpoint is not None else "", encoding="utf-8"
        )

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        full_env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE": str(self.fake),
            "TMPDIR": str(self.root / "work"),
            "HOME": str(self.root),
            **env,
        }
        return subprocess.run(  # noqa: S603 - the repository's own script with fake binaries
            ["bash", str(self.script), *args],  # noqa: S607
            env=full_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def restic_calls(self) -> list[tuple[str, str]]:
        lines = (self.fake / "restic.log").read_text(encoding="utf-8").split("\n")[:-1]
        return [(line.split("|", 1)[0], line.split("|", 1)[1]) for line in lines]

    def docker_calls(self) -> list[str]:
        return (self.fake / "docker.log").read_text(encoding="utf-8").split("\n")[:-1]


@pytest.fixture
def restore(tmp_path: Path) -> _Restore:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run restore.sh")
    sandbox = _Restore(tmp_path)
    sandbox.make_repository(sandbox.host_repo)  # a plain host repository unless a test says otherwise
    return sandbox


COMPLETE = "sovereign-snapshot,mode-container,dumps-1794000000-0123456789abcdef,sovereign-complete"
INCOMPLETE = "sovereign-snapshot,mode-container,sovereign-incomplete"
LEGACY_COMPLETE = "sovereign-snapshot,sovereign-automated-1,mode-host,dumps-1794000000-0123456789abcdef"
LEGACY_UNTAGGED = "sovereign-snapshot,sovereign-automated-3,mode-host"


def test_restore_latest_takes_the_newest_complete_snapshot_not_the_newest(restore: _Restore, tmp_path: Path) -> None:
    restore.snapshots(("aaaa0001", COMPLETE), ("cccc0002", COMPLETE), ("bad00003", INCOMPLETE))
    target = tmp_path / "target"
    result = restore.run("restore-latest", str(target))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "marker").read_text(encoding="utf-8") == "cccc0002"  # was the exit-3 snapshot bad00003
    assert "selected: snapshot cccc0002" in result.stdout
    assert "1 newer snapshot(s) are NOT complete" in result.stdout and "bad00003" in result.stdout
    restores = [call for _, call in restore.restic_calls() if call.startswith("restore ")]
    assert restores == [f"restore cccc0002 --target {target}"]


def test_restore_treats_legacy_dumps_tag_as_complete_and_refuses_when_nothing_is_complete(
    restore: _Restore, tmp_path: Path
) -> None:
    restore.snapshots(("1e9ac001", LEGACY_COMPLETE), ("0c1a5502", LEGACY_UNTAGGED))
    result = restore.run("restore-latest", str(tmp_path / "t1"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "t1" / "marker").read_text(encoding="utf-8") == "1e9ac001"
    assert "0c1a5502" in result.stdout and "unclassified" in result.stdout

    restore.snapshots(("0c1a5502", LEGACY_UNTAGGED), ("bad00003", INCOMPLETE))
    (restore.fake / "restic.log").write_text("", encoding="utf-8")
    result = restore.run("restore-latest", str(tmp_path / "t2"))
    assert result.returncode != 0
    assert "None of the 2 snapshot(s) is marked complete" in result.stdout + result.stderr
    assert not (tmp_path / "t2" / "marker").exists()
    result = restore.run("--verify")
    assert result.returncode != 0 and "marked complete" in result.stdout + result.stderr
    assert not [call for _, call in restore.restic_calls() if call.startswith("restore ")]

    restore.snapshots()
    result = restore.run("restore-latest", str(tmp_path / "t3"))
    assert result.returncode != 0 and "holds no snapshot tagged sovereign-snapshot" in result.stdout + result.stderr


def test_restore_id_of_an_incomplete_snapshot_is_allowed_with_a_warning_and_list_marks_states(
    restore: _Restore, tmp_path: Path
) -> None:
    restore.snapshots(("aaaa0001", COMPLETE), ("bad00003", INCOMPLETE), ("0c1a5502", LEGACY_UNTAGGED))
    result = restore.run("restore-id", "bad00003", str(tmp_path / "t1"))
    assert result.returncode == 0 and "SNAPSHOT bad00003 IS INCOMPLETE" in result.stdout
    assert (tmp_path / "t1" / "marker").read_text(encoding="utf-8") == "bad00003"
    result = restore.run("restore-id", "0c1a5502", str(tmp_path / "t2"))
    assert result.returncode == 0 and "SNAPSHOT 0c1a5502 IS NOT MARKED COMPLETE" in result.stdout
    result = restore.run("restore-id", "aaaa0001", str(tmp_path / "t3"))
    assert result.returncode == 0 and "WARN" not in result.stdout

    listing = restore.run("list").stdout
    rows = {
        fields[0]: fields[2]
        for fields in (line.split() for line in listing.split("\n"))
        if len(fields) >= 4 and fields[0] in ("aaaa0001", "bad00003", "0c1a5502")
    }
    assert rows == {"aaaa0001": "complete", "bad00003": "incomplete", "0c1a5502": "unclassified"}


# --- C: which repository ---------------------------------------------------------------------------------------------


def test_restore_uses_the_backup_volume_when_the_default_path_holds_no_repository_on_the_host(
    restore: _Restore,
) -> None:
    shutil.rmtree(restore.host_repo)
    restore.make_repository(restore.volume_repo)
    restore.volume(restore.volume_repo)
    restore.snapshots(("aaaa0001", COMPLETE))
    result = restore.run("list")
    assert result.returncode == 0, result.stdout + result.stderr
    assert {repository for repository, _ in restore.restic_calls()} == {str(restore.volume_repo)}  # was the host path
    assert f"using the backup container's volume myproj_backup_storage at {restore.volume_repo}" in result.stdout
    assert any(
        call.endswith("myproj_backup_storage") and call.startswith("volume inspect") for call in restore.docker_calls()
    )


def test_restore_stops_when_host_directory_and_volume_both_hold_a_repository(restore: _Restore) -> None:
    restore.make_repository(restore.volume_repo)
    restore.volume(restore.volume_repo)
    restore.snapshots(("aaaa0001", COMPLETE))
    result = restore.run("--verify")
    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "does not guess" in message and str(restore.host_repo) in message and str(restore.volume_repo) in message
    assert restore.restic_calls() == []

    # The operator chooses in the environment of the command; Docker is then not asked at all.
    for chosen in (restore.volume_repo, restore.host_repo):
        (restore.fake / "restic.log").write_text("", encoding="utf-8")
        (restore.fake / "docker.log").write_text("", encoding="utf-8")
        result = restore.run("list", RESTIC_REPOSITORY=str(chosen))
        assert result.returncode == 0, result.stdout + result.stderr
        assert {repository for repository, _ in restore.restic_calls()} == {str(chosen)}
        assert restore.docker_calls() == []


def test_restore_repository_detection_other_cases(restore: _Restore) -> None:
    restore.snapshots(("aaaa0001", COMPLETE))
    # Repository on the host, volume present but empty: the host directory.
    restore.volume_repo.mkdir(parents=True)
    restore.volume(restore.volume_repo)
    result = restore.run("list")
    assert result.returncode == 0 and {r for r, _ in restore.restic_calls()} == {str(restore.host_repo)}
    # No backup volume, or no Docker daemon: the path as it is.
    restore.volume(None)
    assert restore.run("list").returncode == 0
    (restore.fake / "docker_down").write_text("", encoding="utf-8")
    assert restore.run("list").returncode == 0
    (restore.fake / "docker_down").unlink()
    assert {r for r, _ in restore.restic_calls()} == {str(restore.host_repo)}

    # Nothing on the host and no volume found: a clear stop instead of restic's "does not exist".
    shutil.rmtree(restore.host_repo)
    (restore.fake / "restic.log").write_text("", encoding="utf-8")
    result = restore.run("list")
    assert result.returncode != 0 and "Docker reports no backup volume" in result.stdout + result.stderr
    assert restore.restic_calls() == []

    # Nothing on the host and a volume whose mountpoint is not on this filesystem (Docker in a VM).
    restore.volume(restore.root / "not-here" / "_data")
    (restore.fake / "restic.log").write_text("", encoding="utf-8")
    result = restore.run("--verify")
    assert result.returncode != 0 and "throwaway container" in result.stdout + result.stderr
    assert restore.restic_calls() == []
    # Neither place holds a repository.
    restore.volume(restore.volume_repo)
    result = restore.run("--verify")
    assert result.returncode != 0 and "holds a restic repository" in result.stdout + result.stderr
    assert restore.restic_calls() == []

    # A remote repository in .env is used as it is.
    restore.env_file.write_text(
        "RESTIC_REPOSITORY=s3:https://s3.example.invalid/bucket\nRESTIC_PASSWORD=test\n", encoding="utf-8"
    )
    (restore.fake / "docker.log").write_text("", encoding="utf-8")
    assert restore.run("list").returncode == 0
    assert {r for r, _ in restore.restic_calls()} == {"s3:https://s3.example.invalid/bucket"}
    assert restore.docker_calls() == []


def test_backup_readme_documents_both_ways_to_reach_the_container_repository() -> None:
    readme = (ROOT / "software" / "backup" / "README.md").read_text(encoding="utf-8")
    assert "--volumes-from sovereign_backup" in readme
    assert "apk add -q bash sqlite coreutils tar gzip && cd /config/backup && bash ./restore.sh --verify" in readme
    assert "docker volume inspect" in readme and "needs network access" in readme
    assert "sovereign-complete" in readme and "sovereign-incomplete" in readme
    assert "restic forget --tag sovereign-complete" in readme


class TestRound6Leftovers:
    """Items found while closing round 6: one low-battery threshold, chart thresholds, shedder source flag."""

    def test_bootstrap_low_battery_follows_configured_l3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sovereign_dc import config as config_module
        from sovereign_dc.agents import bootstrap_provisioner as bp

        for name, value in (("L1", "60"), ("L2", "40"), ("L3", "35"), ("L4", "15")):
            monkeypatch.setenv(f"SHEDDING_{name}_SOC", value)
        config_module.reset_config()
        assert bp.low_battery_soc() == 35.0

    def test_chart_passes_shedding_thresholds_to_exporter(self) -> None:
        root = Path(__file__).resolve().parents[1] / "kubernetes" / "helm" / "sovereign-stack"
        template = (root / "templates" / "deployment-telemetry.yaml").read_text(encoding="utf-8")
        values = (root / "values.yaml").read_text(encoding="utf-8")
        for level in ("1", "2", "3", "4"):
            assert f"SHEDDING_L{level}_SOC" in template
            assert f"l{level}Soc:" in values

    def test_load_shedder_holds_when_source_is_down(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "software" / "telemetry" / "load_shedder.sh").read_text(
            encoding="utf-8"
        )
        check = script.index("sovereign_telemetry_source_up")
        assert check < script.index("soc=$(awk")


def test_top_level_verify_shortcut_is_the_same_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`smdc verify` and `smdc app verify` reach the same handler with the same arguments."""
    missing = str(tmp_path / "absent.smdc-app")
    codes = []
    for argv in (
        ["smdc", "verify", missing, "--public-key", "00"],
        ["smdc", "app", "verify", missing, "--public-key", "00"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        try:
            codes.append(cli.main() or 0)
        except SystemExit as exc:
            codes.append(exc.code)
    assert codes[0] == codes[1] != 0
