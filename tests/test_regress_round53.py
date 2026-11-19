"""Regression tests for tri-council review round 53.

1. ``_has_established_ledger()`` recovers a disposable copy when ``mode=ro`` hits
   ``SQLITE_READONLY_ROLLBACK`` so a hot rollback journal on an established canonical ledger is
   never misclassified as empty and overwritten.
2. Legacy ledger adoption stages a consistent single-file ``VACUUM INTO`` snapshot under
   ``BEGIN IMMEDIATE`` with post-snapshot state-digest verification and coordinated retirement, so
   concurrent commits/checkpoints cannot mix WAL and main DB states or lose committed credits.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet


def test_round53_hot_rollback_journal_on_canonical_ledger_is_preserved_and_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths = node_state.node_state_paths()
    custom_wallet = NodeWallet.create(node_id="r53-custom")
    est_ledger = Ledger(db_path=paths.ledger)
    est_ledger.mint(custom_wallet.address, 500.0, memo="R53_ESTABLISHED")
    del est_ledger

    # Switch to DELETE journal mode and simulate a crashed writer leaving a genuine hot rollback journal.
    conn = sqlite3.connect(paths.ledger)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA cache_size=1")
    conn.execute("BEGIN EXCLUSIVE")
    conn.execute("CREATE TABLE _r53_spill (pad TEXT)")
    for i in range(60):
        conn.execute("INSERT INTO _r53_spill (pad) VALUES (?)", (f"{i:04d}-" + "x" * 200,))
    hot_journal = Path(f"{paths.ledger}-journal")
    assert hot_journal.exists() and hot_journal.stat().st_size > 0
    saved_db = Path(paths.ledger).read_bytes()
    saved_journal = hot_journal.read_bytes()
    conn.rollback()
    conn.close()

    Path(paths.ledger).write_bytes(saved_db)
    hot_journal.write_bytes(saved_journal)

    # Confirm an independent recovery of the same pair preserves all 500 credits.
    verify_dir = tmp_path / "verify_copy"
    verify_dir.mkdir()
    copy_db = verify_dir / "copy.db"
    copy_db.write_bytes(saved_db)
    Path(f"{copy_db}-journal").write_bytes(saved_journal)
    assert Ledger(db_path=str(copy_db)).get_balance(custom_wallet.address) == 500.0

    cli_set = node_state.LEGACY_SETS[0]
    legacy_wallet = NodeWallet.create(node_id="r53-cli")
    legacy_wallet.save_to_file(str(state_dir / cli_set.wallet))
    Ledger(db_path=str(state_dir / cli_set.ledger)).mint(legacy_wallet.address, 1.0, memo="R53_LEG")

    # Migration detects the established hot-journal ledger, adopts only the wallet, and leaves the
    # original canonical ledger + hot journal untouched for normal recovery.
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert hot_journal.exists()
    assert not (state_dir / node_state._MIGRATING_FILENAME).exists()
    assert (state_dir / cli_set.ledger).exists()
    assert node_state.open_node_ledger().get_balance(custom_wallet.address) == 500.0


def test_round53_legacy_ledger_migration_uses_consistent_snapshot_and_blocks_interleaved_commits(
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
    legacy_wallet = NodeWallet.create(node_id="r53-legacy-cli")
    legacy_wallet_path = tmp_cli / cli_set.wallet
    legacy_wallet.save_to_file(str(legacy_wallet_path))
    os.chmod(legacy_wallet_path, 0o600)

    legacy_ledger_path = tmp_cli / cli_set.ledger
    legacy_ledger = Ledger(db_path=str(legacy_ledger_path))
    legacy_ledger.mint(legacy_wallet.address, 12.5, memo="R53_BASE")
    os.chmod(legacy_ledger_path, 0o600)

    # 1) Inject an interleaved commit (+2.5 -> 15.0) + PASSIVE WAL checkpoint right after the
    # first VACUUM INTO snapshot completes (before BEGIN IMMEDIATE) to prove the state-digest retry
    # detects the change and re-snapshots all 15.0 credits.
    # 2) Hold a concurrent publish pause after ledger publication so an active legacy writer
    # attempting a late +2.5 mint cannot commit silently before retirement.
    paths = node_state.node_state_paths()
    real_chmod = node_state.os.chmod
    real_publish = node_state._publish_staged_item
    snapshot_prefix = os.path.join(str(state_dir), f".{node_state.NODE_LEDGER_FILENAME}.adopt.")
    interleave_armed = {"armed": True}
    ledger_published = threading.Event()
    writer_started = threading.Event()
    release_wallet = threading.Event()
    writer_errors: list[Exception] = []

    def interleave_on_snapshot_chmod(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        real_chmod(path, mode, *args, **kwargs)
        if interleave_armed["armed"] and str(path).startswith(snapshot_prefix):
            interleave_armed["armed"] = False
            legacy_ledger.mint(legacy_wallet.address, 2.5, memo="R53_MID_CHECKPOINT")
            with contextlib.closing(sqlite3.connect(str(legacy_ledger_path))) as ckpt_conn:
                ckpt_conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def paused_publish(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
        real_publish(tmp_dst, dst, overwrite=overwrite)
        if dst == paths.ledger:
            ledger_published.set()
            assert writer_started.wait(timeout=5.0)
            assert release_wallet.wait(timeout=5.0)

    def concurrent_legacy_writer() -> None:
        assert ledger_published.wait(timeout=5.0)
        writer_started.set()
        threading.Timer(0.15, release_wallet.set).start()
        try:
            legacy_ledger.mint(legacy_wallet.address, 2.5, memo="R53_LATE")
        except Exception as exc:  # pragma: no cover - defensive capture
            writer_errors.append(exc)

    writer_thread = threading.Thread(target=concurrent_legacy_writer)
    writer_thread.start()
    try:
        with monkeypatch.context() as mp:
            mp.setattr(node_state.os, "chmod", interleave_on_snapshot_chmod)
            mp.setattr(node_state, "_publish_staged_item", paused_publish)
            assert node_state.migrate_legacy_node_state() == "CLI"
    finally:
        writer_thread.join(timeout=10.0)

    assert interleave_armed["armed"] is False
    assert len(writer_errors) == 1
    assert "legacy ledger migrated" in str(writer_errors[0])
    identity = node_state.get_node_identity()
    assert identity.balance == 15.0
    assert identity.ledger.verify_chain() is True
    assert not legacy_ledger_path.exists()
    for suffix in node_state._SQLITE_SIDECARS:
        assert not (tmp_cli / f"{cli_set.ledger}{suffix}").exists()
