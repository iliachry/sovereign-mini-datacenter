"""Regression tests for tri-council review round 7.

1. backup.sh / restore.sh decode .env values the way Docker Compose does (`$$`, quotes).
2. A long-running EnterpriseManager sees apps registered later by another process.
3. The 3D twin's inspector carries no hardcoded readings next to live telemetry.
4. Ledger export counts every transaction, not at most 10,000.
5. MCP pricing with both inputs supplied does not need the node's telemetry.
6. The Agri-PV deck calls a voltage limit a voltage limit.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.enterprise.manager import EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.hal.power import TelemetryUnavailable
from sovereign_dc.mcp import tools

ROOT = Path(__file__).resolve().parents[1]

# What `docker compose config --environment` (Compose 5.5.0) reports for each line below.
ENV_LINES = {
    "A": ("A=plain$$dd", "plain$dd"),
    "B": ('B="dq$$dd"', "dq$dd"),
    "C": ("C='sq$$dd'", "sq$$dd"),
    "E": ("E='sq$x'", "sq$x"),
    "G": ("G=un quoted # comment", "un quoted"),
    "H": ("H=hash#nospace", "hash#nospace"),
    "I": ("I=a$!b", "a$!b"),
    "J": ('J="has space" # c', "has space"),
    "K": ("K=back\\slash", "back\\slash"),
}
REFUSED_LINES = {"D": "D=lone$x-end", "F": 'F="dq\\"esc\\\\n"', "L": "L=${OTHER}"}


def _env_file_value(script: str, env_file: Path, key: str) -> subprocess.CompletedProcess[str]:
    text = (ROOT / "software" / "backup" / script).read_text(encoding="utf-8")
    start = text.index("env_file_value() {")
    function = text[start : text.index("\n}\n", start) + 3]
    return subprocess.run(  # noqa: S603
        ["bash", "-c", f'{function}\nenv_file_value "$1"', "bash", key],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={"ENV_FILE": str(env_file), "PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_scripts_decode_env_values_like_compose(script: str, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    lines = [line for line, _ in ENV_LINES.values()] + list(REFUSED_LINES.values())
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, (_, expected) in ENV_LINES.items():
        result = _env_file_value(script, env_file, key)
        assert result.returncode == 0 and result.stdout == expected, (key, result.stdout, result.stderr)
    for key in REFUSED_LINES:
        result = _env_file_value(script, env_file, key)
        assert result.returncode == 2 and result.stdout == "" and key in result.stderr, (key, result.stdout)


def test_backup_scripts_stop_when_an_env_value_is_refused() -> None:
    for script in ("backup.sh", "restore.sh"):
        text = (ROOT / "software" / "backup" / script).read_text(encoding="utf-8")
        assert 'value="$(env_file_value "$key")" || exit 2' in text


def test_running_manager_sees_an_app_registered_by_another_process(tmp_path: Path) -> None:
    registry_file = tmp_path / "registry.json"
    manager = EnterpriseManager(registry=EnterpriseRegistry(registry_file=registry_file))
    assert manager.list_runtime_states() == []

    other = EnterpriseRegistry(registry_file=registry_file)
    ok, problems = other.register_app(EnterpriseRegistry.scaffold_manifest("New App", "new-app"))
    assert ok, problems

    assert [state.manifest.app_id for state in manager.list_runtime_states()] == ["new-app"]
    started, message = manager.start_app("new-app")
    assert started, message

    assert other.unregister_app("new-app")
    assert manager.registry.get_app("new-app") is None


def test_registry_refresh_keeps_unsaved_changes_and_survives_a_missing_file(tmp_path: Path) -> None:
    registry_file = tmp_path / "registry.json"
    first = EnterpriseRegistry(registry_file=registry_file)
    assert first.list_apps() == []
    ok, _ = first.register_app(EnterpriseRegistry.scaffold_manifest("Local", "local-app"), persist=False)
    assert ok
    second = EnterpriseRegistry(registry_file=registry_file)
    ok, _ = second.register_app(EnterpriseRegistry.scaffold_manifest("Other", "other-app"))
    assert ok
    assert {app.app_id for app in first.list_apps()} == {"local-app", "other-app"}
    registry_file.unlink()
    assert {app.app_id for app in first.list_apps()} == {"local-app", "other-app"}


def test_twin_inspector_has_no_hardcoded_readings() -> None:
    models = (ROOT / "docs" / "js" / "models.js").read_text(encoding="utf-8")
    block = models[models.index("const COMPONENT_DATA") : models.index("export function getComponentData")]
    hardware = block[: block.index("AI Policy")]
    for key in ("'SoC'", "'Thermal'", "'Temp'", "'Draw'"):
        assert key not in hardware
    assert not re.search(r"'\d+(\.\d+)?°C'", hardware)
    assert "Harvest" not in hardware


def test_ledger_export_counts_all_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = Ledger()
    address = NodeWallet.create("round7-node").address
    for _ in range(5):
        ledger.mint(address, 1.0)
    # The old code counted a limited history query; any limit below the row count shows it.
    original = ledger.get_history

    def limited(*args: Any, **kwargs: Any) -> Any:
        kwargs["limit"] = 2
        return original(*args[:1], **kwargs)

    monkeypatch.setattr(ledger, "get_history", limited)
    assert ledger.export_state()["total_transactions"] == 5


def test_mcp_pricing_with_both_inputs_does_not_read_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise TelemetryUnavailable("exporter down")

    monkeypatch.setattr(tools, "read_power", unavailable)
    result = tools.tool_query_market_pricing({"battery_soc": 85, "solar_watts": 1200})
    assert result["dynamic_multiplier"] == 0.5
    assert result["inputs_source"] == "caller" and result["telemetry_simulated"] is None
    with pytest.raises(TelemetryUnavailable):
        tools.tool_query_market_pricing({"battery_soc": 85})
    assert tools.tool_query_market_pricing({"battery_soc": "x", "solar_watts": 1})["success"] is False


def test_agri_deck_names_the_voltage_limit() -> None:
    deck = (ROOT / "case-studies" / "agri-pv" / "SMDC_Agri_Pitch_Deck.html").read_text(encoding="utf-8")
    assert "exceed thermal limits (" not in deck
    assert "exceed their voltage limit (<strong>V &gt; 1.10 p.u.</strong>)" in deck
