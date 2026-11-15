"""Regression tests for tri-council review round 8.

1. A running EnterpriseManager follows re-registration and unregistration by another process.
2. MCP pricing names the source of each input and needs telemetry only for a missing one.
3. backup.sh / restore.sh: further .env forms decode as Docker Compose decodes them, or are refused.
4. GitLab SMTP credentials reach gitlab.rb through the environment, not as Ruby source text.
5. Documentation: state channels are unidirectional, the CLI is argparse, no unused DOMAIN_MAILCOW.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from sovereign_dc.hal import power as hal_power
from sovereign_dc.hal.power import TelemetryUnavailable
from sovereign_dc.mcp import tools
from tests.test_regress_round7 import _env_file_value

ROOT = Path(__file__).resolve().parents[1]


def _manager(tmp_path: Path) -> tuple[EnterpriseManager, EnterpriseRegistry, SovereignEventBus]:
    registry_file = tmp_path / "registry.json"
    bus = SovereignEventBus()
    manager = EnterpriseManager(registry=EnterpriseRegistry(registry_file=registry_file), event_bus=bus)
    return manager, EnterpriseRegistry(registry_file=registry_file), bus


def test_manager_uses_the_power_tier_another_process_registered(tmp_path: Path) -> None:
    manager, other, bus = _manager(tmp_path)
    idle = EnterpriseRegistry.scaffold_manifest("App", "app-one", power_tier=PowerPriority.L4_IDLE)
    assert other.register_app(idle)[0]
    assert manager.start_app("app-one")[0]

    critical = EnterpriseRegistry.scaffold_manifest("App", "app-one", power_tier=PowerPriority.L0_CRITICAL)
    assert other.register_app(critical)[0]
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))
    state = manager.get_runtime_state("app-one")
    assert state is not None and state.manifest.power.tier == PowerPriority.L0_CRITICAL
    assert state.status == AppStatus.RUNNING

    assert other.register_app(idle)[0]
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))
    state = manager.get_runtime_state("app-one")
    assert state is not None and state.status == AppStatus.PAUSED


def test_manager_drops_an_app_another_process_unregistered(tmp_path: Path) -> None:
    manager, other, _ = _manager(tmp_path)
    assert other.register_app(EnterpriseRegistry.scaffold_manifest("App", "app-two"))[0]
    assert manager.start_app("app-two")[0]
    assert other.unregister_app("app-two")
    assert manager.list_runtime_states() == []
    assert manager.get_runtime_state("app-two") is None
    assert manager.start_app("app-two")[0] is False


def _metrics(monkeypatch: pytest.MonkeyPatch, values: dict[str, float] | Exception) -> None:
    def fetch(*_args: Any, **_kwargs: Any) -> dict[str, float]:
        if isinstance(values, Exception):
            raise values
        return values

    monkeypatch.setattr(hal_power, "fetch_exporter_metrics", fetch)


def test_mcp_pricing_with_soc_only_uses_the_measured_solar(monkeypatch: pytest.MonkeyPatch) -> None:
    # MPPT-only bridge: solar is measured, SoC is not. read_power() would refuse this exporter.
    _metrics(monkeypatch, {hal_power.METRIC_SOURCE_UP: 1.0, hal_power.METRIC_SOLAR_WATTS: 200.0})
    result = tools.tool_query_market_pricing({"battery_soc": 85})
    assert result["solar_pv_watts"] == 200.0 and result["dynamic_multiplier"] == 1.0
    assert result["input_sources"] == {"battery_soc": "caller", "solar_watts": "measured_telemetry"}
    assert result["inputs_source"] == "mixed" and result["telemetry_simulated"] is False


def test_mcp_pricing_with_soc_only_and_no_exporter_invents_no_solar(monkeypatch: pytest.MonkeyPatch) -> None:
    _metrics(monkeypatch, TelemetryUnavailable("down"))
    result = tools.tool_query_market_pricing({"battery_soc": 85})
    assert result["solar_pv_watts"] is None and result["dynamic_multiplier"] == 1.0
    assert result["input_sources"] == {"battery_soc": "caller", "solar_watts": "unknown"}
    assert result["telemetry_simulated"] is None


def test_mcp_pricing_with_solar_only_marks_the_result_mixed(monkeypatch: pytest.MonkeyPatch) -> None:
    _metrics(monkeypatch, {hal_power.METRIC_SOURCE_UP: 1.0, hal_power.METRIC_BATTERY_SOC: 10.0})
    monkeypatch.setattr(
        tools, "read_power", lambda **kw: hal_power.read_power(simulation=False, **{"exporter_url": "x"})
    )
    result = tools.tool_query_market_pricing({"solar_watts": 0})
    assert result["input_sources"] == {"battery_soc": "measured_telemetry", "solar_watts": "caller"}
    assert result["inputs_source"] == "mixed" and result["dynamic_multiplier"] == 3.0


# What `docker compose config --environment` (Compose 5.5.0) reports for each line.
MORE_ENV_LINES = {
    "M": ('M= "a b"', "a b"),
    "N": ("N='abc' # user's password", "abc"),
    "P": ("P=   lead", "lead"),
    "R": ("R='a#b'", "a#b"),
    "T": ('T="a # b"', "a # b"),
    "V": ("V=a'b", "a'b"),
    "W": ('W=a"b', 'a"b'),
    "Z": ("Z=end$", "end$"),
}
# Compose accepts these with a decoding these scripts do not reproduce: they must stop, not differ.
MORE_REFUSED = {"L": "L='abc\\'def'", "O": "O='sq\\back'", "Q": 'Q="dq"tail', "X": "X='unterminated"}


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_scripts_decode_or_refuse_further_env_forms(script: str, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    lines = [line for line, _ in MORE_ENV_LINES.values()] + list(MORE_REFUSED.values())
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, (_, expected) in MORE_ENV_LINES.items():
        result = _env_file_value(script, env_file, key)
        assert result.returncode == 0 and result.stdout == expected, (key, result.stdout, result.stderr)
    for key in MORE_REFUSED:
        result = _env_file_value(script, env_file, key)
        assert result.returncode == 2 and result.stdout == "", (key, result.stdout)


def test_gitlab_smtp_credentials_are_not_ruby_source_text() -> None:
    compose = (ROOT / "software" / "docker-compose.yml").read_text(encoding="utf-8")
    assert '= "${SMTP_PASSWORD}"' not in compose and '= "${SMTP_USER}"' not in compose
    assert "gitlab_rails['smtp_password'] = ENV.fetch('GITLAB_SMTP_PASSWORD', '')" in compose
    assert 'GITLAB_SMTP_PASSWORD: "${SMTP_PASSWORD}"' in compose
    assert 'GITLAB_SMTP_USER: "${SMTP_USER}"' in compose


def test_round8_document_statements() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Click/Rich" not in readme
    for text in (readme, agents):
        assert "bidirectional" not in text.split("state channels")[0][-200:]
    assert "DOMAIN_MAILCOW=" not in (ROOT / "software" / "env.example").read_text(encoding="utf-8")
