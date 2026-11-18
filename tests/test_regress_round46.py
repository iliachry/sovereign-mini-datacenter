"""Regression tests for Round 46 (partial wipe recovery & PQC self-test scoring)."""

from __future__ import annotations

import builtins
import json
import os
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.mcp.tools import tool_run_security_audit
from sovereign_dc.security.pqc import PQCKEM, PQCSigner


def test_round46_partial_wipe_or_repopulated_collection_recovers_missing_unchanged_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_worker reconciles sources on periodic verification so partial wipes still rebuild unchanged files."""
    # Edge branches of _file_has_chunks: oversized, vanished, and transient unreadable OSError.
    big = tmp_path / "big.md"
    big.write_text("too large", encoding="utf-8")
    with monkeypatch.context() as mc:
        mc.setattr(knowledge_indexer, "MAX_FILE_BYTES", 4)
        assert knowledge_indexer._file_has_chunks(str(big)) is False
    assert knowledge_indexer._file_has_chunks(str(tmp_path / "missing.md")) is False
    small = tmp_path / "perm.md"
    small.write_text("readable", encoding="utf-8")
    with monkeypatch.context() as mc:

        def _deny_open(*_args: Any, **_kwargs: Any) -> Any:
            raise PermissionError("denied")

        mc.setattr(builtins, "open", _deny_open)
        assert knowledge_indexer._file_has_chunks(str(small)) is True

    docs = tmp_path / "docs"
    docs.mkdir()
    doc_a = docs / "a.md"
    doc_a.write_text("alpha", encoding="utf-8")

    collection_exists = True
    points: dict[str, dict[str, Any]] = {}
    upserted_in_poll: set[str] = set()
    fail_legacy_delete = False

    class _FakeResp:
        def __init__(self, body: dict[str, Any], status: int = 200) -> None:
            self._raw = json.dumps(body).encode("utf-8")
            self.status = status

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float = 0) -> _FakeResp:
        nonlocal collection_exists, fail_legacy_delete
        del timeout
        url = req.full_url
        method = req.get_method()
        raw_data = req.data.decode("utf-8") if req.data else None
        payload = json.loads(raw_data) if raw_data else None

        if "/api/embeddings" in url:
            return _FakeResp({"embedding": [0.1] * knowledge_indexer.EMBEDDING_DIM})

        coll_url = f"{knowledge_indexer.QDRANT_URL}/collections/{knowledge_indexer.COLLECTION_NAME}"
        if url == coll_url and method == "GET":
            if not collection_exists:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            return _FakeResp({"result": {"points_count": len(points)}, "status": "ok"})
        if url == coll_url and method == "PUT":
            collection_exists = True
            return _FakeResp({"result": True, "status": "ok"})
        if url == f"{coll_url}/index" and method == "PUT":
            return _FakeResp({"result": True, "status": "ok"})
        if url.startswith(f"{coll_url}/points/scroll") and method == "POST":
            if not collection_exists:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            return _FakeResp(
                {
                    "result": {
                        "points": [
                            {"id": pid, "payload": {"path": pt["payload"]["path"]}} for pid, pt in points.items()
                        ],
                        "next_page_offset": None,
                    },
                    "status": "ok",
                }
            )
        if url.startswith(f"{coll_url}/points/delete") and method == "POST":
            must = (payload or {}).get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            if fail_legacy_delete and any(p.startswith(".") for p in paths):
                raise OSError("simulated legacy delete failure")
            keep_ids = {
                hid for cond in (payload or {}).get("filter", {}).get("must_not", []) for hid in cond.get("has_id", [])
            }
            for pid in list(points):
                if points[pid]["payload"]["path"] in paths and pid not in keep_ids:
                    del points[pid]
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"})
        if url.startswith(f"{coll_url}/points") and method == "PUT":
            for pt in (payload or {}).get("points", []):
                points[str(pt["id"])] = pt
                upserted_in_poll.add(str(pt["payload"]["path"]))
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"})
        raise AssertionError(f"Unexpected urlopen call: {method} {url}")

    mode_path = tmp_path / "no_mode"
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "MODE_FILE", str(mode_path))
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_path))
    monkeypatch.setattr(knowledge_indexer, "COLLECTION_VERIFY_IDLE_POLLS", 3)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # Exercise legacy relative-path cleanup failure path before populating worker corpus.
    legacy_rel = os.path.join(".", "docs", "a.md")
    with monkeypatch.context() as mc:
        mc.chdir(tmp_path)
        fail_legacy_delete = True
        assert knowledge_indexer.scan_and_index({(legacy_rel, knowledge_indexer.UNKNOWN_MTIME)}) == (1, 1)
        assert legacy_rel in knowledge_indexer.pending_reconcile_paths()
        fail_legacy_delete = False
    points.clear()
    knowledge_indexer._cleanup_pending.clear()
    knowledge_indexer._reconcile_pending.clear()

    doc_b = docs / "b.md"
    doc_empty = docs / "empty.md"
    doc_b.write_text("beta unchanged", encoding="utf-8")
    doc_empty.write_text("   \n", encoding="utf-8")

    def current_sources() -> set[str]:
        return {pt["payload"]["path"] for pt in points.values()}

    poll = 0

    def on_sleep(_secs: float) -> None:
        nonlocal poll
        poll += 1
        if poll == 1:
            assert current_sources() == {str(doc_a), str(doc_b)}
            # Wipe collection in-place (collection_exists=True) and modify ONLY a.md before poll 2.
            points.clear()
            doc_a.write_text("alpha v2", encoding="utf-8")
        elif poll == 2:
            # Poll 2 repopulated Qdrant with a.md only (non-empty points_count=1); modify a.md again!
            assert current_sources() == {str(doc_a)}
            assert len(points) == 1
            doc_a.write_text("alpha v3", encoding="utf-8")
        elif poll == 3:
            # Poll 3 updated a.md and reached COLLECTION_VERIFY_IDLE_POLLS=3 -> next poll re-verifies sources.
            assert current_sources() == {str(doc_a)}
            upserted_in_poll.clear()
            mode_path.write_text("BALANCED", encoding="utf-8")
        elif poll == 4:
            # Poll 4 reconciled against list_indexed_sources() and rebuilt ONLY missing non-empty b.md.
            assert current_sources() == {str(doc_a), str(doc_b)}
            assert upserted_in_poll == {str(doc_b)}
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", on_sleep)
    with pytest.raises(KeyboardInterrupt):
        knowledge_indexer.run_worker()


def test_round46_run_security_audit_reports_exact_self_test_pass_percentage() -> None:
    """tool_run_security_audit reports 100.0, 50.0, or 0.0 matching passed self-tests."""
    res_all = tool_run_security_audit({})
    assert res_all["pqc_self_tests_total"] == 2
    assert res_all["pqc_self_tests_passed"] == 2
    assert res_all["compliance_score_percent"] == 100.0

    with patch.object(PQCSigner, "verify", return_value=False):
        res_sig_fail = tool_run_security_audit({})
    assert res_sig_fail["pqc_self_tests_passed"] == 1
    assert res_sig_fail["compliance_score_percent"] == 50.0

    with patch.object(PQCKEM, "decapsulate", return_value=b""):
        res_kem_fail = tool_run_security_audit({})
    assert res_kem_fail["pqc_self_tests_passed"] == 1
    assert res_kem_fail["compliance_score_percent"] == 50.0

    with patch.object(PQCSigner, "verify", return_value=False), patch.object(PQCKEM, "decapsulate", return_value=b""):
        res_both_fail = tool_run_security_audit({})
    assert res_both_fail["pqc_self_tests_passed"] == 0
    assert res_both_fail["compliance_score_percent"] == 0.0
    assert res_both_fail["score_basis"] == "pqc_self_tests_only"
