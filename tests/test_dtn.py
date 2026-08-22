import time

import pytest

from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter


def test_bundle_creation_and_serialization():
    src = "dtn://node-alpha/sensor"
    dst = "dtn://ground-station/orbit"
    payload = b"CRITICAL_TELEMETRY_SAMPLE_001"

    b = Bundle(
        source_eid=src, destination_eid=dst, payload=payload, priority=BundlePriority.CRITICAL, lifetime_seconds=3600
    )
    assert b.source_eid == src
    assert b.destination_eid == dst
    assert b.payload == payload
    assert b.priority == BundlePriority.CRITICAL
    assert not b.is_expired()
    assert not b.is_fragment()

    raw = b.serialize()
    assert isinstance(raw, bytes)

    b_restored = Bundle.deserialize(raw)
    assert b_restored.source_eid == src
    assert b_restored.destination_eid == dst
    assert b_restored.payload == payload
    assert b_restored.priority == BundlePriority.CRITICAL
    assert b_restored.bundle_id == b.bundle_id


def test_bundle_expiration():
    b = Bundle("dtn://src/a", "dtn://dst/b", b"data", lifetime_seconds=0)
    time.sleep(0.01)
    assert b.is_expired()


def test_bundle_fragmentation():
    large_payload = b"X" * 1000
    b = Bundle("dtn://src/a", "dtn://dst/b", large_payload, priority=BundlePriority.NORMAL)
    frags = b.create_fragments(max_fragment_size=300)
    assert len(frags) == 4
    assert frags[0].is_fragment()
    assert frags[0].fragment_offset == 0
    assert frags[0].total_application_data_length == 1000
    assert len(frags[0].payload) == 300
    assert len(frags[-1].payload) == 100

    # Test small payload does not fragment
    small_frags = b.create_fragments(max_fragment_size=2000)
    assert len(small_frags) == 1


def test_bundle_deserialize_invalid_version():
    raw_invalid_v = b'{"v":9,"id":"123","src":"a","dst":"b","pri":1,"ts":0,"ttl":100,"off":0,"total_len":4,"payload_b64":"ZGF0YQ==","chk":"xyz"}'
    with pytest.raises(ValueError, match="Unsupported Bundle Protocol version"):
        Bundle.deserialize(raw_invalid_v)


def test_bundle_deserialize_checksum_mismatch():
    b = Bundle("dtn://src/a", "dtn://dst/b", b"data")
    raw = b.serialize()
    # Corrupt checksum in json
    raw_corrupted = raw.replace(b'"chk":"' + b.checksum[:10].encode(), b'"chk":"ffffffffff')
    with pytest.raises(ValueError, match="Bundle integrity checksum mismatch"):
        Bundle.deserialize(raw_corrupted)


def test_dtn_router_spool(tmp_path):
    db_file = str(tmp_path / "spool.db")
    router = DTNRouter(db_path=db_file, local_node_eid="dtn://smdc-node-01")

    b1 = Bundle("dtn://smdc-node-01/status", "dtn://ground/rx", b"normal_p1", priority=BundlePriority.NORMAL)
    b2 = Bundle("dtn://smdc-node-01/alert", "dtn://ground/rx", b"critical_p3", priority=BundlePriority.CRITICAL)
    b3 = Bundle("dtn://smdc-node-01/bulk", "dtn://ground/rx", b"bulk_p0", priority=BundlePriority.BULK)

    assert router.queue_bundle(b1) is True
    assert router.queue_bundle(b2) is True
    assert router.queue_bundle(b3) is True

    # Test rejecting expired bundle
    expired_bundle = Bundle("dtn://smdc/exp", "dtn://ground", b"dead", lifetime_seconds=0)
    time.sleep(0.01)
    assert router.queue_bundle(expired_bundle) is False

    queue = router.get_outbound_queue()
    assert len(queue) == 3
    # Check that highest priority (CRITICAL) comes first
    assert queue[0].priority == BundlePriority.CRITICAL
    assert queue[0].payload == b"critical_p3"

    # Test max_bytes limit in get_outbound_queue
    limited_queue = router.get_outbound_queue(max_bytes=len(queue[0].serialize()) + 1)
    assert len(limited_queue) == 1

    # Test queue stats
    stats = router.get_queue_stats()
    assert stats["queued_bundle_count"] == 3
    assert stats["total_spool_bytes"] > 0
    assert stats["priority_counts"]["critical"] == 1
    assert stats["priority_counts"]["normal"] == 1
    assert stats["priority_counts"]["bulk"] == 1

    # Test mark delivered
    router.mark_delivered(b2.bundle_id)
    new_stats = router.get_queue_stats()
    assert new_stats["queued_bundle_count"] == 2

    # Test purge expired
    b_short = Bundle("dtn://smdc/short", "dtn://ground", b"bye", lifetime_seconds=1)
    router.queue_bundle(b_short)
    assert router.get_queue_stats()["queued_bundle_count"] == 3
    time.sleep(1.1)
    router.purge_expired()
    assert router.get_queue_stats()["queued_bundle_count"] == 2

def test_orbital_blackout_priority_simulation(tmp_path):
    """Simulates a 12-hour orbital blackout where many bundles are queued, and ensures CRITICAL are prioritized upon AOS."""
    db_file = str(tmp_path / "blackout_spool.db")
    router = DTNRouter(db_path=db_file, local_node_eid="dtn://smdc-node-01")
    
    # Simulate a blackout period by queueing bundles with different priorities
    # First, 10 NORMAL telemetry bundles
    for i in range(10):
        b = Bundle(
            source_eid="dtn://smdc-node-01/telemetry",
            destination_eid="dtn://ground-station/log",
            payload=f"Normal telemetry {i}".encode(),
            priority=BundlePriority.NORMAL
        )
        router.queue_bundle(b)
        
    # Then a hardware failure happens during blackout, triggering a CRITICAL alert
    critical_bundle = Bundle(
        source_eid="dtn://smdc-node-01/alert",
        destination_eid="dtn://technician.sovereign.space/alerts",
        payload=b"CRITICAL: NVMe Array Degraded",
        priority=BundlePriority.CRITICAL
    )
    router.queue_bundle(critical_bundle)
    
    # Finally, 5 more NORMAL bundles
    for i in range(5):
        b = Bundle(
            source_eid="dtn://smdc-node-01/telemetry",
            destination_eid="dtn://ground-station/log",
            payload=f"Normal telemetry post-failure {i}".encode(),
            priority=BundlePriority.NORMAL
        )
        router.queue_bundle(b)
        
    # Total bundles: 16
    assert router.get_queue_stats()["queued_bundle_count"] == 16
    
    # Now simulate satellite AOS (Acquisition of Signal). 
    # Fetch queue to transmit.
    transmit_queue = router.get_outbound_queue()
    
    # Ensure the CRITICAL bundle is at the very front of the queue, bypassing all older NORMAL bundles
    assert transmit_queue[0].priority == BundlePriority.CRITICAL
    assert transmit_queue[0].payload == b"CRITICAL: NVMe Array Degraded"
    
    # The rest should be NORMAL
    for b in transmit_queue[1:]:
        assert b.priority == BundlePriority.NORMAL
