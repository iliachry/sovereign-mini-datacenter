"""Regression tests for the round 3 findings (config coercion, MCP slice state, CLI output).

Every test keeps its state under ``tmp_path`` (see ``conftest.py``) and opens no network
connection: HTTP calls are replaced by recording fakes.
"""

from __future__ import annotations

import json
import logging
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from sovereign_dc import cli
from sovereign_dc import config as smdc_config
from sovereign_dc.config import SovereignConfig
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.mcp.resources import read_metaverse_slices_resource
from sovereign_dc.mcp.tools import tool_get_5g_slices_status
from sovereign_dc.web.dashboard import get_metaverse_orchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── 1. A non-numeric value in a numeric setting must not break configuration loading ──


@pytest.mark.parametrize(
    ("variable", "field_name", "default"),
    [
        ("EXPORTER_PORT", "exporter_port", 9101),
        ("SPACE_EXPORTER_PORT", "space_exporter_port", 9102),
        ("MQTT_BROKER_PORT", "mqtt_broker_port", 1883),
        ("MESH_POLL_INTERVAL_SECONDS", "mesh_poll_interval", 60.0),
        ("SENTINEL_POLL_INTERVAL_SECONDS", "sentinel_poll_interval", 30.0),
        ("SHEDDING_L1_SOC", "shedding_l1_soc", 50.0),
    ],
)
def test_non_numeric_env_value_is_ignored_with_a_warning_naming_the_variable(
    variable: str,
    field_name: str,
    default: float,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(variable, "abc")
    caplog.set_level(logging.WARNING, logger="SovereignConfig")

    cfg = SovereignConfig.from_env()

    assert getattr(cfg, field_name) == default
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(variable in message and "'abc'" in message for message in warnings)


def test_get_config_survives_a_bad_numeric_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPORTER_PORT", "abc")
    monkeypatch.setenv("MESH_POLL_INTERVAL_SECONDS", "abc")
    smdc_config.reset_config()

    cfg = smdc_config.get_config()

    assert cfg.exporter_port == 9101
    assert cfg.mesh_poll_interval == 60.0


def test_valid_numeric_env_values_still_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPORTER_PORT", "9201")
    monkeypatch.setenv("MESH_POLL_INTERVAL_SECONDS", "7.5")
    cfg = SovereignConfig.from_env()
    assert cfg.exporter_port == 9201
    assert cfg.mesh_poll_interval == 7.5


def test_bad_env_value_keeps_the_yaml_value_and_bad_yaml_value_keeps_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    yaml_file = tmp_path / "sovereign.yaml"
    yaml_file.write_text('exporter_port: 9301\nspace_exporter_port: "not-a-port"\n', encoding="utf-8")
    monkeypatch.setenv("EXPORTER_PORT", "abc")
    caplog.set_level(logging.WARNING, logger="SovereignConfig")

    cfg = SovereignConfig.from_yaml_and_env(str(yaml_file))

    assert cfg.exporter_port == 9301
    assert cfg.space_exporter_port == 9102
    text = caplog.text
    assert "environment variable EXPORTER_PORT" in text
    assert "key space_exporter_port" in text


# ── 2. MCP 5G slice figures come from the shared orchestrator and state their source ──


def test_mcp_slices_report_fresh_simulation_when_no_cycle_has_run() -> None:
    result = tool_get_5g_slices_status({})

    assert result["source"] == "fresh_simulation"
    assert result["total_cycles_executed"] == 0
    assert result["simulated"] is True
    assert "note" in result
    assert result["slices"]["URLLC"]["total_packets"] == 0


def test_mcp_slices_match_the_dashboard_orchestrator_after_cycles() -> None:
    orch = get_metaverse_orchestrator()
    orch.run_cycles(count=2, deterministic=True)
    expected = orch.slicing.get_summary()
    assert expected["URLLC"]["total_packets"] > 0

    result = tool_get_5g_slices_status({})
    resource = json.loads(read_metaverse_slices_resource())

    assert result["source"] == "process_orchestrator"
    assert result["total_cycles_executed"] == 2
    assert "note" not in result
    assert result["slices"] == expected
    assert resource["source"] == "process_orchestrator"
    assert resource["slices"] == expected
    assert resource["uri"] == "smdc://metaverse/5g/slices"


# ── 3. Ledger history shows micro-credit digits ──


def test_economy_history_prints_micro_credit_amounts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db_path)
    recipient = NodeWallet.create(node_id="history-node").address
    ledger.mint(recipient, 0.000125, memo="MICRO")
    ledger.mint(recipient, 25.0, memo="WHOLE")

    cli.cmd_economy_history(Namespace(limit=10, db_path=db_path))

    out = capsys.readouterr().out
    assert "0.000125 credits" in out
    assert "25.00 credits" in out
    assert "0.00 credits" not in out


# ── 4. `smdc status` reads the configured exporter endpoints ──


def test_cmd_status_uses_configured_exporter_endpoints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POWER_EXPORTER_URL", "http://127.0.0.1:19101/metrics")
    monkeypatch.setenv("SPACE_EXPORTER_PORT", "19102")
    smdc_config.reset_config()
    requested: list[str] = []

    def fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        requested.append(request.full_url)
        raise OSError("refused")

    with (
        patch("subprocess.run", side_effect=OSError("no docker")),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        cli.cmd_status(Namespace())

    assert requested == ["http://127.0.0.1:19101/metrics", "http://localhost:19102/metrics"]
    out = capsys.readouterr().out
    assert "offline at http://127.0.0.1:19101/metrics" in out
    assert ":9101" not in out


# ── 5. The audit is a host hardening check, not a compliance audit or CIS benchmark ──


def test_audit_is_labelled_as_a_host_hardening_audit_everywhere() -> None:
    sources = {
        "cli.py": (REPO_ROOT / "src" / "sovereign_dc" / "cli.py").read_text(encoding="utf-8"),
        "audit.sh": (REPO_ROOT / "src" / "sovereign_dc" / "security" / "audit.sh").read_text(encoding="utf-8"),
        "README.md": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
    }
    for name, text in sources.items():
        assert "Security Compliance Audit" not in text, name
        assert "CIS benchmarks" not in text, name
        assert "Intrusion Prevention" not in text, name
    assert "Host Hardening Audit" in sources["cli.py"]
    assert "Host Hardening Audit" in sources["audit.sh"]
    assert "CrowdSec intrusion detection (no bouncer)" in sources["cli.py"]
