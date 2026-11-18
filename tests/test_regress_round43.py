"""Regression tests for Round 43 (fallback unknown-mtime tracking in knowledge_indexer)."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer


def test_round43_fallback_index_tracks_document_for_deletion_and_fingerprint_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback unknown-mtime indexing still tracks files for deletion, recovery, and re-indexing."""
    points: dict[Any, dict[str, Any]] = {}
    real_doc_version = knowledge_indexer._document_version

    def fake_qdrant(method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "PUT" and endpoint.startswith("/points") and payload is not None:
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
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda text: [float(len(text))])
    monkeypatch.setattr(knowledge_indexer, "_qdrant_request", fake_qdrant)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())

    indexed: set[tuple[str, knowledge_indexer.DocumentVersion]] = set()

    # (a) Transient OSError during fingerprint records UNKNOWN_MTIME so later deletion removes Qdrant points.
    doc_a = docs / "ephemeral.md"
    doc_a.write_text("ephemeral body", encoding="utf-8")

    def raise_eio_for_target(target: Path) -> Any:
        def _inner(path: str) -> tuple[int, int, str]:
            if Path(path) == target:
                raise OSError(errno.EIO, "transient input/output error")
            return real_doc_version(path)

        return _inner

    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_eio_for_target(doc_a))
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert (str(doc_a), knowledge_indexer.UNKNOWN_MTIME) in indexed
    assert any(pt["payload"]["path"] == str(doc_a) for pt in points.values())

    doc_a.unlink()
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert not any(pt["payload"]["path"] == str(doc_a) for pt in points.values())
    assert all(k[0] != str(doc_a) for k in indexed)
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # (b) Surviving file after transient EIO upgrades UNKNOWN_MTIME to real (mtime_ns, size, sha256) and idles.
    doc_b = docs / "surviving.md"
    doc_b.write_text("surviving initial", encoding="utf-8")
    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_eio_for_target(doc_b))
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert (str(doc_b), knowledge_indexer.UNKNOWN_MTIME) in indexed

    monkeypatch.setattr(knowledge_indexer, "_document_version", real_doc_version)
    expected_b_ver = real_doc_version(str(doc_b))
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert (str(doc_b), expected_b_ver) in indexed
    assert (str(doc_b), knowledge_indexer.UNKNOWN_MTIME) not in indexed
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # (c) Seeded UNKNOWN_MTIME entry still re-indexes on another EIO rather than short-circuiting.
    doc_c = docs / "seeded_unknown.md"
    doc_c.write_text("doc c refreshed", encoding="utf-8")
    indexed.add((str(doc_c), knowledge_indexer.UNKNOWN_MTIME))
    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_eio_for_target(doc_c))
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert (str(doc_c), knowledge_indexer.UNKNOWN_MTIME) in indexed
    assert any(
        pt["payload"]["path"] == str(doc_c) and pt["payload"]["text"] == "doc c refreshed" for pt in points.values()
    )
