"""Regression tests for tri-council review round 50.

1. Two-phase legacy migration in ``node_state._adopt()`` stages the entire legacy set
   (including SQLite ``-wal``/``-shm``/``-journal`` sidecars) before updating destination
   files and preserves all legacy sources until ``paths.wallet`` is published.
2. Periodic Qdrant reconciliation in ``knowledge_indexer.run_worker()`` continues advancing
   ``polls_since_verify`` even when ``indexed_files`` is empty so restored snapshots cannot
   reintroduce deleted documents indefinitely.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import sqlite3
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet


def test_round50_interrupted_wal_ledger_migration_preserves_uncheckpointed_wal_credits(
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
    legacy_wallet = NodeWallet.create(node_id="r50-wal-cli")
    legacy_wallet_path = tmp_cli / cli_set.wallet
    legacy_wallet.save_to_file(str(legacy_wallet_path))
    os.chmod(legacy_wallet_path, 0o600)

    legacy_ledger_path = tmp_cli / cli_set.ledger
    legacy_ledger = Ledger(db_path=str(legacy_ledger_path))

    # Hold a concurrent WAL read snapshot at 0 transactions so the 12.5-credit mint stays strictly in -wal.
    reader = sqlite3.connect(str(legacy_ledger_path))
    reader.execute("BEGIN")
    assert reader.execute("SELECT COUNT(*) FROM transactions").fetchone() == (0,)
    legacy_ledger.mint(legacy_wallet.address, 12.5, memo="R50_WAL_ONLY_CREDITS")

    wal_path = tmp_cli / f"{cli_set.ledger}-wal"
    assert wal_path.exists()
    db_bytes = legacy_ledger_path.read_bytes()
    wal_bytes = wal_path.read_bytes()
    reader.close()

    # Restore the exact uncheckpointed db + wal bytes on disk (without auto-checkpointed main db).
    legacy_ledger_path.write_bytes(db_bytes)
    wal_path.write_bytes(wal_bytes)
    for side in ("-shm", "-journal"):
        p = tmp_cli / f"{cli_set.ledger}{side}"
        if p.exists():
            p.unlink()
    os.chmod(legacy_ledger_path, 0o600)
    os.chmod(wal_path, 0o600)

    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "only.db").write_bytes(db_bytes)
    assert Ledger(db_path=str(probe_dir / "only.db")).get_balance(legacy_wallet.address) == 0.0

    # Empty destination ledger + stale -journal + EIO failure after -wal replacement.
    Ledger(db_path=str(state_dir / node_state.NODE_LEDGER_FILENAME))
    stale_journal = state_dir / f"{node_state.NODE_LEDGER_FILENAME}-journal"
    stale_journal.write_bytes(b"stale journal bytes")

    real_replace = node_state.os.replace
    fail_once = {"armed": True}

    def fail_main_db_replace(src: str, dst: str) -> None:
        if fail_once["armed"] and dst.endswith(node_state.NODE_LEDGER_FILENAME):
            fail_once["armed"] = False
            raise OSError(errno.EIO, "simulated main db replace failure")
        real_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(node_state.os, "replace", fail_main_db_replace)
        with pytest.raises(OSError, match="simulated main db replace failure"):
            node_state.migrate_legacy_node_state()

    # Source legacy ledger retains all 12.5 uncheckpointed WAL credits (folded into main DB on snapshot close) for retry.
    assert legacy_ledger_path.exists()
    probe_retry = tmp_path / "probe_retry.db"
    probe_retry.write_bytes(legacy_ledger_path.read_bytes())
    assert Ledger(db_path=str(probe_retry)).get_balance(legacy_wallet.address) == 12.5
    assert (state_dir / node_state._MIGRATING_FILENAME).exists()

    # Retry succeeds and preserves all 12.5 uncheckpointed WAL credits while removing stale -journal.
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not wal_path.exists()
    assert not stale_journal.exists()
    assert not (state_dir / node_state._MIGRATING_FILENAME).exists()
    identity = node_state.get_node_identity()
    assert identity.wallet.address == legacy_wallet.address
    assert identity.balance == 12.5
    assert identity.ledger.verify_chain() is True
    assert node_state.migrate_legacy_node_state() is None


def test_round50_worker_periodic_reconciliation_purges_restored_points_when_local_index_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    only = docs / "only.md"
    only.write_text("ephemeral secret doc", encoding="utf-8")

    points: dict[str, dict[str, Any]] = {}
    saved_snapshot: dict[str, dict[str, Any]] = {}
    coll_url = f"{knowledge_indexer.QDRANT_URL}/collections/{knowledge_indexer.COLLECTION_NAME}"

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

    def fake_urlopen(req: Any, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        url = req.full_url
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else None
        if url == f"{knowledge_indexer.OLLAMA_URL}/api/embeddings":
            return _FakeResp({"embedding": [0.1] * knowledge_indexer.EMBEDDING_DIM})
        if url == coll_url and method == "GET":
            return _FakeResp({"result": {"points_count": len(points)}, "status": "ok"})
        if url.startswith(f"{coll_url}/points/scroll") and method == "POST":
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
            must = (payload or {}).get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            keep = {
                hid for cond in (payload or {}).get("filter", {}).get("must_not", []) for hid in cond.get("has_id", [])
            }
            for pid in [k for k, v in points.items() if v["payload"]["path"] in paths and k not in keep]:
                del points[pid]
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"})
        if url.startswith(f"{coll_url}/points") and method == "PUT":
            for pt in (payload or {}).get("points", []):
                points[str(pt["id"])] = pt
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"})
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    mode_path = tmp_path / "no_mode"
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ())
    monkeypatch.setattr(knowledge_indexer, "MODE_FILE", str(mode_path))
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_path))
    monkeypatch.setattr(knowledge_indexer, "COLLECTION_VERIFY_IDLE_POLLS", 2)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    poll = 0

    def on_sleep(_secs: float) -> None:
        nonlocal poll
        poll += 1
        if poll == 1:
            assert len(points) == 1
            saved_snapshot.update(copy.deepcopy(points))
            only.unlink()
        elif poll == 2:
            # Last document deleted; local indexed_files is now empty.
            assert points == {}
        elif poll == 3:
            # Simulate an older Qdrant snapshot restored while indexed_files is empty.
            points.update(copy.deepcopy(saved_snapshot))
        elif poll == 4:
            # 2nd idle poll with empty indexed_files must schedule Qdrant re-verification.
            assert len(points) == 1
        elif poll == 5:
            # Periodic reconciliation scrolled Qdrant despite empty indexed_files and removed restored point.
            assert points == {}
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", on_sleep)
    with pytest.raises(KeyboardInterrupt):
        knowledge_indexer.run_worker()
