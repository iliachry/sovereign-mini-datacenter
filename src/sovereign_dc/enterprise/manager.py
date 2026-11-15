"""Sovereign Mini Datacenter — Enterprise Application Lifecycle & Power Manager.

Tracks the declared lifecycle state (registered / running / paused / stopped) of
registered enterprise workloads, adapts that state to load-shedding events, and builds
signed `.smdc-app` packages for air-gapped distribution.

Load-shedding policy (the only one this manager implements; see ``schema.SHED_PAUSE_LEVEL``):

====================  ==========================================================
Shedding level        Tracked apps marked PAUSED (by power tier)
====================  ==========================================================
0                     none
1                     ``L4_IDLE``
2                     ``L3_DEFERRABLE``, ``L4_IDLE``
3                     ``L2_BACKGROUND``, ``L3_DEFERRABLE``, ``L4_IDLE``
4                     ``L1_STANDARD``, ``L2_BACKGROUND``, ``L3_DEFERRABLE``, ``L4_IDLE``
====================  ==========================================================

``L0_CRITICAL`` is never paused by shedding. The level arrives in a ``load_shedding.*``
event (the sentinel derives it from battery SoC with ``SovereignConfig.shedding_l1..l4_soc``,
50 / 30 / 20 / 10 % by default). An app is resumed when the level falls below the level
that pauses its tier. In addition, when the event carries ``battery_soc``, an app (other
than ``L0_CRITICAL``) is paused while that SoC is below its manifest's
``power.min_battery_soc`` and resumed once the SoC is back at or above it plus
``SOC_FLOOR_HYSTERESIS_PERCENT``. Pausing is the only action: nothing is throttled,
scheduled or terminated, and only apps this manager paused for shedding are resumed by it.

The policy is not applied on events only. The manager remembers the level and SoC of the
last ``load_shedding.*`` event it understood, and ``start_app`` applies them at once: an app
that the current level (or its SoC floor) would pause is started **paused by shedding**
(status ``paused``, ``paused_by: load_shedding``) instead of running until the next event,
and is resumed like every other shed app when shedding eases. ``start_app`` still returns
success, with a message that says so. A manager that has seen no event yet (a new process,
e.g. one CLI invocation) knows no level and starts the app running.

Scope note: this manager is a state tracker, not a process supervisor. ``start_app`` /
``stop_app`` / ``pause_app`` / ``resume_app`` record the intended state and publish events;
they do not launch, signal or probe an operating-system process, ``pid`` is never set, and
the CPU / RAM / power figures are estimates derived from the manifest's resource quotas
(``metrics_source == "estimated"``), not measurements. State lives in memory only, so each
``EnterpriseManager`` instance (e.g. each CLI invocation) starts from "registered".
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import (
    SHED_PAUSE_LEVEL,
    AppManifest,
    AppStatus,
    is_valid_app_id,
    is_valid_app_version,
)
from sovereign_dc.events import Event, SovereignEventBus, get_event_bus

logger = logging.getLogger("smdc.enterprise.manager")

PACKAGE_SIGNATURE_ALGORITHM = "NIST-FIPS-204-ML-DSA-87"
# Scheme 2 signs the canonical JSON of (app_id, version, sha256, package_size_bytes);
# sidecars without a scheme marker were signed over the SHA-256 hex digest only.
PACKAGE_SIGNATURE_SCHEME = "smdc-package-v2"

METRICS_SOURCE_NONE = "none"
METRICS_SOURCE_ESTIMATED = "estimated"

MAX_SHEDDING_LEVEL = 4
# An app paused because the reported battery SoC fell below its manifest's
# ``power.min_battery_soc`` is resumed only at or above that floor plus this margin.
SOC_FLOOR_HYSTERESIS_PERCENT = 2.0
# Event payload key that carries the battery state of charge (percent), when known.
SOC_PAYLOAD_KEY = "battery_soc"
CAUSE_TIER = "tier"
CAUSE_SOC_FLOOR = "soc_floor"
# Level implied by a mode name when an event carries no numeric level.
_MODE_LEVELS: dict[str, int] = {
    "NORMAL": 0,
    "NOMINAL": 0,
    "SOLAR_SURPLUS": 0,
    "SOLAR_SURPLUS_COMPUTE": 0,
    "ECO_PRESERVATION": 2,
    "SHEDDING": 2,
    "LOW_BATTERY": 2,
    "CRITICAL_SHEDDING": 3,
    "EMERGENCY": 3,
    "CRITICAL": 3,
}


@dataclass
class AppRuntimeState:
    """Tracked lifecycle state of an application.

    ``cpu_usage_percent``, ``ram_usage_mb`` and ``power_draw_w`` are estimates computed from
    the manifest quotas when ``metrics_source`` is ``"estimated"``; nothing is measured.
    """

    manifest: AppManifest
    status: AppStatus = AppStatus.REGISTERED
    pid: int | None = None
    started_at: float | None = None
    last_health_check: float | None = None
    health_status: str = "unknown"
    restart_count: int = 0
    cpu_usage_percent: float = 0.0
    ram_usage_mb: float = 0.0
    power_draw_w: float = 0.0
    error_message: str | None = None
    custom_metrics: dict[str, Any] = field(default_factory=dict)
    metrics_source: str = METRICS_SOURCE_NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.manifest.app_id,
            "name": self.manifest.name,
            "category": self.manifest.category.value,
            "power_tier": self.manifest.power.tier.value,
            "status": self.status.value,
            "pid": self.pid,
            "uptime_seconds": round(time.time() - self.started_at, 1) if self.started_at else 0.0,
            "restart_count": self.restart_count,
            "health_status": self.health_status,
            "cpu_usage_percent": self.cpu_usage_percent,
            "ram_usage_mb": self.ram_usage_mb,
            "power_draw_w": self.power_draw_w,
            "metrics_source": self.metrics_source,
            "process_supervised": False,
            "error_message": self.error_message,
            "custom_metrics": dict(self.custom_metrics),
        }


def package_sidecar_path(package_path: Path | str) -> Path:
    """Returns the path of the signature/metadata sidecar that belongs to a package file."""
    return Path(package_path).with_suffix(".smdc-app.sig")


def _package_signing_input(metadata: dict[str, Any], scheme: str | None) -> bytes:
    """Builds the byte string covered by a package signature for the given scheme."""
    if scheme == PACKAGE_SIGNATURE_SCHEME:
        return json.dumps(
            {
                "scheme": PACKAGE_SIGNATURE_SCHEME,
                "app_id": metadata.get("app_id"),
                "version": metadata.get("version"),
                "sha256": metadata.get("sha256"),
                "package_size_bytes": metadata.get("package_size_bytes"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return str(metadata.get("sha256", "")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_package(
    path: Path | str,
    trusted_public_key: bytes | str,
    sidecar_path: Path | str | None = None,
) -> tuple[bool, str]:
    """Verifies a ``.smdc-app`` package against its sidecar and a pinned ML-DSA-87 public key.

    Checks that the archive's SHA-256 and size match the sidecar, that the sidecar was signed
    by exactly ``trusted_public_key`` (raw bytes or hex) and that the ML-DSA-87 signature is
    valid. The key embedded in the sidecar is never trusted on its own.

    Returns:
        ``(True, "ok")`` on success, otherwise ``(False, <reason>)``.
    """
    package = Path(path)
    sidecar = Path(sidecar_path) if sidecar_path is not None else package_sidecar_path(package)
    try:
        trusted = bytes.fromhex(trusted_public_key) if isinstance(trusted_public_key, str) else trusted_public_key
    except ValueError:
        return False, "trusted public key is not valid hex"
    if not isinstance(trusted, (bytes, bytearray)) or not trusted:
        return False, "trusted public key is required"
    if not package.is_file():
        return False, f"package not found: {package}"
    if not sidecar.is_file():
        return False, f"signature sidecar not found: {sidecar}"

    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"unreadable signature sidecar: {e}"
    if not isinstance(metadata, dict):
        return False, "signature sidecar is not a JSON object"

    try:
        actual_digest = _sha256_file(package)
        actual_size = package.stat().st_size
    except OSError as e:
        return False, f"unreadable package: {e}"
    if not hmac.compare_digest(actual_digest, str(metadata.get("sha256", ""))):
        return False, "package SHA-256 does not match the sidecar"

    sig_block = metadata.get("pqc_signature")
    if not isinstance(sig_block, dict):
        return False, "package is not signed"
    if sig_block.get("algorithm") != PACKAGE_SIGNATURE_ALGORITHM:
        return False, f"unsupported signature algorithm: {sig_block.get('algorithm')!r}"
    scheme = sig_block.get("scheme")
    if scheme not in (None, PACKAGE_SIGNATURE_SCHEME):
        return False, f"unsupported signature scheme: {scheme!r}"
    if scheme == PACKAGE_SIGNATURE_SCHEME and metadata.get("package_size_bytes") != actual_size:
        return False, "package size does not match the sidecar"

    try:
        signer_key = bytes.fromhex(str(sig_block.get("public_key_hex", "")))
        signature = bytes.fromhex(str(sig_block.get("signature_hex", "")))
    except ValueError:
        return False, "malformed signature block"
    if not hmac.compare_digest(signer_key, bytes(trusted)):
        return False, "package was not signed by the trusted public key"

    from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner

    if not PQCSigner(PQCAlgorithm.ML_DSA_87).verify(_package_signing_input(metadata, scheme), signature, signer_key):
        return False, "invalid ML-DSA-87 signature"
    return True, "ok"


class EnterpriseManager:
    """Tracks enterprise application lifecycle state and applies tiered load shedding to it."""

    def __init__(
        self,
        registry: EnterpriseRegistry | None = None,
        event_bus: SovereignEventBus | None = None,
    ) -> None:
        self.registry = registry or EnterpriseRegistry()
        self.event_bus = event_bus or get_event_bus()
        self._lock = threading.RLock()
        self._runtimes: dict[str, AppRuntimeState] = {}
        # Apps this manager paused because of load shedding (as opposed to an operator
        # pause), with the causes still in force: CAUSE_TIER and/or CAUSE_SOC_FLOOR.
        self._shed_paused: dict[str, set[str]] = {}
        # Incremented for every handled load-shedding event (see _shed_plan_is_stale).
        self._shed_generation = 0
        # Level and battery SoC of the last load-shedding event this manager understood
        # (None until the first one / while the SoC is unknown); applied by start_app().
        self._shedding_level: int | None = None
        self._shedding_soc: float | None = None

        # Initialize runtime states from registry
        for app in self.registry.list_apps():
            self._runtimes[app.app_id] = AppRuntimeState(manifest=app)

        # Hook into SMDC Event Bus for dynamic load shedding
        self._subscribe_event_bus()

    def _subscribe_event_bus(self) -> None:
        """Subscribe to hardware and power load shedding events."""
        try:
            self.event_bus.subscribe("load_shedding.*", self._on_load_shedding_event)
            self.event_bus.subscribe("enterprise.*", self._on_enterprise_telemetry)
        except Exception as e:
            logger.warning("Could not subscribe to event bus: %s", e)

    @staticmethod
    def _parse_level(raw: Any) -> int | None:
        """Parses one level value (2, 2.0, "2", "2.0", "L2", "critical") or returns None."""
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            if not math.isfinite(raw) or raw < 0:
                return None
            return int(raw)
        if isinstance(raw, str):
            cleaned = raw.strip().upper()
            if cleaned in _MODE_LEVELS:
                return _MODE_LEVELS[cleaned]
            numeric = cleaned[1:] if cleaned.startswith("L") else cleaned
            try:
                value = float(numeric)
            except ValueError:
                return None
            if not math.isfinite(value) or value < 0:
                return None
            return int(value)
        return None

    @classmethod
    def _extract_shedding_level(cls, payload: dict[str, Any]) -> int | None:
        """Normalize the load shedding level from a numeric, L-tier or Sentinel mode payload.

        Returns None when the payload carries no level this manager understands; callers
        must treat that as "no change" rather than as level 0.
        """
        level = cls._parse_level(payload.get("level"))
        if level is not None:
            return level
        return cls._parse_level(payload.get("mode"))

    @staticmethod
    def _extract_soc(payload: dict[str, Any]) -> float | None:
        """Battery SoC (percent) carried by the event, or None when absent or not a finite number."""
        raw = payload.get(SOC_PAYLOAD_KEY)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            return None
        return float(raw)

    @staticmethod
    def _shed_causes(manifest: AppManifest, level: int, soc: float | None, previous: set[str]) -> set[str]:
        """Reasons for which ``manifest``'s app must be paused at this level / SoC.

        ``previous`` is the set recorded for the app so far: an SoC-floor pause is kept while
        the SoC is unknown or inside the hysteresis band (a recovery needs a reading).
        """
        tier = manifest.power.tier
        pause_level = SHED_PAUSE_LEVEL.get(tier)
        if pause_level is None:  # L0_CRITICAL
            return set()
        causes: set[str] = set()
        if level >= pause_level:
            causes.add(CAUSE_TIER)
        floor = manifest.power.min_battery_soc
        if soc is None:
            if CAUSE_SOC_FLOOR in previous:
                causes.add(CAUSE_SOC_FLOOR)
        elif soc < floor or (CAUSE_SOC_FLOOR in previous and soc < floor + SOC_FLOOR_HYSTERESIS_PERCENT):
            causes.add(CAUSE_SOC_FLOOR)
        return causes

    def _sync_with_registry(self) -> None:
        """Brings the tracked runtimes in line with the registry (which re-reads its file).

        Another process may have registered, re-registered or unregistered apps since this
        manager started: every tracked runtime takes the current manifest (so the shedding
        policy uses the current power tier), newly registered apps get a runtime, and the
        runtime of an app that is no longer registered is dropped together with its
        shedding bookkeeping.
        """
        current = {app.app_id: app for app in self.registry.list_apps()}
        with self._lock:
            for app_id in [known for known in self._runtimes if known not in current]:
                del self._runtimes[app_id]
                self._shed_paused.pop(app_id, None)
            for app_id, manifest in current.items():
                runtime = self._runtimes.get(app_id)
                if runtime is None:
                    self._runtimes[app_id] = AppRuntimeState(manifest=manifest)
                else:
                    runtime.manifest = manifest

    def _on_load_shedding_event(self, event: Event) -> None:
        """Apply the tiered shedding policy (module docstring) to the tracked app states."""
        self._sync_with_registry()
        payload = event.payload if isinstance(event.payload, dict) else {}
        level = self._extract_shedding_level(payload)
        if level is None:
            logger.warning(
                "Ignoring load shedding event with unrecognised level/mode: level=%r mode=%r",
                payload.get("level"),
                payload.get("mode"),
            )
            return
        level = min(level, MAX_SHEDDING_LEVEL)
        soc = self._extract_soc(payload)
        logger.info(
            "EnterpriseManager received load shedding signal level %s (battery SoC %s)",
            level,
            "unknown" if soc is None else f"{soc:.1f}%",
        )

        to_pause: list[tuple[str, set[str]]] = []
        to_resume: list[str] = []
        with self._lock:
            self._shedding_level = level
            self._shedding_soc = soc
            self._shed_generation += 1
            generation = self._shed_generation
            for app_id, runtime in self._runtimes.items():
                previous = self._shed_paused.get(app_id, set())
                causes = self._shed_causes(runtime.manifest, level, soc, previous)
                if runtime.status == AppStatus.RUNNING:
                    if causes:
                        to_pause.append((app_id, causes))
                elif runtime.status == AppStatus.PAUSED and app_id in self._shed_paused:
                    # Paused by shedding: keep the causes current, resume when none is left.
                    # An operator's pause is not in _shed_paused and stays in force.
                    if causes:
                        self._shed_paused[app_id] = causes
                    else:
                        to_resume.append(app_id)
            # Forget apps that are no longer paused (stopped, removed) or no longer tracked.
            for app_id in list(self._shed_paused):
                tracked = self._runtimes.get(app_id)
                if tracked is None or tracked.status != AppStatus.PAUSED:
                    del self._shed_paused[app_id]

        # The actions run outside the lock (they publish events). A newer shedding event may
        # be handled meanwhile, by a subscriber publishing from inside one of them or by
        # another thread; it recomputes every app, so what is left of this older plan must
        # not be applied on top of it. Each action therefore re-checks the plan generation
        # under the same lock in which it changes the app state (see _pause / resume_app).
        soc_text = "" if soc is None else f", battery SoC {soc:.1f}%"
        for app_id, causes in to_pause:
            self._pause(
                app_id,
                reason=f"Load shedding level {level}{soc_text} ({' + '.join(sorted(causes))})",
                by_shedding=True,
                causes=causes,
                plan_generation=generation,
            )
        for app_id in sorted(to_resume):
            self.resume_app(
                app_id, reason=f"Load shedding eased to level {level}{soc_text}", plan_generation=generation
            )

    def _shed_plan_is_stale(self, generation: int | None) -> bool:
        """True when a newer load-shedding event was handled since ``generation``.

        Called with ``self._lock`` held (it is re-entrant), so the answer stays true while
        the caller changes the app state. ``None`` (an operator action) is never stale.
        """
        if generation is None:
            return False
        with self._lock:
            stale = self._shed_generation != generation
        if stale:
            logger.info("Skipping an action of an older load-shedding plan: a newer event was handled.")
        return stale

    def _on_enterprise_telemetry(self, event: Event) -> None:
        """Ingest custom telemetry published by enterprise app instances."""
        payload = event.payload if isinstance(event.payload, dict) else {}
        app_id = payload.get("app_id")
        metrics = payload.get("metrics", {})
        if not isinstance(app_id, str) or not isinstance(metrics, dict):
            return
        with self._lock:
            runtime = self._runtimes.get(app_id)
            if runtime is not None:
                runtime.custom_metrics.update(metrics)

    @staticmethod
    def _apply_estimates(runtime: AppRuntimeState, *, paused: bool) -> None:
        """Fills the resource figures with manifest-derived estimates (nothing is measured)."""
        resources = runtime.manifest.resources
        if paused:
            runtime.power_draw_w = resources.max_power_w * 0.1  # Assumed standby draw
            runtime.cpu_usage_percent = 1.0
        else:
            runtime.power_draw_w = resources.max_power_w * 0.65
            runtime.ram_usage_mb = float(resources.ram_mb * 0.45)
            runtime.cpu_usage_percent = float(resources.cpu_cores * 15.0)
        runtime.metrics_source = METRICS_SOURCE_ESTIMATED

    def start_app(self, app_id: str) -> tuple[bool, str]:
        """Mark an application as running and publish ``enterprise.<app_id>.started``.

        No process is launched: the caller (or an external supervisor) is responsible for
        actually executing the manifest's entrypoint.

        The shedding policy is applied immediately, with the level and SoC of the last
        load-shedding event: an app the policy pauses right now is started **paused by
        shedding** (``enterprise.<app_id>.started`` followed by ``enterprise.<app_id>.paused``
        with ``paused_by: load_shedding``) and resumed when shedding eases. The call still
        succeeds; the message names the level. An operator ``resume_app`` overrides it until
        the next event.
        """
        self._sync_with_registry()
        manifest = self.registry.get_app(app_id)
        if not manifest:
            return False, f"Application '{app_id}' is not registered."

        with self._lock:
            runtime = self._runtimes.get(app_id)
            if runtime is None:
                runtime = AppRuntimeState(manifest=manifest)
                self._runtimes[app_id] = runtime
            runtime.manifest = manifest

            if runtime.status == AppStatus.RUNNING:
                return True, f"Application '{app_id}' is already running."

            level, soc = self._shedding_level, self._shedding_soc
            causes = set() if level is None else self._shed_causes(runtime.manifest, level, soc, set())
            runtime.started_at = time.time()
            runtime.health_status = "not_probed"
            runtime.error_message = None
            if causes:
                runtime.status = AppStatus.PAUSED
                self._apply_estimates(runtime, paused=False)  # RAM estimate of a loaded app
                self._apply_estimates(runtime, paused=True)
                self._shed_paused[app_id] = causes
            else:
                runtime.status = AppStatus.RUNNING
                self._apply_estimates(runtime, paused=False)
                self._shed_paused.pop(app_id, None)
            payload = runtime.to_dict()

        self.event_bus.publish(
            Event(event_type=f"enterprise.{app_id}.started", source="enterprise_manager", payload=payload)
        )
        if causes:
            soc_text = "" if soc is None else f", battery SoC {soc:.1f}%"
            reason = f"Load shedding level {level}{soc_text} ({' + '.join(sorted(causes))})"
            logger.warning("Started enterprise app '%s' paused: %s", app_id, reason)
            paused_payload: dict[str, Any] = {"reason": reason, "paused_by": "load_shedding"}
            paused_payload.update(payload)
            self.event_bus.publish(
                Event(event_type=f"enterprise.{app_id}.paused", source="enterprise_manager", payload=paused_payload)
            )
            return True, (
                f"Application '{app_id}' started but held paused by load shedding: {reason}. "
                "It resumes automatically when shedding eases (state tracked; no process is supervised by this manager)."
            )
        logger.info("Marked enterprise app '%s' running (Power Tier: %s)", app_id, manifest.power.tier.value)
        return True, f"Application '{app_id}' started (state tracked; no process is supervised by this manager)."

    def stop_app(self, app_id: str) -> tuple[bool, str]:
        """Mark an application as stopped and publish ``enterprise.<app_id>.stopped``."""
        with self._lock:
            runtime = self._runtimes.get(app_id)
            if not runtime:
                return False, f"Application '{app_id}' not found."

            if runtime.status == AppStatus.STOPPED:
                return True, f"Application '{app_id}' is already stopped."

            runtime.status = AppStatus.STOPPED
            runtime.pid = None
            runtime.power_draw_w = 0.0
            runtime.cpu_usage_percent = 0.0
            runtime.ram_usage_mb = 0.0
            runtime.metrics_source = METRICS_SOURCE_NONE
            runtime.health_status = "stopped"
            self._shed_paused.pop(app_id, None)
            payload = runtime.to_dict()

        logger.info("Marked enterprise app '%s' stopped", app_id)
        self.event_bus.publish(
            Event(event_type=f"enterprise.{app_id}.stopped", source="enterprise_manager", payload=payload)
        )
        return True, f"Application '{app_id}' stopped."

    def restart_app(self, app_id: str) -> tuple[bool, str]:
        """Restart an enterprise application (stop + start of the tracked state)."""
        self.stop_app(app_id)
        success, msg = self.start_app(app_id)
        if success:
            with self._lock:
                runtime = self._runtimes.get(app_id)
                if runtime is not None:
                    runtime.restart_count += 1
        return success, msg

    def _pause(
        self,
        app_id: str,
        reason: str,
        *,
        by_shedding: bool,
        causes: set[str] | None = None,
        plan_generation: int | None = None,
    ) -> tuple[bool, str]:
        with self._lock:
            if self._shed_plan_is_stale(plan_generation):
                return False, f"Application '{app_id}' left as it is: a newer load-shedding event was handled."
            runtime = self._runtimes.get(app_id)
            if not runtime or runtime.status != AppStatus.RUNNING:
                return False, f"Application '{app_id}' is not currently running."

            runtime.status = AppStatus.PAUSED
            self._apply_estimates(runtime, paused=True)
            if by_shedding:
                self._shed_paused[app_id] = set(causes) if causes else {CAUSE_TIER}
            else:
                self._shed_paused.pop(app_id, None)
            payload = {"reason": reason, "paused_by": "load_shedding" if by_shedding else "operator"}
            payload.update(runtime.to_dict())

        logger.warning("Paused enterprise app '%s': %s", app_id, reason)
        self.event_bus.publish(
            Event(event_type=f"enterprise.{app_id}.paused", source="enterprise_manager", payload=payload)
        )
        return True, f"Application '{app_id}' paused: {reason}"

    def pause_app(self, app_id: str, reason: str = "Energy preservation") -> tuple[bool, str]:
        """Mark an application paused on request. Such a pause is not lifted when shedding eases."""
        return self._pause(app_id, reason, by_shedding=False)

    def resume_app(
        self, app_id: str, reason: str = "Solar surplus", *, plan_generation: int | None = None
    ) -> tuple[bool, str]:
        """Resume execution of a paused application.

        ``plan_generation`` is set only by the load-shedding handler: the resume is then
        skipped if a newer shedding event has been handled since that plan was made.
        """
        with self._lock:
            if self._shed_plan_is_stale(plan_generation):
                return False, f"Application '{app_id}' left as it is: a newer load-shedding event was handled."
            runtime = self._runtimes.get(app_id)
            if not runtime or runtime.status != AppStatus.PAUSED:
                return False, f"Application '{app_id}' is not paused."

            runtime.status = AppStatus.RUNNING
            self._apply_estimates(runtime, paused=False)
            self._shed_paused.pop(app_id, None)
            payload = {"reason": reason}
            payload.update(runtime.to_dict())

        logger.info("Resumed enterprise app '%s': %s", app_id, reason)
        self.event_bus.publish(
            Event(event_type=f"enterprise.{app_id}.resumed", source="enterprise_manager", payload=payload)
        )
        return True, f"Application '{app_id}' resumed: {reason}"

    def get_runtime_state(self, app_id: str) -> AppRuntimeState | None:
        """Get the tracked runtime state for a specific app."""
        self._sync_with_registry()
        with self._lock:
            return self._runtimes.get(app_id)

    def list_runtime_states(self) -> list[AppRuntimeState]:
        """Return tracked runtime states of all registered applications."""
        self._sync_with_registry()
        with self._lock:
            return list(self._runtimes.values())

    @staticmethod
    def _is_excluded_name(name: str) -> bool:
        """Dotfiles (.env, .git, .venv, ...), bytecode caches and `smdc app init --force` backups
        (``<name>.bak-<timestamp>``) never go into a package."""
        return name.startswith(".") or name.startswith("__pycache__") or ".bak-" in name

    @classmethod
    def _collect_package_files(cls, source_dir: Path, skip: set[Path]) -> list[tuple[Path, str]]:
        """Walks ``source_dir`` and returns (path, archive name) pairs, filtering at every depth."""
        root = source_dir.resolve()
        collected: list[tuple[Path, str]] = []
        for dirpath, dirnames, filenames in os.walk(source_dir, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not cls._is_excluded_name(d))
            current = Path(dirpath)
            # Symlinked directories are listed in dirnames but not descended into.
            for name in sorted(filenames) + [d for d in dirnames if (current / d).is_symlink()]:
                if cls._is_excluded_name(name):
                    continue
                item = current / name
                resolved = item.resolve()
                if resolved in skip:
                    continue
                if item.is_symlink() and root not in resolved.parents and resolved != root:
                    logger.warning("Skipping symlink that points outside the app directory: %s", item)
                    continue
                collected.append((item, item.relative_to(source_dir).as_posix()))
        return collected

    def package_app(
        self, app_path: Path | str, output_path: Path | str | None = None, sign_pqc: bool = True
    ) -> tuple[bool, str, dict[str, Any]]:
        """Bundle an enterprise application directory into a `.smdc-app` archive with a sidecar.

        With ``sign_pqc`` the sidecar carries an ML-DSA-87 signature by this node's persistent
        identity key. Signing is not verification: consumers must call ``verify_package()``
        with a public key they already trust.
        """
        source_dir = Path(app_path)
        if not source_dir.exists() or not source_dir.is_dir():
            return False, f"Directory not found: {source_dir}", {}

        manifest_file = source_dir / "smdc-app.yaml"
        if not manifest_file.exists():
            manifest_file = source_dir / "smdc-app.json"
        if not manifest_file.exists():
            return False, "Directory does not contain `smdc-app.yaml` or `smdc-app.json`.", {}

        manifest = self.registry.load_manifest_file(manifest_file)
        if not manifest:
            return False, "Failed to parse application manifest.", {}

        # Both values are embedded in the default output file name: refuse anything that
        # could act as a path (separators, "..", leading dots).
        if not is_valid_app_id(manifest.app_id):
            return False, f"Refusing to package: invalid app_id {manifest.app_id!r}.", {}
        if not is_valid_app_version(manifest.version):
            return False, f"Refusing to package: invalid version {manifest.version!r}.", {}

        out_file = Path(output_path or f"{manifest.app_id}-{manifest.version}.smdc-app")
        sidecar = package_sidecar_path(out_file)
        # Archive and sidecar are built under temporary dot-names (never collected into a
        # package) and renamed into place only when every requested step, signing included,
        # has succeeded: a failed run leaves no file that looks like a finished package.
        tmp_token = f"{os.getpid()}-{time.time_ns()}"
        tmp_out = out_file.with_name(f".{out_file.name}.{tmp_token}.tmp")
        tmp_sidecar = sidecar.with_name(f".{sidecar.name}.{tmp_token}.tmp")

        try:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            members = self._collect_package_files(
                source_dir,
                skip={out_file.resolve(), sidecar.resolve(), tmp_out.resolve(), tmp_sidecar.resolve()},
            )

            # Build tarball
            with tarfile.open(tmp_out, "w:gz") as tar:
                for item, arcname in members:
                    tar.add(item, arcname=arcname, recursive=False)

            sha256_digest = _sha256_file(tmp_out)

            metadata: dict[str, Any] = {
                "app_id": manifest.app_id,
                "version": manifest.version,
                "sha256": sha256_digest,
                "package_size_bytes": tmp_out.stat().st_size,
                "created_at": time.time(),
                "pqc_signature": None,
            }

            # NIST FIPS 204 ML-DSA attestation. Requested signing that fails is a packaging
            # failure (fail closed): an unsigned package is produced only with sign_pqc=False.
            if sign_pqc:
                try:
                    from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner, get_node_pqc_keypair

                    signer = PQCSigner(PQCAlgorithm.ML_DSA_87)
                    # Only the persisted node identity may sign a package (never a throwaway key).
                    kp = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87, require_persistent=True)
                    sig = signer.sign(_package_signing_input(metadata, PACKAGE_SIGNATURE_SCHEME), kp.private_key)
                    if not sig:
                        raise ValueError("signer returned an empty signature")
                    metadata["pqc_signature"] = {
                        "algorithm": PACKAGE_SIGNATURE_ALGORITHM,
                        "scheme": PACKAGE_SIGNATURE_SCHEME,
                        "key_id": kp.key_id,
                        "public_key_hex": kp.public_key.hex(),
                        "signature_hex": sig.hex(),
                    }
                except Exception as e:
                    reason = str(e) or type(e).__name__
                    logger.error("PQC package signing failed; no package was produced: %s", reason)
                    return (
                        False,
                        f"PQC signing failed ({reason}); no package was written to {out_file}. "
                        "Fix the signing key/library, or pass sign_pqc=False (--no-pqc) for an unsigned package.",
                        {},
                    )

            # Save manifest sidecar, then publish sidecar and archive.
            tmp_sidecar.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            os.replace(tmp_out, out_file)
            os.replace(tmp_sidecar, sidecar)

            logger.info(
                "Successfully packaged enterprise application to %s (SHA-256: %s...)",
                out_file,
                sha256_digest[:12],
            )
            return True, f"Application packaged to {out_file}", metadata
        except Exception as e:
            logger.error("Failed to package application: %s", e)
            return False, f"Packaging error: {e}", {}
        finally:
            for leftover in (tmp_out, tmp_sidecar):
                with contextlib.suppress(OSError):
                    leftover.unlink()
