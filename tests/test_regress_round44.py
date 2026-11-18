"""Regression tests for Round 44 (failed re-index cache invalidation in knowledge_indexer)."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer


def test_round44_failed_reindex_invalidates_cached_fingerprint_until_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed re-indexing invalidates stale cached fingerprints so subsequent healthy scans recover."""
    points: dict[Any, dict[str, Any]] = {}
    upsert_calls = 0
    fail_on_upsert_call: int | None = None
    real_doc_version = knowledge_indexer._document_version

    def fake_qdrant(method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal upsert_calls
        if method == "PUT" and endpoint.startswith("/points") and payload is not None:
            upsert_calls += 1
            if fail_on_upsert_call is not None and upsert_calls == fail_on_upsert_call:
                raise OSError("simulated Qdrant upsert failure")
            for pt in payload["points"]:
                points[pt["id"]] = pt
            return {"status": "ok"}
        if method == "POST" and endpoint.startswith("/points/delete") and payload is not None:
            must = payload.get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            keep_ids: set[Any] = set()
            for cond in payload.get("filter", {}).get("must_not", []):
                keep_ids.update(cond.get("has_id", []))
            for pid in list(points):
                p_val = points[pid]["payload"]["path"]
                if p_val in paths and pid not in keep_ids:
                    del points[pid]
            return {"status": "ok"}
        raise AssertionError(f"Unexpected Qdrant call: {method} {endpoint}")

    docs = tmp_path / "docs"
    docs.mkdir()

    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "UPSERT_BATCH_SIZE", 2)
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda text: [float(len(text))])
    monkeypatch.setattr(knowledge_indexer, "_qdrant_request", fake_qdrant)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())

    def raise_eio_for_target(target: Path) -> Any:
        def _inner(path: str) -> tuple[int, int, str]:
            if Path(path) == target:
                raise OSError(errno.EIO, "transient input/output error")
            return real_doc_version(path)

        return _inner

    def doc_point_count(target: Path) -> int:
        return sum(1 for pt in points.values() if pt["payload"]["path"] == str(target))

    indexed: set[tuple[str, knowledge_indexer.DocumentVersion]] = set()

    # Subcase A: Astra 44 exact multi-batch rollback reproduction (5 chunks across 3 batches of 2).
    doc_a = docs / "long_runbook.md"
    doc_a.write_text(" ".join(f"w{i}" for i in range(2000)), encoding="utf-8")
    real_ver_a = real_doc_version(str(doc_a))

    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert doc_point_count(doc_a) == 5
    assert (str(doc_a), real_ver_a) in indexed

    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_eio_for_target(doc_a))
    fail_on_upsert_call = upsert_calls + 2  # 1st batch succeeds, 2nd fails -> rollback wipes all 5 points
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert doc_point_count(doc_a) == 0
    assert (str(doc_a), real_ver_a) not in indexed
    assert (str(doc_a), knowledge_indexer.UNKNOWN_MTIME) in indexed

    monkeypatch.setattr(knowledge_indexer, "_document_version", real_doc_version)
    fail_on_upsert_call = None
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert doc_point_count(doc_a) == 5
    assert (str(doc_a), real_ver_a) in indexed
    assert (str(doc_a), knowledge_indexer.UNKNOWN_MTIME) not in indexed
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # Subcase B: first-batch PUT failure -> _reconcile_pending + top-of-scan pending_invalid guard.
    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_eio_for_target(doc_a))
    fail_on_upsert_call = upsert_calls + 1  # 1st batch fails -> _reconcile_pending
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc_a)})
    assert all(k[0] != str(doc_a) for k in indexed)

    monkeypatch.setattr(knowledge_indexer, "_document_version", real_doc_version)
    fail_on_upsert_call = None
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert knowledge_indexer.pending_reconcile_paths() == frozenset()
    assert (str(doc_a), real_ver_a) in indexed

    # Direct index_file first-batch failure while cached entry stays present -> invalidated at top of next scan.
    fail_on_upsert_call = upsert_calls + 1
    assert knowledge_indexer.index_file(str(doc_a)) is False
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc_a)})
    assert (str(doc_a), real_ver_a) in indexed

    fail_on_upsert_call = None
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert knowledge_indexer.pending_reconcile_paths() == frozenset()
    assert doc_point_count(doc_a) == 5
    assert (str(doc_a), real_ver_a) in indexed

    # Subcase C: pre-Qdrant failure during fallback preserves deletion tracking.
    doc_b = docs / "short_note.md"
    doc_b.write_text("doc b body", encoding="utf-8")
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert doc_point_count(doc_b) == 1

    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_eio_for_target(doc_b))
    monkeypatch.setattr(
        knowledge_indexer, "get_embedding", lambda _text: (_ for _ in ()).throw(RuntimeError("ollama down"))
    )
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert doc_point_count(doc_b) == 1
    assert (str(doc_b), knowledge_indexer.UNKNOWN_MTIME) in indexed

    doc_b.unlink()
    monkeypatch.setattr(knowledge_indexer, "_document_version", real_doc_version)
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda text: [float(len(text))])
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert doc_point_count(doc_b) == 0
    assert doc_point_count(doc_a) == 5
    assert all(k[0] != str(doc_b) for k in indexed)
