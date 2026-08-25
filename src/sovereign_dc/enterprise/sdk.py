"""Sovereign Mini Datacenter — Enterprise Application SDK & Client Library.

Provides a lightweight, zero-dependency client SDK for enterprise workloads to
interface with SMDC power telemetry, event bus, DTN space routing, and PQC security.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from collections.abc import Callable
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("smdc.enterprise.sdk")


class SMDCClient:
    """Zero-dependency client library for communicating with local SMDC services."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float = 3.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("SMDC_API_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.timeout_sec = timeout_sec

    def _http_get(self, endpoint: str) -> dict[str, Any]:
        """Perform internal HTTP GET request against SMDC REST API."""
        url = f"{self.base_url}{endpoint}"
        try:
            req = Request(url, headers={"User-Agent": "SMDC-Enterprise-SDK/1.0", "Accept": "application/json"})
            with urlopen(req, timeout=self.timeout_sec) as response:
                if response.status == 200:
                    res_data = json.loads(response.read().decode("utf-8"))
                    if isinstance(res_data, dict):
                        return res_data
        except (URLError, TimeoutError, Exception) as e:
            logger.debug("Failed GET %s: %s (falling back to local default telemetry)", url, e)
        return {}

    def _http_post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform internal HTTP POST request against SMDC REST API."""
        url = f"{self.base_url}{endpoint}"
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                url,
                data=data,
                headers={
                    "User-Agent": "SMDC-Enterprise-SDK/1.0",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with urlopen(req, timeout=self.timeout_sec) as response:
                if response.status in [200, 201]:
                    res_data = json.loads(response.read().decode("utf-8"))
                    if isinstance(res_data, dict):
                        return res_data
        except (URLError, TimeoutError, Exception) as e:
            logger.debug("Failed POST %s: %s", url, e)
        return {}

    def get_telemetry(self) -> dict[str, Any]:
        """Fetch current hardware, solar, battery, and thermal telemetry snapshot."""
        data = self._http_get("/api/status")
        if data:
            return data

        # Fallback to direct HAL reading if running locally in-process
        try:
            from sovereign_dc.telemetry import get_telemetry_metrics

            metrics = get_telemetry_metrics()
            return {
                "solar_watts": metrics.get("victron_solar_power_watts", 750.0),
                "battery_soc": metrics.get("victron_battery_soc_percent", 85.0),
                "coolant_temp_c": metrics.get("cooling_liquid_temp_celsius", 28.5),
                "ambient_temp_c": metrics.get("cooling_ambient_temp_celsius", 24.0),
                "power_state": "NOMINAL",
            }
        except Exception:
            return {
                "solar_watts": 850.0,
                "battery_soc": 90.0,
                "coolant_temp_c": 28.0,
                "ambient_temp_c": 24.0,
                "power_state": "NOMINAL",
            }

    def emit_telemetry(self, app_id: str, metrics: dict[str, Any]) -> bool:
        """Publish custom application telemetry to SMDC event bus and metrics pipeline."""
        payload = {"app_id": app_id, "timestamp": time.time(), "metrics": metrics}
        res = self._http_post("/api/enterprise/telemetry", payload)
        if res:
            return True

        # In-process event bus fallback
        try:
            from sovereign_dc.events import Event, SovereignEventBus

            bus = SovereignEventBus()
            bus.publish(
                Event(
                    event_type=f"enterprise.{app_id}.telemetry",
                    source=f"enterprise.{app_id}",
                    payload=payload,
                )
            )
            return True
        except Exception:
            return False

    def send_dtn_bundle(self, destination: str, payload_data: str | bytes, ttl_sec: int = 86400 * 7) -> dict[str, Any]:
        """Spool an RFC 9171 Delay-Tolerant Networking (BPv7) space bundle."""
        if isinstance(payload_data, bytes):
            payload_str = payload_data.decode("utf-8", errors="replace")
            payload_bytes = payload_data
        else:
            payload_str = str(payload_data)
            payload_bytes = payload_str.encode("utf-8")

        req_payload = {
            "source": "dtn://smdc-enterprise.local/app",
            "destination": destination,
            "payload": payload_str,
            "ttl": ttl_sec,
        }
        res = self._http_post("/api/control/dtn-transmit", req_payload)
        if res:
            return res

        # In-process DTN router fallback
        try:
            from sovereign_dc.space.dtn.bundle import Bundle
            from sovereign_dc.space.dtn.router import DTNRouter

            router = DTNRouter()
            bundle = Bundle(
                source_eid="dtn://smdc-enterprise.local/app",
                destination_eid=destination,
                payload=payload_bytes,
                lifetime_seconds=ttl_sec,
            )
            ok = router.queue_bundle(bundle)
            return {"status": "spooled" if ok else "failed", "bundle_id": bundle.bundle_id}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_dynamic_pricing(self, soc: float | None = None, solar_w: float | None = None) -> dict[str, Any]:
        """Query real-time dynamic solar-aware compute rates."""
        try:
            from sovereign_dc.economy.market import ComputeMarket, ServiceType

            market = ComputeMarket()
            current_soc = soc if soc is not None else 85.0
            current_solar = solar_w if solar_w is not None else 850.0
            quote = market.calculate_quote(
                ServiceType.LLM_INFERENCE, quantity=1.0, battery_soc=current_soc, solar_power_w=current_solar
            )
            return quote.to_dict()
        except Exception as e:
            return {"base_compute_unit": 0.05, "error": str(e)}


class AppLifecycleHandler:
    """Manages application signals, shutdown triggers, and power load shedding events."""

    def __init__(
        self,
        app_id: str,
        client: SMDCClient | None = None,
        on_pause: Callable[[], None] | None = None,
        on_resume: Callable[[], None] | None = None,
    ) -> None:
        self.app_id = app_id
        self.client = client or SMDCClient()
        self.on_pause = on_pause
        self.on_resume = on_resume
        self._is_running = True
        self._is_paused = False

        # Register OS signal handlers for graceful shutdown
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            pass

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("Received signal %d. Triggering graceful stop for %s...", signum, self.app_id)
        self.stop()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def pause(self) -> None:
        """Pause workload execution in response to power shedding."""
        if not self._is_paused:
            self._is_paused = True
            logger.warning("Enterprise app '%s' paused by SMDC power scheduler.", self.app_id)
            if self.on_pause:
                try:
                    self.on_pause()
                except Exception as e:
                    logger.error("Error in on_pause callback: %s", e)

    def resume(self) -> None:
        """Resume workload execution when power conditions recover."""
        if self._is_paused:
            self._is_paused = False
            logger.info("Enterprise app '%s' resumed by SMDC power scheduler.", self.app_id)
            if self.on_resume:
                try:
                    self.on_resume()
                except Exception as e:
                    logger.error("Error in on_resume callback: %s", e)

    def stop(self) -> None:
        """Terminate workload run loop."""
        self._is_running = False
        logger.info("Enterprise app '%s' marked stopped.", self.app_id)
