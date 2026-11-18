"""Regression tests for tri-council review round 48.

1. When ``os.link`` is unavailable during ``NodeWallet.save_to_file(..., overwrite=False)``
   and the fallback exclusive open fails mid-write, the partial keystore is unlinked so
   subsequent ``load_node_wallet()`` calls retry cleanly instead of failing with JSONDecodeError.
2. ``_expected_point_ids()`` returns ``set()`` for readable empty documents so periodic Qdrant
   reconciliation via ``seed_indexed_sources()`` detects and purges unexpected stale points.
"""

from __future__ import annotations

import errno
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.economy.node_state import load_node_wallet


def test_round48_no_overwrite_link_fallback_cleans_up_partial_keystore_so_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))

    def unsupported_link(_src: str, _dst: str) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr("sovereign_dc.economy.wallet.os.link", unsupported_link)

    real_dump = json.dump
    dump_calls = {"n": 0}

    def flaky_dump(obj: Any, fp: Any, **kwargs: Any) -> None:
        dump_calls["n"] += 1
        if dump_calls["n"] == 1:
            real_dump(obj, fp, **kwargs)
            return
        fp.write('{"partial":')
        fp.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as mp:
        mp.setattr("sovereign_dc.economy.wallet.json.dump", flaky_dump)
        with pytest.raises(OSError, match="No space left on device"):
            load_node_wallet()

    assert dump_calls["n"] == 2
    assert not (state_dir / "smdc_node_wallet.json").exists()
    assert not list(state_dir.glob("*.tmp.*"))

    # Storage space recovers while os.link remains unsupported: fallback now publishes cleanly.
    w1 = load_node_wallet()
    wallet_file = state_dir / "smdc_node_wallet.json"
    assert wallet_file.exists()
    assert stat.S_IMODE(wallet_file.stat().st_mode) == 0o600
    assert not list(state_dir.glob("*.tmp.*"))
    assert load_node_wallet().address == w1.address


def test_round48_qdrant_reconciliation_purges_stale_points_for_cached_empty_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "note.md"
    doc.write_text("obsolete draft chunk", encoding="utf-8")

    points: dict[str, dict[str, Any]] = {}

    def fake_qdrant(method: str, suffix: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        if suffix.startswith("/points/delete") and body is not None:
            must = body.get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            keep = {hid for cond in body.get("filter", {}).get("must_not", []) for hid in cond.get("has_id", [])}
            for pid in [
                k
                for k, v in points.items()
                if v["payload"].get(knowledge_indexer.SOURCE_PATH_FIELD) in paths and k not in keep
            ]:
                del points[pid]
        elif suffix.startswith("/points/scroll"):
            return {
                "result": {
                    "points": [{"id": pid, "payload": pt["payload"]} for pid, pt in points.items()],
                    "next_page_offset": None,
                },
                "status": "ok",
            }
        elif method == "PUT" and suffix.startswith("/points") and body is not None:
            for pt in body.get("points", []):
                points[str(pt["id"])] = {"id": str(pt["id"]), "payload": pt["payload"]}
        return {"result": {}, "status": "ok"}

    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ())
    monkeypatch.setattr(knowledge_indexer, "_collection_created", True)
    monkeypatch.setattr(knowledge_indexer, "_qdrant_request", fake_qdrant)
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda _text: [0.1, 0.2])

    indexed: set[tuple[str, Any]] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert len(points) == 1

    # Emptying the document deletes its Qdrant points and caches its empty version.
    doc.write_text("", encoding="utf-8")
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert points == {}
    assert knowledge_indexer._expected_point_ids(str(doc)) == set()
    cached_entry = next(entry for entry in indexed if entry[0] == str(doc))
    assert cached_entry[1] != knowledge_indexer.UNKNOWN_MTIME

    # Restore an obsolete point into Qdrant (e.g. snapshot restore) without touching note.md.
    stale_id = knowledge_indexer.chunk_point_id(str(doc), 0)
    points[stale_id] = {
        "id": stale_id,
        "payload": {knowledge_indexer.SOURCE_PATH_FIELD: str(doc), "text": "obsolete draft chunk"},
    }

    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert (str(doc), knowledge_indexer.UNKNOWN_MTIME) in indexed
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert points == {}

    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
