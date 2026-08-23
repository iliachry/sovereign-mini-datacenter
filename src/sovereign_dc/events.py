"""Sovereign Mini Datacenter — In-Process Event Bus for Agent Coordination.

Provides a lightweight publish/subscribe event bus enabling decoupled communication
between autonomous agents (Sentinel Copilot, Knowledge Indexer, Bootstrap Provisioner)
without shared filesystem state.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger("SovereignEventBus")


class EventType(StrEnum):
    """Core event types for inter-agent coordination."""

    # Power & Energy
    LOAD_SHEDDING_CHANGED = "load_shedding.changed"
    BATTERY_LOW = "battery.low"
    BATTERY_CRITICAL = "battery.critical"
    SOLAR_SURPLUS = "solar.surplus"

    # Node Lifecycle
    NODE_ONLINE = "node.online"
    NODE_DEGRADED = "node.degraded"
    NODE_SHUTDOWN = "node.shutdown"
    BOOTSTRAP_PHASE_COMPLETE = "bootstrap.phase_complete"
    BOOTSTRAP_COMPLETE = "bootstrap.complete"

    # Network
    MESH_PEER_UP = "mesh.peer_up"
    MESH_PEER_DOWN = "mesh.peer_down"
    MESH_PARTITION = "mesh.partition"
    DTN_BUNDLE_QUEUED = "dtn.bundle_queued"
    DTN_BUNDLE_DELIVERED = "dtn.bundle_delivered"

    # AI & Services
    OLLAMA_READY = "ollama.ready"
    QDRANT_READY = "qdrant.ready"
    MODEL_SWAP = "model.swap"
    INDEX_COMPLETE = "index.complete"

    # Technician
    TECHNICIAN_ALERT = "technician.alert"
    HUMAN_INTERVENTION_REQUESTED = "human.intervention_requested"

    # Generic
    CUSTOM = "custom"


@dataclass
class Event:
    """Immutable event payload dispatched through the event bus."""

    event_type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"Event({self.event_type}, source={self.source}, keys={list(self.payload.keys())})"


# Type alias for event handler callbacks
EventHandler = Callable[[Event], None]


class SovereignEventBus:
    """Thread-safe in-process publish/subscribe event bus.

    Agents subscribe to event types and receive callbacks when events are published.
    Supports wildcard subscriptions via ``*`` and prefix matching via ``topic.*``.

    Example::

        bus = SovereignEventBus()
        bus.subscribe("load_shedding.changed", my_handler)
        bus.publish(Event(event_type="load_shedding.changed", source="sentinel", payload={"level": 2}))
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = threading.Lock()
        self._history: list[Event] = []
        self._max_history: int = 100

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for a specific event type.

        Args:
            event_type: Event type string to listen for. Use ``*`` for all events.
            handler: Callback function invoked with the Event when published.
        """
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            logger.debug("Subscribed handler %s to event type '%s'", handler.__name__, event_type)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """Remove a handler from a specific event type.

        Returns:
            True if the handler was found and removed, False otherwise.
        """
        with self._lock:
            if event_type in self._handlers:
                try:
                    self._handlers[event_type].remove(handler)
                    return True
                except ValueError:
                    pass
        return False

    def publish(self, event: Event) -> int:
        """Dispatch an event to all matching subscribers.

        Matching rules:
        1. Exact match on ``event.event_type``
        2. Wildcard ``*`` subscribers receive all events
        3. Prefix match: ``load_shedding.*`` matches ``load_shedding.changed``

        Args:
            event: The event to dispatch.

        Returns:
            Number of handlers that were invoked.
        """
        handlers_invoked = 0

        with self._lock:
            # Record in history ring buffer
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]

            # Collect all matching handlers
            matched_handlers: list[EventHandler] = []

            for pattern, handlers in self._handlers.items():
                if _pattern_matches(pattern, event.event_type):
                    matched_handlers.extend(handlers)

        # Invoke handlers outside the lock to prevent deadlocks
        for handler in matched_handlers:
            try:
                handler(event)
                handlers_invoked += 1
            except Exception as exc:
                logger.error(
                    "Event handler %s raised exception for %s: %s",
                    handler.__name__,
                    event.event_type,
                    exc,
                )

        if handlers_invoked > 0:
            logger.debug("Published %s → %d handler(s) invoked", event, handlers_invoked)

        return handlers_invoked

    def get_history(self, event_type: str | None = None, limit: int = 50) -> list[Event]:
        """Retrieve recent events from the history ring buffer.

        Args:
            event_type: Filter by event type. None returns all events.
            limit: Maximum number of events to return.

        Returns:
            List of recent events, newest first.
        """
        with self._lock:
            if event_type is None:
                return list(reversed(self._history[-limit:]))
            filtered = [e for e in self._history if _pattern_matches(event_type, e.event_type)]
            return list(reversed(filtered[-limit:]))

    def clear(self) -> None:
        """Remove all subscriptions and event history."""
        with self._lock:
            self._handlers.clear()
            self._history.clear()

    def subscriber_count(self, event_type: str | None = None) -> int:
        """Count registered subscribers.

        Args:
            event_type: Count for specific event type. None counts all subscribers.
        """
        with self._lock:
            if event_type is None:
                return sum(len(h) for h in self._handlers.values())
            return len(self._handlers.get(event_type, []))


def _pattern_matches(pattern: str, event_type: str) -> bool:
    """Check if a subscription pattern matches an event type.

    Supports:
    - Exact match: ``"load_shedding.changed"`` matches ``"load_shedding.changed"``
    - Wildcard: ``"*"`` matches everything
    - Prefix: ``"load_shedding.*"`` matches ``"load_shedding.changed"``
    """
    if pattern == "*":
        return True
    if pattern == event_type:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return event_type.startswith(prefix + ".")
    return False


# ── Module-level singleton ────────────────────────────────────────────────
_global_bus: SovereignEventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> SovereignEventBus:
    """Return the global event bus singleton, creating it on first access."""
    global _global_bus
    with _bus_lock:
        if _global_bus is None:
            _global_bus = SovereignEventBus()
    return _global_bus


def reset_event_bus() -> None:
    """Clear and reset the global event bus singleton (useful for testing)."""
    global _global_bus
    with _bus_lock:
        if _global_bus is not None:
            _global_bus.clear()
        _global_bus = None
