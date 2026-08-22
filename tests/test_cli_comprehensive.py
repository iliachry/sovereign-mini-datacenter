import json
import os
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc import cli

# === Test cmd_status ===


def test_cmd_status_full_online(capsys):
    mock_docker_res = MagicMock()
    mock_docker_res.returncode = 0
    mock_docker_res.stdout = "sovereign_ollama\tUp 3 hours\t11434/tcp\nsovereign_qdrant\tUp 3 hours\t6333/tcp"

    mock_power_resp = MagicMock()
    mock_power_resp.read.return_value = (
        b"sovereign_battery_soc_percent 92.5\n"
        b"sovereign_battery_voltage_volts 53.4\n"
        b"sovereign_solar_pv_power_watts 1420.0\n"
        b"sovereign_system_power_draw_watts 310.0\n"
        b"sovereign_temp_coolant_celsius 26.5\n"
        b"sovereign_load_shedding_active 0.0\n"
    )
    mock_power_resp.__enter__.return_value = mock_power_resp

    mock_space_resp = MagicMock()
    mock_space_resp.read.return_value = (
        b"sovereign_space_link_active 1\n"
        b"sovereign_space_elevation_degrees 45.2\n"
        b"sovereign_space_azimuth_degrees 182.0\n"
        b"sovereign_space_link_snr_db 14.8\n"
        b"sovereign_space_doppler_shift_hz 1200.0\n"
        b"sovereign_space_next_pass_seconds 300\n"
        b"sovereign_space_bundle_spool_count 4\n"
    )
    mock_space_resp.__enter__.return_value = mock_space_resp

    with patch("subprocess.run", return_value=mock_docker_res):
        with patch("urllib.request.urlopen", side_effect=[mock_power_resp, mock_space_resp]):
            cli.cmd_status(Namespace())

    out = capsys.readouterr().out
    assert "System Status" in out
    assert "Battery Bank:" in out and "92.5%" in out
    assert "Solar PV Input:" in out and "1420 W" in out
    assert "ONLINE (IN CONTACT)" in out
    assert "4 bundles queued" in out


def test_cmd_status_offline_fallbacks(capsys):
    with patch("subprocess.run", side_effect=Exception("Docker socket missing")):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            cli.cmd_status(Namespace())

    out = capsys.readouterr().out
    assert "Docker daemon unavailable" in out
    assert "Power telemetry exporter offline" in out
    assert "STANDBY (Offline Exporter)" in out


# === Test cmd_audit ===


def test_cmd_audit(capsys):
    with patch("sys.platform", "win32"):
        cli.cmd_audit(Namespace())
    out = capsys.readouterr().out
    assert "Security Compliance Audit" in out
    assert "Address Space Layout Randomization" in out
    assert "Zero-Trust WireGuard Mesh" in out
    assert "All critical hardening benchmarks satisfied" in out


# === Test cmd_mesh ===


def test_cmd_mesh(capsys):
    cli.cmd_mesh(Namespace())
    out = capsys.readouterr().out
    assert "Sovereign Global Mesh" in out
    assert "smdc-node-01" in out
    assert "smdc-node-02" in out
    assert "smdc-node-03" in out


# === Test cmd_space_passes & cmd_space_status ===


def test_cmd_space_passes(capsys):
    args = Namespace(hours=6.0, min_el=10.0)
    cli.cmd_space_passes(args)
    out = capsys.readouterr().out
    assert "Upcoming Space Contact Passes" in out


def test_cmd_space_status(capsys):
    cli.cmd_space_status(Namespace())
    out = capsys.readouterr().out
    assert "Space Communication & Link Budget Status" in out


# === Test cmd_space_send & cmd_space_queue ===


def test_cmd_space_send_and_queue(tmp_path, capsys):
    spool_db = str(tmp_path / "cli_spool.db")
    with patch.dict(os.environ, {"DTN_DB_PATH": spool_db}):
        # Send text message
        send_args = Namespace(
            destination_eid="dtn://target.earth/rx", message_or_file="HELLO_SPACE_MESH", priority=3, ttl=3600
        )
        cli.cmd_space_send(send_args)
        out = capsys.readouterr().out
        assert "Bundle queued successfully" in out
        assert "dtn://target.earth/rx" in out

        # Send binary file
        test_file = tmp_path / "telemetry.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03\x04")
        send_file_args = Namespace(
            destination_eid="dtn://target.earth/rx", message_or_file=str(test_file), priority=1, ttl=86400
        )
        cli.cmd_space_send(send_file_args)
        out = capsys.readouterr().out
        assert "Bundle queued successfully" in out

        # Queue inspection
        cli.cmd_space_queue(Namespace())
        queue_out = capsys.readouterr().out
        assert "DTN Store-and-Forward Spool Queue" in queue_out
        assert "Total Queued Bundles: 2" in queue_out


# === Test cmd_deploy ===


def test_cmd_deploy_dry_run(tmp_path, capsys):
    soft_dir = tmp_path / "software"
    soft_dir.mkdir()
    (soft_dir / "docker-compose.yml").write_text("version: '3.8'")

    mock_run = MagicMock()
    mock_run.returncode = 0

    with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)):
        with patch("subprocess.run", return_value=mock_run) as mock_sub:
            args = Namespace(
                all=True,
                with_vpn=False,
                with_backup=False,
                with_telemetry=False,
                with_space=False,
                with_agents=False,
                with_security=False,
                dry_run=True,
            )
            cli.cmd_deploy(args)
            mock_sub.assert_called_once()
    out = capsys.readouterr().out
    assert "DRY RUN: Validating compose stack" in out


def test_cmd_deploy_missing_compose(tmp_path, capsys):
    with patch("sovereign_dc.cli.get_project_root", return_value=str(tmp_path)):
        with pytest.raises(SystemExit):
            cli.cmd_deploy(
                Namespace(
                    all=False,
                    with_vpn=False,
                    with_backup=False,
                    with_telemetry=False,
                    with_space=False,
                    with_agents=False,
                    with_security=False,
                    dry_run=False,
                )
            )
    out = capsys.readouterr().out
    assert "Error: Could not find docker-compose.yml" in out


# === Test cmd_telemetry ===


def test_cmd_telemetry(capsys):
    with patch("sovereign_dc.telemetry.run") as mock_run:
        cli.cmd_telemetry(Namespace(port=9101, hardware=False))
        mock_run.assert_called_once_with(port=9101, simulation=True)
    out = capsys.readouterr().out
    assert "Starting Sovereign Power & Thermal Exporter" in out


# === Test cmd_agent_* ===


def test_cmd_agent_status_online(capsys):
    mock_ollama = MagicMock()
    mock_ollama.read.return_value = json.dumps({"models": [{"name": "qwen2.5-coder:7b"}]}).encode("utf-8")
    mock_ollama.__enter__.return_value = mock_ollama

    mock_qdrant = MagicMock()
    mock_qdrant.read.return_value = json.dumps({"result": {"collections": [{"name": "sovereign_knowledge"}]}}).encode(
        "utf-8"
    )
    mock_qdrant.__enter__.return_value = mock_qdrant

    with patch("urllib.request.urlopen", side_effect=[mock_ollama, mock_qdrant]):
        cli.cmd_agent_status(Namespace())

    out = capsys.readouterr().out
    assert "Ollama LLM Engine:" in out and "ONLINE" in out
    assert "Qdrant Vector DB:" in out and "ONLINE" in out
    assert "qwen2.5-coder:7b" in out


def test_cmd_agent_status_offline(capsys):
    with patch("urllib.request.urlopen", side_effect=Exception("Offline")):
        cli.cmd_agent_status(Namespace())
    out = capsys.readouterr().out
    assert "Ollama LLM Engine:" in out and "OFFLINE" in out
    assert "Qdrant Vector DB:" in out and "OFFLINE" in out


def test_cmd_agent_ask_success(capsys):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"response": "To throttle jobs, set Sentinel to L2."}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        cli.cmd_agent_ask(Namespace(query="How do I throttle jobs?", model=None))

    out = capsys.readouterr().out
    assert "To throttle jobs, set Sentinel to L2." in out


def test_cmd_agent_ask_error(capsys):
    with patch("urllib.request.urlopen", side_effect=Exception("Timeout")):
        cli.cmd_agent_ask(Namespace(query="test", model=None))
    out = capsys.readouterr().out
    assert "Error communicating with Ollama" in out


def test_cmd_agent_review(tmp_path, capsys):
    diff_file = tmp_path / "patch.diff"
    diff_file.write_text("+def test(): pass")

    with patch("sovereign_dc.agents.gitlab_reviewer.query_ollama", return_value="LGTM"):
        cli.cmd_agent_review(Namespace(target=str(diff_file)))

    out = capsys.readouterr().out
    assert "Running AI Code Review on" in out
    assert "LGTM" in out

    # Test missing file
    cli.cmd_agent_review(Namespace(target="/non/existent/diff.patch"))
    err_out = capsys.readouterr().out
    assert "Error: Target file or diff" in err_out


def test_cmd_agent_index(tmp_path, capsys):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("# Setup Guide")

    with patch("sovereign_dc.agents.knowledge_indexer.ensure_qdrant_collection"):
        with patch("sovereign_dc.agents.knowledge_indexer.process_file", create=True):
            cli.cmd_agent_index(Namespace(directory=str(docs_dir)))

    out = capsys.readouterr().out
    assert "Indexing documents from" in out
    assert "Successfully indexed" in out

    # Test missing dir
    cli.cmd_agent_index(Namespace(directory="/non/existent/dir"))
    err_out = capsys.readouterr().out
    assert "Error: Target directory" in err_out


def test_cmd_docs(capsys):
    cli.cmd_docs(Namespace())
    out = capsys.readouterr().out
    assert "https://github.com/iliachry/sovereign-mini-datacenter" in out


# === Test main() CLI Router ===


def test_cli_main_router(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["smdc", "docs"])
    cli.main()
    assert "https://github.com/iliachry/sovereign-mini-datacenter" in capsys.readouterr().out


def test_cli_main_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["smdc"])
    cli.main()
    assert "Available commands" in capsys.readouterr().out or "usage:" in capsys.readouterr().out


def test_main_module_execution(monkeypatch, capsys):
    import runpy

    monkeypatch.setattr(sys, "argv", ["smdc", "docs"])
    runpy.run_module("sovereign_dc", run_name="__main__")
    assert "https://github.com/iliachry/sovereign-mini-datacenter" in capsys.readouterr().out
