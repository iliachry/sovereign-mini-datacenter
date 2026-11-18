"""Regression tests for tri-council review round 49.

1. Cross-filesystem legacy migration stages and SHA-256-verifies files inside the private
   state directory, uses an interrupted-migration marker and ``_has_established_ledger()``,
   and recovers cleanly over an empty/partial destination ledger without losing legacy credits.
2. Periodic Qdrant reconciliation compares stored ``chunk_sha256`` digests alongside point
   IDs so restored Qdrant snapshots with matching IDs and stale text are repaired.
"""

from __future__ import annotations

import copy
import errno
import os
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet


def test_round49_cross_fs_legacy_migration_stages_ledger_and_recovers_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_cli = tmp_path / "cli_tmp"
    tmp_cli.mkdir()
    os.chmod(tmp_cli, 0o700)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)

    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: str(tmp_cli))

    cli_set = node_state.LEGACY_SETS[0]
    legacy_wallet = NodeWallet.create(node_id="r49-legacy-cli")
    legacy_wallet_path = tmp_cli / cli_set.wallet
    legacy_wallet.save_to_file(str(legacy_wallet_path))
    os.chmod(legacy_wallet_path, 0o600)

    legacy_ledger_path = tmp_cli / cli_set.ledger
    legacy_ledger = Ledger(db_path=str(legacy_ledger_path))
    legacy_ledger.mint(legacy_wallet.address, 12.5, memo="R49_LEGACY_CREDITS")
    os.chmod(legacy_ledger_path, 0o600)

    # Exercise _has_established_ledger() defensive branches directly.
    assert node_state._has_established_ledger(str(state_dir / "absent.db")) is False
    non_sqlite = state_dir / "non_sqlite.db"
    non_sqlite.write_bytes(b"not sqlite" * 20)
    assert node_state._has_established_ledger(str(non_sqlite)) is False
    trunc_sqlite = state_dir / "trunc_sqlite.db"
    trunc_sqlite.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    assert node_state._has_established_ledger(str(trunc_sqlite)) is False
    empty_db = state_dir / "empty.db"
    Ledger(db_path=str(empty_db))
    assert node_state._has_established_ledger(str(empty_db)) is False
    assert node_state._has_established_ledger(str(legacy_ledger_path)) is True

    # Simulate cross-filesystem migration where staging copy hits ENOSPC.
    def cross_fs_rename(_src: str, _dst: str) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    def failing_copy(src_fp: Any, dst_fp: Any) -> None:
        dst_fp.write(src_fp.read(32))
        dst_fp.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(node_state.os, "rename", cross_fs_rename)
    with monkeypatch.context() as mp:
        mp.setattr(node_state.shutil, "copyfileobj", failing_copy)
        with pytest.raises(OSError, match="No space left on device"):
            node_state.migrate_legacy_node_state()

    assert not (state_dir / node_state.NODE_LEDGER_FILENAME).exists()
    assert not (state_dir / node_state.NODE_WALLET_FILENAME).exists()
    assert legacy_wallet_path.exists()
    assert legacy_ledger_path.exists()
    marker_path = state_dir / node_state._MIGRATING_FILENAME
    assert not marker_path.exists()
    assert not list(state_dir.glob("*.adopt.*"))

    # Even if an unestablished 0-tx ledger exists at destination and os.link is unavailable, retry recovers.
    Ledger(db_path=str(state_dir / node_state.NODE_LEDGER_FILENAME))
    monkeypatch.setattr(
        node_state.os, "link", lambda _s, _d: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, "no link"))
    )

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not marker_path.exists()
    assert not legacy_wallet_path.exists()
    assert not legacy_ledger_path.exists()
    identity = node_state.get_node_identity()
    assert identity.wallet.address == legacy_wallet.address
    assert identity.balance == 12.5
    assert identity.ledger.verify_chain() is True


def test_round49_qdrant_reconciliation_detects_stale_chunk_contents_with_matching_point_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "account.md"
    doc.write_text("Approved account number OLD-111", encoding="utf-8")

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
    old_points = copy.deepcopy(points)

    # Update content with same 1-chunk count (same point ID) and re-index cleanly.
    doc.write_text("Approved account number NEW-222", encoding="utf-8")
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    pid = knowledge_indexer.chunk_point_id(str(doc), 0)
    assert points[pid]["payload"]["text"] == "Approved account number NEW-222"

    # Restore older snapshot keeping identical point IDs but obsolete OLD-111 content/hash.
    points.clear()
    points.update(copy.deepcopy(old_points))
    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert (str(doc), knowledge_indexer.UNKNOWN_MTIME) in indexed
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert points[pid]["payload"]["text"] == "Approved account number NEW-222"

    # Also repair legacy points whose payload lacks chunk_sha256 entirely.
    points[pid]["payload"] = {knowledge_indexer.SOURCE_PATH_FIELD: str(doc), "text": "Approved account number OLD-000"}
    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert (str(doc), knowledge_indexer.UNKNOWN_MTIME) in indexed
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert points[pid]["payload"]["text"] == "Approved account number NEW-222"

    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
