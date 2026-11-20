"""Regression tests for tri-council review round 55.

1. Post-wallet migration recovery coordinates destination inspection and in-place SQLite table/index
   synchronization under ``BEGIN EXCLUSIVE`` on ``paths.ledger`` so concurrent canonical ``Ledger``
   transactions committed right before or during recovery are never overwritten or directed onto an
   unlinked inode.
2. Pre-wallet adoption over an unestablished (0-transaction) canonical ledger also verifies and
   replaces contents under ``BEGIN EXCLUSIVE``, aborting cleanly if a concurrent canonical writer
   establishes the ledger first.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet


def test_round55_post_wallet_recovery_preserves_concurrent_canonical_commits_before_and_during_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_set = node_state.LEGACY_SETS[0]
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    def _interrupt_after_wallet(name: str) -> tuple[Path, node_state.NodeStatePaths, NodeWallet, Ledger, Path]:
        sdir = tmp_path / name
        sdir.mkdir()
        os.chmod(sdir, 0o700)
        monkeypatch.setenv("SMDC_STATE_DIR", str(sdir))
        p = node_state.node_state_paths()
        lw = NodeWallet.create(node_id=f"r55-{name}")
        lw.save_to_file(str(sdir / cli_set.wallet))
        lp = sdir / cli_set.ledger
        ll = Ledger(db_path=str(lp))
        ll.mint(lw.address, 12.5, memo=f"R55_{name}_BASE")
        real_retire = node_state._retire_legacy_ledger
        armed = {"flag": True}

        def fail_once(src_conn: sqlite3.Connection, legacy_ledger: str) -> None:
            if armed["flag"]:
                armed["flag"] = False
                raise sqlite3.OperationalError("simulated post-wallet retirement interruption")
            real_retire(src_conn, legacy_ledger)

        with monkeypatch.context() as mp:
            mp.setattr(node_state, "_retire_legacy_ledger", fail_once)
            with pytest.raises(sqlite3.OperationalError, match="interruption"):
                node_state.migrate_legacy_node_state()
        return sdir, p, lw, ll, lp

    # Subcase A: Concurrent canonical mint commits right after legacy snapshot staging, before
    # canonical BEGIN EXCLUSIVE is acquired — recovery sees 62.5 and preserves BOTH ledgers.
    state_a, paths_a, wallet_a, legacy_a, legacy_a_path = _interrupt_after_wallet("subcase_a")
    legacy_a.mint(wallet_a.address, 2.5, memo="R55_A_LEGACY")
    del legacy_a
    canon_a = Ledger(db_path=paths_a.ledger)
    real_stage = node_state._stage_legacy_ledger_snapshot
    late_txs: list[Any] = []
    race_armed = {"flag": True}

    @contextlib.contextmanager
    def stage_then_canonical_mint(*args: Any, **kwargs: Any) -> Iterator[tuple[str, sqlite3.Connection, str]]:
        with real_stage(*args, **kwargs) as staged_tuple:
            if race_armed["flag"]:
                race_armed["flag"] = False
                late_txs.append(canon_a.mint(wallet_a.address, 50.0, memo="ACKNOWLEDGED_CANONICAL_WRITE"))
            yield staged_tuple

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_stage_legacy_ledger_snapshot", stage_then_canonical_mint)
        assert node_state.migrate_legacy_node_state() == "CLI"

    assert len(late_txs) == 1
    assert not (state_a / node_state._MIGRATING_FILENAME).exists()
    assert not (state_a / cli_set.wallet).exists()
    assert legacy_a_path.exists()
    assert canon_a.get_balance(wallet_a.address) == 62.5
    assert canon_a.has_transaction(late_txs[0].tx_id) is True
    assert canon_a.verify_chain() is True
    assert Ledger(db_path=str(legacy_a_path)).get_balance(wallet_a.address) == 15.0

    # Subcase B: Concurrent canonical mint arrives while _replace_sqlite_contents_in_place holds
    # BEGIN EXCLUSIVE — blocks on the same inode, wakes up post-commit, and lands on top of 15.0 (= 65.0).
    state_b, paths_b, wallet_b, legacy_b, legacy_b_path = _interrupt_after_wallet("subcase_b")
    legacy_b.mint(wallet_b.address, 2.5, memo="R55_B_LEGACY")
    del legacy_b
    canon_b = Ledger(db_path=paths_b.ledger)
    real_replace_contents = node_state._replace_sqlite_contents_in_place
    entered_replace = threading.Event()
    writer_started = threading.Event()
    release_replace = threading.Event()
    in_flight_txs: list[Any] = []
    writer_errors: list[Exception] = []

    def paused_replace_contents(src_snapshot: str, dst_conn: sqlite3.Connection, expected_digest: str) -> None:
        real_replace_contents(src_snapshot, dst_conn, expected_digest)
        entered_replace.set()
        assert writer_started.wait(timeout=5.0)
        assert release_replace.wait(timeout=5.0)

    def concurrent_canonical_mint() -> None:
        assert entered_replace.wait(timeout=5.0)
        writer_started.set()
        threading.Timer(0.15, release_replace.set).start()
        try:
            in_flight_txs.append(canon_b.mint(wallet_b.address, 50.0, memo="IN_FLIGHT_CANONICAL_WRITE"))
        except Exception as exc:  # pragma: no cover - defensive capture
            writer_errors.append(exc)

    t = threading.Thread(target=concurrent_canonical_mint)
    t.start()
    try:
        with monkeypatch.context() as mp:
            mp.setattr(node_state, "_replace_sqlite_contents_in_place", paused_replace_contents)
            assert node_state.migrate_legacy_node_state() == "CLI"
    finally:
        t.join(timeout=10.0)

    assert writer_errors == []
    assert len(in_flight_txs) == 1
    assert not legacy_b_path.exists()
    assert not (state_b / node_state._MIGRATING_FILENAME).exists()
    assert canon_b.get_balance(wallet_b.address) == 65.0
    assert canon_b.has_transaction(in_flight_txs[0].tx_id) is True
    assert canon_b.verify_chain() is True

    # Subcase C: Corrupt <100-byte canonical DB during post-wallet recovery is cleanly replaced.
    state_c, paths_c, wallet_c, legacy_c, legacy_c_path = _interrupt_after_wallet("subcase_c")
    legacy_c.mint(wallet_c.address, 2.5, memo="R55_C_LEGACY")
    del legacy_c
    Path(paths_c.ledger).write_bytes(b"corrupt")
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not legacy_c_path.exists()
    assert node_state.open_node_ledger().get_balance(wallet_c.address) == 15.0

    # Subcase D: Corrupt >=100-byte SQLite header file hits _is_corrupt_sqlite_error fallback cleanly.
    _state_d, paths_d, wallet_d, legacy_d, legacy_d_path = _interrupt_after_wallet("subcase_d")
    legacy_d.mint(wallet_d.address, 2.5, memo="R55_D_LEGACY")
    del legacy_d
    Path(paths_d.ledger).write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not legacy_d_path.exists()
    assert node_state.open_node_ledger().get_balance(wallet_d.address) == 15.0


def test_round55_adopt_over_empty_canonical_ledger_never_clobbers_concurrent_first_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_set = node_state.LEGACY_SETS[0]
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    # Subcase A: Concurrent first mint lands after snapshot staging, before BEGIN EXCLUSIVE on dst.
    state_a = tmp_path / "adopt_a"
    state_a.mkdir()
    os.chmod(state_a, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_a))
    paths_a = node_state.node_state_paths()

    canon_a = Ledger(db_path=paths_a.ledger)
    custom_wallet = NodeWallet.create(node_id="r55-canon-first")
    legacy_wallet_a = NodeWallet.create(node_id="r55-leg-a")
    legacy_wallet_a.save_to_file(str(state_a / cli_set.wallet))
    Ledger(db_path=str(state_a / cli_set.ledger)).mint(legacy_wallet_a.address, 1.0, memo="R55_LEG_A")

    real_stage = node_state._stage_legacy_ledger_snapshot
    armed = {"flag": True}

    @contextlib.contextmanager
    def stage_then_first_mint(*args: Any, **kwargs: Any) -> Iterator[tuple[str, sqlite3.Connection, str]]:
        with real_stage(*args, **kwargs) as staged_tuple:
            if armed["flag"]:
                armed["flag"] = False
                canon_a.mint(custom_wallet.address, 500.0, memo="FIRST_CANON_MINT")
            yield staged_tuple

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_stage_legacy_ledger_snapshot", stage_then_first_mint)
        with pytest.raises(sqlite3.OperationalError, match="became established concurrently"):
            node_state.migrate_legacy_node_state()

    assert not Path(paths_a.wallet).exists()
    assert canon_a.get_balance(custom_wallet.address) == 500.0

    # Retrying adopts only the wallet and keeps the newly established 500-credit canonical ledger.
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert (state_a / cli_set.ledger).exists()
    assert node_state.open_node_ledger().get_balance(custom_wallet.address) == 500.0

    # Subcase B: Uncontended 0-tx canonical ledger is updated cleanly in place.
    state_b = tmp_path / "adopt_b"
    state_b.mkdir()
    os.chmod(state_b, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_b))
    paths_b = node_state.node_state_paths()
    canon_b = Ledger(db_path=paths_b.ledger)
    legacy_wallet_b = NodeWallet.create(node_id="r55-leg-b")
    legacy_wallet_b.save_to_file(str(state_b / cli_set.wallet))
    Ledger(db_path=str(state_b / cli_set.ledger)).mint(legacy_wallet_b.address, 12.5, memo="R55_LEG_B")

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not (state_b / cli_set.ledger).exists()
    assert canon_b.get_balance(legacy_wallet_b.address) == 12.5
    assert canon_b.verify_chain() is True
