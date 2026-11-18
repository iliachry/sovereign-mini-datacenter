"""Regression tests for Round 45 (failed removal cache invalidation, MCP alert dispatch, collection recovery)."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.mcp.server import MCPServer


def test_round45_failed_removal_invalidates_cached_fingerprint_when_document_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed remove_file downgrades cached fingerprint so a restored unchanged file re-indexes."""
    points: dict[Any, dict[str, Any]] = {}
    lose_delete_response = False

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
                if points[pid]["payload"]["path"] in paths and pid not in keep_ids:
                    del points[pid]
            if lose_delete_response:
                raise TimeoutError("lost response after Qdrant committed delete")
            return {"status": "ok"}
        raise AssertionError(f"Unexpected Qdrant call: {method} {endpoint}")

    docs = tmp_path / "docs"
    staging = tmp_path / "staging"
    docs.mkdir()
    staging.mkdir()

    doc = docs / "runbook.md"
    moved = staging / "runbook.md"
    doc.write_text("critical runbook content", encoding="utf-8")

    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda text: [float(len(text))])
    monkeypatch.setattr(knowledge_indexer, "_qdrant_request", fake_qdrant)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())

    indexed: set[tuple[str, knowledge_indexer.DocumentVersion]] = set()
    real_ver = knowledge_indexer._document_version(str(doc))

    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert len(points) == 1
    assert (str(doc), real_ver) in indexed

    # Move outside WATCH_DIR while Qdrant commits delete but drops the response.
    doc.rename(moved)
    lose_delete_response = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert len(points) == 0
    assert (str(doc), real_ver) not in indexed
    assert (str(doc), knowledge_indexer.UNKNOWN_MTIME) in indexed

    # Move the identical file back (same mtime, size, hash) and restore healthy Qdrant.
    moved.rename(doc)
    lose_delete_response = False
    assert knowledge_indexer._document_version(str(doc)) == real_ver
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert len(points) == 1
    assert (str(doc), real_ver) in indexed
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # Permanent removal still retries cleanly if file remains deleted after a failed delete.
    doc.unlink()
    lose_delete_response = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert (str(doc), knowledge_indexer.UNKNOWN_MTIME) in indexed
    lose_delete_response = False
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert len(points) == 0
    assert indexed == set()
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)


def test_round45_mcp_dispatch_technician_alert_reports_false_when_all_channels_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispatch_technician_alert reports dispatched=False when every notifier channel fails."""
    blocked_file = tmp_path / "not_a_dir"
    blocked_file.write_text("file blocking directory creation", encoding="utf-8")

    monkeypatch.setenv("SOVEREIGN_LOG_DIR", str(blocked_file))
    monkeypatch.setenv("DTN_DB_PATH", str(blocked_file / "dtn.db"))
    monkeypatch.delenv("MQTT_BROKER", raising=False)
    monkeypatch.delenv("LORA_DEVICE", raising=False)

    server = MCPServer()
    req_fail = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "dispatch_technician_alert",
            "arguments": {"message": "Battery critical", "severity": "CRITICAL"},
        },
    }
    res_fail = server.handle_request(req_fail)
    assert res_fail["result"]["isError"] is False
    content_fail = json.loads(res_fail["result"]["content"][0]["text"])
    assert content_fail["channels"]
    assert all(ok is False for ok in content_fail["channels"].values())
    assert content_fail["dispatched"] is False

    monkeypatch.setenv("SOVEREIGN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "dtn" / "dtn.db"))
    req_ok = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "dispatch_technician_alert",
            "arguments": {"message": "Battery recovered", "severity": "INFO"},
        },
    }
    res_ok = server.handle_request(req_ok)
    assert res_ok["result"]["isError"] is False
    content_ok = json.loads(res_ok["result"]["content"][0]["text"])
    assert any(content_ok["channels"].values())
    assert content_ok["dispatched"] is True


def test_round45_worker_rebuilds_index_when_qdrant_collection_is_dropped_or_wiped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_worker detects dropped or wiped collections and rebuilds all documents including unchanged ones."""
    docs = tmp_path / "docs"
    docs.mkdir()
    doc_a = docs / "a.md"
    doc_b = docs / "b.md"
    doc_a.write_text("alpha initial", encoding="utf-8")
    doc_b.write_text("beta unchanged", encoding="utf-8")

    collection_exists = False
    points: dict[str, dict[str, Any]] = {}

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
        nonlocal collection_exists
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
            if not collection_exists:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            return _FakeResp({"result": True, "status": "ok"})
        if url.startswith(f"{coll_url}/points/scroll") and method == "POST":
            if not collection_exists:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            return _FakeResp(
                {
                    "result": {
                        "points": [{"id": pid, "payload": pt["payload"]} for pid, pt in points.items()],
                        "next_page_offset": None,
                    },
                    "status": "ok",
                }
            )
        if url.startswith(f"{coll_url}/points/delete") and method == "POST":
            if not collection_exists:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            must = (payload or {}).get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            keep_ids = {
                hid for cond in (payload or {}).get("filter", {}).get("must_not", []) for hid in cond.get("has_id", [])
            }
            for pid in list(points):
                if points[pid]["payload"]["path"] in paths and pid not in keep_ids:
                    del points[pid]
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"})
        if url.startswith(f"{coll_url}/points") and method == "PUT":
            if not collection_exists:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            for pt in (payload or {}).get("points", []):
                points[str(pt["id"])] = pt
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"})
        raise AssertionError(f"Unexpected urlopen call: {method} {url}")

    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "MODE_FILE", str(tmp_path / "no_mode"))
    monkeypatch.delenv("SOVEREIGN_MODE_FILE", raising=False)
    monkeypatch.setattr(knowledge_indexer, "COLLECTION_VERIFY_IDLE_POLLS", 2)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())
    coll_url = f"{knowledge_indexer.QDRANT_URL}/collections/{knowledge_indexer.COLLECTION_NAME}"
    monkeypatch.setattr("urllib.request.urlopen", lambda _req, timeout=0: _FakeResp({}, status=204))
    assert knowledge_indexer.ensure_qdrant_collection() is False

    err_503 = urllib.error.HTTPError(coll_url, 503, "Unavailable", {}, None)  # type: ignore[arg-type]
    err_404 = urllib.error.HTTPError(coll_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
    try:

        def _raise_503(_req: Any, timeout: float = 0) -> _FakeResp:
            raise err_503

        monkeypatch.setattr("urllib.request.urlopen", _raise_503)
        assert knowledge_indexer.ensure_qdrant_collection() is False

        def _fail_create(req: Any, timeout: float = 0) -> _FakeResp:
            if req.get_method() == "GET":
                raise err_404
            raise OSError("disk full")

        monkeypatch.setattr("urllib.request.urlopen", _fail_create)
        assert knowledge_indexer.ensure_qdrant_collection() is False
    finally:
        err_503.close()
        err_404.close()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    def current_sources() -> set[str]:
        return {pt["payload"]["path"] for pt in points.values()}

    poll = 0

    def on_sleep(_secs: float) -> None:
        nonlocal poll, collection_exists
        poll += 1
        if poll == 1:
            assert current_sources() == {str(doc_a), str(doc_b)}
            # (b) Drop collection and modify ONLY a.md -> next poll 404s, following poll rebuilds BOTH a.md and b.md.
            collection_exists = False
            points.clear()
            doc_a.write_text("alpha updated v2", encoding="utf-8")
        elif poll == 2:
            assert current_sources() == set()
        elif poll == 3:
            assert current_sources() == {str(doc_a), str(doc_b)}
            # (a) Drop collection with ZERO file changes -> after 2 idle polls (4 & 5), poll 6 recreates & rebuilds.
            collection_exists = False
            points.clear()
        elif poll == 6:
            assert current_sources() == {str(doc_a), str(doc_b)}
            # (c) Wipe points in-place (collection exists with points_count=0) -> after 2 idle polls (7 & 8), poll 9 rebuilds.
            points.clear()
        elif poll == 9:
            assert current_sources() == {str(doc_a), str(doc_b)}
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", on_sleep)
    with pytest.raises(KeyboardInterrupt):
        knowledge_indexer.run_worker()
