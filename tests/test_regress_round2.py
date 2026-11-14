"""Regression tests for the tri-council round 2 findings (dashboard, economy, SDK, HAL, mesh).

Every test keeps its state under ``tmp_path`` (see ``conftest.py``) and talks only to servers
it starts itself on 127.0.0.1.
"""

from __future__ import annotations

import http.client
import http.server
import json
import logging
import math
import os
import random
import re
import sqlite3
import sys
import threading
from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from sovereign_dc import cli
from sovereign_dc import config as smdc_config
from sovereign_dc.economy import ledger as ledger_module
from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import (
    MAX_CREDITS,
    Ledger,
    StateChannel,
    StateChannelPromise,
    TransactionRejected,
    credits_to_micro,
)
from sovereign_dc.economy.market import ComputeMarket
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.enterprise.sdk import SMDCClient
from sovereign_dc.events import get_event_bus
from sovereign_dc.hal.power import PowerReading, read_power
from sovereign_dc.hal.thermal import ThermalReading, read_thermal
from sovereign_dc.jsonutil import dumps_strict, replace_non_finite
from sovereign_dc.mcp.resources import read_market_resource, read_telemetry_resource
from sovereign_dc.mcp.tools import (
    tool_get_system_status,
    tool_get_telemetry,
    tool_get_wallet_balances,
    tool_query_market_pricing,
)
from sovereign_dc.mesh import mesh_sync
from sovereign_dc.web import dashboard
from sovereign_dc.web.dashboard import DASHBOARD_HTML, DashboardHandler

REPO_ROOT = Path(__file__).resolve().parent.parent
MICRO = 1_000_000


# ── helpers ──────────────────────────────────────────────────────────────────


def _serve(handler: type[http.server.BaseHTTPRequestHandler], host: str = "127.0.0.1") -> http.server.HTTPServer:
    server = http.server.ThreadingHTTPServer((host, 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return server


@pytest.fixture
def node() -> Iterator[int]:
    """A real dashboard server on a loopback port."""
    server = _serve(DashboardHandler)
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _request(
    port: int, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body) if body is not None else None
    all_headers = {"Content-Type": "application/json"} if body is not None else {}
    all_headers.update(headers or {})
    conn.request(method, path, body=payload, headers=all_headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    return resp.status, raw


def _reject_constant(token: str) -> Any:
    raise AssertionError(f"non-standard JSON token {token!r} in a response")


def _strict_loads(raw: str) -> Any:
    """Parses like a browser would: NaN / Infinity are errors."""
    return json.loads(raw, parse_constant=_reject_constant)


class _Exporter(http.server.BaseHTTPRequestHandler):
    """A fake Prometheus power exporter; ``body`` is set per test."""

    body = b""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Exporter]]:
    """Starts a fake exporter and points the node's POWER_EXPORTER_URL at it."""

    class Handler(_Exporter):
        body = b""

    server = _serve(Handler)
    Handler.url = f"http://127.0.0.1:{server.server_address[1]}/metrics"  # type: ignore[attr-defined]
    monkeypatch.setenv("POWER_EXPORTER_URL", Handler.url)  # type: ignore[attr-defined]
    smdc_config.reset_config()
    yield Handler
    server.shutdown()
    server.server_close()


def _hardware_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL_MODE", "hardware")
    smdc_config.reset_config()
    assert not smdc_config.get_config().is_simulation()


GOOD_METRICS = (
    b"sovereign_telemetry_source_up 1\n"
    b"sovereign_battery_soc_percent 61.5\n"
    b"sovereign_solar_pv_power_watts 900\n"
    b"sovereign_battery_voltage_volts 52.1\n"
    b"sovereign_system_power_draw_watts 310\n"
    b"sovereign_temp_coolant_celsius 31.0\n"
    b"sovereign_temp_rack_inlet_celsius 22.5\n"
    b"sovereign_temp_rack_exhaust_celsius 33.0\n"
)
MINIMAL_METRICS = b"sovereign_battery_soc_percent 61.5\nsovereign_temp_coolant_celsius 31.0\n"


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Runs ``smdc <argv>`` in-process and returns its exit status."""
    monkeypatch.setattr(sys, "argv", ["smdc", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


# ── F-A: control POST auth uses the same "local" rule as the data GETs ────────

PROXY_HEADER_CASES = [
    {"X-Forwarded-For": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9"},
    {"X-Real-IP": "203.0.113.9"},
    {"Via": "1.1 edge"},
    {"X-Forwarded-Proto": "https"},
]


@pytest.mark.parametrize("proxy_headers", PROXY_HEADER_CASES)
def test_proxied_control_post_is_refused_without_a_token(node: int, proxy_headers: dict[str, str]) -> None:
    status, raw = _request(node, "POST", "/api/control/rack-door", {"open": True}, proxy_headers)
    assert status == 403
    assert json.loads(raw)["success"] is False
    assert dashboard._HARDWARE_STATE["door_open"] is False


def test_local_unproxied_control_post_is_still_accepted_without_a_token(node: int) -> None:
    status, raw = _request(node, "POST", "/api/control/rack-door", {"open": True})
    assert status == 200 and json.loads(raw)["door_open"] is True


def test_proxied_control_post_needs_the_token_when_one_is_set(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "s3cret")
    forwarded = {"X-Forwarded-For": "203.0.113.9"}
    assert _request(node, "POST", "/api/control/rack-door", {"open": True}, forwarded)[0] == 401
    with_token = {**forwarded, "Authorization": "Bearer s3cret"}
    assert _request(node, "POST", "/api/control/rack-door", {"open": True}, with_token)[0] == 200


def test_server_bound_beyond_loopback_refuses_proxied_control_without_a_token() -> None:
    server = _serve(DashboardHandler, host="0.0.0.0")  # noqa: S104 - reached over loopback only
    try:
        port = server.server_address[1]
        forwarded = {"X-Forwarded-For": "203.0.113.9"}
        assert _request(port, "POST", "/api/control/pdu-outlet", {"outlet": 1, "state": False}, forwarded)[0] == 403
        assert dashboard._HARDWARE_STATE["pdu_outlets"][1] is True
        # A genuine local, non-proxied client is the one exception.
        assert _request(port, "POST", "/api/control/pdu-outlet", {"outlet": 1, "state": False})[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_get_and_post_auth_share_one_definition_of_local(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DashboardHandler, "_is_local_request", lambda self: False)
    assert _request(node, "POST", "/api/control/rack-door", {"open": True})[0] == 403
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "s3cret")
    assert _request(node, "GET", "/api/status")[0] == 401


# ── F-B: one node wallet, one ledger, no implicit minting ────────────────────


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _cli_wallet_report(capsys: pytest.CaptureFixture[str]) -> tuple[str, float]:
    """``(address, balance)`` as printed by ``smdc economy wallet``."""
    capsys.readouterr()
    assert cli.cmd_economy_wallet(Namespace(create=False, pqc=False, mint=None)) == 0
    out = _ANSI.sub("", capsys.readouterr().out)
    address = re.search(r"Wallet Address:\s+(\S+)", out)
    balance = re.search(r"Balance:\s+([0-9.]+) SMDC-Credits", out)
    assert address is not None and balance is not None
    return address.group(1), float(balance.group(1))


def _three_surfaces(capsys: pytest.CaptureFixture[str]) -> tuple[set[str], set[float]]:
    """Addresses and balances as the CLI, dashboard payload and MCP tool report them."""
    dashboard.invalidate_status_cache()
    cli_address, cli_balance = _cli_wallet_report(capsys)
    economy = dashboard.get_system_status_payload()["economy"]
    mcp = tool_get_wallet_balances({})
    return (
        {cli_address, economy["wallet_address"], mcp["address_ed25519"]},
        {cli_balance, economy["balance_credits"], mcp["balance_sov"]},
    )


def test_new_node_has_one_wallet_and_a_zero_balance_on_every_surface(capsys: pytest.CaptureFixture[str]) -> None:
    addresses, balances = _three_surfaces(capsys)
    assert len(addresses) == 1 and balances == {0.0}
    paths = node_state.node_state_paths()
    assert Ledger(db_path=paths.ledger).get_history() == []  # nothing was minted by any surface
    state_files = set(os.listdir(os.path.dirname(paths.wallet)))
    assert {"smdc_node_wallet.json", "smdc_node_pqc_wallet.json", "smdc_node_ledger.db"} <= state_files
    assert not {name for name in state_files if "mcp" in name or "dashboard" in name or "default" in name}
    mcp = tool_get_wallet_balances({})
    assert mcp["address_pqc_mldsa87"].startswith("sov_pqc_")
    assert mcp["credits_kind"] == "local_test_credits"


def test_economy_mint_is_the_explicit_source_of_credits(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run_cli(monkeypatch, "economy", "mint", "12.5", "--memo", "local test") == 0
    out = capsys.readouterr().out
    assert "LOCAL TEST credits" in out and "no external value" in out
    addresses, balances = _three_surfaces(capsys)
    assert len(addresses) == 1 and balances == {12.5}
    history = node_state.open_node_ledger().get_history()
    assert [(tx.sender, tx.amount, tx.memo) for tx in history] == [("MINT", 12.5, "local test")]
    assert _run_cli(monkeypatch, "economy", "mint", "0.000001") == 0
    assert node_state.open_node_ledger().get_history()[0].memo == node_state.LOCAL_TEST_MINT_MEMO
    assert node_state.get_node_identity().balance == 12.500001


@pytest.mark.parametrize("amount", ["0", "-1", "nan", "inf", "-inf", "abc", "", "0.0000001", "2000000000.000001"])
def test_economy_mint_rejects_invalid_amounts_with_a_non_zero_exit(
    amount: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run_cli(monkeypatch, "economy", "mint", "--", amount) != 0
    assert "Invalid mint amount" in capsys.readouterr().out
    assert node_state.get_node_identity().balance == 0.0
    assert node_state.open_node_ledger().get_history() == []


def test_wallet_mint_flag_is_validated_and_labelled(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_economy_wallet(Namespace(create=False, pqc=False, mint="nan")) == 1
    assert cli.cmd_economy_wallet(Namespace(create=False, pqc=False, mint="3")) == 0
    assert "no external value" in capsys.readouterr().out
    assert node_state.get_node_identity().balance == 3.0


def test_wallet_create_never_overwrites_the_node_wallet(capsys: pytest.CaptureFixture[str]) -> None:
    address = node_state.load_node_wallet().address
    assert cli.cmd_economy_wallet(Namespace(create=True, pqc=False, mint=None)) == 1
    assert "not overwritten" in capsys.readouterr().out
    assert node_state.load_node_wallet().address == address


def test_send_from_an_unfunded_node_wallet_fails_and_mints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    recipient = NodeWallet.create(node_id="peer").address
    assert cli.cmd_economy_send(Namespace(recipient=recipient, amount=1.0, memo="")) == 1
    assert "Insufficient balance" in capsys.readouterr().out
    assert node_state.open_node_ledger().get_history() == []
    node_state.mint_local_test_credits(5.0)
    assert cli.cmd_economy_send(Namespace(recipient=recipient, amount=1.0, memo="")) == 0
    assert node_state.get_node_identity().balance == 4.0


def test_pqc_flag_shows_the_node_mldsa_wallet(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_economy_wallet(Namespace(create=False, pqc=True, mint=None)) == 0
    assert node_state.load_node_pqc_wallet().address in capsys.readouterr().out
    assert node_state.load_node_pqc_wallet().keypair.algorithm == AddressType.ML_DSA_87


def _make_legacy_set(state_dir: Path, legacy: node_state.LegacySet, balance: float) -> NodeWallet:
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    wallet = NodeWallet.create(node_id=f"legacy-{legacy.surface}")
    wallet.save_to_file(str(state_dir / legacy.wallet))
    Ledger(db_path=str(state_dir / legacy.ledger)).mint(wallet.address, balance, memo=f"{legacy.surface}_GENESIS")
    if legacy.pqc_wallet:
        NodeWallet.create(node_id="legacy-pqc", algorithm=AddressType.ML_DSA_87).save_to_file(
            str(state_dir / legacy.pqc_wallet)
        )
    return wallet


def _snapshot(directory: Path, names: list[str]) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in names if (directory / name).exists()}


@pytest.mark.parametrize("legacy", node_state.LEGACY_SETS, ids=lambda item: item.surface)
def test_a_single_legacy_set_is_adopted_with_its_balance(
    legacy: node_state.LegacySet, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = Path(smdc_config.get_state_dir())
    balance = 1000.0 if legacy.surface == "MCP" else 100.0
    wallet = _make_legacy_set(state_dir, legacy, balance)
    legacy_pqc = NodeWallet.load_from_file(str(state_dir / legacy.pqc_wallet)).address if legacy.pqc_wallet else None

    assert node_state.migrate_legacy_node_state() == legacy.surface
    assert node_state.migrate_legacy_node_state() is None  # idempotent

    identity = node_state.get_node_identity()
    assert identity.wallet.address == wallet.address and identity.balance == balance
    assert identity.ledger.verify_chain()
    # Renamed, not copied: the legacy names are gone and nothing else was created from them.
    assert not (state_dir / legacy.wallet).exists() and not (state_dir / legacy.ledger).exists()
    if legacy_pqc is not None:
        assert identity.pqc_wallet.address == legacy_pqc
    addresses, balances = _three_surfaces(capsys)
    assert addresses == {wallet.address} and balances == {balance}


def test_with_several_legacy_sets_the_cli_one_wins_and_the_others_are_left_alone(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = Path(smdc_config.get_state_dir())
    cli_set, dash_set, mcp_set = node_state.LEGACY_SETS
    cli_wallet = _make_legacy_set(state_dir, cli_set, 100.0)
    _make_legacy_set(state_dir, dash_set, 100.0)
    _make_legacy_set(state_dir, mcp_set, 1000.0)
    other_names = [dash_set.wallet, dash_set.ledger, mcp_set.wallet, mcp_set.ledger, str(mcp_set.pqc_wallet)]
    before = _snapshot(state_dir, other_names)
    assert len(before) == 5

    caplog.set_level(logging.WARNING, logger="sovereign_dc.economy.node_state")
    addresses, balances = _three_surfaces(capsys)
    assert addresses == {cli_wallet.address} and balances == {100.0}

    assert _snapshot(state_dir, other_names) == before  # byte-for-byte untouched
    warnings = [r.getMessage() for r in caplog.records if "left untouched" in r.getMessage()]
    assert len(warnings) == 1
    assert all(name in warnings[0] for name in other_names)


def test_without_a_cli_set_the_dashboard_set_is_preferred_over_the_mcp_set() -> None:
    state_dir = Path(smdc_config.get_state_dir())
    _, dash_set, mcp_set = node_state.LEGACY_SETS
    dash_wallet = _make_legacy_set(state_dir, dash_set, 100.0)
    _make_legacy_set(state_dir, mcp_set, 1000.0)
    assert node_state.migrate_legacy_node_state() == "dashboard"
    assert node_state.load_node_wallet().address == dash_wallet.address
    assert (state_dir / mcp_set.wallet).exists() and (state_dir / mcp_set.ledger).exists()


def test_an_established_node_identity_is_never_replaced_by_a_legacy_set() -> None:
    address = node_state.load_node_wallet().address
    state_dir = Path(smdc_config.get_state_dir())
    _make_legacy_set(state_dir, node_state.LEGACY_SETS[0], 100.0)
    assert node_state.migrate_legacy_node_state() is None
    assert node_state.get_node_identity().wallet.address == address
    assert (state_dir / "smdc_default_wallet.json").exists()


def test_custom_wallet_and_ledger_paths_are_used_unchanged_and_start_at_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = {"wallet_file": str(tmp_path / "w.json"), "db_path": str(tmp_path / "l.db")}
    assert cli.cmd_economy_wallet(Namespace(create=True, pqc=False, mint=None, node_id="n1", **args)) == 0
    wallet = NodeWallet.load_from_file(args["wallet_file"])
    assert Ledger(db_path=args["db_path"]).get_balance(wallet.address) == 0.0
    assert cli.cmd_economy_mint(Namespace(amount="2.5", memo="", **args)) == 0
    assert Ledger(db_path=args["db_path"]).get_balance(wallet.address) == 2.5
    assert not os.path.exists(node_state.node_state_paths().wallet)


# ── ASTRA 3: the SDK never forwards its token on a redirect ───────────────────


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list[dict[str, Any]] = []

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        type(self).hits.append(
            {"path": self.path, "authorization": self.headers.get("Authorization"), "body": self.rfile.read(length)}
        )
        body = b'{"power": {"solar_watts": 1, "battery_soc": 2}, "success": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _record
    do_POST = _record

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def redirecting_node() -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
    """``(redirecting base URL, redirect target URL, requests the target received)``."""

    class Target(_Recorder):
        hits: list[dict[str, Any]] = []

    target = _serve(Target)
    target_url = f"http://127.0.0.1:{target.server_address[1]}/stolen"

    class Redirector(http.server.BaseHTTPRequestHandler):
        code = 302

        def _redirect(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            self.send_response(type(self).code)
            self.send_header("Location", target_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _redirect
        do_POST = _redirect

        def log_message(self, format: str, *args: Any) -> None:
            return

    redirector = _serve(Redirector)
    yield f"http://127.0.0.1:{redirector.server_address[1]}", target_url, Target.hits
    for server in (redirector, target):
        server.shutdown()
        server.server_close()


def test_sdk_does_not_follow_a_redirect_or_forward_the_token(
    redirecting_node: tuple[str, str, list[dict[str, Any]]], caplog: pytest.LogCaptureFixture
) -> None:
    base_url, target_url, hits = redirecting_node
    client = SMDCClient(base_url=base_url, token="audit-token")  # noqa: S106 - test value
    caplog.set_level(logging.WARNING)

    assert client._http_get("/api/status") == {}
    assert client.last_http_error is not None and target_url in client.last_http_error

    result = client._http_post("/api/enterprise/telemetry", {"app_id": "a", "metrics": {}})
    assert result["node_rejected"] is True and result["redirect"] is True
    assert result["http_status"] == 302 and result["redirect_location"] == target_url
    assert target_url in result["error"]

    assert hits == []  # the other origin never saw a request, let alone the bearer token
    assert "audit-token" not in caplog.text and target_url in caplog.text


def test_sdk_reports_a_redirected_bundle_as_rejected_and_spools_nothing_locally(
    redirecting_node: tuple[str, str, list[dict[str, Any]]],
) -> None:
    from sovereign_dc.space.dtn.router import DTNRouter

    base_url, target_url, hits = redirecting_node
    client = SMDCClient(base_url=base_url, token="audit-token")  # noqa: S106 - test value
    result = client.send_dtn_bundle("dtn://ground/x", "hello")
    assert result["status"] == "rejected" and target_url in result["error"]
    assert client.emit_telemetry("app-1", {"x": 1}) is False
    assert hits == []
    stats = DTNRouter(db_path=smdc_config.get_config().dtn_db_path).get_queue_stats()
    assert stats.get("queued_bundle_count", 0) == 0


def test_sdk_still_talks_to_a_node_that_does_not_redirect(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "s3cret")
    client = SMDCClient(base_url=f"http://127.0.0.1:{node}", token="s3cret")  # noqa: S106 - test value
    telemetry = client.get_telemetry()
    assert telemetry["source"] == "api" and client.last_http_error is None
    assert client.send_dtn_bundle("dtn://ground/x", b"\x00\x01")["status"] == "spooled"


# ── ASTRA 6: controls and fan speed say what they are ─────────────────────────


def test_simulation_mode_control_changes_the_digital_shadow_only(node: int) -> None:
    events: list[dict[str, Any]] = []
    get_event_bus().subscribe("hardware.*", lambda event: events.append(event.payload))

    status, raw = _request(node, "POST", "/api/control/rack-door", {"open": True})
    door = _strict_loads(raw)
    assert status == 200 and door["success"] is True and door["door_open"] is True
    assert door["actuated"] is False and door["simulated"] is True and door["effect"] == "digital_shadow_only"

    status, raw = _request(node, "POST", "/api/control/pdu-outlet", {"outlet": 2, "state": False})
    pdu = _strict_loads(raw)
    assert status == 200 and pdu["pdu_outlets"][2] is False
    assert pdu["actuated"] is False and pdu["simulated"] is True

    assert [event["actuated"] for event in events] == [False, False]

    hardware = _strict_loads(_request(node, "GET", "/api/status")[1])["hardware"]
    assert hardware["door_open"] is True and hardware["pdu_outlets"][2] is False
    assert hardware["simulated"] is True and hardware["actuated"] is False
    assert hardware["state_source"] == "digital_shadow"
    assert hardware["actuators"] == {"rack_door": False, "pdu_outlet": False}
    assert hardware["fan_rpm_estimated"] is True and isinstance(hardware["fan_rpm"], int)


@pytest.mark.parametrize(
    ("path", "body"),
    [("/api/control/rack-door", {"open": True}), ("/api/control/pdu-outlet", {"outlet": 2, "state": False})],
)
def test_hardware_mode_without_an_actuator_refuses_instead_of_pretending(
    node: int, monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, Any]
) -> None:
    _hardware_mode(monkeypatch)
    status, raw = _request(node, "POST", path, body)
    answer = _strict_loads(raw)
    assert status == 501
    assert answer["success"] is False and answer["actuated"] is False and answer["state_changed"] is False
    assert "actuator" in answer["error"]
    assert dashboard._HARDWARE_STATE["door_open"] is False
    assert dashboard._HARDWARE_STATE["pdu_outlets"] == [True] * 8
    assert get_event_bus().get_history() == []


def test_a_registered_actuator_is_what_makes_a_control_actuated(
    node: int, monkeypatch: pytest.MonkeyPatch, exporter: type[_Exporter]
) -> None:
    exporter.body = GOOD_METRICS
    _hardware_mode(monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []

    def door(request: dict[str, Any]) -> bool:
        calls.append(("door", request))
        return True

    def outlet(request: dict[str, Any]) -> bool:
        calls.append(("outlet", request))
        return True

    dashboard.register_actuator(dashboard.ACTUATOR_RACK_DOOR, door)
    hardware = _strict_loads(_request(node, "GET", "/api/status")[1])["hardware"]
    assert hardware["actuators"] == {"rack_door": True, "pdu_outlet": False}
    assert hardware["simulated"] is True  # not every control is backed by hardware yet

    dashboard.register_actuator(dashboard.ACTUATOR_PDU_OUTLET, outlet)
    status, raw = _request(node, "POST", "/api/control/rack-door", {"open": True})
    answer = _strict_loads(raw)
    assert status == 200 and answer["actuated"] is True and answer["simulated"] is False
    assert answer["effect"] == "actuated"
    assert _request(node, "POST", "/api/control/pdu-outlet", {"outlet": 3, "state": False})[0] == 200
    assert calls == [("door", {"open": True}), ("outlet", {"outlet": 3, "state": False})]

    hardware = _strict_loads(_request(node, "GET", "/api/status")[1])["hardware"]
    assert hardware["simulated"] is False and hardware["actuated"] is True
    assert hardware["state_source"] == "actuator" and hardware["door_open"] is True
    assert hardware["fan_rpm_estimated"] is True  # still no tachometer

    dashboard.unregister_actuator(dashboard.ACTUATOR_RACK_DOOR)
    assert _request(node, "POST", "/api/control/rack-door", {"open": False})[0] == 501


@pytest.mark.parametrize("failure", ["returns_false", "raises"])
def test_a_failing_actuator_is_reported_and_leaves_the_shadow_unchanged(node: int, failure: str) -> None:
    def backend(request: dict[str, Any]) -> bool:
        if failure == "raises":
            raise OSError("relay board not responding")
        return False

    dashboard.register_actuator(dashboard.ACTUATOR_RACK_DOOR, backend)
    status, raw = _request(node, "POST", "/api/control/rack-door", {"open": True})
    answer = _strict_loads(raw)
    assert status == 502 and answer["success"] is False and answer["actuated"] is False
    assert dashboard._HARDWARE_STATE["door_open"] is False


def test_register_actuator_rejects_unknown_controls() -> None:
    with pytest.raises(ValueError, match="Unknown actuator"):
        dashboard.register_actuator("warp_drive", lambda request: True)


def test_fan_rpm_is_an_estimate_and_is_labelled_as_one_on_the_page_and_the_twin() -> None:
    assert dashboard.estimate_fan_rpm(25.0, None) == 1800
    assert dashboard.estimate_fan_rpm(35.0, 300.0) == 1800 + 800 + 400
    assert dashboard.estimate_fan_rpm(500.0, 5000.0) == 4800
    assert "estimated" in DASHBOARD_HTML and 'id="fan-rpm"' in DASHBOARD_HTML
    assert "DIGITAL SHADOW (NOT ACTUATED)" in DASHBOARD_HTML
    hud = (REPO_ROOT / "docs" / "js" / "hud.js").read_text(encoding="utf-8")
    assert "json.actuated === true" in hud and "digital shadow only" in hud
    assert "res.status === 501" in hud
    # The solenoid wording is only reachable for an actuated response.
    assert hud.count("Solenoid Unlocked") == 1 and "Solenoid Unlocked (actuated)" in hud


# ── ASTRA 7: no measurement is never a measurement ────────────────────────────

UNUSABLE_POWER = {
    "nan_soc": b"sovereign_battery_soc_percent NaN\nsovereign_solar_pv_power_watts 10\n",
    "inf_soc": b"sovereign_battery_soc_percent +Inf\n",
    "source_down": b"sovereign_telemetry_source_up 0\nsovereign_battery_soc_percent 80\n",
    "source_nan": b"sovereign_telemetry_source_up NaN\nsovereign_battery_soc_percent 80\n",
    "simulated_flag_nan": b"sovereign_telemetry_simulated NaN\nsovereign_battery_soc_percent 80\n",
    "missing_soc": b"sovereign_solar_pv_power_watts 10\n",
}
UNUSABLE_THERMAL = {
    "nan_coolant": b"sovereign_temp_coolant_celsius NaN\n",
    "inf_coolant": b"sovereign_temp_coolant_celsius -Inf\n",
    "source_down": b"sovereign_telemetry_source_up 0\nsovereign_temp_coolant_celsius 30\n",
    "missing_coolant": b"sovereign_temp_rack_inlet_celsius 20\n",
}


@pytest.mark.parametrize("case", sorted(UNUSABLE_POWER))
def test_power_without_a_finite_measurement_is_unavailable(exporter: type[_Exporter], case: str) -> None:
    exporter.body = UNUSABLE_POWER[case]
    url = exporter.url  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        read_power(url, simulation=False)
    simulated = read_power(url, simulation=True)
    assert simulated.is_simulated is True and math.isfinite(simulated.battery_soc)


@pytest.mark.parametrize("case", sorted(UNUSABLE_THERMAL))
def test_thermal_without_a_finite_measurement_is_unavailable(exporter: type[_Exporter], case: str) -> None:
    exporter.body = UNUSABLE_THERMAL[case]
    url = exporter.url  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        read_thermal(url, simulation=False)
    simulated = read_thermal(url, simulation=True)
    assert simulated.is_simulated is True and simulated.is_overtemp is False


def test_missing_or_non_finite_secondary_values_are_none_not_nominal(exporter: type[_Exporter]) -> None:
    url = exporter.url  # type: ignore[attr-defined]
    exporter.body = MINIMAL_METRICS
    power = read_power(url, simulation=False)
    assert power == PowerReading(battery_soc=61.5, solar_watts=None)
    assert power.battery_voltage is None and power.system_load_watts is None and power.net_power is None
    thermal = read_thermal(url, simulation=False)
    assert thermal == ThermalReading(coolant_celsius=31.0)
    assert thermal.rack_inlet_celsius is None and thermal.thermal_delta is None

    exporter.body = MINIMAL_METRICS + (
        b"sovereign_battery_voltage_volts NaN\nsovereign_system_power_draw_watts +Inf\n"
        b"sovereign_solar_pv_power_watts NaN\nsovereign_temp_rack_inlet_celsius NaN\n"
        b"sovereign_temp_rack_exhaust_celsius 33\n"
    )
    power = read_power(url, simulation=False)
    assert (power.battery_voltage, power.system_load_watts, power.solar_watts) == (None, None, None)
    thermal = read_thermal(url, simulation=False)
    assert thermal.rack_inlet_celsius is None and thermal.rack_exhaust_celsius == 33.0

    exporter.body = GOOD_METRICS
    power = read_power(url, simulation=False)
    assert (power.battery_voltage, power.system_load_watts, power.net_power) == (52.1, 310.0, 590.0)
    assert read_thermal(url, simulation=False).thermal_delta == 10.5


def test_status_api_reports_unknowns_as_null_and_is_strict_json(
    node: int, monkeypatch: pytest.MonkeyPatch, exporter: type[_Exporter]
) -> None:
    exporter.body = MINIMAL_METRICS
    _hardware_mode(monkeypatch)
    status, raw = _request(node, "GET", "/api/status")
    assert status == 200 and "NaN" not in raw and "Infinity" not in raw
    payload = _strict_loads(raw)
    power, thermal = payload["power"], payload["thermal"]
    assert power["battery_soc"] == 61.5 and power["is_simulated"] is False
    assert power["battery_voltage"] is None and power["system_load_watts"] is None
    assert power["solar_watts"] is None and power["net_power_watts"] is None
    assert thermal["rack_inlet_celsius"] is None and thermal["rack_exhaust_celsius"] is None
    assert payload["economy"]["energy_state"] == "NOMINAL_ENERGY_STATE"  # unknown solar earns no discount
    assert payload["hardware"]["fan_rpm"] == dashboard.estimate_fan_rpm(31.0, None)

    flat = SMDCClient(base_url=f"http://127.0.0.1:{node}").get_telemetry()
    assert flat["source"] == "api" and flat["battery_soc"] == 61.5
    assert flat["solar_watts"] is None and flat["ambient_temp_c"] is None and flat["coolant_temp_c"] == 31.0


@pytest.mark.parametrize(
    "body",
    [UNUSABLE_POWER["nan_soc"], UNUSABLE_POWER["source_down"], GOOD_METRICS + b"sovereign_temp_coolant_celsius NaN\n"],
)
def test_status_api_is_unavailable_in_hardware_mode_without_measurements(
    node: int, monkeypatch: pytest.MonkeyPatch, exporter: type[_Exporter], body: bytes
) -> None:
    exporter.body = body
    _hardware_mode(monkeypatch)
    status, raw = _request(node, "GET", "/api/status")
    assert status == 503 and "NaN" not in raw
    assert _strict_loads(raw)["success"] is False
    telemetry = SMDCClient(base_url=f"http://127.0.0.1:{node}").get_telemetry()
    assert telemetry["source"] == "unavailable" and telemetry["power_state"] == "UNKNOWN"


def test_mcp_and_sdk_consumers_handle_unknown_values(
    monkeypatch: pytest.MonkeyPatch, exporter: type[_Exporter]
) -> None:
    exporter.body = MINIMAL_METRICS
    _hardware_mode(monkeypatch)
    telemetry = tool_get_telemetry({})
    assert telemetry["power"]["battery_voltage_volts"] is None
    assert telemetry["power"]["system_load_watts"] is None and telemetry["power"]["net_power_watts"] is None
    assert telemetry["thermal"]["rack_inlet_celsius"] is None
    assert tool_get_system_status({})["market"]["multiplier"] == 1.0
    pricing = tool_query_market_pricing({})
    assert pricing["solar_pv_watts"] is None and pricing["dynamic_multiplier"] == 1.0
    assert tool_query_market_pricing({"battery_soc": "nan"})["success"] is False
    resource = _strict_loads(read_telemetry_resource())
    assert resource["power"]["battery_voltage_volts"] is None and resource["power"]["net_power_watts"] is None
    assert _strict_loads(read_market_resource())["solar_watts"] is None

    with patch.object(SMDCClient, "_http_get", return_value={}):
        flat = SMDCClient().get_telemetry()
    assert flat["source"] == "hal" and flat["solar_watts"] is None and flat["ambient_temp_c"] is None

    exporter.body = UNUSABLE_POWER["source_down"]
    with pytest.raises(RuntimeError):
        tool_get_telemetry({})


def test_sdk_flattening_never_passes_on_non_numbers() -> None:
    flatten = SMDCClient._flatten_status_payload
    assert flatten({"power": {"battery_soc": None, "solar_watts": 5}}) is None
    assert flatten({"power": {"battery_soc": float("nan"), "solar_watts": 5}}) is None
    assert flatten({"power": {"battery_soc": True}}) is None
    flat = flatten({"power": {"battery_soc": 40, "solar_watts": "many"}, "thermal": {"coolant_celsius": float("inf")}})
    assert flat is not None
    assert flat["battery_soc"] == 40.0 and flat["solar_watts"] is None and flat["coolant_temp_c"] is None


def test_market_multiplier_with_unknown_or_invalid_inputs() -> None:
    assert ComputeMarket.get_dynamic_multiplier(90.0, None) == (1.0, "NOMINAL_ENERGY_STATE")
    assert ComputeMarket.get_dynamic_multiplier(90.0, float("nan")) == (1.0, "NOMINAL_ENERGY_STATE")
    assert ComputeMarket.get_dynamic_multiplier(90.0, 800.0)[0] == 0.5
    with pytest.raises(ValueError, match="battery_soc"):
        ComputeMarket.get_dynamic_multiplier(float("nan"), 800.0)


def test_strict_json_never_emits_nan_or_infinity(caplog: pytest.LogCaptureFixture) -> None:
    document = {"a": float("nan"), "b": [1.5, float("inf"), {"c": float("-inf")}], "d": (2, "x"), "e": None}
    caplog.set_level(logging.WARNING)
    raw = dumps_strict(document)
    assert _strict_loads(raw) == {"a": None, "b": [1.5, None, {"c": None}], "d": [2, "x"], "e": None}
    assert "Non-finite" in caplog.text
    assert dumps_strict({"x": 1.5}, allow_nan=True, indent=None) == '{"x": 1.5}'
    assert replace_non_finite(3) == 3


def test_cli_status_shows_missing_values_as_not_available(
    capsys: pytest.CaptureFixture[str], exporter: type[_Exporter]
) -> None:
    from sovereign_dc.hal import power as hal_power

    real_fetch = hal_power.fetch_exporter_metrics
    url = exporter.url  # type: ignore[attr-defined]

    def run(body: bytes) -> str:
        exporter.body = body
        with (
            patch("subprocess.run", side_effect=OSError("no docker")),
            patch.object(hal_power, "fetch_exporter_metrics", lambda _url, timeout=2.0: real_fetch(url, timeout)),
        ):
            cli.cmd_status(Namespace())
        return capsys.readouterr().out

    out = run(MINIMAL_METRICS + b"sovereign_battery_voltage_volts NaN\n")
    assert "61.5%" in out and "(n/a)" in out and "Solar PV Input:" in out and "nan" not in out.lower()
    assert "52.8" not in out and "280" not in out
    out = run(UNUSABLE_POWER["source_down"])
    assert "has no measurements" in out and "Battery Bank" not in out


# ── ASTRA 9: a promise settles exactly the amount that was signed ─────────────


def _channel_pair(deposit: float = 2.0) -> tuple[NodeWallet, StateChannel, StateChannel]:
    payer = NodeWallet.create(node_id="payer")
    payee = NodeWallet.create(node_id="payee")
    sender = StateChannel("ch-1", payer.address, payee.address, deposit)
    receiver = StateChannel("ch-1", payer.address, payee.address, deposit)
    return payer, sender, receiver


@pytest.mark.parametrize("tampered", [1.0000015, 1.0000014, 1.0000005, 1.0000009999, 1.000001 + 2**-40])
def test_a_promise_amount_between_micro_credits_does_not_verify(tampered: float) -> None:
    payer, sender, receiver = _channel_pair()
    promise = sender.stream_micropayment(1.000001, payer)
    promise.amount_transferred = tampered
    assert receiver.verify_promise(promise, payer.keypair.public_key_hex) is False
    assert receiver.last_verified_promise is None
    assert receiver.close() == (0.0, 2.0)
    assert sender.close() == (1.000001, 0.999999)


def test_both_sides_settle_the_signed_micro_credit_amount() -> None:
    payer, sender, receiver = _channel_pair()
    for amount in (0.1, 0.2, 0.700001):
        promise = sender.stream_micropayment(amount, payer)
        assert receiver.verify_promise(promise, payer.keypair.public_key_hex) is True
    assert credits_to_micro(receiver.settled_amount()) == 1_000_001
    assert sender.close() == receiver.close() == (1.000001, 0.999999)


def test_promises_signed_with_the_six_decimal_text_of_older_releases_still_verify() -> None:
    payer, _, receiver = _channel_pair()
    timestamp = "2026-01-01T00:00:00+00:00"
    amount = 1.25
    signature = payer.sign_payload(f"ch-1:1:{amount:.6f}:{timestamp}".encode())
    promise = StateChannelPromise("ch-1", 1, amount, timestamp, signature)
    assert receiver.verify_promise(promise, payer.keypair.public_key_hex) is True
    assert ledger_module._micro_text(1_250_000) == "1.250000"
    assert ledger_module._micro_text(1) == "0.000001" and ledger_module._micro_text(-1_500_000) == "-1.500000"


# ── ASTRA 10: balances are integers of micro-credits ──────────────────────────


def _total_micro(ledger: Ledger) -> int:
    return sum(entry["balance_micro"] for entry in ledger.export_state()["balances"].values())


def test_balances_are_exact_at_the_top_of_the_accepted_range() -> None:
    ledger = Ledger()
    rich = NodeWallet.create(node_id="rich")
    other = NodeWallet.create(node_id="other")
    ledger.mint(rich.address, float(MAX_CREDITS))
    ledger.transfer(rich, other.address, 0.000001, fee=0.000001)
    assert ledger.get_balance_micro(rich.address) == MAX_CREDITS * MICRO - 2
    assert ledger.get_balance_micro(other.address) == 1
    # The float API identifies the same integer.
    assert credits_to_micro(ledger.get_balance(rich.address)) == MAX_CREDITS * MICRO - 2
    assert ledger.get_balance(rich.address) == 1999999999.999998
    ledger.transfer(other, rich.address, 0.000001)
    assert ledger.get_balance_micro(rich.address) == MAX_CREDITS * MICRO - 1
    assert ledger.get_balance_micro(other.address) == 0
    assert ledger.verify_chain()


def test_amounts_and_balances_beyond_the_bound_are_refused() -> None:
    ledger = Ledger()
    wallet = NodeWallet.create(node_id="w")
    for too_much in (9_000_000_000.0, MAX_CREDITS + 0.000001, float(2**53) / MICRO):
        with pytest.raises(ValueError):
            ledger.mint(wallet.address, too_much)
        with pytest.raises(ValueError):
            credits_to_micro(too_much)
    ledger.mint(wallet.address, float(MAX_CREDITS))
    with pytest.raises(TransactionRejected) as overflow:
        ledger.mint(wallet.address, 0.000001)
    assert overflow.value.reason == "balance_overflow"
    assert ledger.get_balance_micro(wallet.address) == MAX_CREDITS * MICRO


def test_every_micro_credit_count_within_the_bound_has_its_own_float() -> None:
    rng = random.Random(20261114)
    top = MAX_CREDITS * MICRO
    samples = [0, 1, top, top - 1, top - 2, 2**50, 2**50 + 1, *(rng.randrange(top + 1) for _ in range(50_000))]
    samples += [top - offset for offset in range(2_000)]
    for micro in samples:
        credits = ledger_module._from_micro(micro)
        assert ledger_module._to_micro(credits) == micro
        assert ledger_module._exact_micro(credits) == micro
    # The previous bound (2**53 micro-credits) does not have this property.
    assert ledger_module._to_micro(ledger_module._from_micro(9_000_000_000_000_001)) != 9_000_000_000_000_001


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_conservation_minted_minus_fees_equals_the_sum_of_balances(seed: int, tmp_path: Path) -> None:
    rng = random.Random(seed)
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    wallets = [NodeWallet.create(node_id=f"n{i}") for i in range(4)]
    top = MAX_CREDITS * MICRO
    minted = fees = 0

    def random_micro(limit: int) -> int:
        # Mix magnitudes: single micro-credits, ordinary amounts, and amounts near the bound.
        return max(1, min(limit, rng.choice([1, 10**3, 10**6, 10**9, 10**12, limit]) * rng.randrange(1, 1000) // 999))

    minted_first = top - rng.randrange(1000)
    ledger.mint(wallets[0].address, minted_first / MICRO)
    minted += minted_first
    for _ in range(60):
        sender = rng.choice(wallets)
        recipient = rng.choice([w for w in wallets if w is not sender])
        balance = ledger.get_balance_micro(sender.address)
        if balance < 2 or rng.random() < 0.15:
            room = top - ledger.get_balance_micro(recipient.address)
            if room > 0:
                amount = random_micro(min(room, 10**12))
                ledger.mint(recipient.address, amount / MICRO)
                minted += amount
            continue
        amount = random_micro(balance - 1)
        fee = rng.choice([0, 1, min(balance - amount, 250_000)])
        if ledger.get_balance_micro(recipient.address) + amount > top:
            continue
        ledger.transfer(sender, recipient.address, amount / MICRO, fee=fee / MICRO)
        fees += fee
        assert _total_micro(ledger) == minted - fees

    assert _total_micro(ledger) == minted - fees
    for wallet in wallets:
        micro = ledger.get_balance_micro(wallet.address)
        assert 0 <= micro <= top and credits_to_micro(ledger.get_balance(wallet.address)) == micro
    assert ledger.verify_chain()
    # A second process sees the same integers.
    assert _total_micro(Ledger(db_path=str(tmp_path / "ledger.db"))) == minted - fees


def test_a_ledger_with_real_balances_only_is_migrated_in_place(tmp_path: Path) -> None:
    db_path = str(tmp_path / "old.db")
    wallet = NodeWallet.create(node_id="old")
    peer = NodeWallet.create(node_id="peer")
    Ledger(db_path=db_path).mint(wallet.address, 10.0)
    # Put the file back into the shape older releases wrote: REAL balances, no balance_micro.
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE balances DROP COLUMN balance_micro")
    conn.execute("UPDATE balances SET balance = 0.19999999999999998 WHERE address = ?", (wallet.address,))
    conn.execute("INSERT INTO balances (address, balance, nonce) VALUES (?, 1234.5678915, 0)", (peer.address,))
    conn.commit()
    conn.close()

    ledger = Ledger(db_path=db_path)
    assert ledger.get_balance_micro(wallet.address) == 200_000 and ledger.get_balance(wallet.address) == 0.2
    assert ledger.get_balance_micro(peer.address) == 1_234_567_892  # nearest micro-credit, exactly rounded
    assert ledger.verify_chain()
    ledger.transfer(wallet, peer.address, 0.2)

    conn = sqlite3.connect(db_path)
    rows = dict((row[0], row[1:]) for row in conn.execute("SELECT address, balance, balance_micro FROM balances"))
    conn.close()
    # Both columns are kept in step: an older reader of the REAL column sees the same balance.
    assert rows[wallet.address] == (0.0, 0)
    assert rows[peer.address] == (1234.767892, 1_234_767_892)


def test_a_real_balance_written_by_an_older_release_takes_effect(tmp_path: Path) -> None:
    db_path = str(tmp_path / "shared.db")
    wallet = NodeWallet.create(node_id="w")
    newcomer = NodeWallet.create(node_id="n")
    ledger = Ledger(db_path=db_path)
    ledger.mint(wallet.address, 5.0)
    conn = sqlite3.connect(db_path)
    # What an older release does: it updates/inserts the REAL column and knows no balance_micro.
    conn.execute("UPDATE balances SET balance = 7.25 WHERE address = ?", (wallet.address,))
    conn.execute("INSERT INTO balances (address, balance, nonce) VALUES (?, 0.5, 0)", (newcomer.address,))
    conn.commit()
    conn.close()
    assert ledger.get_balance_micro(wallet.address) == 7_250_000
    assert ledger.get_balance_micro(newcomer.address) == 500_000
    assert ledger.export_state()["balances"][wallet.address] == {
        "balance": 7.25,
        "balance_micro": 7_250_000,
        "nonce": 0,
    }


# ── Leftover: the mesh daemon's poll interval is configurable ─────────────────


@pytest.mark.parametrize(
    ("raw", "expected"), [("", 60.0), ("  ", 60.0), ("5", 5.0), ("2.5", 2.5), ("0.2", 1.0), ("0", 1.0), ("-9", 1.0)]
)
def test_mesh_poll_interval_parsing(raw: str, expected: float) -> None:
    assert mesh_sync.poll_interval_seconds(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "nan", "inf"])
def test_mesh_poll_interval_ignores_unusable_values(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert mesh_sync.poll_interval_seconds(raw) == mesh_sync.DEFAULT_POLL_INTERVAL_SECONDS
    assert "MESH_POLL_INTERVAL_SECONDS" in caplog.text


def test_mesh_daemon_sleeps_for_the_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESH_POLL_INTERVAL_SECONDS", "7")
    assert mesh_sync.poll_interval_seconds() == 7.0
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        raise StopIteration

    with (
        patch.object(mesh_sync, "load_peers", return_value=[]),
        patch.object(mesh_sync.time, "sleep", side_effect=fake_sleep),
        pytest.raises(StopIteration),
    ):
        mesh_sync.run_mesh_daemon()
    assert slept == [7.0]
    monkeypatch.delenv("MESH_POLL_INTERVAL_SECONDS")
    assert mesh_sync.poll_interval_seconds() == 60.0


def test_config_mesh_poll_interval_uses_the_env_name_the_daemon_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    assert smdc_config.SovereignConfig.from_env().mesh_poll_interval == 60.0
    monkeypatch.setenv("MESH_POLL_INTERVAL_SECONDS", "12")
    assert smdc_config.SovereignConfig.from_env().mesh_poll_interval == 12.0
    monkeypatch.setenv("MESH_POLL_INTERVAL_SECONDS", "0")
    assert smdc_config.SovereignConfig.from_env().mesh_poll_interval == 1.0
    assert smdc_config.SovereignConfig(mesh_poll_interval=float("nan")).mesh_poll_interval == 60.0
    standalone = (REPO_ROOT / "software" / "mesh" / "mesh_sync.py").read_bytes()
    assert standalone == (REPO_ROOT / "src" / "sovereign_dc" / "mesh" / "mesh_sync.py").read_bytes()
