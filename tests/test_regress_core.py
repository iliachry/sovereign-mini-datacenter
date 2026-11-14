"""Regression tests for defects found in the full-repository review (CLI, HAL, dashboard)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc import cli
from sovereign_dc.events import Event, SovereignEventBus
from sovereign_dc.hal.power import read_power
from sovereign_dc.hal.thermal import read_thermal
from sovereign_dc.web import dashboard
from sovereign_dc.web.dashboard import DashboardHandler


class _Resp:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


NO_SOURCE = "sovereign_telemetry_simulated 0\nsovereign_telemetry_source_up 0\n"


class TestHalNeverInventsReadings:
    def test_power_exporter_without_measurements_raises_in_hardware_mode(self):
        with patch("urllib.request.urlopen", return_value=_Resp(NO_SOURCE)):
            with pytest.raises(RuntimeError):
                read_power(simulation=False)

    def test_power_exporter_without_measurements_is_flagged_simulated(self):
        with patch("urllib.request.urlopen", return_value=_Resp(NO_SOURCE)):
            assert read_power(simulation=True).is_simulated is True

    def test_thermal_exporter_without_measurements_raises_in_hardware_mode(self):
        with patch("urllib.request.urlopen", return_value=_Resp(NO_SOURCE)):
            with pytest.raises(RuntimeError):
                read_thermal(simulation=False)

    def test_simulated_exporter_values_are_flagged(self):
        text = "sovereign_telemetry_simulated 1\nsovereign_battery_soc_percent 42.0\n"
        with patch("urllib.request.urlopen", return_value=_Resp(text)):
            reading = read_power(simulation=False)
        assert reading.battery_soc == 42.0
        assert reading.is_simulated is True


def _handler(path: str, body: bytes = b"", headers: dict[str, str] | None = None, peer: str = "127.0.0.1"):
    handler = DashboardHandler.__new__(DashboardHandler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(body)), **(headers or {})}
    handler.client_address = (peer, 0)
    handler.sse_max_events = 2
    handler.sse_interval_seconds = 0.0
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    return handler


class TestDashboardControlAuth:
    def test_remote_peer_without_token_is_refused(self):
        h = _handler("/api/control/pdu-outlet", b'{"outlet": 3, "state": false}', peer="192.168.1.50")
        h.do_POST()
        h.send_response.assert_called_with(403)

    def test_foreign_origin_without_token_is_refused(self):
        h = _handler("/api/control/rack-door", b'{"open": true}', headers={"Origin": "https://evil.example"})
        h.do_POST()
        h.send_response.assert_called_with(403)

    def test_loopback_origin_without_token_is_allowed(self):
        h = _handler("/api/control/rack-door", b'{"open": true}', headers={"Origin": "http://localhost:8088"})
        h.do_POST()
        h.send_response.assert_called_with(200)

    def test_remote_peer_with_valid_token_is_allowed(self, monkeypatch):
        monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "tok")
        h = _handler(
            "/api/control/rack-door",
            b'{"open": false}',
            headers={"Authorization": "Bearer tok", "Origin": "https://example.org"},
            peer="10.0.0.9",
        )
        h.do_POST()
        h.send_response.assert_called_with(200)

    def test_default_bind_is_loopback(self, monkeypatch):
        monkeypatch.delenv("SMDC_DASHBOARD_HOST", raising=False)
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("sovereign_dc.web.dashboard.HTTPServer", return_value=server) as ctor:
            dashboard.run_dashboard_server(port=8899)
        assert ctor.call_args.args[0] == ("127.0.0.1", 8899)


class TestDashboardInputValidation:
    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/api/control/pdu-outlet", b'{"outlet": "abc"}'),
            ("/api/control/pdu-outlet", b'{"outlet": 99}'),
            ("/api/control/pdu-outlet", b'{"outlet": 1, "state": "false"}'),
            ("/api/control/rack-door", b'{"open": "false"}'),
            ("/api/control/dtn-transmit", b'{"priority": "hi"}'),
            ("/api/metaverse/optimize", b'{"cycles": null}'),
            ("/api/control/rack-door", b"not json"),
            ("/api/control/rack-door", b"[1]"),
        ],
    )
    def test_bad_fields_return_400(self, path, body):
        h = _handler(path, body)
        h.do_POST()
        h.send_response.assert_called_with(400)

    def test_emergency_stop_is_idempotent_and_resume_is_explicit(self):
        orch = dashboard.get_metaverse_orchestrator()
        orch.resume_operation()
        for _ in range(2):
            h = _handler("/api/metaverse/emergency-stop", b"{}")
            h.do_POST()
            assert json.loads(h.wfile.getvalue())["emergency_stopped"] is True
        h = _handler("/api/metaverse/resume", b"{}")
        h.do_POST()
        assert json.loads(h.wfile.getvalue())["emergency_stopped"] is False

    def test_sse_stream_emits_repeated_events_and_closes(self):
        h = _handler("/api/telemetry/stream")
        h.do_GET()
        assert h.wfile.getvalue().decode("utf-8").count("data: ") == 2
        assert h.close_connection is True

    def test_status_payload_marks_simulated_telemetry(self):
        payload = dashboard.get_system_status_payload()
        assert payload["power"]["is_simulated"] is True
        assert payload["thermal"]["is_simulated"] is True


class TestCliRegressions:
    def test_status_survives_no_upcoming_pass(self, capsys):
        with (
            patch("urllib.request.urlopen", side_effect=OSError("offline")),
            patch("sovereign_dc.cli.GroundStation.predict_passes", return_value=[]),
        ):
            cli.cmd_status(Namespace())
        assert "No pass in the next 6 hours" in capsys.readouterr().out

    def test_market_accepts_zero_soc_and_solar(self, capsys):
        cli.cmd_economy_market(Namespace(soc=0.0, solar=0.0))
        out = capsys.readouterr().out
        assert "85.0%" not in out
        assert "0.0%" in out

    def test_arg_keeps_zero(self):
        assert cli._arg(Namespace(limit=0), "limit", 15) == 0
        assert cli._arg(Namespace(), "limit", 15) == 15

    def test_consensus_rejects_non_positive_nodes(self, capsys):
        assert cli.cmd_mesh_consensus(Namespace(nodes=-1)) == 2

    def test_sim_run_zero_cycles_does_not_crash(self, capsys):
        assert cli.cmd_sim_run(Namespace(cycles=0, json=False)) in (0, None)

    def test_space_send_directory_is_treated_as_text(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
        args = Namespace(destination_eid="dtn://x.y/z", message_or_file=str(tmp_path), priority=1, ttl=60)
        assert cli.cmd_space_send(args) == 0

    def test_space_send_reports_rejected_bundle(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
        args = Namespace(destination_eid="dtn://x.y/z", message_or_file="hi", priority=1, ttl=60)
        with patch("sovereign_dc.cli.DTNRouter.queue_bundle", return_value=False):
            assert cli.cmd_space_send(args) == 1
        assert "not queued" in capsys.readouterr().out

    def test_malformed_exporter_port_does_not_break_cli(self):
        res = subprocess.run(
            [sys.executable, "-m", "sovereign_dc.cli", "--version"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "EXPORTER_PORT": "abc", "PYTHONPATH": "src"},
        )
        assert res.returncode == 0, res.stderr


def test_event_history_limit_zero_returns_nothing():
    bus = SovereignEventBus()
    bus.publish(Event(event_type="x.y", source="t", payload={}))
    assert bus.get_history(limit=0) == []
    assert len(bus.get_history(limit=1)) == 1


class TestDeployGuards:
    def _args(self, **kw):
        base = dict(
            all=False,
            with_vpn=False,
            with_backup=False,
            with_telemetry=False,
            with_space=False,
            with_agents=False,
            with_security=False,
            dry_run=False,
        )
        return Namespace(**{**base, **kw})

    def test_deploy_refuses_placeholder_env(self, tmp_path, capsys):
        soft = tmp_path / "software"
        soft.mkdir()
        (soft / "docker-compose.yml").write_text("services: {}\n")
        (soft / ".env").write_text("GRAFANA_ADMIN_PASSWORD=change_me_strong_password\nOK=1\n")
        with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)), patch("subprocess.run") as run:
            assert cli.cmd_deploy(self._args()) == 1
        run.assert_not_called()
        assert "GRAFANA_ADMIN_PASSWORD" in capsys.readouterr().out

    def test_deploy_propagates_compose_failure(self, tmp_path):
        soft = tmp_path / "software"
        soft.mkdir()
        (soft / "docker-compose.yml").write_text("services: {}\n")
        (soft / ".env").write_text("OK=1\n")
        with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)), patch("subprocess.run") as run:
            run.return_value.returncode = 17
            assert cli.cmd_deploy(self._args()) == 17


class TestLegacyEconomyStatePath:
    def test_planted_world_writable_legacy_file_is_not_trusted(self, tmp_path, monkeypatch):
        legacy_dir = tmp_path / "tmp"
        legacy_dir.mkdir()
        planted = legacy_dir / "smdc_default_wallet.json"
        planted.write_text("{}")
        planted.chmod(0o666)
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(legacy_dir))
        assert not cli._economy_state_path("smdc_default_wallet.json").startswith(str(legacy_dir))

    def test_own_private_legacy_file_keeps_being_used(self, tmp_path, monkeypatch):
        legacy_dir = tmp_path / "tmp"
        legacy_dir.mkdir()
        mine = legacy_dir / "smdc_default_wallet.json"
        mine.write_text("{}")
        mine.chmod(0o600)
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(legacy_dir))
        assert cli._economy_state_path("smdc_default_wallet.json") == str(mine)


def test_deploy_placeholder_markers_cover_env_example():
    """Every placeholder style in env.example must be caught, matching setup.sh."""
    from pathlib import Path

    env_example = Path(__file__).parents[1] / "software" / "env.example"
    keys = cli._env_placeholder_keys(str(env_example))
    assert {"SMTP_HOST", "SMTP_USER", "GRAFANA_ADMIN_PASSWORD"} <= set(keys)


class TestDashboardQueryStrings:
    @pytest.mark.parametrize(
        "path", ["/?token=operator-token", "/index.html?token=x", "/api/status?t=1", "/health?probe=1"]
    )
    def test_get_routes_ignore_query_string(self, path):
        h = _handler(path)
        h.do_GET()
        h.send_response.assert_called_with(200)

    def test_post_routes_ignore_query_string(self):
        h = _handler("/api/control/rack-door?src=twin", b'{"open": true}')
        h.do_POST()
        h.send_response.assert_called_with(200)


class TestEconomyVerifyCommand:
    def test_verify_reports_and_anchor_adopts_a_ledger(self, tmp_path, capsys):
        from sovereign_dc.economy.ledger import Ledger
        from sovereign_dc.economy.wallet import NodeWallet

        db = str(tmp_path / "ledger.db")
        wallet = NodeWallet.create(node_id="n1")
        Ledger(db_path=db).mint(wallet.address, 10.0)
        assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 0
        assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=True)) == 0
        assert "VALID" in capsys.readouterr().out

    def test_verify_missing_ledger_fails(self, tmp_path):
        assert cli.cmd_economy_verify(Namespace(db_path=str(tmp_path / "none.db"), anchor=False)) == 1


class TestLiveBenchmark:
    def test_measures_from_ollama_timing_counters(self):
        payload = json.dumps({"eval_count": 120, "eval_duration": 2_000_000_000, "load_duration": 500_000_000})
        with patch("urllib.request.urlopen", return_value=_Resp(payload)):
            out = cli._measure_ollama_generation("http://ollama:11434", "m")
        assert out["tokens_per_second"] == 60.0
        assert out["eval_tokens"] == 120

    def test_unreachable_server_reports_error_not_a_number(self):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            out = cli._measure_ollama_generation("http://ollama:11434", "m")
        assert "error" in out and "tokens_per_second" not in out
