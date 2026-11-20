"""Regression tests for tri-council review round 56.

1. Failed ``overwrite=False`` destination publication (when ``paths.ledger`` or
   ``paths.pqc_wallet`` was absent at plan time and created concurrently) never leaves false
   ownership in ``.smdc_node_state.migrating``.
2. Retrying an interrupted pre-wallet adoption where ``paths.ledger`` was previously synced runs
   locked digest reconciliation rather than blindly overwriting intervening canonical commits, and
   preserves both ledgers when both diverged.
3. Empty canonical ledgers carrying an inactive ``PERSIST`` rollback journal still go through locked
   in-place reconciliation rather than falling back to ``os.replace``.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import AddressType, NodeWallet


def test_round56_concurrent_destination_creation_when_absent_at_plan_leaves_no_false_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_set = node_state.LEGACY_SETS[2]
    assert mcp_set.pqc_wallet is not None
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    # Subcase A: paths.ledger absent at plan time, concurrently created + minted before publish.
    state_a = tmp_path / "absent_a"
    state_a.mkdir()
    os.chmod(state_a, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_a))
    paths_a = node_state.node_state_paths()

    lw_a = NodeWallet.create(node_id="r56-leg-a")
    lw_a.save_to_file(str(state_a / mcp_set.wallet))
    lp_a = state_a / mcp_set.ledger
    Ledger(db_path=str(lp_a)).mint(lw_a.address, 1.0, memo="R56_A_LEG")

    canon_wallet_a = NodeWallet.create(node_id="r56-canon-a")
    real_stage = node_state._stage_legacy_ledger_snapshot
    race_a = {"armed": True}

    @contextlib.contextmanager
    def stage_then_create_canon(*args: Any, **kwargs: Any) -> Iterator[tuple[str, sqlite3.Connection, str]]:
        with real_stage(*args, **kwargs) as staged_tuple:
            if race_a["armed"]:
                race_a["armed"] = False
                Ledger(db_path=paths_a.ledger).mint(canon_wallet_a.address, 500.0, memo="R56_A_CONCURRENT")
            yield staged_tuple

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_stage_legacy_ledger_snapshot", stage_then_create_canon)
        with pytest.raises(sqlite3.OperationalError, match="became established concurrently"):
            node_state.migrate_legacy_node_state()

    assert not (state_a / node_state._MIGRATING_FILENAME).exists()
    assert node_state.migrate_legacy_node_state() == "MCP"
    assert lp_a.exists()
    assert node_state.open_node_ledger().get_balance(canon_wallet_a.address) == 500.0

    # Subcase B: paths.pqc_wallet absent at plan time, concurrently created after _stage_adopt_item.
    state_b = tmp_path / "absent_b"
    state_b.mkdir()
    os.chmod(state_b, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_b))
    paths_b = node_state.node_state_paths()

    canon_b_holder = NodeWallet.create(node_id="r56-canon-b")
    Ledger(db_path=paths_b.ledger).mint(canon_b_holder.address, 500.0, memo="R56_B_CANON")
    concurrent_pqc = NodeWallet.create(node_id="r56-concurrent-pqc", algorithm=AddressType.ML_DSA_87)

    lw_b = NodeWallet.create(node_id="r56-leg-b")
    lw_b.save_to_file(str(state_b / mcp_set.wallet))
    Ledger(db_path=str(state_b / mcp_set.ledger)).mint(lw_b.address, 1.0, memo="R56_B_LEG")
    leg_pqc = NodeWallet.create(node_id="r56-leg-pqc", algorithm=AddressType.ML_DSA_87)
    leg_pqc.save_to_file(str(state_b / mcp_set.pqc_wallet))

    real_stage_item = node_state._stage_adopt_item
    race_b = {"armed": True}

    def stage_then_create_pqc(
        src: str, dst: str, state_dir: str, same_dir: bool, *, require_trusted: bool = False
    ) -> str:
        tmp = real_stage_item(src, dst, state_dir, same_dir, require_trusted=require_trusted)
        if race_b["armed"] and dst == paths_b.pqc_wallet:
            race_b["armed"] = False
            concurrent_pqc.save_to_file(paths_b.pqc_wallet, overwrite=False)
        return tmp

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_stage_adopt_item", stage_then_create_pqc)
        with pytest.raises(FileExistsError):
            node_state.migrate_legacy_node_state()

    assert not (state_b / node_state._MIGRATING_FILENAME).exists()
    assert node_state.migrate_legacy_node_state() == "MCP"
    assert (state_b / mcp_set.pqc_wallet).exists()
    assert NodeWallet.load_from_file(paths_b.pqc_wallet).address == concurrent_pqc.address
    assert node_state.open_node_ledger().get_balance(canon_b_holder.address) == 500.0


def test_round56_interrupted_pre_wallet_retry_reconciles_canonical_commits_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_set = node_state.LEGACY_SETS[0]
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    def _interrupt_pre_wallet(name: str) -> tuple[Path, node_state.NodeStatePaths, NodeWallet, Ledger, Path]:
        sdir = tmp_path / name
        sdir.mkdir()
        os.chmod(sdir, 0o700)
        monkeypatch.setenv("SMDC_STATE_DIR", str(sdir))
        p = node_state.node_state_paths()
        lw = NodeWallet.create(node_id=f"r56-{name}")
        lw.save_to_file(str(sdir / cli_set.wallet))
        lp = sdir / cli_set.ledger
        ll = Ledger(db_path=str(lp))
        ll.mint(lw.address, 12.5, memo=f"R56_{name}_BASE")

        real_publish = node_state._publish_staged_item
        armed = {"flag": True}

        def fail_wallet_once(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
            if armed["flag"] and dst == p.wallet:
                armed["flag"] = False
                raise OSError(errno.EIO, "simulated pre-wallet failure")
            real_publish(tmp_dst, dst, overwrite=overwrite)

        with monkeypatch.context() as mp:
            mp.setattr(node_state, "_publish_staged_item", fail_wallet_once)
            with pytest.raises(OSError, match="simulated pre-wallet failure"):
                node_state.migrate_legacy_node_state()
        return sdir, p, lw, ll, lp

    # Subcase A: Canonical receives +50.0 after interrupted pre-wallet sync; legacy unchanged (12.5).
    state_a, paths_a, wallet_a, legacy_a, legacy_a_path = _interrupt_pre_wallet("pre_a")
    del legacy_a
    marker_a = state_a / node_state._MIGRATING_FILENAME
    marker_data = json.loads(marker_a.read_text(encoding="utf-8"))
    assert marker_data["owned"] == ["ledger"]
    assert isinstance(marker_data.get("owned_ledger_digest"), str)

    canon_a = Ledger(db_path=paths_a.ledger)
    late_tx = canon_a.mint(wallet_a.address, 50.0, memo="PRE_WALLET_CANON")
    orig_ino = os.stat(paths_a.ledger).st_ino

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not marker_a.exists()
    assert not legacy_a_path.exists()
    assert os.stat(paths_a.ledger).st_ino == orig_ino
    assert canon_a.get_balance(wallet_a.address) == 62.5
    assert canon_a.has_transaction(late_tx.tx_id) is True
    assert canon_a.verify_chain() is True

    # Subcase B: Both canonical (+50.0 -> 62.5) and legacy (+2.5 -> 15.0) diverge before retry.
    state_b, paths_b, wallet_b, legacy_b, legacy_b_path = _interrupt_pre_wallet("pre_b")
    canon_b = Ledger(db_path=paths_b.ledger)
    canon_b.mint(wallet_b.address, 50.0, memo="PRE_WALLET_SPLIT_CANON")
    legacy_b.mint(wallet_b.address, 2.5, memo="PRE_WALLET_SPLIT_LEGACY")
    del legacy_b

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not (state_b / node_state._MIGRATING_FILENAME).exists()
    assert not (state_b / cli_set.wallet).exists()
    assert legacy_b_path.exists()
    assert canon_b.get_balance(wallet_b.address) == 62.5
    assert Ledger(db_path=str(legacy_b_path)).get_balance(wallet_b.address) == 15.0


def test_round56_inactive_persist_journal_destination_uses_locked_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "persist_state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths = node_state.node_state_paths()
    canon = Ledger(db_path=paths.ledger)
    # Leave a genuine inactive PERSIST rollback journal on disk with 0 transactions.
    with sqlite3.connect(paths.ledger) as jconn:
        jconn.execute("PRAGMA journal_mode=PERSIST")
        jconn.execute("BEGIN IMMEDIATE")
        jconn.execute("INSERT INTO balances (address, balance, nonce, balance_micro) VALUES ('tmp', 0.0, 0, 0)")
        jconn.execute("DELETE FROM balances WHERE address = 'tmp'")
        jconn.commit()
    assert Path(f"{paths.ledger}-journal").exists()

    custom_wallet = NodeWallet.create(node_id="r56-persist-canon")
    cli_set = node_state.LEGACY_SETS[0]
    lw = NodeWallet.create(node_id="r56-persist-leg")
    lw.save_to_file(str(state_dir / cli_set.wallet))
    lp = state_dir / cli_set.ledger
    Ledger(db_path=str(lp)).mint(lw.address, 1.0, memo="R56_PERSIST_LEG")

    real_stage = node_state._stage_legacy_ledger_snapshot
    armed = {"flag": True}

    @contextlib.contextmanager
    def stage_then_mint(*args: Any, **kwargs: Any) -> Iterator[tuple[str, sqlite3.Connection, str]]:
        with real_stage(*args, **kwargs) as staged_tuple:
            if armed["flag"]:
                armed["flag"] = False
                canon.mint(custom_wallet.address, 50.0, memo="PERSIST_RACE")
            yield staged_tuple

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_stage_legacy_ledger_snapshot", stage_then_mint)
        with pytest.raises(sqlite3.OperationalError, match="became established concurrently"):
            node_state.migrate_legacy_node_state()

    assert not (state_dir / node_state._MIGRATING_FILENAME).exists()
    assert not Path(paths.wallet).exists()
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert lp.exists()
    assert node_state.open_node_ledger().get_balance(custom_wallet.address) == 50.0
