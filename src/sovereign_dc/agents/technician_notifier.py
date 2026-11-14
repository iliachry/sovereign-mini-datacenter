#!/usr/bin/env python3
"""
Sovereign Mini Datacenter — Autonomous Technician Notifier
Multi-channel notification dispatcher enabling sovereign nodes to communicate with human technicians
via Local Log, MQTT (Home Assistant/OLED), Sub-GHz LoRa (Meshtastic), and Delay-Tolerant Space (DTN/BPv7).
"""

from __future__ import annotations

import enum
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.config import default_dtn_db_path, default_log_dir

logger = logging.getLogger("TechnicianNotifier")


class MessageSeverity(enum.StrEnum):
    """Severity levels for technician notifications."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class TechnicianMessage:
    """Structured notification payload sent to human technicians."""

    node_id: str
    event_type: str
    severity: MessageSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    action_required: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary representation."""
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return json.dumps(self.to_dict())

    def to_compact_text(self) -> str:
        """Compact text format for low-bandwidth LoRa or OLED display."""
        action_part = f" | ACTION: {self.action_required}" if self.action_required else ""
        return f"[{self.severity.value}] [{self.node_id}] {self.event_type}: {self.message}{action_part}"


class BaseNotifier:
    """Abstract base class for notification channels."""

    def send(self, message: TechnicianMessage) -> bool:
        """Send message through this channel. Returns True if successful."""
        raise NotImplementedError


class FileNotifier(BaseNotifier):
    """Appends structured JSON notifications to a persistent local log file.

    Without ``log_path`` the log is ``technician_notifications.jsonl`` in ``SOVEREIGN_LOG_DIR``
    if that is set, else in ``<state dir>/sovereign_logs`` (``SMDC_STATE_DIR`` or ``~/.smdc``,
    private to the user and kept across reboots; never shared ``/tmp``). The file is created
    with mode 0600.
    """

    def __init__(self, log_path: str | None = None):
        default_dir = os.getenv("SOVEREIGN_LOG_DIR") or default_log_dir()
        self.log_path: str = log_path if log_path else os.path.join(default_dir, "technician_notifications.jsonl")

    def send(self, message: TechnicianMessage) -> bool:
        try:
            directory = os.path.dirname(self.log_path)
            if directory:  # a bare filename means "current directory": nothing to create
                os.makedirs(directory, mode=0o700, exist_ok=True)
            # 0600 like the wallet: alerts carry node details and must not be world-readable.
            # The mode only applies when the file is created; an existing log keeps its own.
            fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(message.to_json() + "\n")
            logger.info("Technician notification written to %s", self.log_path)
            return True
        except Exception as e:
            logger.warning("FileNotifier failed to write to %s: %s", self.log_path, e)
            return False


class MQTTNotifier(BaseNotifier):
    """MQTT channel for Home Assistant / ESP32 OLED alerts.

    NOT IMPLEMENTED: no MQTT client is wired in, so ``send`` publishes nothing and
    reports failure. It never claims delivery.
    """

    def __init__(
        self,
        broker_host: str | None = None,
        broker_port: int = 1883,
        topic_prefix: str = "sovereign/technician",
    ):
        self.broker_host: str = str(broker_host or os.getenv("MQTT_BROKER_HOST") or "localhost")
        self.broker_port: int = int(os.getenv("MQTT_BROKER_PORT", str(broker_port)))
        self.topic_prefix: str = topic_prefix

    def send(self, message: TechnicianMessage) -> bool:
        topic = f"{self.topic_prefix}/{message.node_id}/alerts"
        logger.warning(
            "MQTT transport is not implemented: %s alert NOT published to %s on %s:%d",
            message.event_type,
            topic,
            self.broker_host,
            self.broker_port,
        )
        return False


class LoRaNotifier(BaseNotifier):
    """Sub-GHz Meshtastic channel for compact emergency alerts.

    NOT IMPLEMENTED: no radio transport exists, so ``send`` broadcasts nothing and
    reports failure. It never claims delivery.
    """

    def __init__(self, gateway_eid: str = "smdc-lora-gw-01"):
        self.gateway_eid: str = gateway_eid

    def send(self, message: TechnicianMessage) -> bool:
        logger.warning(
            "LoRa transport is not implemented: alert NOT broadcast via %s: %s",
            self.gateway_eid,
            message.to_compact_text(),
        )
        return False


class DTNNotifier(BaseNotifier):
    """Enqueues high-priority notification bundles for transmission via satellite pass.

    Spool path: ``db_path``, else ``DTN_DB_PATH``, else the private state-directory default
    (``sovereign_dc.config.default_dtn_db_path``).
    """

    def __init__(
        self,
        db_path: str | None = None,
        technician_eid: str = "dtn://technician.sovereign.space/alerts",
    ):
        self.db_path: str = str(db_path or os.getenv("DTN_DB_PATH") or default_dtn_db_path())
        self.technician_eid: str = technician_eid

    def send(self, message: TechnicianMessage) -> bool:
        try:
            from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
            from sovereign_dc.space.dtn.router import DTNRouter

            router = DTNRouter(db_path=self.db_path)
            priority = (
                BundlePriority.CRITICAL
                if message.severity in (MessageSeverity.CRITICAL, MessageSeverity.ERROR)
                else BundlePriority.EXPEDITED
            )
            bundle = Bundle(
                source_eid=f"dtn://{message.node_id}.sovereign.space",
                destination_eid=self.technician_eid,
                payload=message.to_json().encode("utf-8"),
                priority=priority,
                lifetime_seconds=86400 * 3,
            )
            if not router.queue_bundle(bundle):
                logger.warning("DTNNotifier: spool rejected bundle %s", bundle.bundle_id)
                return False
            logger.info("DTN bundle queued for technician: %s", bundle.bundle_id)
            return True
        except Exception as e:
            logger.warning("DTNNotifier failed to queue bundle: %s", e)
            return False


class TechnicianNotifierChain:
    """Multi-tier notification coordinator that dispatches to all available channels."""

    def __init__(
        self,
        notifiers: list[BaseNotifier] | None = None,
        node_id: str | None = None,
    ):
        self.node_id: str = str(node_id or os.getenv("NODE_ID") or "smdc-dgx-01")
        if notifiers is not None:
            self.notifiers = notifiers
        else:
            self.notifiers = [
                FileNotifier(),
                MQTTNotifier(),
                LoRaNotifier(),
                DTNNotifier(),
            ]

    def notify(
        self,
        event_type: str,
        severity: MessageSeverity,
        message: str,
        details: dict[str, Any] | None = None,
        action_required: str | None = None,
    ) -> dict[str, bool]:
        """Dispatches notification across all configured channels."""
        msg = TechnicianMessage(
            node_id=self.node_id,
            event_type=event_type,
            severity=severity,
            message=message,
            details=details or {},
            action_required=action_required,
        )

        results: dict[str, bool] = {}
        for notifier in self.notifiers:
            name = notifier.__class__.__name__
            try:
                results[name] = notifier.send(msg)
            except Exception as e:
                logger.error("Notifier %s encountered exception: %s", name, e)
                results[name] = False

        return results

    def request_human_help(
        self,
        issue_title: str,
        remediation_step: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        """Convenience method to dispatch an urgent human intervention alert."""
        return self.notify(
            event_type="ACTION_REQUIRED",
            severity=MessageSeverity.CRITICAL,
            message=issue_title,
            details=details,
            action_required=remediation_step,
        )
