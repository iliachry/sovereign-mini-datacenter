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


# --------------------------------------------------------------------------------------
# Tri-council round 1
# --------------------------------------------------------------------------------------


def _sent_headers(handler) -> dict[str, str]:
    return {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}


class TestDeployOpenProjectVolumeGuard:
    """`smdc deploy` must refuse, like setup.sh, to recreate OpenProject on an empty volume."""

    _args = TestDeployGuards._args

    @staticmethod
    def _stack(tmp_path):
        soft = tmp_path / "software"
        soft.mkdir()
        (soft / "docker-compose.yml").write_text("services: {}\n")
        (soft / ".env").write_text("OK=1\n")

    @staticmethod
    def _docker(mounts: list[dict] | None, container_exists: bool = True, project: str = "software"):
        calls: list[list[str]] = []

        def run(cmd, **_kwargs):
            calls.append(list(cmd))
            res = MagicMock()
            res.returncode, res.stdout = 0, ""
            if cmd[:3] == ["docker", "container", "inspect"]:
                if not container_exists:
                    res.returncode = 1
                elif "{{ json .Mounts }}" in cmd:
                    res.stdout = json.dumps(mounts)
                elif "--format" in cmd:
                    res.stdout = f"{project}\n"
            elif cmd[-1] == "config":
                res.stdout = f"name: {project}\nservices: {{}}\n"
            return res

        return run, calls

    def test_anonymous_pgdata_volume_blocks_deploy_with_migration_steps(self, tmp_path, capsys):
        self._stack(tmp_path)
        anonymous = "3f9c2b7d1e5a4c6f8b0d2e4a6c8e0f1a3b5d7f9c1e3a5b7d9f1c3e5a7b9d1f3e"
        run, calls = self._docker(
            [{"Type": "volume", "Name": anonymous, "Destination": "/var/openproject/pgdata"}], project="myedge"
        )
        with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)), patch("subprocess.run", run):
            assert cli.cmd_deploy(self._args(with_vpn=True)) == 1
        assert not any("up" in c for c in calls), "compose up must not run"
        err = capsys.readouterr().err
        assert anonymous in err
        assert "myedge_openproject_pgdata" in err
        assert "docker volume create" in err and "docker rm sovereign_openproject" in err
        assert "smdc deploy --with-vpn" in err

    def test_named_volume_bind_mount_or_missing_container_deploys(self, tmp_path):
        self._stack(tmp_path)
        named = [{"Type": "volume", "Name": "software_openproject_pgdata", "Destination": "/var/openproject/pgdata"}]
        bind = [{"Type": "bind", "Source": "/srv/pg", "Destination": "/var/openproject/pgdata"}]
        for run, calls in (self._docker(named), self._docker(bind), self._docker(None, container_exists=False)):
            with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)), patch("subprocess.run", run):
                assert cli.cmd_deploy(self._args()) == 0
            assert calls[-1][-3:] == ["up", "-d", "--remove-orphans"]

    def test_unknown_project_name_refuses(self, tmp_path, capsys):
        self._stack(tmp_path)
        run, calls = self._docker([], project="")
        with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)), patch("subprocess.run", run):
            assert cli.cmd_deploy(self._args()) == 1
        assert not any("up" in c for c in calls)
        assert "Cannot determine the Compose project name" in capsys.readouterr().err

    def test_dry_run_never_inspects_containers(self, tmp_path):
        self._stack(tmp_path)
        run, calls = self._docker([{"Type": "volume", "Name": "anon", "Destination": "/var/openproject/pgdata"}])
        with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)), patch("subprocess.run", run):
            assert cli.cmd_deploy(self._args(dry_run=True)) == 0
        assert len(calls) == 1 and calls[0][-2:] == ["config", "--quiet"]


class TestDashboardBadgesAreDataDriven:
    BADGES = ("shedding-mode", "mppt-status", "thermal-status", "space-link-status", "raft-role", "pqc-status")

    def test_page_has_no_static_health_claims_or_invented_peers(self):
        html = dashboard.DASHBOARD_HTML
        markup = html.split("<script>")[0]
        for claim in ("100.64.0.", "ONLINE", "DTN ARMED", "RAFT LEADER", "MPPT ACTIVE", "TRACKING", "L2 SHEDDING"):
            assert claim not in markup, claim
        assert ">NORMAL<" not in markup and "PQC OPERATIONAL" not in markup
        assert "L2 SHEDDING" not in html

    def test_every_badge_is_written_by_the_page_script(self):
        script = dashboard.DASHBOARD_HTML.split("<script>")[1]
        for badge in self.BADGES:
            assert f"setBadge('{badge}'" in script, badge
        assert "data.thermal.is_overtemp" in script and "'OVERTEMP'" in script
        assert "mesh-nodes-list" in script and "mesh.simulated" in script

    def test_status_payload_carries_what_the_badges_need(self):
        payload = dashboard.get_system_status_payload()
        mesh = payload["mesh"]
        assert mesh["simulated"] is True
        assert [n["node_id"] for n in mesh["nodes"]] == ["smdc-node-01", "smdc-node-02", "smdc-node-03"]
        assert sum(n["role"] == "leader" for n in mesh["nodes"]) == 1
        # The real node is not a member of the simulated cluster, so it is not its leader.
        assert payload["node_id"] not in {n["node_id"] for n in mesh["nodes"]}
        assert mesh["local_role"] == "not_a_member"
        assert payload["space"]["contact_state"] in {"no_satellites", "no_pass", "tracking", "in_contact"}
        assert payload["power"]["shedding_level"] in range(5)

    def test_shedding_level_follows_configured_soc_tiers(self):
        low = MagicMock(battery_soc=15.0, solar_watts=0.0, battery_voltage=48.0, system_load_watts=100.0)
        low.load_shedding_active, low.is_simulated = True, True
        with patch("sovereign_dc.web.dashboard.read_power", return_value=low):
            power = dashboard.get_system_status_payload()["power"]
        assert power["load_shedding_active"] is True
        assert power["shedding_level"] == 3  # < 20 % SoC is tier L3, not "L2"

    def test_contact_state_reports_a_pass_in_progress_and_no_pass(self):
        import time as _time

        now = _time.time()
        in_progress = [{"aos_time": now - 60, "los_time": now + 300}]
        with patch("sovereign_dc.web.dashboard.GroundStation.predict_passes", return_value=in_progress):
            assert dashboard.get_system_status_payload()["space"]["contact_state"] == "in_contact"
        with patch("sovereign_dc.web.dashboard.GroundStation.predict_passes", return_value=[]):
            assert dashboard.get_system_status_payload()["space"]["contact_state"] == "no_pass"


class TestDashboardCors:
    def test_foreign_origin_gets_no_cors_access_by_default(self):
        for path in ("/api/status", "/api/telemetry/stream", "/"):
            h = _handler(path, headers={"Origin": "https://evil.example"})
            h.do_GET()
            assert "Access-Control-Allow-Origin" not in _sent_headers(h), path
        h = _handler("/api/control/rack-door", headers={"Origin": "https://evil.example"})
        h.do_OPTIONS()
        assert "Access-Control-Allow-Origin" not in _sent_headers(h)

    @pytest.mark.parametrize("origin", ["http://localhost:8088", "http://127.0.0.1:5173", "http://[::1]:8088"])
    def test_loopback_origin_is_reflected(self, origin):
        h = _handler("/api/status", headers={"Origin": origin})
        h.do_GET()
        headers = _sent_headers(h)
        assert headers["Access-Control-Allow-Origin"] == origin
        assert headers["Vary"] == "Origin"

    @pytest.mark.parametrize("origin", ["null", "file://", "https://localhost.evil.example", "ftp://localhost"])
    def test_lookalike_origins_are_not_loopback(self, origin):
        h = _handler("/api/status", headers={"Origin": origin})
        h.do_GET()
        assert "Access-Control-Allow-Origin" not in _sent_headers(h)

    def test_configured_origins_and_wildcard_opt_in(self, monkeypatch):
        monkeypatch.setenv("SMDC_CORS_ORIGIN", "https://iliachry.gr, https://ops.example/")
        h = _handler("/api/status", headers={"Origin": "https://ops.example"})
        h.do_GET()
        assert _sent_headers(h)["Access-Control-Allow-Origin"] == "https://ops.example"
        h = _handler("/api/status", headers={"Origin": "https://evil.example"})
        h.do_GET()
        assert "Access-Control-Allow-Origin" not in _sent_headers(h)

        monkeypatch.setenv("SMDC_CORS_ORIGIN", "*")
        h = _handler("/api/status", headers={"Origin": "https://evil.example"})
        h.do_GET()
        assert _sent_headers(h)["Access-Control-Allow-Origin"] == "*"


class TestDashboardReadAuth:
    PROTECTED = ("/api/status", "/api/metaverse/status", "/api/metaverse/slices", "/api/telemetry/stream")

    @pytest.mark.parametrize("path", PROTECTED)
    def test_remote_reads_need_the_token_when_one_is_set(self, monkeypatch, path):
        monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "tok-9f2")
        h = _handler(path, peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(401)
        assert b"power" not in h.wfile.getvalue()

        h = _handler(path, headers={"Authorization": "Bearer wrong"}, peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(401)

        h = _handler(path, headers={"Authorization": "Bearer tok-9f2"}, peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(200)

    def test_stream_accepts_query_token_but_json_endpoints_do_not(self, monkeypatch):
        monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "tok-9f2")
        h = _handler("/api/telemetry/stream?token=tok-9f2", peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(200)
        assert h.wfile.getvalue().decode("utf-8").count("data: ") == 2

        h = _handler("/api/telemetry/stream?token=nope", peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(401)

        h = _handler("/api/status?token=tok-9f2", peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(401)

    def test_local_reads_health_and_page_stay_open(self, monkeypatch):
        monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "tok-9f2")
        h = _handler("/api/status")
        h.do_GET()
        h.send_response.assert_called_with(200)
        for path in ("/health", "/healthz", "/"):
            h = _handler(path, peer="192.168.1.50")
            h.do_GET()
            h.send_response.assert_called_with(200)

    def test_request_relayed_by_a_local_proxy_is_not_local(self, monkeypatch):
        monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", "tok-9f2")
        h = _handler("/api/status", headers={"X-Forwarded-For": "203.0.113.7"})
        h.do_GET()
        h.send_response.assert_called_with(401)

    def test_without_a_token_reads_are_unchanged(self):
        h = _handler("/api/status", peer="192.168.1.50")
        h.do_GET()
        h.send_response.assert_called_with(200)

    def test_page_script_sends_the_token_and_clears_it_from_the_url(self):
        script = dashboard.DASHBOARD_HTML.split("<script>")[1]
        assert "fetch('/api/status', { headers: authHeaders() })" in script
        assert "replaceState" in script


class TestStatusPayloadCache:
    def test_many_clients_share_one_payload_build(self):
        dashboard.invalidate_status_cache()
        real = dashboard.get_system_status_payload
        with patch("sovereign_dc.web.dashboard.get_system_status_payload", side_effect=real) as build:
            for _ in range(3):
                h = _handler("/api/telemetry/stream")
                h.do_GET()
            h = _handler("/api/status")
            h.do_GET()
            assert build.call_count == 1

            # A control action invalidates it, so the next read reflects the change at once.
            h = _handler("/api/control/rack-door", b'{"open": true}')
            h.do_POST()
            h = _handler("/api/status")
            h.do_GET()
            assert build.call_count == 2
            assert json.loads(h.wfile.getvalue())["hardware"]["door_open"] is True

    def test_cache_expires_and_failures_are_not_cached(self):
        dashboard.invalidate_status_cache()
        with patch("sovereign_dc.web.dashboard.get_system_status_payload", side_effect=RuntimeError("no exporter")):
            h = _handler("/api/status")
            h.do_GET()
            h.send_response.assert_called_with(503)
        h = _handler("/api/status")
        h.do_GET()
        h.send_response.assert_called_with(200)
        first = dashboard.get_cached_system_status_payload()
        assert dashboard.get_cached_system_status_payload() is first
        assert dashboard.get_cached_system_status_payload(max_age_seconds=0.0) is not first


class TestDtnSubmissionContract:
    def _post(self, body: dict) -> tuple[int, dict]:
        h = _handler("/api/control/dtn-transmit", json.dumps(body).encode("utf-8"))
        h.do_POST()
        return h.send_response.call_args.args[0], json.loads(h.wfile.getvalue())

    def _stored(self):
        from sovereign_dc.config import get_config
        from sovereign_dc.space.dtn.router import DTNRouter

        return DTNRouter(db_path=get_config().dtn_db_path).get_outbound_queue()

    def test_binary_payload_ttl_and_defaults_are_stored_as_sent(self):
        import base64
        import hashlib

        raw = b"\xff\x00\x80"
        status, res = self._post({"payload_b64": base64.b64encode(raw).decode(), "ttl": 60, "destination": "dtn://g/x"})
        assert status == 200
        (bundle,) = self._stored()
        assert bundle.payload == raw
        assert bundle.lifetime_seconds == 60
        assert bundle.priority == 1  # NORMAL: the one default on every path
        assert bundle.source_eid == "dtn://smdc-dgx-01/api"
        assert res["ttl_seconds"] == 60 and res["priority"] == 1 and res["priority_name"] == "NORMAL"
        assert res["source"] == bundle.source_eid and res["payload_bytes"] == 3
        assert res["payload_sha256"] == hashlib.sha256(raw).hexdigest()

    def test_legacy_text_payload_and_explicit_fields(self):
        status, res = self._post({"payload": "héllo", "priority": 3, "source": "dtn://app.local/svc"})
        assert status == 200
        (bundle,) = self._stored()
        assert bundle.payload == "héllo".encode()
        assert bundle.priority == 3 and res["priority_name"] == "CRITICAL"
        assert bundle.source_eid == "dtn://app.local/svc"

    def test_invalid_source_falls_back_to_the_node_eid_and_ttl_is_capped(self):
        status, res = self._post({"payload": "x", "source": "not an eid", "ttl": 10**9})
        assert status == 200
        assert res["source"] == "dtn://smdc-dgx-01/api"
        assert res["ttl_seconds"] == dashboard.DTN_MAX_TTL_SECONDS and res["ttl_capped"] is True

    @pytest.mark.parametrize(
        "body",
        [
            {"payload": "x", "ttl": 0},
            {"payload": "x", "ttl": -5},
            {"payload": "x", "ttl": "60"},
            {"payload": "x", "ttl": True},
            {"payload": "x", "priority": 7},
            {"payload_b64": "!!not base64!!"},
            {"payload": 5},
            {"payload": "x", "destination": ""},
        ],
    )
    def test_invalid_fields_are_rejected_and_nothing_is_spooled(self, body):
        status, res = self._post(body)
        assert status == 400 and res["success"] is False
        assert self._stored() == []


class TestCliExitCodes:
    def _wallet(self, tmp_path):
        args = dict(wallet_file=str(tmp_path / "w.json"), db_path=str(tmp_path / "l.db"))
        cli.cmd_economy_wallet(Namespace(create=True, pqc=False, mint=None, node_id="n1", **args))
        return args

    def test_failed_transfer_exits_non_zero(self, tmp_path, capsys):
        from sovereign_dc.economy.wallet import NodeWallet

        args = self._wallet(tmp_path)
        recipient = NodeWallet.create(node_id="peer").address
        assert cli.cmd_economy_send(Namespace(recipient=recipient, amount=5000.0, memo="", **args)) == 1
        assert "Transfer failed" in capsys.readouterr().out
        assert cli.cmd_economy_send(Namespace(recipient=recipient, amount=5.0, memo="", **args)) == 0

    def test_failed_transfer_sets_the_process_exit_status(self, tmp_path, monkeypatch):
        args = self._wallet(tmp_path)
        argv = ["smdc", "economy", "send", "sov_x", "1", "--wallet-file", args["wallet_file"]]
        monkeypatch.setattr(sys, "argv", [*argv, "--db-path", args["db_path"]])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

    def test_app_commands_report_failure(self, tmp_path, capsys):
        missing = Namespace(app_id="no-such-app")
        assert cli.cmd_app_start(missing) == 1
        assert cli.cmd_app_stop(missing) == 1
        assert cli.cmd_app_restart(missing) == 1
        assert cli.cmd_app_status(missing) == 1
        assert cli.cmd_app_unregister(missing) == 1
        assert cli.cmd_app_validate(Namespace(path=str(tmp_path / "nope.yaml"))) == 1
        assert cli.cmd_app_register(Namespace(path=str(tmp_path / "nope.yaml"))) == 1
        assert cli.cmd_app_package(Namespace(path=str(tmp_path / "nope"), output=None, no_pqc=True)) == 1

        bad = tmp_path / "smdc-app.yaml"
        bad.write_text("name: X\napp_id: 'Bad Id!'\nentrypoint: ''\n")
        assert cli.cmd_app_validate(Namespace(path=str(bad))) == 1

    def test_agent_commands_report_failure(self, tmp_path):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            assert cli.cmd_agent_ask(Namespace(query="q", model=None)) == 1
        assert cli.cmd_agent_review(Namespace(target=str(tmp_path / "none.diff"))) == 1
        assert cli.cmd_agent_index(Namespace(directory=str(tmp_path / "none"))) == 1
        diff = tmp_path / "a.diff"
        diff.write_text("+x")
        down = "Local LLM review unavailable: refused"
        with patch("sovereign_dc.agents.gitlab_reviewer.query_ollama", return_value=down):
            assert cli.cmd_agent_review(Namespace(target=str(diff))) == 1
        with patch("sovereign_dc.agents.knowledge_indexer.ensure_qdrant_collection", return_value=False):
            assert cli.cmd_agent_index(Namespace(directory=str(tmp_path))) == 1

    def test_chaos_and_bootstrap_report_failure(self, capsys):
        failed = MagicMock(success=False, scenario="split_brain", elapsed_ms=1.0, resilience_score=0.0, details={})
        with patch("sovereign_dc.mesh.chaos.MeshChaosSimulator.simulate_split_brain_partition", return_value=failed):
            assert cli.cmd_mesh_chaos(Namespace(scenario="split-brain")) == 1
        assert cli.cmd_mesh_chaos(Namespace(scenario="split-brain")) == 0

        boom = patch(
            "sovereign_dc.agents.bootstrap_provisioner.BootstrapProvisioner.phase_1_discovery",
            side_effect=RuntimeError("no hardware"),
        )
        with boom:
            assert cli.cmd_bootstrap(Namespace(node_id="n", role="r", dry_run=True, phase=1)) == 1
            assert cli.cmd_bootstrap(Namespace(node_id="n", role="r", dry_run=True)) == 1
        assert "no hardware" in capsys.readouterr().out
        assert cli.cmd_bootstrap(Namespace(node_id="n", role="r", dry_run=True, phase=1)) == 0


class TestCliUsesConfiguredDtnSpool:
    def test_space_commands_follow_the_configured_spool_path(self, tmp_path, monkeypatch, capsys):
        import sovereign_dc.config as config_module
        from sovereign_dc.space.dtn.router import DTNRouter

        configured = tmp_path / "configured" / "spool.db"
        configured.parent.mkdir()
        cfg = config_module.SovereignConfig(dtn_db_path=str(configured), node_id="edge-7")
        monkeypatch.delenv("DTN_DB_PATH", raising=False)
        monkeypatch.setattr(config_module, "get_config", lambda: cfg)

        args = Namespace(destination_eid="dtn://x.y/z", message_or_file="hi", priority=1, ttl=60)
        assert cli.cmd_space_send(args) == 0
        (bundle,) = DTNRouter(db_path=str(configured)).get_outbound_queue()
        assert bundle.source_eid == "dtn://edge-7/cli"

        cli.cmd_space_queue(Namespace())
        out = capsys.readouterr().out
        assert str(configured) in out and "Total Queued Bundles: 1" in out

    def test_environment_variable_still_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "env.db"))
        assert cli._dtn_spool_path() == str(tmp_path / "env.db")
