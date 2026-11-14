"""Regression tests for the MCP JSON-RPC server and tool handlers."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from sovereign_dc import config as smdc_config
from sovereign_dc.enterprise.schema import AppManifest
from sovereign_dc.mcp import tools as mcp_tools
from sovereign_dc.mcp.server import MCPServer


def _call(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    res = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    assert res is not None
    assert res["result"]["isError"] is False, res
    return json.loads(res["result"]["content"][0]["text"])


# --------------------------------------------------------------------------------------
# Finding 8: never answer a message without an id; -32602 for unknown tool; resilient loop
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}},
        {"jsonrpc": "2.0", "method": "notifications/progress"},
        {"jsonrpc": "2.0", "method": "no/such/method"},
        {"jsonrpc": "2.0", "method": "ping"},
        {"jsonrpc": "2.0", "method": "tools/list", "params": [1, 2]},
        {"jsonrpc": "2.0"},
        {"jsonrpc": "2.0", "method": 42},
    ],
)
def test_messages_without_id_never_get_a_response(message: dict[str, Any]) -> None:
    assert MCPServer().handle_request(message) is None


def test_notification_shaped_tool_call_is_not_executed() -> None:
    server = MCPServer()
    executed: list[dict[str, Any]] = []
    server.tools["get_telemetry"].handler = lambda args: executed.append(args) or {}  # type: ignore[func-returns-value]

    assert (
        server.handle_request({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_telemetry"}}) is None
    )
    assert executed == []


def test_requests_with_an_id_still_get_errors() -> None:
    server = MCPServer()
    missing_method = server.handle_request({"jsonrpc": "2.0", "id": 7})
    assert missing_method is not None and missing_method["error"]["code"] == -32600
    null_id = server.handle_request({"jsonrpc": "2.0", "id": None, "method": "nope"})
    assert null_id is not None and null_id["id"] is None and null_id["error"]["code"] == -32601


@pytest.mark.parametrize("tool_name", ["unregistered_tool", None, 5, ["get_telemetry"]])
def test_unknown_tool_is_invalid_params(tool_name: Any) -> None:
    res = MCPServer().handle_request(
        {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": tool_name, "arguments": {}}}
    )
    assert res is not None
    assert res["error"]["code"] == -32602


def test_stdio_loop_survives_pathological_lines_and_stays_silent_on_notifications() -> None:
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        "[" * 200_000,  # RecursionError inside json.loads
        "9" * 10_000,  # ValueError (integer string conversion limit), not JSONDecodeError
        json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled"}),
        json.dumps({"jsonrpc": "2.0", "method": "unknown/notification"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
    ]
    out = io.StringIO()

    MCPServer().run_stdio(in_stream=io.StringIO("\n".join(lines) + "\n"), out_stream=out)

    responses = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert [r.get("id") for r in responses] == [1, None, None, 2]
    assert [r["error"]["code"] for r in responses[1:3]] == [-32700, -32700]
    assert responses[3]["result"] == {}  # the server was still alive after the bad lines


# --------------------------------------------------------------------------------------
# Finding 16: spool_dtn_bundle must report a rejected bundle
# --------------------------------------------------------------------------------------


def test_spool_dtn_bundle_reports_rejection() -> None:
    server = MCPServer()
    rejected = _call(
        server,
        "spool_dtn_bundle",
        {"destination": "dtn://ground.earth/t", "payload": "late", "ttl_seconds": -5},
    )
    assert rejected["success"] is False
    assert rejected["status"] == "REJECTED"
    assert "error" in rejected

    accepted = _call(server, "spool_dtn_bundle", {"destination": "dtn://ground.earth/t", "payload": "ok"})
    assert accepted["success"] is True
    assert accepted["status"] == "QUEUED_IN_NVME_SPOOL"
    assert accepted["signed"] is False

    description = server.tools["spool_dtn_bundle"].description.lower()
    assert "unsigned" in description
    assert "create, sign" not in description


# --------------------------------------------------------------------------------------
# Finding 18 (MCP part): scaffold slug and a YAML preview that is YAML
# --------------------------------------------------------------------------------------


def test_scaffold_enterprise_app_derives_a_valid_app_id_and_real_yaml() -> None:
    result = _call(MCPServer(), "scaffold_enterprise_app", {"name": "My App! v2"})

    assert result["manifest"]["app_id"] == "my-app-v2"
    assert result["valid"] is True
    assert result["validation_errors"] == []
    assert AppManifest.from_dict(result["manifest"]).validate() == []

    preview = result["yaml_preview"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(preview)  # it used to be JSON mislabelled as YAML
    assert yaml.safe_load(preview) == result["manifest"]


def test_scaffold_enterprise_app_reports_an_invalid_explicit_app_id() -> None:
    result = _call(MCPServer(), "scaffold_enterprise_app", {"name": "X", "app_id": "Not Valid!"})
    assert result["manifest"]["app_id"] == "Not Valid!"
    assert result["valid"] is False
    assert any("app_id" in e for e in result["validation_errors"])


# --------------------------------------------------------------------------------------
# Finding 19: the temp-dir fallback must not trust a directory it does not own/control
# --------------------------------------------------------------------------------------


@pytest.fixture
def broken_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Makes SMDC_STATE_DIR unusable and redirects the temp dir into tmp_path."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SMDC_STATE_DIR", str(blocker / "state"))
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(fake_tmp))
    monkeypatch.setattr(smdc_config, "_FALLBACK_STATE_DIR", None)
    return fake_tmp


@pytest.mark.parametrize("mode", [0o755, 0o777, 0o750])
def test_state_dir_fallback_rejects_preexisting_directory_with_open_mode(broken_state_dir: Path, mode: int) -> None:
    planted = broken_state_dir / f"smdc-{os.getuid()}"
    planted.mkdir()
    os.chmod(planted, mode)

    chosen = Path(mcp_tools._get_mcp_state_dir())

    assert chosen != planted
    assert chosen.parent == broken_state_dir
    st = chosen.lstat()
    assert stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700 and st.st_uid == os.getuid()
    assert Path(mcp_tools._get_mcp_state_dir()) == chosen  # stable for the rest of the process


def test_state_dir_fallback_rejects_symlink(broken_state_dir: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "attacker-controlled"
    elsewhere.mkdir(mode=0o700)
    planted = broken_state_dir / f"smdc-{os.getuid()}"
    planted.symlink_to(elsewhere, target_is_directory=True)

    chosen = Path(mcp_tools._get_mcp_state_dir())

    assert chosen != planted
    assert chosen.resolve() != elsewhere.resolve()
    assert list(elsewhere.iterdir()) == []


def test_state_dir_fallback_reuses_its_own_private_directory(broken_state_dir: Path) -> None:
    first = Path(mcp_tools._get_mcp_state_dir())
    assert first == broken_state_dir / f"smdc-{os.getuid()}"
    assert stat.S_IMODE(first.stat().st_mode) == 0o700
    smdc_config._FALLBACK_STATE_DIR = None
    assert Path(mcp_tools._get_mcp_state_dir()) == first


# --------------------------------------------------------------------------------------
# Honesty items
# --------------------------------------------------------------------------------------


def test_security_audit_does_not_claim_unchecked_zero_trust_compliance() -> None:
    server = MCPServer()
    result = _call(server, "run_security_audit", {})

    assert result["nist_sp_800_207_zero_trust"]["status"] == "NOT_ASSESSED"
    assert "COMPLIANT" not in json.dumps(result).replace("NOT_ASSESSED", "")
    assert result["pqc_self_tests_passed"] == result["pqc_self_tests_total"] == 2
    assert result["score_basis"] == "pqc_self_tests_only"
    assert "NOT_ASSESSED" in server.tools["run_security_audit"].description


def test_set_load_shedding_says_it_only_published_an_event() -> None:
    result = _call(MCPServer(), "set_load_shedding", {"level": "L3"})

    assert result["success"] is True
    assert result["effect"] == "event_published"
    assert result["hardware_actuated"] is False
    assert result["handlers_notified"] >= 1  # the enterprise manager subscription


def test_enterprise_app_state_is_reported_as_estimated(tmp_path: Path) -> None:
    from sovereign_dc.enterprise.registry import EnterpriseRegistry

    manifest = EnterpriseRegistry.scaffold_manifest(name="Job", app_id="mcp-job")
    mcp_tools.get_enterprise_manager().registry.register_app(manifest)
    server = MCPServer()

    started = _call(server, "manage_enterprise_app", {"app_id": "mcp-job", "action": "start"})
    assert started["state"]["metrics_source"] == "estimated"
    assert started["state"]["process_supervised"] is False
    assert started["state"]["pid"] is None


# --------------------------------------------------------------------------------------
# Tri-council round 1: provenance, DTN contract, truthful capability descriptions
# --------------------------------------------------------------------------------------


def test_telemetry_tools_and_resources_keep_simulation_provenance() -> None:
    server = MCPServer()
    telemetry = _call(server, "get_telemetry", {})
    assert telemetry["power"]["is_simulated"] is True
    assert telemetry["thermal"]["is_simulated"] is True
    assert telemetry["telemetry_simulated"] is True
    assert _call(server, "get_system_status", {})["telemetry_simulated"] is True

    pricing = _call(server, "query_market_pricing", {})
    assert pricing["inputs_source"] == "simulated_telemetry" and pricing["telemetry_simulated"] is True
    what_if = _call(server, "query_market_pricing", {"battery_soc": 50, "solar_watts": 100})
    assert what_if["inputs_source"] == "caller"

    res = server.handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "smdc://telemetry/current"}}
    )
    assert res is not None
    resource = json.loads(res["result"]["contents"][0]["text"])
    assert resource["power"]["is_simulated"] is True and resource["thermal"]["is_simulated"] is True


def test_measured_readings_are_not_marked_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    from sovereign_dc.hal.power import PowerReading
    from sovereign_dc.hal.thermal import ThermalReading

    measured_power = PowerReading(battery_soc=80.0, solar_watts=900.0, is_simulated=False)
    measured_thermal = ThermalReading(
        coolant_celsius=30.0, rack_inlet_celsius=24.0, rack_exhaust_celsius=33.0, is_simulated=False
    )
    monkeypatch.setattr(mcp_tools, "read_power", lambda **_k: measured_power)
    monkeypatch.setattr(mcp_tools, "read_thermal", lambda **_k: measured_thermal)
    telemetry = _call(MCPServer(), "get_telemetry", {})
    assert telemetry["power"]["is_simulated"] is False
    assert telemetry["telemetry_simulated"] is False


def test_spool_dtn_bundle_follows_the_shared_contract() -> None:
    import base64

    from sovereign_dc.config import get_config
    from sovereign_dc.space.dtn.router import DTNRouter

    server = MCPServer()
    default = _call(server, "spool_dtn_bundle", {"destination": "dtn://ground.earth/t", "payload": "ok"})
    assert default["priority"] == "NORMAL" and default["priority_level"] == 1
    assert default["source"] == "dtn://smdc-dgx-01/mcp"
    assert default["ttl_seconds"] == 7 * 86400  # the shared contract's default (was 86400 in MCP only)

    binary = _call(
        server,
        "spool_dtn_bundle",
        {"destination": "dtn://ground.earth/t", "payload_b64": base64.b64encode(b"\xff\x00").decode(), "priority": "3"},
    )
    assert binary["payload_bytes"] == 2 and binary["priority"] == "CRITICAL"
    stored = {b.bundle_id: b for b in DTNRouter(db_path=get_config().dtn_db_path).get_outbound_queue()}
    assert stored[binary["bundle_id"]].payload == b"\xff\x00"

    bad = _call(server, "spool_dtn_bundle", {"destination": "dtn://ground.earth/t", "payload": "x", "priority": 9})
    assert bad["success"] is False and bad["status"] == "REJECTED" and "priority" in bad["error"]


def test_capability_descriptions_match_the_implementation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = MCPServer()
    passes = server.tools["predict_satellite_passes"]
    assert "sgp4 orbital mechanics" not in passes.description.lower()
    assert "not sgp4" in passes.description.lower()
    assert "not SGP4" in _call(server, "predict_satellite_passes", {"duration_hours": 1})["orbit_model"]

    search = server.tools["query_knowledge_indexer"]
    assert "semantic rag" not in search.description.lower()
    assert "keyword search" in search.description.lower()

    (tmp_path / "MANUAL.md").write_text("The coolant loop uses a radiator.\n\nBattery bank notes.\n\nCoolant only.\n")
    monkeypatch.chdir(tmp_path)
    result = _call(server, "query_knowledge_indexer", {"query": "Coolant radiator", "limit": 5})
    assert result["search_mode"] == "keyword"
    assert [c["keyword_score"] for c in result["chunks"]] == [1.0, 0.5]
    assert result["chunks"][0]["matched_terms"] == ["coolant", "radiator"]
    assert all("relevance_score" not in c for c in result["chunks"])
    assert _call(server, "query_knowledge_indexer", {"query": "zz"})["results_count"] == 0
