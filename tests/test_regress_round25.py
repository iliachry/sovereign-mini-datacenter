"""Regression tests for tri-council review round 25.

1. ``software/telemetry/load_shedder.sh`` treats a missing/unreachable Docker daemon
   socket error (``connect: no such file or directory``) as ``unknown`` rather than
   ``absent``, keeping pause ownership intact until the daemon returns.
2. ``SMDCClient.get_telemetry()`` sets ``is_simulated=True`` whenever either power or
   thermal fell back to simulation (plus ``power_is_simulated`` / ``thermal_is_simulated``
   provenance), instead of reporting simulated temperatures with ``is_simulated=False``.
"""

from __future__ import annotations

import http.server
import os
import subprocess
import threading
from pathlib import Path

import pytest

from sovereign_dc.config import reset_config
from sovereign_dc.enterprise.sdk import SMDCClient

ROOT = Path(__file__).resolve().parent.parent


def _run_shedder_with_docker_stderr(tmp_path: Path, inspect_stderr: str, soc: str = "70.0") -> tuple[Path, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_docker = bin_dir / "docker"
    stderr_file = tmp_path / "docker_stderr.txt"
    stderr_file.write_text(inspect_stderr, encoding="utf-8")
    fake_docker.write_text(
        f'#!/usr/bin/env bash\ncat "{stderr_file}" >&2\nexit 1\n',
        encoding="utf-8",
    )
    os.chmod(fake_docker, 0o700)

    exporter_body = (
        f"sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent {soc}\nsovereign_temp_coolant_celsius 30.0\n"
    ).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(exporter_body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        state_file = tmp_path / "pause_state"
        state_file.write_text("container=ollama\ncause=soc\n", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LOAD_SHED_ACTION": "pause",
            "LOAD_SHED_STATE_FILE": str(state_file),
            "TELEMETRY_METRICS_URL": f"http://127.0.0.1:{server.server_port}/metrics",
            "TMPDIR": str(tmp_path),
        }
        proc = subprocess.run(
            ["bash", str(ROOT / "software" / "telemetry" / "load_shedder.sh")],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        return state_file, proc.stdout + proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_load_shedder_keeps_pause_marker_on_docker_socket_no_such_file(tmp_path: Path) -> None:
    state_file, output = _run_shedder_with_docker_stderr(
        tmp_path,
        "failed to connect to the docker API at unix:///run/docker.sock; connect: no such file or directory\n",
    )
    assert state_file.exists(), f"marker must be preserved when Docker socket is missing; output={output}"
    assert "cannot be determined" in output


def test_load_shedder_clears_pause_marker_when_daemon_reports_no_such_container(tmp_path: Path) -> None:
    state_file, output = _run_shedder_with_docker_stderr(
        tmp_path,
        "Error response from daemon: No such container: ollama\n",
    )
    assert not state_file.exists()
    assert "state: absent" in output


def test_sdk_get_telemetry_marks_is_simulated_true_when_thermal_falls_back_to_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exporter has measured power (source_up=1, simulated=0, SoC=82%) but NO coolant probes,
    # in simulation mode -> read_power() is measured, read_thermal() falls back to simulated.
    body = (
        b"sovereign_telemetry_source_up 1\n"
        b"sovereign_telemetry_simulated 0\n"
        b"sovereign_battery_soc_percent 82.0\n"
        b"sovereign_solar_pv_power_watts 900.0\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HAL_MODE", "simulation")
        monkeypatch.setenv("POWER_EXPORTER_URL", f"http://127.0.0.1:{server.server_port}/metrics")
        reset_config()

        client = SMDCClient(base_url="http://127.0.0.1:9")
        tele = client.get_telemetry()
        assert tele["source"] == "hal"
        assert tele["battery_soc"] == 82.0
        assert tele["solar_watts"] == 900.0
        assert tele["power_is_simulated"] is False
        assert tele["thermal_is_simulated"] is True
        assert tele["is_simulated"] is True

        # Dynamic pricing still reflects the measured power inputs accurately.
        quote = client.get_dynamic_pricing()
        assert quote["energy_multiplier"] == 0.5
        assert quote["inputs"]["is_simulated"] is False
    finally:
        reset_config()
        server.shutdown()
        server.server_close()
