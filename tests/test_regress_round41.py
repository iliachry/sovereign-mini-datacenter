"""Regression tests for Tri-Council Round 41 findings."""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter


def test_round41_dtn_upsert_updates_expiry_and_metadata_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate bundle_id upsert refreshes timestamp, lifetime, endpoints, and fragment metadata."""
    now = 1000.0
    monkeypatch.setattr("sovereign_dc.space.dtn.bundle.time.time", lambda: now)
    monkeypatch.setattr("sovereign_dc.space.dtn.router.time.time", lambda: now)

    router = DTNRouter(db_path=str(tmp_path / "spool.db"), local_node_eid="dtn://smdc-node-01")
    first = Bundle(
        "dtn://a",
        "dtn://b",
        b"v1",
        bundle_id="retry-id",
        creation_timestamp=1000.0,
        lifetime_seconds=10,
        priority=BundlePriority.NORMAL,
    )
    assert router.queue_bundle(first) is True

    replacement = Bundle(
        "dtn://a2",
        "dtn://b2",
        b"v2-longer",
        bundle_id="retry-id",
        creation_timestamp=1005.0,
        lifetime_seconds=3600,
        priority=BundlePriority.CRITICAL,
        fragment_offset=4,
        total_application_data_length=20,
    )
    assert router.queue_bundle(replacement) is True

    # Past original expiry (1000 + 10) but well before replacement expiry (1005 + 3600).
    now = 1015.0
    router.purge_expired()
    queue = router.get_outbound_queue()
    assert len(queue) == 1
    got = queue[0]
    assert got.bundle_id == "retry-id"
    assert got.payload == b"v2-longer"
    assert got.lifetime_seconds == 3600
    assert got.creation_timestamp == 1005.0
    assert got.source_eid == "dtn://a2"
    assert got.destination_eid == "dtn://b2"
    assert got.fragment_offset == 4
    assert got.total_application_data_length == 20

    # Past replacement expiry (1005 + 3600).
    now = 4606.0
    assert router.get_outbound_queue() == []


def test_round41_bundle_refragmentation_preserves_offsets_and_total_adu_length() -> None:
    """Refragmenting an existing fragment preserves original ADU offsets and total length."""
    original = Bundle("dtn://sender", "dtn://receiver", b"abcdefghij", bundle_id="b1")
    frags = original.create_fragments(6)
    assert len(frags) == 2
    assert (frags[0].fragment_offset, frags[0].total_application_data_length, frags[0].payload) == (0, 10, b"abcdef")
    assert (frags[1].fragment_offset, frags[1].total_application_data_length, frags[1].payload) == (6, 10, b"ghij")

    children = frags[1].create_fragments(2)
    assert len(children) == 2
    assert [(c.bundle_id, c.fragment_offset, c.total_application_data_length, c.payload) for c in children] == [
        ("b1-frag-6", 6, 10, b"gh"),
        ("b1-frag-8", 8, 10, b"ij"),
    ]
    for child in children:
        assert child.is_fragment() is True
        restored = Bundle.deserialize(child.serialize())
        assert restored.fragment_offset == child.fragment_offset
        assert restored.total_application_data_length == 10
        assert restored.header_checksum == child.header_checksum

    reassembled_frags = sorted(
        [*frags[0].create_fragments(4), *children],
        key=lambda frag: frag.fragment_offset,
    )
    assert [frag.fragment_offset for frag in reassembled_frags] == [0, 4, 6, 8]
    assert all(frag.total_application_data_length == 10 for frag in reassembled_frags)
    assert b"".join(frag.payload for frag in reassembled_frags) == b"abcdefghij"

    with pytest.raises(ValueError):
        original.create_fragments(True)  # type: ignore[arg-type]

    zero_ts_frag = Bundle("dtn://a", "dtn://b", b"yz", creation_timestamp=0.0, fragment_offset=8)
    assert zero_ts_frag.creation_timestamp == 0.0
    assert zero_ts_frag.total_application_data_length == 10
    assert zero_ts_frag.is_fragment() is True
