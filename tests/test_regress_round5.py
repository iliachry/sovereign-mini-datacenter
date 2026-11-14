"""Regression tests for the round-5 close-out (round-4 leftovers).

* load_shedder.sh: an unknown Docker state holds the pause marker; the four SoC thresholds
  are one policy.
* The shedding policy is validated (L1 > L2 > L3 > L4, finite, 0-100) and tracked with a
  hysteresis, by one block of code that is byte-identical in ``config.py`` and the
  standalone sentinel.
* ``EnterpriseManager.start_app`` applies the last known shedding level at once.
* MCP ``set_load_shedding`` describes the implemented effect; ``run_metaverse_sim_cycle``
  runs on the shared orchestrator.
* ``register_app`` fails when the registry cannot be saved.
* One node id: the DTN router's default EID is derived from ``NODE_ID``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from sovereign_dc import cli
from sovereign_dc import config as config_module
from sovereign_dc.agents import sentinel_copilot
from sovereign_dc.config import DEFAULT_NODE_ID, SovereignConfig
from sovereign_dc.enterprise import AppManifest, AppStatus, EnterpriseManager, EnterpriseRegistry, PowerPriority
from sovereign_dc.enterprise.schema import SHED_PAUSE_LEVEL, PowerPolicy
from sovereign_dc.events import Event, SovereignEventBus, get_event_bus
from sovereign_dc.mcp import tools as mcp_tools
from sovereign_dc.space.dtn import router as dtn_router

ROOT = Path(__file__).resolve().parent.parent
ENV_NAMES = ("SHEDDING_L1_SOC", "SHEDDING_L2_SOC", "SHEDDING_L3_SOC", "SHEDDING_L4_SOC")
DEFAULTS = (50.0, 30.0, 20.0, 10.0)
BLOCK_BEGIN = "# --- shared shedding policy: begin"
BLOCK_END = "# --- shared shedding policy: end"


@pytest.fixture(autouse=True)
def _no_shedding_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*ENV_NAMES, "SHEDDING_HYSTERESIS_SOC", "NODE_ID", "DTN_LOCAL_EID"):
        monkeypatch.delenv(name, raising=False)


# --- one policy block, two files ---------------------------------------------------------------


def _shared_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(BLOCK_BEGIN) == 1 and text.count(BLOCK_END) == 1, path
    return text[text.index(BLOCK_BEGIN) : text.index(BLOCK_END)]


def test_policy_block_is_byte_identical_in_config_and_both_sentinel_copies() -> None:
    config_block = _shared_block(ROOT / "src" / "sovereign_dc" / "config.py")
    assert "def compute_shedding_level(" in config_block
    assert "def checked_shedding_thresholds(" in config_block
    assert config_block == _shared_block(ROOT / "src" / "sovereign_dc" / "agents" / "sentinel_copilot.py")
    assert config_block == _shared_block(ROOT / "software" / "agents" / "sentinel_copilot.py")
    assert config_module.DEFAULT_SHEDDING_THRESHOLDS == sentinel_copilot.DEFAULT_SHEDDING_THRESHOLDS == DEFAULTS
    assert config_module.SHEDDING_ENV_NAMES == sentinel_copilot.SHEDDING_ENV_NAMES == ENV_NAMES
    assert config_module.DEFAULT_SHEDDING_HYSTERESIS_SOC == sentinel_copilot.DEFAULT_SHEDDING_HYSTERESIS_SOC == 2.0


def test_config_and_sentinel_agree_for_a_sweep_of_soc_and_previous_levels() -> None:
    cfg = SovereignConfig()
    assert cfg.shedding_hysteresis_soc == sentinel_copilot.SHEDDING_HYSTERESIS == 2.0
    for previous in (None, 0, 1, 2, 3, 4):
        for step in range(-20, 1021):
            soc = step / 10.0
            level = cfg.get_shedding_level(soc, previous)
            assert level == sentinel_copilot.shedding_level(soc, previous_level=previous), (soc, previous)
            assert level == config_module.compute_shedding_level(soc, DEFAULTS, previous, 2.0)
            plain = cfg.get_shedding_level(soc)
            # Hysteresis never lowers the level below the plain mapping, and never raises it
            # above the level the node was already at.
            assert level >= plain
            if previous is None or previous <= plain:
                assert level == plain
            else:
                assert level <= previous


# --- hysteresis ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous", "soc", "expected"),
    [
        (None, 30.5, 1),  # stateless: the plain mapping
        (2, 29.9, 2),
        (2, 30.0, 2),  # at the threshold: plain mapping says 1, the level is held
        (2, 31.9, 2),
        (2, 32.0, 1),  # 2 points above the level-2 threshold: left
        (1, 30.5, 1),  # a level that was not entered is not held
        (1, 29.9, 2),  # rising is immediate
        (3, 21.9, 3),
        (3, 22.0, 2),
        (4, 11.9, 4),
        (4, 12.0, 3),
        (1, 51.9, 1),
        (1, 52.0, 0),
        (4, 60.0, 0),  # a large recovery walks down through every level
        (3, 25.0, 2),
        (0, 5.0, 4),
    ],
)
def test_a_level_is_left_only_two_points_above_its_threshold(previous: int | None, soc: float, expected: int) -> None:
    assert SovereignConfig().get_shedding_level(soc, previous) == expected
    assert sentinel_copilot.shedding_level(soc, previous_level=previous) == expected


def test_hysteresis_is_configurable_and_zero_switches_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHEDDING_HYSTERESIS_SOC", "5")
    cfg = SovereignConfig.from_env()
    assert cfg.shedding_hysteresis_soc == sentinel_copilot.shedding_hysteresis() == 5.0
    assert cfg.get_shedding_level(34.9, 2) == 2 and cfg.get_shedding_level(35.0, 2) == 1
    monkeypatch.setenv("SHEDDING_HYSTERESIS_SOC", "0")
    cfg = SovereignConfig.from_env()
    assert cfg.shedding_hysteresis_soc == sentinel_copilot.shedding_hysteresis() == 0.0
    assert cfg.get_shedding_level(30.0, 2) == 1


@pytest.mark.parametrize("raw", ["-1", "11", "nan", "inf", "two"])
def test_unusable_hysteresis_falls_back_to_the_default(
    raw: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SHEDDING_HYSTERESIS_SOC", raw)
    with caplog.at_level(logging.WARNING):
        assert sentinel_copilot.shedding_hysteresis() == 2.0
        assert SovereignConfig.from_env().shedding_hysteresis_soc == 2.0
    assert "SHEDDING_HYSTERESIS_SOC" in caplog.text


def _run_sentinel(sequence: list[float]) -> list[dict[str, Any]]:
    """Runs run_copilot() over a sequence of SoC readings and returns the published payloads."""
    payloads: list[dict[str, Any]] = []

    def record(event: Event) -> None:
        payloads.append(dict(event.payload))

    feed = iter(sequence)

    def telemetry_feed() -> dict[str, float]:
        try:
            soc = next(feed)
        except StopIteration:
            raise KeyboardInterrupt from None
        return {sentinel_copilot.SOC_METRIC: soc, sentinel_copilot.SOLAR_METRIC: 0.0}

    bus = get_event_bus()
    bus.subscribe("load_shedding.changed", record)
    try:
        with (
            patch.object(sentinel_copilot, "get_telemetry", side_effect=telemetry_feed),
            patch("time.sleep", return_value=None),
            pytest.raises(KeyboardInterrupt),
        ):
            sentinel_copilot.run_copilot()
    finally:
        bus.unsubscribe("load_shedding.changed", record)
    return payloads


def test_sentinel_does_not_flap_when_soc_hovers_at_a_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mode_file = tmp_path / "sovereign_mode"
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))
    hovering = [29.9, 30.1, 29.8, 30.2, 29.9, 30.3, 30.0, 30.4]
    payloads = _run_sentinel([35.0, *hovering, 32.0])
    levels = [p["level"] for p in payloads]
    modes = [p["mode"] for p in payloads]
    # Before: 1, 2, 1, 2, 1, 2, 1, ... (ECO_PRESERVATION <-> NORMAL on every poll).
    assert levels == [1, 2, 1]
    assert modes == ["NORMAL", "ECO_PRESERVATION", "NORMAL"]
    assert payloads[-1]["battery_soc"] == 32.0
    assert mode_file.read_text(encoding="utf-8") == "NORMAL"


def test_sentinel_hovering_drives_the_manager_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager = EnterpriseManager(registry=registry, event_bus=get_event_bus())
    registry.register_app(_manifest("batch", PowerPriority.L3_DEFERRABLE))
    assert manager.start_app("batch")[0]
    transitions: list[str] = []
    get_event_bus().subscribe("enterprise.batch.*", lambda event: transitions.append(event.event_type.split(".")[-1]))
    _run_sentinel([40.0, 29.9, 30.1, 29.9, 30.1, 29.9, 31.5, 33.0])
    assert transitions == ["paused", "resumed"]  # was paused/resumed on every poll


def test_a_restarted_sentinel_starts_from_the_plain_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    assert [p["level"] for p in _run_sentinel([30.5])] == [1]
    assert sentinel_copilot.evaluate_state({sentinel_copilot.SOC_METRIC: 30.5}, 0.0, previous_level=2).level == 2  # type: ignore[union-attr]


# --- threshold validation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"SHEDDING_L2_SOC": "15"}, "SHEDDING_L2_SOC=15 is not above SHEDDING_L3_SOC=20"),
        ({"SHEDDING_L3_SOC": "30"}, "SHEDDING_L2_SOC=30 is not above SHEDDING_L3_SOC=30"),
        ({"SHEDDING_L1_SOC": "20", "SHEDDING_L3_SOC": "50"}, "SHEDDING_L1_SOC=20 is not above SHEDDING_L2_SOC=30"),
        ({"SHEDDING_L1_SOC": "101"}, "SHEDDING_L1_SOC=101 is outside 0-100"),
        ({"SHEDDING_L4_SOC": "-5"}, "SHEDDING_L4_SOC=-5 is outside 0-100"),
        ({"SHEDDING_L3_SOC": "nan"}, "SHEDDING_L3_SOC is not a finite number"),
        ({"SHEDDING_L1_SOC": "inf"}, "SHEDDING_L1_SOC is not a finite number"),
    ],
)
def test_a_broken_policy_falls_back_to_all_defaults_with_one_warning(
    overrides: dict[str, str], named: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    for build in (
        lambda: SovereignConfig.from_env().shedding_thresholds,
        sentinel_copilot.shedding_thresholds,
    ):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert build() == DEFAULTS
        warnings = [r for r in caplog.records if "load-shedding thresholds" in r.getMessage()]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert named in message
        assert all(name in message for name in ENV_NAMES)
        assert "SHEDDING_L1_SOC > SHEDDING_L2_SOC > SHEDDING_L3_SOC > SHEDDING_L4_SOC" in message


def test_a_valid_custom_policy_is_kept_without_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name, value in zip(ENV_NAMES, ("100", "60.5", "40", "0"), strict=True):
        monkeypatch.setenv(name, value)
    with caplog.at_level(logging.WARNING):
        assert SovereignConfig.from_env().shedding_thresholds == (100.0, 60.5, 40.0, 0.0)
        assert sentinel_copilot.shedding_thresholds() == (100.0, 60.5, 40.0, 0.0)
    assert "load-shedding thresholds" not in caplog.text


def test_yaml_policy_is_validated_too(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    yaml_file = tmp_path / "sovereign.yaml"
    yaml_file.write_text("shedding_l1_soc: 25\nshedding_l2_soc: [1]\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = SovereignConfig.from_yaml_and_env(str(yaml_file))
    assert cfg.shedding_thresholds == DEFAULTS
    assert "load-shedding thresholds" in caplog.text


# --- load_shedder.sh ---------------------------------------------------------------------------

FAKE_CURL = '#!/bin/sh\ncat "$FAKE/metrics"\n'
FAKE_DOCKER = """#!/bin/sh
case "$1" in
  inspect)
    if [ -e "$FAKE/daemon_down" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi
    if [ -e "$FAKE/absent" ]; then echo "Error: No such object: $4" >&2; exit 1; fi
    cat "$FAKE/status" ;;
  pause) echo "pause $2" >>"$FAKE/docker.log"; echo paused >"$FAKE/status" ;;
  unpause) echo "unpause $2" >>"$FAKE/docker.log"; echo running >"$FAKE/status" ;;
esac
"""


class _Shedder:
    """Runs software/telemetry/load_shedder.sh against fake ``curl`` and ``docker`` binaries."""

    def __init__(self, tmp_path: Path) -> None:
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        self.fake.mkdir()
        self.bin.mkdir()
        for name, body in (("curl", FAKE_CURL), ("docker", FAKE_DOCKER)):
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        (self.fake / "status").write_text("running\n", encoding="utf-8")
        (self.fake / "docker.log").write_text("", encoding="utf-8")
        self.marker = self.fake / "marker"

    def run(self, soc: float, coolant: float = 30.0, **env: str) -> subprocess.CompletedProcess[str]:
        (self.fake / "metrics").write_text(
            f"sovereign_battery_soc_percent {soc}\nsovereign_temp_coolant_celsius {coolant}\n", encoding="utf-8"
        )
        full_env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE": str(self.fake),
            "TMPDIR": str(self.fake),
            "HOME": str(self.fake),
            "LOAD_SHED_ACTION": "pause",
            "LOAD_SHED_STATE_FILE": str(self.marker),
            **env,
        }
        return subprocess.run(  # noqa: S603 - the repository's own script with fake binaries
            ["bash", str(ROOT / "software" / "telemetry" / "load_shedder.sh")],  # noqa: S607
            env=full_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def docker_log(self) -> list[str]:
        return (self.fake / "docker.log").read_text(encoding="utf-8").split("\n")[:-1]


@pytest.fixture
def shedder(tmp_path: Path) -> _Shedder:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")
    return _Shedder(tmp_path)


def test_load_shedder_holds_the_marker_while_the_docker_state_is_unknown(shedder: _Shedder) -> None:
    assert shedder.run(19.0).returncode == 0
    assert shedder.docker_log() == ["pause sovereign_ollama"] and shedder.marker.exists()

    (shedder.fake / "daemon_down").touch()
    result = shedder.run(80.0)  # recovery telemetry healthy, docker inspect fails
    assert result.returncode == 0
    assert "cannot be determined" in result.stdout
    assert shedder.marker.read_text(encoding="utf-8").split("\n")[:2] == ["container=sovereign_ollama", "cause=soc"]
    assert shedder.docker_log() == ["pause sovereign_ollama"]  # was: marker deleted here

    (shedder.fake / "daemon_down").unlink()
    result = shedder.run(80.0)  # docker is back: the still-paused container is resumed
    assert result.returncode == 0
    assert shedder.docker_log() == ["pause sovereign_ollama", "unpause sovereign_ollama"]
    assert not shedder.marker.exists()
    assert "not by this sentinel" not in result.stdout


@pytest.mark.parametrize(
    ("state_file", "content", "word"), [("absent", "", "absent"), ("status", "exited\n", "exited")]
)
def test_load_shedder_clears_the_marker_on_a_definite_answer(
    shedder: _Shedder, state_file: str, content: str, word: str
) -> None:
    assert shedder.run(19.0).returncode == 0 and shedder.marker.exists()
    (shedder.fake / state_file).write_text(content, encoding="utf-8")
    result = shedder.run(80.0)
    assert result.returncode == 0 and f"state: {word}" in result.stdout
    assert not shedder.marker.exists()
    assert shedder.docker_log() == ["pause sovereign_ollama"]


def test_load_shedder_does_not_shed_on_an_unknown_docker_state(shedder: _Shedder) -> None:
    (shedder.fake / "daemon_down").touch()
    result = shedder.run(5.0)
    assert result.returncode == 0 and "Cannot determine the state" in result.stdout
    assert shedder.docker_log() == [] and not shedder.marker.exists()


@pytest.mark.parametrize(
    "env",
    [
        {"SHEDDING_L2_SOC": "15"},
        {"SHEDDING_L4_SOC": "20"},
        {"SHEDDING_L1_SOC": "101"},
        {"SHEDDING_L4_SOC": "-1"},
        {"SHEDDING_L3_SOC": "50"},
        {"SHEDDING_L2_SOC": "abc"},
    ],
)
def test_load_shedder_refuses_a_broken_policy(shedder: _Shedder, env: dict[str, str]) -> None:
    result = shedder.run(5.0, **env)
    assert result.returncode == 2
    assert "SHEDDING_L1_SOC > SHEDDING_L2_SOC > SHEDDING_L3_SOC > SHEDDING_L4_SOC" in result.stdout
    assert shedder.docker_log() == [] and not shedder.marker.exists()


def test_load_shedder_defaults_for_all_four_thresholds_equal_the_config_defaults() -> None:
    script = (ROOT / "software" / "telemetry" / "load_shedder.sh").read_text(encoding="utf-8")
    found = dict(re.findall(r'^SOC_THRESHOLD_\w+="\$\{(SHEDDING_L[1-4]_SOC):-([0-9.]+)\}"$', script, re.MULTILINE))
    assert {name: float(value) for name, value in found.items()} == dict(zip(ENV_NAMES, DEFAULTS, strict=True))


def test_shell_scripts_under_pipefail_have_no_early_exit_readers() -> None:
    """`producer | grep -q` / `| head` under pipefail turns a match into SIGPIPE (141)."""
    offenders: list[str] = []
    checked = 0
    for script in sorted([*(ROOT / "software").rglob("*.sh"), *(ROOT / "scripts").rglob("*.sh")]):
        text = script.read_text(encoding="utf-8")
        if not re.search(r"^set -[a-z]*o pipefail$", text, re.MULTILINE):
            continue
        checked += 1
        for number, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
            if re.search(r"\|\s*(grep\s+-[A-Za-z]*q|head\b)", code):
                offenders.append(f"{script.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == []
    assert checked >= 8  # restore.sh, load_shedder.sh, setup.sh, register-node.sh, ...


# --- start_app applies the policy at once --------------------------------------------------------


def _manifest(app_id: str, tier: PowerPriority, min_battery_soc: float = 0.0) -> AppManifest:
    return AppManifest(
        name=app_id,
        app_id=app_id,
        entrypoint="python3 app.py",
        power=PowerPolicy(tier=tier, min_battery_soc=min_battery_soc),
    )


@pytest.fixture
def manager(tmp_path: Path) -> EnterpriseManager:
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    return EnterpriseManager(registry=registry, event_bus=SovereignEventBus())


def _shed(mgr: EnterpriseManager, **payload: Any) -> None:
    mgr.event_bus.publish(Event(event_type="load_shedding.changed", source="test", payload=payload))


def _status(mgr: EnterpriseManager, app_id: str) -> AppStatus:
    state = mgr.get_runtime_state(app_id)
    assert state is not None
    return state.status


def test_app_started_while_shedding_is_active_starts_paused_and_resumes_later(manager: EnterpriseManager) -> None:
    for tier in PowerPriority:
        manager.registry.register_app(_manifest(f"app-{tier.value.lower()}", tier))
    events: list[tuple[str, dict[str, Any]]] = []
    manager.event_bus.subscribe("enterprise.*", lambda event: events.append((event.event_type, dict(event.payload))))
    _shed(manager, level=3, mode="ECO_PRESERVATION", battery_soc=18.0)  # before any app runs

    for tier in PowerPriority:
        app_id = f"app-{tier.value.lower()}"
        ok, message = manager.start_app(app_id)
        assert ok
        pause_level = SHED_PAUSE_LEVEL.get(tier)
        if pause_level is not None and pause_level <= 3:
            assert _status(manager, app_id) == AppStatus.PAUSED  # was RUNNING until the next event
            assert "held paused by load shedding" in message and "level 3" in message
        else:
            assert _status(manager, app_id) == AppStatus.RUNNING
            assert "held paused" not in message

    kinds = [name.rsplit(".", 1)[-1] for name, _ in events if name.startswith("enterprise.app-l4_idle.")]
    assert kinds == ["started", "paused"]
    paused_payload = next(p for name, p in events if name == "enterprise.app-l4_idle.paused")
    assert paused_payload["paused_by"] == "load_shedding" and paused_payload["status"] == "paused"
    assert "battery SoC 18.0%" in paused_payload["reason"]

    _shed(manager, level=0, mode="NORMAL", battery_soc=60.0)
    assert all(_status(manager, f"app-{tier.value.lower()}") == AppStatus.RUNNING for tier in PowerPriority)


def test_start_app_applies_the_soc_floor_of_the_last_event(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("floor", PowerPriority.L1_STANDARD, min_battery_soc=40.0))
    _shed(manager, level=1, mode="NORMAL", battery_soc=35.0)
    ok, message = manager.start_app("floor")
    assert ok and "soc_floor" in message
    assert _status(manager, "floor") == AppStatus.PAUSED
    _shed(manager, level=1, mode="NORMAL", battery_soc=41.0)  # inside the floor's hysteresis band
    assert _status(manager, "floor") == AppStatus.PAUSED
    _shed(manager, level=1, mode="NORMAL", battery_soc=42.0)
    assert _status(manager, "floor") == AppStatus.RUNNING


def test_start_app_runs_when_no_event_was_seen_or_shedding_has_eased(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("idle", PowerPriority.L4_IDLE))
    assert manager.start_app("idle")[0] and _status(manager, "idle") == AppStatus.RUNNING  # no level known yet
    assert manager.stop_app("idle")[0]
    _shed(manager, level=2, mode="ECO_PRESERVATION")
    _shed(manager, level=0, mode="NORMAL")
    assert manager.start_app("idle")[0] and _status(manager, "idle") == AppStatus.RUNNING
    # An unrecognised event does not overwrite the last known level.
    _shed(manager, level=2)
    _shed(manager, mode="???")
    assert manager.restart_app("idle")[0] and _status(manager, "idle") == AppStatus.PAUSED
    # The operator can still override the hold.
    assert manager.resume_app("idle", reason="operator")[0] and _status(manager, "idle") == AppStatus.RUNNING


def test_cli_app_start_reports_the_hold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manager = mcp_tools.get_enterprise_manager()
    manager.registry.register_app(_manifest("cli-idle", PowerPriority.L4_IDLE))
    get_event_bus().publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4}))
    with patch("sovereign_dc.enterprise.manager.EnterpriseManager", return_value=manager):
        assert cli.cmd_app_start(Namespace(app_id="cli-idle")) == 0
    out = capsys.readouterr().out
    assert "held paused by load shedding" in out and "✔" not in out


# --- MCP ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4])
def test_set_load_shedding_describes_the_implemented_effect(level: int) -> None:
    result = mcp_tools.tool_set_load_shedding({"level": f"L{level}"})
    expected = sorted(tier.value for tier, pause_level in SHED_PAUSE_LEVEL.items() if level >= pause_level)
    assert result["success"] is True and result["hardware_actuated"] is False
    assert result["paused_tiers"] == expected
    assert "L0_CRITICAL" not in result["paused_tiers"]
    for tier in expected:
        assert tier in result["description"]
    # Hardware actions are no longer passed off as what the level does.
    for word in ("GPU", "PDU", "TDP", "outlets", "Jetson"):
        assert word not in result["description"]
    assert isinstance(result["design_targets_not_implemented"], list)
    assert bool(result["design_targets_not_implemented"]) is (level >= 2)
    assert "design_targets_not_implemented" in result["note"]


def test_set_load_shedding_description_matches_what_the_manager_does(tmp_path: Path) -> None:
    manager = mcp_tools.get_enterprise_manager()
    for tier in PowerPriority:
        manager.registry.register_app(_manifest(f"mcp-{tier.value.lower()}", tier))
        assert manager.start_app(f"mcp-{tier.value.lower()}")[0]
    for level in (0, 1, 2, 3, 4, 2, 0):
        result = mcp_tools.tool_set_load_shedding({"level": f"L{level}"})
        paused = sorted(
            s.manifest.power.tier.value for s in manager.list_runtime_states() if s.status == AppStatus.PAUSED
        )
        assert paused == result["paused_tiers"], level
    tool = next(t for t in mcp_tools.get_mcp_tools() if t.name == "set_load_shedding")
    assert "L4_IDLE" in tool.description and "L0_CRITICAL" in tool.description
    assert "paused at L2+" not in tool.description


def test_metaverse_cycles_run_on_the_shared_orchestrator() -> None:
    assert mcp_tools.get_5g_slices_snapshot()["source"] == "fresh_simulation"
    result = mcp_tools.tool_run_metaverse_sim_cycle({"cycles": 2, "deterministic": True})
    assert result["orchestrator"] == "process_orchestrator"
    assert result["cycles_executed"] == 2 and result["total_cycles_executed"] == 2
    snapshot = mcp_tools.get_5g_slices_snapshot()  # was still "fresh_simulation" with 0 cycles
    assert snapshot["source"] == "process_orchestrator" and snapshot["total_cycles_executed"] == 2
    again = mcp_tools.tool_run_metaverse_sim_cycle({"cycles": 1})
    assert again["total_cycles_executed"] == 3
    assert mcp_tools.tool_get_5g_slices_status({})["total_cycles_executed"] == 3


def test_metaverse_cycles_with_a_seed_use_a_separate_run_and_say_so() -> None:
    first = mcp_tools.tool_run_metaverse_sim_cycle({"cycles": 2, "seed": 7})
    second = mcp_tools.tool_run_metaverse_sim_cycle({"cycles": 2, "seed": 7})
    assert first["orchestrator"] == second["orchestrator"] == "separate_seeded_run"
    assert first["seed"] == 7 and "separate orchestrator" in first["note"]
    assert first["total_cycles_executed"] == 2
    assert first["uav_final_position"] == second["uav_final_position"]  # the seed keeps its meaning
    assert mcp_tools.get_5g_slices_snapshot()["total_cycles_executed"] == 0  # shared state untouched
    schema = next(t for t in mcp_tools.get_mcp_tools() if t.name == "run_metaverse_sim_cycle").input_schema
    assert "default" not in schema["properties"]["seed"]


# --- registry: a failed save is a failed registration ---------------------------------------------


def test_register_app_fails_when_the_registry_directory_is_unwritable(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    registry = EnterpriseRegistry(registry_file=blocker / "registry.json")  # parent is a file
    ok, errors = registry.register_app(_manifest("alpha", PowerPriority.L1_STANDARD))
    assert ok is False  # was (True, [])
    assert len(errors) == 1 and "registration was not saved" in errors[0] and "registry.json" in errors[0]
    assert registry.get_app("alpha") is None and registry.list_apps() == []
    assert registry.last_save_error


def test_register_app_reports_a_lock_timeout_and_keeps_the_previous_entry(tmp_path: Path) -> None:
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    original = _manifest("alpha", PowerPriority.L1_STANDARD)
    assert registry.register_app(original) == (True, [])
    replacement = _manifest("alpha", PowerPriority.L4_IDLE)
    with patch.object(EnterpriseRegistry, "_exclusive_lock", side_effect=TimeoutError("registry lock is held")):
        ok, errors = registry.register_app(replacement)
        assert ok is False and "registry lock is held" in errors[0]
        ok_new, errors_new = registry.register_app(_manifest("beta", PowerPriority.L2_BACKGROUND))
        assert ok_new is False and errors_new
        assert registry.unregister_app("alpha") is False and "registry lock is held" in (registry.last_save_error or "")
    kept = registry.get_app("alpha")
    assert kept is not None and kept.power.tier == original.power.tier == PowerPriority.L1_STANDARD
    assert registry.get_app("beta") is None
    # Nothing of the failed changes is replayed by a later, successful save.
    assert registry.register_app(_manifest("gamma", PowerPriority.L1_STANDARD)) == (True, [])
    reopened = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert sorted(app.app_id for app in reopened.list_apps()) == ["alpha", "gamma"]
    alpha = reopened.get_app("alpha")
    assert alpha is not None and alpha.power.tier == PowerPriority.L1_STANDARD


def test_register_app_without_persist_is_unchanged(tmp_path: Path) -> None:
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    with patch.object(EnterpriseRegistry, "save_registry", side_effect=AssertionError("must not save")):
        assert registry.register_app(_manifest("alpha", PowerPriority.L1_STANDARD), persist=False) == (True, [])
    assert registry.get_app("alpha") is not None


def test_smdc_app_register_exits_non_zero_when_the_save_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app_dir = tmp_path / "demo-app"
    manifest = EnterpriseRegistry.scaffold_manifest(name="Demo App", app_id="demo-app")
    EnterpriseRegistry.create_project_scaffold(app_dir, manifest, create_sample_code=False)
    with patch.object(EnterpriseRegistry, "_exclusive_lock", side_effect=TimeoutError("registry lock is held")):
        assert cli.cmd_app_register(Namespace(path=str(app_dir))) == 1
    out = capsys.readouterr().out
    assert "Registration rejected" in out and "registry lock is held" in out
    assert "Successfully registered" not in out
    assert EnterpriseRegistry().get_app("demo-app") is None
    assert cli.cmd_app_register(Namespace(path=str(app_dir))) == 0
    with patch.object(EnterpriseRegistry, "_exclusive_lock", side_effect=TimeoutError("registry lock is held")):
        assert cli.cmd_app_unregister(Namespace(app_id="demo-app")) == 1
    out = capsys.readouterr().out
    assert "Not unregistered" in out and "not found" not in out


# --- one node identity ----------------------------------------------------------------------------


def test_dtn_router_default_eid_is_derived_from_the_node_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert dtn_router.DEFAULT_NODE_ID == DEFAULT_NODE_ID
    db = str(tmp_path / "spool.db")
    assert dtn_router.DTNRouter(db_path=db).local_node_eid == f"dtn://{DEFAULT_NODE_ID}.sovereign.space"
    monkeypatch.setenv("NODE_ID", "smdc-athens-07")
    assert dtn_router.DTNRouter(db_path=db).local_node_eid == "dtn://smdc-athens-07.sovereign.space"
    assert dtn_router.DTNRouter(db_path=db, local_node_eid="dtn://explicit").local_node_eid == "dtn://explicit"
    monkeypatch.setenv("DTN_LOCAL_EID", "dtn://override.example/space")
    assert dtn_router.default_local_eid() == "dtn://override.example/space"


def test_no_second_default_node_identity_in_the_sources() -> None:
    literal = re.compile(r"smdc-dgx-01")
    allowed = {
        "src/sovereign_dc/config.py",  # DEFAULT_NODE_ID
        "src/sovereign_dc/mesh/mesh_sync.py",  # standalone copy of the literal (tests compare)
        "src/sovereign_dc/space/dtn/router.py",  # standalone copy of the literal (tests compare)
    }
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative not in allowed:
            assert not literal.search(text), relative
        # The legacy identity is no longer a default EID anywhere.
        assert "dtn://smdc-node-01.space" not in text, relative
        assert 'local_node_eid: str = "dtn://' not in text, relative
    values = (ROOT / "kubernetes" / "helm" / "sovereign-stack" / "values.yaml").read_text(encoding="utf-8")
    assert "smdc-node-01" not in values
    assert f'nodeId: "{DEFAULT_NODE_ID}"' in values and 'localEid: ""' in values
    template = (ROOT / "kubernetes" / "helm" / "sovereign-stack" / "templates" / "deployment-space.yaml").read_text(
        encoding="utf-8"
    )
    assert "name: NODE_ID" in template and ".Values.space.dtnNode.nodeId" in template


def test_bootstrap_cli_default_node_id_is_the_shared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    class _Stop(Exception):
        pass

    def fake_chain(node_id: str) -> Any:
        seen["node_id"] = node_id
        raise _Stop

    with (
        patch("sovereign_dc.agents.technician_notifier.TechnicianNotifierChain", side_effect=fake_chain),
        pytest.raises(_Stop),
    ):
        cli.cmd_bootstrap(Namespace(node_id=None, role=None, dry_run=True, notify_test=False))
    assert seen["node_id"] == DEFAULT_NODE_ID
    monkeypatch.setenv("NODE_ID", "smdc-custom-02")
    with (
        patch("sovereign_dc.agents.technician_notifier.TechnicianNotifierChain", side_effect=fake_chain),
        pytest.raises(_Stop),
    ):
        cli.cmd_bootstrap(Namespace(node_id=None, role=None, dry_run=True, notify_test=False))
    assert seen["node_id"] == "smdc-custom-02"


# --- env template ---------------------------------------------------------------------------------


def test_env_example_documents_the_current_backup_and_shedding_variables() -> None:
    text = (ROOT / "software" / "env.example").read_text(encoding="utf-8")
    assert "BACKUP_POLL_SECONDS=300" in text.splitlines()
    assert not re.search(r"^#?BACKUP_INTERVAL_SECONDS=", text, re.MULTILINE)
    assert "BACKUP_INTERVAL_SECONDS is no longer read" in text
    assert "BACKUP_DUMP_DIR/generations/<epoch>/smdc-nextcloud-files" in text
    for name, default in zip(ENV_NAMES, DEFAULTS, strict=True):
        assert f"#{name}={default:g}" in text.splitlines()
    assert "#SHEDDING_HYSTERESIS_SOC=2" in text.splitlines()
    compose = (ROOT / "software" / "backup" / "docker-compose.backup.yml").read_text(encoding="utf-8")
    assert "BACKUP_POLL_SECONDS=${BACKUP_POLL_SECONDS:-300}" in compose and "BACKUP_INTERVAL_SECONDS" not in compose
    agents = (ROOT / "software" / "agents" / "docker-compose.agents.yml").read_text(encoding="utf-8")
    assert "SHEDDING_HYSTERESIS_SOC=${SHEDDING_HYSTERESIS_SOC:-2}" in agents
