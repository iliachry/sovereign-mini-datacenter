"""Regression tests for tri-council review round 20.

1. Firmware Arduino & ESPHome bridges permanently expire stale readings and preserve
   monotonic wrap-safe frame age across full 32-bit ``millis()`` rollovers.
2. ``EnterpriseManager.start_app()`` reconciles shedding-owned pauses caused by
   ``.started`` callbacks when the callback also upgrades the app to ``L0_CRITICAL``,
   while preserving operator pauses.
3. ``SMDCClient.get_dynamic_pricing()`` reads available solar/power telemetry even
   when the exporter omits SoC (MPPT-only bridge) or coolant probes.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sovereign_dc.config import reset_config
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.enterprise.sdk import SMDCClient
from sovereign_dc.events import Event, SovereignEventBus


def test_firmware_wrap_guards_and_exporter_age_rejection(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    ino = (root / "firmware" / "esp32_telemetry_bridge.ino").read_text(encoding="utf-8")
    assert "bool veFrameStale = false;" in ino
    assert "readingSet[idx] = false;" in ino
    assert "expireStaleReadings();" in ino
    assert "ageMs += (1ULL << 32);" in ino

    esp = (root / "firmware" / "esphome_smdc_bridge.yaml").read_text(encoding="utf-8")
    assert "if (id(ve_stale) && age_ms <= 10000ULL)" in esp

    from sovereign_dc import telemetry as exporter_mod

    values, reason = exporter_mod.bridge_values({"frame_age": 4294968.3, "battery_soc": 90.0}, max_frame_age=10.0)
    assert reason is not None and "4294968.3 s old" in reason
    assert "battery_soc" not in values and "load_shedding" not in values


def test_start_app_resumes_shed_pause_when_started_callback_upgrades_to_l0_critical(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App Idle", "app-idle", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)

    resumed_events: list[Event] = []
    paused_events: list[Event] = []
    bus.subscribe("enterprise.app-idle.paused", paused_events.append)
    bus.subscribe("enterprise.app-idle.resumed", resumed_events.append)

    fired: list[bool] = []

    def shed_then_upgrade(_event: Event) -> None:
        if not fired:
            fired.append(True)
            bus.publish(
                Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0})
            )
            assert registry.register_app(
                EnterpriseRegistry.scaffold_manifest("App Idle", "app-idle", power_tier=PowerPriority.L0_CRITICAL)
            )[0]

    bus.subscribe("enterprise.app-idle.started", shed_then_upgrade)
    ok, msg = manager.start_app("app-idle")
    assert ok is True
    assert "held paused by load shedding" not in msg

    state = manager.get_runtime_state("app-idle")
    assert state is not None
    assert state.status == AppStatus.RUNNING
    assert "app-idle" not in manager._shed_paused
    assert len(paused_events) == 1
    assert len(resumed_events) == 1
    assert resumed_events[0].payload["state_seq"] > paused_events[0].payload["state_seq"]


def test_start_app_preserves_operator_pause_during_callback_manifest_upgrade(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App Op", "app-op", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)

    fired: list[bool] = []

    def operator_pause_and_upgrade(_event: Event) -> None:
        if not fired:
            fired.append(True)
            bus.publish(
                Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0})
            )
            # Clear shedding ownership with an explicit operator pause before manifest upgrade.
            with manager._lock:
                manager._shed_paused.pop("app-op", None)
            assert registry.register_app(
                EnterpriseRegistry.scaffold_manifest("App Op", "app-op", power_tier=PowerPriority.L0_CRITICAL)
            )[0]

    bus.subscribe("enterprise.app-op.started", operator_pause_and_upgrade)
    ok, msg = manager.start_app("app-op")
    assert ok is True
    assert "held paused by load shedding" not in msg
    state = manager.get_runtime_state("app-op")
    assert state is not None
    assert state.status == AppStatus.PAUSED
    assert "app-op" not in manager._shed_paused


def test_sdk_get_dynamic_pricing_reads_mppt_only_solar_and_sovereign_without_coolant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_holder = {
        "body": (
            "# TYPE sovereign_telemetry_source_up gauge\n"
            "sovereign_telemetry_source_up 1\n"
            "sovereign_telemetry_simulated 0\n"
            "sovereign_solar_pv_power_watts 1200.0\n"
        )
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            data = body_holder["body"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HAL_MODE", "hardware")
        monkeypatch.setenv("POWER_EXPORTER_URL", f"http://127.0.0.1:{server.server_port}/metrics")
        reset_config()

        client = SMDCClient(base_url="http://127.0.0.1:9")

        # MPPT-only exporter (no SoC, no coolant): solar telemetry still unlocks 0.5x surplus discount.
        quote = client.get_dynamic_pricing(soc=85.0)
        assert quote["status"] == "quoted" and quote["pricing_available"] is True
        assert quote["energy_multiplier"] == 0.5
        assert quote["inputs"] == {
            "battery_soc": 85.0,
            "solar_watts": 1200.0,
            "battery_soc_source": "caller",
            "solar_watts_source": "telemetry:hal",
            "is_simulated": False,
        }

        # Exporter with SoC + solar, still no coolant: unparameterized quote still succeeds cleanly.
        body_holder["body"] = (
            "sovereign_telemetry_source_up 1\n"
            "sovereign_telemetry_simulated 0\n"
            "sovereign_battery_soc_percent 85.0\n"
            "sovereign_solar_pv_power_watts 1200.0\n"
        )
        full_quote = client.get_dynamic_pricing()
        assert full_quote["status"] == "quoted" and full_quote["pricing_available"] is True
        assert full_quote["energy_multiplier"] == 0.5
        assert full_quote["inputs"] == {
            "battery_soc": 85.0,
            "solar_watts": 1200.0,
            "battery_soc_source": "telemetry:hal",
            "solar_watts_source": "telemetry:hal",
            "is_simulated": False,
        }

        # Source down in hardware mode: solar is not invented, multiplier is nominal 1.0x.
        body_holder["body"] = "sovereign_telemetry_source_up 0\nsovereign_solar_pv_power_watts 1200.0\n"
        down_quote = client.get_dynamic_pricing(soc=85.0)
        assert down_quote["status"] == "quoted" and down_quote["energy_multiplier"] == 1.0
        assert down_quote["inputs"]["solar_watts"] is None
        assert down_quote["inputs"]["solar_watts_source"] == "unavailable"
    finally:
        reset_config()
        server.shutdown()
        server.server_close()
