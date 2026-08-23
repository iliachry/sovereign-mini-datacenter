"""Tests for the in-process event bus module."""

import threading

from sovereign_dc.events import (
    Event,
    EventType,
    SovereignEventBus,
    _pattern_matches,
    get_event_bus,
    reset_event_bus,
)


class TestEvent:
    """Test Event dataclass."""

    def test_event_creation(self):
        e = Event(event_type="test.event", source="unit_test", payload={"key": "value"})
        assert e.event_type == "test.event"
        assert e.source == "unit_test"
        assert e.payload == {"key": "value"}
        assert e.timestamp > 0

    def test_event_str(self):
        e = Event(event_type="test.event", source="unit_test", payload={"key": "value"})
        s = str(e)
        assert "test.event" in s
        assert "unit_test" in s

    def test_event_default_payload(self):
        e = Event(event_type="test.event", source="unit_test")
        assert e.payload == {}


class TestEventType:
    """Test EventType enumeration."""

    def test_load_shedding_value(self):
        assert EventType.LOAD_SHEDDING_CHANGED == "load_shedding.changed"

    def test_node_online_value(self):
        assert EventType.NODE_ONLINE == "node.online"

    def test_dtn_bundle_queued_value(self):
        assert EventType.DTN_BUNDLE_QUEUED == "dtn.bundle_queued"


class TestPatternMatching:
    """Test subscription pattern matching."""

    def test_exact_match(self):
        assert _pattern_matches("load_shedding.changed", "load_shedding.changed") is True

    def test_exact_mismatch(self):
        assert _pattern_matches("load_shedding.changed", "node.online") is False

    def test_wildcard_matches_everything(self):
        assert _pattern_matches("*", "load_shedding.changed") is True
        assert _pattern_matches("*", "node.online") is True
        assert _pattern_matches("*", "anything") is True

    def test_prefix_match(self):
        assert _pattern_matches("load_shedding.*", "load_shedding.changed") is True
        assert _pattern_matches("load_shedding.*", "load_shedding.cleared") is True

    def test_prefix_no_match(self):
        assert _pattern_matches("load_shedding.*", "node.online") is False

    def test_prefix_requires_dot_separator(self):
        assert _pattern_matches("load.*", "loading") is False


class TestSovereignEventBus:
    """Test the event bus core functionality."""

    def test_subscribe_and_publish(self):
        bus = SovereignEventBus()
        received = []

        def handler(event: Event):
            received.append(event)

        bus.subscribe("test.event", handler)
        event = Event(event_type="test.event", source="test")
        count = bus.publish(event)

        assert count == 1
        assert len(received) == 1
        assert received[0] is event

    def test_multiple_handlers(self):
        bus = SovereignEventBus()
        results = []

        def handler_a(event: Event):
            results.append("A")

        def handler_b(event: Event):
            results.append("B")

        bus.subscribe("test.event", handler_a)
        bus.subscribe("test.event", handler_b)
        bus.publish(Event(event_type="test.event", source="test"))

        assert "A" in results
        assert "B" in results

    def test_wildcard_subscription(self):
        bus = SovereignEventBus()
        received = []

        def handler(event: Event):
            received.append(event.event_type)

        bus.subscribe("*", handler)
        bus.publish(Event(event_type="a.event", source="test"))
        bus.publish(Event(event_type="b.event", source="test"))

        assert received == ["a.event", "b.event"]

    def test_prefix_subscription(self):
        bus = SovereignEventBus()
        received = []

        def handler(event: Event):
            received.append(event.event_type)

        bus.subscribe("mesh.*", handler)
        bus.publish(Event(event_type="mesh.peer_up", source="test"))
        bus.publish(Event(event_type="mesh.peer_down", source="test"))
        bus.publish(Event(event_type="node.online", source="test"))

        assert received == ["mesh.peer_up", "mesh.peer_down"]

    def test_no_matching_handlers(self):
        bus = SovereignEventBus()
        count = bus.publish(Event(event_type="unhandled.event", source="test"))
        assert count == 0

    def test_unsubscribe(self):
        bus = SovereignEventBus()
        received = []

        def handler(event: Event):
            received.append(event)

        bus.subscribe("test.event", handler)
        bus.publish(Event(event_type="test.event", source="test"))
        assert len(received) == 1

        result = bus.unsubscribe("test.event", handler)
        assert result is True

        bus.publish(Event(event_type="test.event", source="test"))
        assert len(received) == 1  # No new events

    def test_unsubscribe_nonexistent(self):
        bus = SovereignEventBus()

        def handler(event: Event):
            pass

        result = bus.unsubscribe("test.event", handler)
        assert result is False

    def test_handler_exception_doesnt_crash(self):
        bus = SovereignEventBus()
        received = []

        def bad_handler(event: Event):
            raise ValueError("Handler error")

        def good_handler(event: Event):
            received.append(event)

        bus.subscribe("test.event", bad_handler)
        bus.subscribe("test.event", good_handler)

        count = bus.publish(Event(event_type="test.event", source="test"))
        assert count == 1  # Only good_handler succeeded
        assert len(received) == 1

    def test_event_history(self):
        bus = SovereignEventBus()
        bus.publish(Event(event_type="a.event", source="test"))
        bus.publish(Event(event_type="b.event", source="test"))

        history = bus.get_history()
        assert len(history) == 2
        assert history[0].event_type == "b.event"  # Newest first
        assert history[1].event_type == "a.event"

    def test_event_history_filtered(self):
        bus = SovereignEventBus()
        bus.publish(Event(event_type="mesh.peer_up", source="test"))
        bus.publish(Event(event_type="node.online", source="test"))
        bus.publish(Event(event_type="mesh.peer_down", source="test"))

        history = bus.get_history(event_type="mesh.*")
        assert len(history) == 2

    def test_event_history_limit(self):
        bus = SovereignEventBus()
        for i in range(10):
            bus.publish(Event(event_type=f"event.{i}", source="test"))

        history = bus.get_history(limit=3)
        assert len(history) == 3

    def test_history_ring_buffer_overflow(self):
        bus = SovereignEventBus()
        bus._max_history = 5
        for i in range(10):
            bus.publish(Event(event_type=f"event.{i}", source="test"))

        history = bus.get_history()
        assert len(history) == 5

    def test_clear(self):
        bus = SovereignEventBus()
        bus.subscribe("test.event", lambda e: None)
        bus.publish(Event(event_type="test.event", source="test"))

        bus.clear()
        assert bus.subscriber_count() == 0
        assert len(bus.get_history()) == 0

    def test_subscriber_count(self):
        bus = SovereignEventBus()
        bus.subscribe("a.event", lambda e: None)
        bus.subscribe("a.event", lambda e: None)
        bus.subscribe("b.event", lambda e: None)

        assert bus.subscriber_count() == 3
        assert bus.subscriber_count("a.event") == 2
        assert bus.subscriber_count("b.event") == 1
        assert bus.subscriber_count("c.event") == 0

    def test_thread_safety(self):
        bus = SovereignEventBus()
        received = []
        lock = threading.Lock()

        def handler(event: Event):
            with lock:
                received.append(event.event_type)

        bus.subscribe("*", handler)

        def publish_events(prefix: str):
            for i in range(20):
                bus.publish(Event(event_type=f"{prefix}.{i}", source="thread"))

        threads = [threading.Thread(target=publish_events, args=(f"thread_{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 80  # 4 threads × 20 events


class TestEventBusSingleton:
    """Test global event bus singleton."""

    def setup_method(self):
        reset_event_bus()

    def teardown_method(self):
        reset_event_bus()

    def test_get_event_bus_creates_instance(self):
        bus = get_event_bus()
        assert isinstance(bus, SovereignEventBus)

    def test_get_event_bus_returns_same(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_reset_clears_and_recreates(self):
        b1 = get_event_bus()
        b1.subscribe("test", lambda e: None)
        reset_event_bus()
        b2 = get_event_bus()
        assert b2 is not b1
        assert b2.subscriber_count() == 0
