"""Regression tests for tri-council review round 28.

1. SoC recovery hysteresis caps at 100.0 % in both ``compute_shedding_level`` (shared
   between ``config.py`` and ``sentinel_copilot.py``) and ``EnterpriseManager._shed_causes``,
   so valid high thresholds like 99 % or 100 % always recover at 100 % battery SoC.
2. ``software/telemetry/load_shedder.sh`` embeds the host boot ID in lock/reap symlinks
   so stale locks left after a power-cut reboot are reaped even when the old PID now
   belongs to PID 1 or another long-lived live process.
3. ``hardware/ASSEMBLY_MANUAL.md`` commissioning checklist matches ``BENCHMARKS.md`` ambient
   coolant targets and the actual non-throttling load-shedding scope.
"""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import sovereign_dc.agents.sentinel_copilot as sentinel_copilot
from sovereign_dc.config import SovereignConfig
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, EventType, SovereignEventBus

ROOT = Path(__file__).resolve().parent.parent


def test_shedding_level_hysteresis_recovers_at_100_percent_soc_for_high_thresholds(monkeypatch) -> None:
    for l1 in (99, 100):
        monkeypatch.setenv("SHEDDING_L1_SOC", str(l1))
        monkeypatch.setenv("SHEDDING_L2_SOC", "30")
        monkeypatch.setenv("SHEDDING_L3_SOC", "20")
        monkeypatch.setenv("SHEDDING_L4_SOC", "10")
        monkeypatch.setenv("SHEDDING_HYSTERESIS_SOC", "2")
        cfg = SovereignConfig.from_env()
        thresholds = cfg.shedding_thresholds

        assert thresholds[0] == float(l1)
        assert cfg.get_shedding_level(l1 - 1.0) == 1
        assert cfg.get_shedding_level(float(l1) - 0.5, previous_level=1) == 1
        assert cfg.get_shedding_level(100.0, previous_level=1) == 0

        assert sentinel_copilot.compute_shedding_level(l1 - 1.0, thresholds) == 1
        assert (
            sentinel_copilot.compute_shedding_level(float(l1) - 0.5, thresholds, previous_level=1, hysteresis=2.0) == 1
        )
        assert sentinel_copilot.compute_shedding_level(100.0, thresholds, previous_level=1, hysteresis=2.0) == 0


def test_enterprise_manager_min_battery_soc_99_recovers_at_100_percent_soc(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager = EnterpriseManager(registry=registry, event_bus=bus)

    for app_id, floor in (("app-floor-99", 99.0), ("app-floor-100", 100.0)):
        manifest = EnterpriseRegistry.scaffold_manifest(name=app_id, app_id=app_id)
        manifest.power.tier = PowerPriority.L2_BACKGROUND
        manifest.power.min_battery_soc = floor
        registry.register_app(manifest)
        assert manager.start_app(app_id)[0]

    bus.publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 0, "battery_soc": 98.0})
    )
    for app_id in ("app-floor-99", "app-floor-100"):
        st = manager.get_runtime_state(app_id)
        assert st is not None and st.status == AppStatus.PAUSED

    bus.publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 0, "battery_soc": 99.5})
    )
    for app_id in ("app-floor-99", "app-floor-100"):
        st = manager.get_runtime_state(app_id)
        assert st is not None and st.status == AppStatus.PAUSED

    bus.publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 0, "battery_soc": 100.0})
    )
    for app_id in ("app-floor-99", "app-floor-100"):
        st = manager.get_runtime_state(app_id)
        assert st is not None and st.status == AppStatus.RUNNING


def test_load_shedder_reaps_lock_left_from_previous_boot_even_when_pid_1_is_alive(tmp_path: Path) -> None:
    import pytest

    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    fake = tmp_path / "fake"
    bin_dir = tmp_path / "bin"
    fake.mkdir()
    bin_dir.mkdir()
    (fake / "metrics").write_text(
        "sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent 80.0\nsovereign_temp_coolant_celsius 30.0\n",
        encoding="utf-8",
    )
    (fake / "status").write_text("paused\n", encoding="utf-8")

    curl_bin = bin_dir / "curl"
    curl_bin.write_text('#!/bin/sh\ncat "$FAKE/metrics"\n', encoding="utf-8")
    os.chmod(curl_bin, 0o700)

    docker_bin = bin_dir / "docker"
    docker_bin.write_text(
        '#!/bin/sh\ncase "$1" in\n  inspect) cat "$FAKE/status" ;;\n  unpause) echo running >"$FAKE/status" ;;\nesac\n',
        encoding="utf-8",
    )
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
    script_path = str(ROOT / "software" / "telemetry" / "load_shedder.sh")
    try:
        state_file.write_text("container=sovereign_ollama\ncause=soc\n", encoding="utf-8")
        # Both .lock and .lock.reap belong to PID 1 (always alive / EPERM) from an earlier boot.
        os.symlink("1:0.0:boot=stale-pre-powercut-boot", f"{state_file}.lock")
        os.symlink("1:boot=stale-pre-powercut-boot", f"{state_file}.lock.reap")

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE": str(fake),
            "TMPDIR": str(tmp_path),
            "HOME": str(fake),
            "LOAD_SHED_ACTION": "pause",
            "LOAD_SHED_STATE_FILE": str(state_file),
            "LOAD_SHED_LOCK_TIMEOUT": "0.2",
            "TELEMETRY_METRICS_URL": f"http://127.0.0.1:{server.server_port}/metrics",
        }
        proc = subprocess.run(
            ["bash", script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
    finally:
        server.shutdown()
        server.server_close()

    assert (fake / "status").read_text(encoding="utf-8").strip() == "running"
    assert not state_file.exists()
    assert list(fake.glob("load_shedder.paused.lock*")) == []


def test_assembly_manual_commissioning_checklist_matches_benchmarks_and_shedding_scope() -> None:
    manual = (ROOT / "hardware" / "ASSEMBLY_MANUAL.md").read_text(encoding="utf-8")
    assert "throttles background AI jobs" not in manual
    assert "ECO_PRESERVATION" in manual and "docker pause" in manual
    assert "39.4" in manual and "BENCHMARKS.md" in manual
