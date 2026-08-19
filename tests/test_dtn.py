import time
import pytest
from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter

def test_bundle_creation_and_serialization():
    src = "dtn://node-alpha/sensor"
    dst = "dtn://ground-station/orbit"
    payload = b"CRITICAL_TELEMETRY_SAMPLE_001"
    
    b = Bundle(source_eid=src, destination_eid=dst, payload=payload, priority=BundlePriority.CRITICAL, lifetime_seconds=3600)
    assert b.source_eid == src
    assert b.destination_eid == dst
    assert b.payload == payload
    assert b.priority == BundlePriority.CRITICAL
    assert not b.is_expired()
    
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

def test_dtn_router_spool(tmp_path):
    db_file = str(tmp_path / "spool.db")
    router = DTNRouter(db_path=db_file, local_node_eid="dtn://smdc-node-01")
    
    b1 = Bundle("dtn://smdc-node-01/status", "dtn://ground/rx", b"normal_p1", priority=BundlePriority.NORMAL)
    b2 = Bundle("dtn://smdc-node-01/alert", "dtn://ground/rx", b"critical_p3", priority=BundlePriority.CRITICAL)
    b3 = Bundle("dtn://smdc-node-01/bulk", "dtn://ground/rx", b"bulk_p0", priority=BundlePriority.BULK)
    
    router.queue_bundle(b1)
    router.queue_bundle(b2)
    router.queue_bundle(b3)
    
    queue = router.get_outbound_queue()
    assert len(queue) == 3
    # Check that highest priority (CRITICAL) comes first
    assert queue[0].priority == BundlePriority.CRITICAL
    assert queue[0].payload == b"critical_p3"
