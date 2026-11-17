"""Sovereign Mini Datacenter — Enterprise Application SDK & Client Library.

Provides a lightweight, standard-library-only client SDK for enterprise workloads to
read SMDC power telemetry, publish app telemetry, spool DTN bundles and query pricing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import signal
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

logger = logging.getLogger("smdc.enterprise.sdk")


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never follows an HTTP redirect.

    urllib's default handler re-sends the request headers, including ``Authorization``, to
    whatever ``Location`` names, on any host. The SDK's requests carry the node's operator
    token, so a 3xx answer is surfaced as an ``HTTPError`` (with its ``Location`` header)
    instead of being followed.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_OPENER: OpenerDirector = build_opener(_NoRedirectHandler())


def urlopen(req: Request, timeout: float | None = None) -> Any:
    """Opens ``req`` without following redirects (every SDK request goes through here)."""
    return _OPENER.open(req, timeout=timeout)


def _redirect_error(url: str, err: HTTPError) -> str:
    """Error text for a 3xx answer that was not followed, naming where it pointed."""
    location = err.headers.get("Location") if err.headers is not None else None
    return (
        f"HTTP {err.code} redirect from {url} to {location or '(no Location header)'} was not followed: "
        "the SDK never forwards a request (or the operator token) to another location"
    )


def _finite_or_none(value: Any) -> float | None:
    """``value`` as a finite float, or None for anything else (missing, null, text, NaN, bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class SMDCClient:
    """Zero-dependency client library for communicating with local SMDC services."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float = 3.0,
        token: str | None = None,
    ) -> None:
        """Creates a client for the SMDC node at ``base_url`` (default ``SMDC_API_URL`` or loopback).

        ``token`` is the node's dashboard operator token; it defaults to the
        ``SMDC_DASHBOARD_TOKEN`` environment variable and is sent as
        ``Authorization: Bearer <token>``, which the node requires for control POSTs from
        non-loopback clients. It is sent to ``base_url`` only and is never logged: HTTP
        redirects are not followed (a 3xx answer is reported as an error naming its
        ``Location``), so the token cannot be forwarded to another origin.
        """
        self.base_url = (base_url or os.getenv("SMDC_API_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.timeout_sec = timeout_sec
        self._token = (token if token is not None else os.getenv("SMDC_DASHBOARD_TOKEN", "")).strip()
        # Why the last GET/POST produced no data (e.g. an unfollowed redirect); None after a success.
        self.last_http_error: str | None = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"User-Agent": "SMDC-Enterprise-SDK/1.0", "Accept": "application/json"}
        if extra:
            headers.update(extra)
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _http_get(self, endpoint: str) -> dict[str, Any]:
        """Perform internal HTTP GET request against SMDC REST API.

        Returns the node's JSON object, or ``{}`` when nothing usable came back; the reason is
        then in ``last_http_error``. A redirect is not followed.
        """
        url = f"{self.base_url}{endpoint}"
        self.last_http_error = None
        try:
            req = Request(url, headers=self._headers())
            with urlopen(req, timeout=self.timeout_sec) as response:
                if response.status == 200:
                    res_data = json.loads(response.read().decode("utf-8"))
                    if isinstance(res_data, dict):
                        return res_data
                self.last_http_error = f"GET {url} returned no JSON object (HTTP {response.status})"
        except HTTPError as e:
            if 300 <= e.code < 400:
                self.last_http_error = _redirect_error(url, e)
                logger.warning("%s", self.last_http_error)
            else:
                self.last_http_error = f"GET {url} failed: HTTP {e.code}"
                logger.debug("%s", self.last_http_error)
        except (URLError, TimeoutError, Exception) as e:
            self.last_http_error = f"GET {url} failed: {e}"
            logger.debug("Failed GET %s: %s (falling back to local default telemetry)", url, e)
        return {}

    def _http_post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform internal HTTP POST request against SMDC REST API.

        Three outcomes are kept apart:

        * the node accepted the request: its JSON object is returned;
        * the node answered with an HTTP error (4xx/5xx, e.g. 400 invalid, 401/403 not
          authorised): ``{"node_rejected": True, "http_status": <code>, "error": <message>}``.
          The node made a decision, so callers must NOT fall back to an in-process path.
          A redirect (3xx) is reported the same way, with ``"redirect": True`` and
          ``"redirect_location"``: it is never followed, so the request body and the operator
          token are not re-sent anywhere;
        * the node could not be reached (connection refused, DNS, timeout, unusable reply):
          ``{}``, the only case in which callers may fall back.
        """
        url = f"{self.base_url}{endpoint}"
        self.last_http_error = None
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(url, data=data, headers=self._headers({"Content-Type": "application/json"}))
            with urlopen(req, timeout=self.timeout_sec) as response:
                if response.status in [200, 201]:
                    res_data = json.loads(response.read().decode("utf-8"))
                    if isinstance(res_data, dict):
                        return res_data
                self.last_http_error = f"POST {url} returned no JSON object (HTTP {response.status})"
        except HTTPError as e:
            if 300 <= e.code < 400:
                self.last_http_error = _redirect_error(url, e)
                logger.warning("%s", self.last_http_error)
                return {
                    "node_rejected": True,
                    "http_status": int(e.code),
                    "error": self.last_http_error,
                    "redirect": True,
                    "redirect_location": e.headers.get("Location") if e.headers is not None else None,
                }
            message = self._http_error_message(e)
            self.last_http_error = f"POST {url} was rejected: HTTP {e.code} {message}"
            logger.warning("POST %s was rejected by the node: HTTP %s %s", url, e.code, message)
            return {"node_rejected": True, "http_status": int(e.code), "error": message}
        except (URLError, TimeoutError, Exception) as e:
            self.last_http_error = f"POST {url} failed: {e}"
            logger.debug("Failed POST %s: %s", url, e)
        return {}

    @staticmethod
    def _http_error_message(err: HTTPError) -> str:
        """The node's own ``error`` text from a JSON error body, else the HTTP reason phrase."""
        try:
            body = json.loads(err.read().decode("utf-8", errors="replace"))
            if isinstance(body, dict) and isinstance(body.get("error"), str) and body["error"]:
                return str(body["error"])
        except Exception:
            logger.debug("HTTP %s error body was not JSON", err.code)
        return str(err.reason or f"HTTP {err.code}")

    @staticmethod
    def _flatten_status_payload(data: dict[str, Any]) -> dict[str, Any] | None:
        """Maps the dashboard ``/api/status`` document onto the SDK's flat telemetry schema.

        Returns None when the document carries no usable power section (no finite
        ``battery_soc``). ``solar_watts``, ``coolant_temp_c`` and ``ambient_temp_c`` are None
        when the node reports them as unknown (``null``) or not at all.
        """
        power = data.get("power")
        thermal = data.get("thermal")
        if not isinstance(power, dict):
            return None
        battery_soc = _finite_or_none(power.get("battery_soc"))
        if battery_soc is None:
            return None
        flat: dict[str, Any] = {
            "solar_watts": _finite_or_none(power.get("solar_watts")),
            "battery_soc": battery_soc,
        }
        thermal = thermal if isinstance(thermal, dict) else {}
        for flat_key, nested_key in (("coolant_temp_c", "coolant_celsius"), ("ambient_temp_c", "rack_inlet_celsius")):
            flat[flat_key] = _finite_or_none(thermal.get(nested_key))
        flat["power_state"] = "LOAD_SHEDDING" if power.get("load_shedding_active") else "NOMINAL"
        p_sim = power.get("is_simulated")
        t_sim = thermal.get("is_simulated")
        if p_sim is not None or t_sim is not None:
            flat["is_simulated"] = bool(p_sim or t_sim)
            if p_sim is not None:
                flat["power_is_simulated"] = bool(p_sim)
            if t_sim is not None:
                flat["thermal_is_simulated"] = bool(t_sim)
        flat["source"] = "api"
        return flat

    def get_telemetry(self) -> dict[str, Any]:
        """Fetch current solar, battery and thermal telemetry as one flat dictionary.

        The same keys are returned whichever path supplies the data:
        ``solar_watts``, ``battery_soc`` (percent), ``coolant_temp_c``, ``ambient_temp_c``
        (rack inlet air), ``power_state`` (``"NOMINAL"`` / ``"LOAD_SHEDDING"`` /
        ``"UNKNOWN"``) and ``source``. ``solar_watts``, ``coolant_temp_c`` and
        ``ambient_temp_c`` are ``None`` when the node has no measurement for them (a missing
        value is never replaced by a nominal one); ``battery_soc`` is always a number.
        ``source`` is:

        - ``"api"``: read from the node's ``/api/status`` endpoint (the endpoint's own nested
          sections such as ``power`` and ``thermal`` are kept alongside the flat keys);
        - ``"hal"``: read in-process from the hardware abstraction layer, using the node's
          configured exporter URL and HAL mode (``is_simulated`` tells whether those are
          simulated readings; simulated values are only ever returned in simulation mode);
        - ``"unavailable"``: nothing could be read; numeric fields are placeholders
          (0 W solar, 0 % battery) chosen so callers fail safe rather than assume full power.
        """
        data = self._http_get("/api/status")
        if data:
            flat = self._flatten_status_payload(data)
            if flat is not None:
                return {**data, **flat}

        # Fallback to direct HAL reading if running locally in-process
        try:
            from sovereign_dc.config import get_config
            from sovereign_dc.hal.power import read_power
            from sovereign_dc.hal.thermal import read_thermal

            # Honour the node's configuration: in hardware mode a dead exporter must surface
            # as "unavailable", never as the HAL's healthy-looking simulated defaults.
            cfg = get_config()
            exporter_url = cfg.power_exporter_url
            simulation = cfg.is_simulation()
            power = read_power(exporter_url, simulation=simulation)
            thermal = read_thermal(exporter_url, simulation=simulation)
            return {
                "solar_watts": power.solar_watts,
                "battery_soc": float(power.battery_soc),
                "coolant_temp_c": float(thermal.coolant_celsius),
                "ambient_temp_c": thermal.rack_inlet_celsius,
                "power_state": "LOAD_SHEDDING" if power.load_shedding_active else "NOMINAL",
                "source": "hal",
                "is_simulated": bool(power.is_simulated or thermal.is_simulated),
                "power_is_simulated": bool(power.is_simulated),
                "thermal_is_simulated": bool(thermal.is_simulated),
            }
        except Exception as e:
            logger.warning("SMDC telemetry unavailable (API unreachable, HAL read failed: %s)", e)
            return {
                "solar_watts": 0.0,
                "battery_soc": 0.0,
                "coolant_temp_c": None,
                "ambient_temp_c": None,
                "power_state": "UNKNOWN",
                "source": "unavailable",
            }

    def emit_telemetry(self, app_id: str, metrics: dict[str, Any]) -> bool:
        """Publish custom application telemetry to the SMDC node.

        Tries the node's REST API first and, only if the node is unreachable, publishes on
        this process's shared event bus. Returns True only if the API accepted the sample
        or at least one in-process subscriber (e.g. an ``EnterpriseManager``) received it;
        False means the sample reached nobody or the node rejected it (HTTP 4xx/5xx).
        """
        payload = {"app_id": app_id, "timestamp": time.time(), "metrics": metrics}
        res = self._http_post("/api/enterprise/telemetry", payload)
        if res.get("node_rejected"):
            # The node refused the sample: delivering it in-process would hide that refusal.
            return False
        if res:
            return True

        # In-process event bus fallback: the process-wide bus, where subscribers actually are.
        try:
            from sovereign_dc.events import Event, get_event_bus

            delivered = get_event_bus().publish(
                Event(
                    event_type=f"enterprise.{app_id}.telemetry",
                    source=f"enterprise.{app_id}",
                    payload=payload,
                )
            )
            return delivered > 0
        except Exception as e:
            logger.debug("In-process telemetry publish failed: %s", e)
            return False

    def send_dtn_bundle(
        self,
        destination: str,
        payload_data: str | bytes,
        ttl_sec: int | None = None,
        *,
        priority: int | None = None,
        source_eid: str | None = None,
    ) -> dict[str, Any]:
        """Spool an RFC 9171 Delay-Tolerant Networking (BPv7) bundle on the node.

        Uses the node's DTN submission contract (``sovereign_dc.web.dashboard``) on both
        paths, the REST API and the in-process fallback, so the stored bundle is the same
        either way: the payload bytes are carried losslessly (base64 over HTTP), ``ttl_sec``
        is the bundle lifetime (positive, capped by the node; omitted, the contract's default
        of 7 days applies, the same as for the REST API and the MCP tool), ``priority`` is 0 bulk /
        1 normal (default) / 2 expedited / 3 critical, and the source is ``source_eid`` if it
        is a valid endpoint ID, otherwise the node's own ``dtn://<node-id>/api``.

        Returns ``status`` (``"spooled"``, ``"failed"`` or ``"error"``) and, when spooled,
        what was actually stored: ``bundle_id``, ``source``, ``destination``, ``priority``,
        ``ttl_seconds``, ``payload_bytes`` and ``payload_sha256``.
        """
        payload_bytes = payload_data if isinstance(payload_data, bytes) else str(payload_data).encode("utf-8")
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()

        req_payload: dict[str, Any] = {
            "destination": destination,
            "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
        }
        if ttl_sec is not None:
            req_payload["ttl"] = ttl_sec
        try:
            # Text is also sent in the legacy field, which is all an older node understands.
            req_payload["payload"] = payload_bytes.decode("utf-8")
        except UnicodeDecodeError:
            pass
        if priority is not None:
            req_payload["priority"] = priority
        if source_eid is not None:
            req_payload["source"] = source_eid

        res = self._http_post("/api/control/dtn-transmit", req_payload)
        if res.get("node_rejected"):
            # A remote node's refusal must not turn into a bundle in THIS process's spool.
            return {
                "status": "rejected",
                "success": False,
                "http_status": res.get("http_status"),
                "error": res.get("error", "rejected by the node"),
            }
        if res:
            stored_sha256 = res.get("payload_sha256")
            if "payload" not in req_payload and stored_sha256 != payload_sha256:
                # A node that predates payload_b64 ignores it: do not report its bundle as ours.
                return {
                    **res,
                    "status": "error",
                    "error": "The node did not confirm the binary payload (it does not support payload_b64).",
                }
            return {**res, "status": "spooled" if res.get("success", True) else "failed"}

        # In-process DTN router fallback
        try:
            from sovereign_dc.config import get_config
            from sovereign_dc.space.dtn.router import DTNRouter
            from sovereign_dc.web.dashboard import describe_dtn_bundle, parse_dtn_submission

            cfg = get_config()
            try:
                submission = parse_dtn_submission(req_payload, cfg.node_id)
            except ValueError as e:
                return {"status": "failed", "success": False, "error": str(e)}
            # Use the node's configured spool, not DTNRouter's built-in default path.
            router = DTNRouter(db_path=cfg.dtn_db_path)
            bundle = submission.to_bundle()
            ok = router.queue_bundle(bundle)
            return {
                "status": "spooled" if ok else "failed",
                "success": bool(ok),
                **describe_dtn_bundle(bundle, ttl_capped=submission.ttl_capped),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_dynamic_pricing(self, soc: float | None = None, solar_w: float | None = None) -> dict[str, Any]:
        """Quote the energy-aware LLM-inference rate for the node's present power state.

        ``soc`` (battery percent) and ``solar_w`` (PV watts) override the node's readings;
        whichever is omitted is read from the node with ``get_telemetry()``. No healthy
        default is ever substituted:

        - If the battery state of charge is neither given nor readable, there is no quote:
          the result is ``{"status": "unavailable", "pricing_available": False, "error": ...}``
          with no multiplier and no price (the caller decides whether to wait or refuse).
        - If only the solar power is unknown it is priced as unknown (``None``), which can
          never earn the surplus discount.

        Every result carries ``inputs``: the ``battery_soc`` and ``solar_watts`` that were
        priced, the source of each (``"caller"``, ``"telemetry:api"``, ``"telemetry:hal"`` or
        ``"unavailable"``) and ``is_simulated`` (True / False as reported by the node, None
        when unknown or when both inputs came from the caller). A successful result is the
        quote (``final_unit_price``, ``energy_multiplier``, ``energy_state``, ...) plus
        ``status: "quoted"``, ``pricing_available: True`` and ``inputs``.
        """
        inputs: dict[str, Any] = {
            "battery_soc": soc,
            "solar_watts": solar_w,
            "battery_soc_source": "caller" if soc is not None else "unavailable",
            "solar_watts_source": "caller" if solar_w is not None else "unavailable",
            "is_simulated": None,
        }
        try:
            if soc is None or solar_w is None:
                telemetry = self.get_telemetry()
                source = str(telemetry.get("source") or "unavailable")
                if source == "unavailable" and getattr(self.get_telemetry, "__func__", None) is _ORIG_GET_TELEMETRY:
                    telemetry = self._read_pricing_telemetry(need_soc=(soc is None), need_solar=(solar_w is None))
                    source = str(telemetry.get("source") or "unavailable")
                if source != "unavailable":
                    nested_power = telemetry.get("power")
                    simulated = telemetry.get("power_is_simulated")
                    if simulated is None and isinstance(nested_power, dict):
                        simulated = nested_power.get("is_simulated")
                    if simulated is None:
                        simulated = telemetry.get("is_simulated")
                    inputs["is_simulated"] = bool(simulated) if simulated is not None else None
                    if soc is None:
                        inputs["battery_soc"] = _finite_or_none(telemetry.get("battery_soc"))
                        if inputs["battery_soc"] is not None:
                            inputs["battery_soc_source"] = f"telemetry:{source}"
                    if solar_w is None:
                        inputs["solar_watts"] = _finite_or_none(telemetry.get("solar_watts"))
                        if inputs["solar_watts"] is not None:
                            inputs["solar_watts_source"] = f"telemetry:{source}"

            if inputs["battery_soc"] is None:
                return {
                    "status": "unavailable",
                    "pricing_available": False,
                    "error": "Battery state of charge is unavailable (node telemetry could not be read); "
                    "no price multiplier was derived.",
                    "inputs": inputs,
                }

            from sovereign_dc.economy.market import ComputeMarket, ServiceType

            quote = ComputeMarket().calculate_quote(
                ServiceType.LLM_INFERENCE,
                quantity=1.0,
                battery_soc=float(inputs["battery_soc"]),
                solar_power_w=inputs["solar_watts"],
            )
            return {**quote.to_dict(), "status": "quoted", "pricing_available": True, "inputs": inputs}
        except Exception as e:
            return {"status": "error", "pricing_available": False, "error": str(e), "inputs": inputs}

    def _read_pricing_telemetry(self, *, need_soc: bool, need_solar: bool) -> dict[str, Any]:
        """Reads only missing pricing inputs so partial bridges (e.g. MPPT-only solar) stay usable."""
        data = self._http_get("/api/status")
        if isinstance(data, dict) and isinstance(data.get("power"), dict):
            power_dict = data["power"]
            api_soc = _finite_or_none(power_dict.get("battery_soc"))
            api_solar = _finite_or_none(power_dict.get("solar_watts"))
            if not need_soc or api_soc is not None:
                return {
                    **data,
                    "battery_soc": api_soc,
                    "solar_watts": api_solar,
                    "is_simulated": power_dict.get("is_simulated"),
                    "source": "api",
                }

        try:
            from sovereign_dc.config import get_config
            from sovereign_dc.hal.power import (
                METRIC_SIMULATED,
                METRIC_SOLAR_WATTS,
                fetch_exporter_metrics,
                optional_metric,
                read_power,
            )

            cfg = get_config()
            exporter_url = cfg.power_exporter_url
            simulation = cfg.is_simulation()
            if need_soc:
                power = read_power(exporter_url, simulation=simulation)
                return {
                    "battery_soc": float(power.battery_soc),
                    "solar_watts": power.solar_watts if need_solar else None,
                    "is_simulated": bool(power.is_simulated),
                    "source": "hal",
                }
            if need_solar:
                try:
                    metrics = fetch_exporter_metrics(exporter_url)
                    return {
                        "battery_soc": None,
                        "solar_watts": optional_metric(metrics, METRIC_SOLAR_WATTS),
                        "is_simulated": bool(metrics.get(METRIC_SIMULATED, 0.0) > 0),
                        "source": "hal",
                    }
                except Exception:
                    if simulation:
                        power = read_power(exporter_url, simulation=True)
                        return {
                            "battery_soc": None,
                            "solar_watts": power.solar_watts,
                            "is_simulated": bool(power.is_simulated),
                            "source": "hal",
                        }
        except Exception as e:
            logger.debug("Partial pricing telemetry unavailable: %s", e)
        return {"source": "unavailable"}


_ORIG_GET_TELEMETRY = SMDCClient.get_telemetry


class AppLifecycleHandler:
    """Manages application signals, shutdown triggers, and pause/resume notifications.

    ``is_paused`` follows the ``enterprise.<app_id>.paused`` / ``.resumed`` events that an
    ``EnterpriseManager`` publishes when it pauses this app (operator request or load
    shedding) or resumes it. Those events travel on the in-process event bus, so they are
    only received when the app runs in the same Python process as the manager; a workload
    started as a separate OS process must call ``pause()`` / ``resume()`` itself (for
    example from its own polling of ``SMDCClient.get_telemetry()``).
    """

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
        self._lock = threading.RLock()
        self._bus: Any = None
        self._last_state_seq: int = -1
        self._subscribe_power_events()

        # Register OS signal handlers for graceful shutdown
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            pass

    def _subscribe_power_events(self) -> None:
        """Follows the manager's pause/resume decisions for this app on the shared event bus."""
        try:
            from sovereign_dc.events import get_event_bus

            bus = get_event_bus()
            bus.subscribe(f"enterprise.{self.app_id}.paused", self._on_paused_event)
            bus.subscribe(f"enterprise.{self.app_id}.resumed", self._on_resumed_event)
            bus.subscribe(f"enterprise.{self.app_id}.started", self._on_started_event)
            bus.subscribe(f"enterprise.{self.app_id}.stopped", self._on_stopped_event)
            self._bus = bus
        except Exception as e:
            logger.debug("Lifecycle handler for %s could not subscribe to the event bus: %s", self.app_id, e)

    def _unsubscribe_power_events(self) -> None:
        bus, self._bus = self._bus, None
        if bus is None:
            return
        try:
            bus.unsubscribe(f"enterprise.{self.app_id}.paused", self._on_paused_event)
            bus.unsubscribe(f"enterprise.{self.app_id}.resumed", self._on_resumed_event)
            bus.unsubscribe(f"enterprise.{self.app_id}.started", self._on_started_event)
            bus.unsubscribe(f"enterprise.{self.app_id}.stopped", self._on_stopped_event)
        except Exception as e:
            logger.debug("Lifecycle handler for %s could not unsubscribe: %s", self.app_id, e)

    def _accept_state_event(self, event: Any) -> bool:
        payload = getattr(event, "payload", None)
        seq = payload.get("state_seq") if isinstance(payload, dict) else None
        if isinstance(seq, int) and not isinstance(seq, bool):
            if seq < self._last_state_seq:
                return False
            self._last_state_seq = seq
        return True

    def _on_paused_event(self, event: Any) -> None:
        with self._lock:
            if self._accept_state_event(event):
                self.pause()

    def _on_resumed_event(self, event: Any) -> None:
        with self._lock:
            if self._accept_state_event(event):
                self.resume()

    def _on_started_event(self, event: Any) -> None:
        with self._lock:
            if self._accept_state_event(event):
                self._is_paused = False

    def _on_stopped_event(self, event: Any) -> None:
        with self._lock:
            if self._accept_state_event(event):
                self._is_paused = False

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
        """Pause workload execution (called on a manager pause event, or directly by the app)."""
        with self._lock:
            if not self._is_paused:
                self._is_paused = True
                logger.warning("Enterprise app '%s' paused.", self.app_id)
                if self.on_pause:
                    try:
                        self.on_pause()
                    except Exception as e:
                        logger.error("Error in on_pause callback: %s", e)

    def resume(self) -> None:
        """Resume workload execution (called on a manager resume event, or directly by the app)."""
        with self._lock:
            if self._is_paused:
                self._is_paused = False
                logger.info("Enterprise app '%s' resumed.", self.app_id)
                if self.on_resume:
                    try:
                        self.on_resume()
                    except Exception as e:
                        logger.error("Error in on_resume callback: %s", e)

    def stop(self) -> None:
        """Terminate workload run loop."""
        self._is_running = False
        self._unsubscribe_power_events()
        logger.info("Enterprise app '%s' marked stopped.", self.app_id)
