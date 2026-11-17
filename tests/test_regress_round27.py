"""Regression tests for tri-council review round 27.

1. ``software/telemetry/load_shedder.sh`` uses an ownership-safe write-once guard-chain
   and atomic ``mv -f`` handover for stale lock / stale reaper takeover, so two concurrent
   waiters discovering a dead ``.lock`` and dead ``.lock.reap`` cannot strip each other's
   locks or overlap in the protected critical section.
2. ``docs/index.html`` Architecture Summary badge states ``BOM ~$24.7K`` consistent with
   the itemized ``$24,701`` BOM.
"""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_load_shedder_double_stale_lock_and_reaper_preserves_mutual_exclusion_and_thermal_hold(
    tmp_path: Path,
) -> None:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    fake = tmp_path / "fake"
    bin_dir = tmp_path / "bin"
    fake.mkdir()
    bin_dir.mkdir()
    (fake / "metrics").write_text(
        "sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent 70.0\nsovereign_temp_coolant_celsius 30.0\n",
        encoding="utf-8",
    )
    (fake / "status").write_text("paused\n", encoding="utf-8")
    (fake / "docker.log").write_text("", encoding="utf-8")

    curl_bin = bin_dir / "curl"
    curl_bin.write_text('#!/bin/sh\ncat "$FAKE/metrics"\n', encoding="utf-8")
    os.chmod(curl_bin, 0o700)

    docker_script = """#!/bin/sh
if ! mkdir "$FAKE/in_docker" 2>/dev/null; then
  touch "$FAKE/overlap_detected"
fi
case "$1" in
  inspect)
    if mkdir "$FAKE/first_inspect_claimed" 2>/dev/null; then
      touch "$FAKE/in_inspect"
      i=0
      while [ ! -e "$FAKE/release_inspect" ] && [ "$i" -lt 250 ]; do
        sleep 0.02
        i=$((i + 1))
      done
    fi
    cat "$FAKE/status" ;;
  pause) echo "pause $2" >>"$FAKE/docker.log"; echo paused >"$FAKE/status" ;;
  unpause) echo "unpause $2" >>"$FAKE/docker.log"; echo running >"$FAKE/status" ;;
esac
rmdir "$FAKE/in_docker" 2>/dev/null || true
"""
    docker_bin = bin_dir / "docker"
    docker_bin.write_text(docker_script, encoding="utf-8")
    os.chmod(docker_bin, 0o700)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = (fake / "metrics").read_bytes()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state_file = fake / "load_shedder.paused"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE": str(fake),
        "TMPDIR": str(tmp_path),
        "HOME": str(fake),
        "LOAD_SHED_ACTION": "pause",
        "LOAD_SHED_STATE_FILE": str(state_file),
        "TELEMETRY_METRICS_URL": f"http://127.0.0.1:{server.server_port}/metrics",
    }
    script_path = str(ROOT / "software" / "telemetry" / "load_shedder.sh")
    procs: list[subprocess.Popen[str]] = []
    try:
        state_file.write_text("container=sovereign_ollama\ncause=soc\n", encoding="utf-8")
        # Double-stale lock state left by earlier crashed runs: both .lock and .lock.reap exist.
        os.symlink("99999999", f"{state_file}.lock")
        os.symlink("99999998", f"{state_file}.lock.reap")

        for _ in range(2):
            procs.append(
                subprocess.Popen(
                    ["bash", script_path],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        deadline = time.monotonic() + 10.0
        while not (fake / "in_inspect").exists():
            if time.monotonic() > deadline:
                raise AssertionError("First winner did not enter docker inspect before deadline")
            time.sleep(0.01)

        # While winner holds the reaped lock on 70%/30C, overheating occurs and a third run arrives.
        (fake / "metrics").write_text(
            "sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent 70.0\nsovereign_temp_coolant_celsius 65.0\n",
            encoding="utf-8",
        )
        procs.append(
            subprocess.Popen(
                ["bash", script_path],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
        time.sleep(0.15)
        (fake / "release_inspect").touch()

        for proc in procs:
            out, err = proc.communicate(timeout=15)
            assert proc.returncode == 0, err or out
    finally:
        (fake / "release_inspect").touch()
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        server.shutdown()
        server.server_close()

    assert not (fake / "overlap_detected").exists()
    assert (fake / "status").read_text(encoding="utf-8").strip() == "paused"
    assert state_file.exists()
    assert "cause=thermal" in state_file.read_text(encoding="utf-8").splitlines()
    assert list(fake.glob("load_shedder.paused.lock*")) == []


def test_docs_index_html_bom_badge_matches_itemized_24_7k_bom() -> None:
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "BOM ~$24.7K" in html
    assert "24.6K" not in html
