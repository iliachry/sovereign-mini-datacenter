"""Regression tests for tri-council review round 54.

1. ``Ledger.verify_readonly_conn()`` classifies a canonical ledger as established based strictly on
   structural hash-chain validity and non-emptiness (``links_ok and count > 0``), so PQC anchor key
   loss or rotation never lets legacy migration overwrite an established ledger.
2. ``_inspect_unrecovered_ledger_copy()`` fingerprints the main DB and SQLite sidecars before and
   after copying and triggers a bounded retry in ``_has_established_ledger()`` when concurrent
   hot-journal/WAL recovery or copy instability occurs, failing closed rather than overwriting.
3. Legacy migration uses ``BEGIN EXCLUSIVE`` before publishing destination files (rejecting active
   ``DELETE``-mode readers cleanly before ``paths.wallet`` exists) and persists post-wallet
   retirement state in ``.smdc_node_state.migrating`` so interrupted retirement reconciles both
   legacy-only, canonical-only, split-brain, and pre-``post_wallet`` marker recovery paths.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from sovereign_dc.economy import ledger as ledger_mod
from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger, Transaction
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.security import pqc as pqc_mod
from sovereign_dc.security.pqc import PQCAlgorithm, get_node_pqc_keypair, node_pqc_key_path


def test_round54_established_ledger_with_rotated_anchor_key_is_preserved_on_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths = node_state.node_state_paths()
    custom_wallet = NodeWallet.create(node_id="r54-custom")
    est_ledger = Ledger(db_path=paths.ledger)
    est_ledger.mint(custom_wallet.address, 500.0, memo="R54_ANCHOR")
    del est_ledger

    # Rotate/regenerate the PQC anchor key (clearing in-process key caches as a fresh process would)
    # so chain_report() reports ANCHOR_INVALID while transaction links stay intact.
    key_path = Path(node_pqc_key_path(PQCAlgorithm.ML_DSA_65))
    assert key_path.exists()
    key_path.unlink()
    pqc_mod._CACHED_NODE_KEYPAIRS.pop((PQCAlgorithm.ML_DSA_65, str(key_path)), None)
    ledger_mod._ANCHOR_KEYS.pop(str(key_path), None)
    get_node_pqc_keypair(PQCAlgorithm.ML_DSA_65)
    assert Ledger(db_path=paths.ledger).chain_report() == (True, "invalid", 1)

    cli_set = node_state.LEGACY_SETS[0]
    legacy_wallet = NodeWallet.create(node_id="r54-cli-anchor")
    legacy_wallet.save_to_file(str(state_dir / cli_set.wallet))
    Ledger(db_path=str(state_dir / cli_set.ledger)).mint(legacy_wallet.address, 1.0, memo="R54_LEG")

    assert node_state._has_established_ledger(paths.ledger) is True
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert (state_dir / cli_set.ledger).exists()
    assert node_state.open_node_ledger().get_balance(custom_wallet.address) == 500.0


def test_round54_concurrent_hot_journal_recovery_during_probe_preserves_canonical_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths = node_state.node_state_paths()
    custom_wallet = NodeWallet.create(node_id="r54-hot-recovery")
    est_ledger = Ledger(db_path=paths.ledger)
    est_ledger.mint(custom_wallet.address, 500.0, memo="R54_HOT_BASE")

    # Produce a genuine spilled hot rollback journal in DELETE mode.
    conn = sqlite3.connect(paths.ledger)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA cache_size=1")
    conn.execute("BEGIN IMMEDIATE")
    tx = Transaction(
        sender="MINT",
        recipient=custom_wallet.address,
        amount=2.5,
        fee=0.0,
        nonce=0,
        memo="a" * 100_000,
        prev_hash=est_ledger._get_last_tx_hash(conn),
    )
    est_ledger._apply(conn, tx, allow_privileged=True)
    db_file = Path(paths.ledger)
    journal_file = Path(f"{paths.ledger}-journal")
    assert journal_file.exists() and journal_file.stat().st_size > 0
    saved_db = db_file.read_bytes()
    saved_journal = journal_file.read_bytes()
    conn.rollback()
    conn.close()
    del est_ledger

    db_file.write_bytes(saved_db)
    journal_file.write_bytes(saved_journal)

    # First verify persistent instability during copy retries fails closed (OperationalError), not False.
    real_copy2 = node_state.shutil.copy2

    def mutate_probe_copy(src: str | os.PathLike[str], dst: str | os.PathLike[str], **kwargs: object) -> object:
        res = real_copy2(src, dst, **kwargs)  # type: ignore[arg-type]
        if str(src) == paths.ledger:
            Path(dst).write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        return res

    with monkeypatch.context() as mp:
        mp.setattr(node_state.shutil, "copy2", mutate_probe_copy)
        with pytest.raises(sqlite3.OperationalError, match="changed concurrently"):
            node_state._has_established_ledger(paths.ledger)

    # Restore hot journal and run actual migration with concurrent Ledger recovery mid-probe.
    db_file.write_bytes(saved_db)
    journal_file.write_bytes(saved_journal)

    cli_set = node_state.LEGACY_SETS[0]
    legacy_wallet = NodeWallet.create(node_id="r54-cli-hot")
    legacy_wallet.save_to_file(str(state_dir / cli_set.wallet))
    Ledger(db_path=str(state_dir / cli_set.ledger)).mint(legacy_wallet.address, 1.0, memo="R54_HOT_LEG")

    interleave = {"armed": True}

    def recover_mid_probe(src: str | os.PathLike[str], dst: str | os.PathLike[str], **kwargs: object) -> object:
        res = real_copy2(src, dst, **kwargs)  # type: ignore[arg-type]
        if interleave["armed"] and str(src) == paths.ledger:
            interleave["armed"] = False
            assert Ledger(db_path=paths.ledger).get_balance(custom_wallet.address) == 500.0
        return res

    with monkeypatch.context() as mp:
        mp.setattr(node_state.shutil, "copy2", recover_mid_probe)
        assert node_state.migrate_legacy_node_state() == "CLI"

    assert interleave["armed"] is False
    assert (state_dir / cli_set.ledger).exists()
    assert node_state.open_node_ledger().get_balance(custom_wallet.address) == 500.0


def test_round54_delete_mode_reader_aborts_pre_publish_and_interrupted_retirement_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_set = node_state.LEGACY_SETS[0]
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    def _setup_legacy_cli_state(name: str) -> tuple[Path, node_state.NodeStatePaths, NodeWallet, Ledger, Path]:
        sdir = tmp_path / name
        sdir.mkdir()
        os.chmod(sdir, 0o700)
        monkeypatch.setenv("SMDC_STATE_DIR", str(sdir))
        p = node_state.node_state_paths()
        lw = NodeWallet.create(node_id=f"r54-{name}")
        lw.save_to_file(str(sdir / cli_set.wallet))
        lp = sdir / cli_set.ledger
        ll = Ledger(db_path=str(lp))
        ll.mint(lw.address, 12.5, memo=f"R54_{name}_BASE")
        with sqlite3.connect(str(lp)) as mode_conn:
            mode_conn.execute("PRAGMA journal_mode=DELETE")
        return sdir, p, lw, ll, lp

    # Subcase A: DELETE-mode active reader causes BEGIN EXCLUSIVE to abort BEFORE paths.wallet exists.
    state_a, paths_a, legacy_a_wallet, legacy_a_ledger, legacy_a_ledger_path = _setup_legacy_cli_state("state_a")
    reader = sqlite3.connect(str(legacy_a_ledger_path))
    reader.execute("BEGIN")
    assert reader.execute("SELECT COUNT(*) FROM transactions").fetchone() == (1,)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            node_state.migrate_legacy_node_state()
    finally:
        reader.rollback()
        reader.close()

    assert not Path(paths_a.wallet).exists()
    assert not Path(paths_a.ledger).exists()
    legacy_a_ledger.mint(legacy_a_wallet.address, 2.5, memo="R54_A_LATE")
    del legacy_a_ledger

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not (state_a / node_state._MIGRATING_FILENAME).exists()
    assert not legacy_a_ledger_path.exists()
    assert node_state.open_node_ledger().get_balance(legacy_a_wallet.address) == 15.0

    # Subcase B: Interrupted post-wallet retirement reconciles subsequent legacy writes on retry.
    state_b, paths_b, legacy_b_wallet, legacy_b_ledger, legacy_b_ledger_path = _setup_legacy_cli_state("state_b")
    real_retire = node_state._retire_legacy_ledger

    def _run_with_one_shot_retire_failure() -> None:
        armed = {"flag": True}

        def _fail_once(src_conn: sqlite3.Connection, legacy_ledger: str) -> None:
            if armed["flag"]:
                armed["flag"] = False
                raise sqlite3.OperationalError("simulated post-wallet retirement failure")
            real_retire(src_conn, legacy_ledger)

        with monkeypatch.context() as mp:
            mp.setattr(node_state, "_retire_legacy_ledger", _fail_once)
            with pytest.raises(sqlite3.OperationalError, match="simulated post-wallet retirement failure"):
                node_state.migrate_legacy_node_state()

    _run_with_one_shot_retire_failure()
    marker_b = state_b / node_state._MIGRATING_FILENAME
    assert Path(paths_b.wallet).exists() and marker_b.exists() and legacy_b_ledger_path.exists()
    legacy_b_ledger.mint(legacy_b_wallet.address, 2.5, memo="R54_B_POST_WALLET")
    del legacy_b_ledger
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not marker_b.exists() and not legacy_b_ledger_path.exists()
    assert node_state.migrate_legacy_node_state() is None
    assert node_state.open_node_ledger().get_balance(legacy_b_wallet.address) == 15.0

    # Subcase C: Unchanged legacy ledger + canonical post-wallet mint keeps canonical writes and retires legacy.
    state_c, paths_c, legacy_c_wallet, legacy_c_ledger, legacy_c_ledger_path = _setup_legacy_cli_state("state_c")
    del legacy_c_ledger
    _run_with_one_shot_retire_failure()
    Ledger(db_path=paths_c.ledger).mint(legacy_c_wallet.address, 50.0, memo="R54_C_CANON_ONLY")
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not (state_c / node_state._MIGRATING_FILENAME).exists() and not legacy_c_ledger_path.exists()
    assert node_state.open_node_ledger().get_balance(legacy_c_wallet.address) == 62.5

    # Subcase D: Divergent post-wallet writes on BOTH ledgers preserve both cleanly (split-brain safety).
    state_d, paths_d, legacy_d_wallet, legacy_d_ledger, legacy_d_ledger_path = _setup_legacy_cli_state("state_d")
    _run_with_one_shot_retire_failure()
    Ledger(db_path=paths_d.ledger).mint(legacy_d_wallet.address, 50.0, memo="R54_D_CANON")
    legacy_d_ledger.mint(legacy_d_wallet.address, 2.5, memo="R54_D_LEGACY")
    del legacy_d_ledger
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not (state_d / node_state._MIGRATING_FILENAME).exists()
    assert not (state_d / cli_set.wallet).exists()
    assert legacy_d_ledger_path.exists()
    assert node_state.open_node_ledger().get_balance(legacy_d_wallet.address) == 62.5
    assert Ledger(db_path=str(legacy_d_ledger_path)).get_balance(legacy_d_wallet.address) == 15.0

    # Subcase E: Crash between paths.wallet publish and post_wallet marker update (+ trigger strip + orphan cleanup).
    state_e = tmp_path / "state_e"
    state_e.mkdir()
    os.chmod(state_e, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_e))
    paths_e = node_state.node_state_paths()
    mcp_set = node_state.LEGACY_SETS[2]
    assert mcp_set.pqc_wallet is not None
    lw_e = NodeWallet.create(node_id="r54-state-e")
    lw_e.save_to_file(str(state_e / mcp_set.wallet))
    lw_e.save_to_file(paths_e.wallet, overwrite=False)
    lp_e = state_e / mcp_set.ledger
    ll_e = Ledger(db_path=str(lp_e))
    ll_e.mint(lw_e.address, 12.5, memo="R54_E_BASE")
    del ll_e
    with sqlite3.connect(str(lp_e)) as trig_conn:
        trig_conn.execute(
            "CREATE TRIGGER IF NOT EXISTS _smdc_migrated BEFORE INSERT ON transactions "
            "BEGIN SELECT RAISE(ABORT, 'legacy'); END"
        )
        trig_conn.commit()
    NodeWallet.create(node_id="r54-pqc-e", algorithm=AddressType.ML_DSA_87).save_to_file(
        str(state_e / mcp_set.pqc_wallet)
    )
    marker_e = state_e / node_state._MIGRATING_FILENAME
    marker_e.write_text(json.dumps({"owned": ["ledger"]}), encoding="utf-8")

    assert node_state.migrate_legacy_node_state() == "MCP"
    assert not marker_e.exists()
    assert not lp_e.exists()
    assert (state_e / mcp_set.pqc_wallet).exists()
    canon_e = node_state.open_node_ledger()
    assert canon_e.get_balance(lw_e.address) == 12.5
    canon_e.mint(lw_e.address, 1.0, memo="R54_E_WRITABLE")
    assert canon_e.get_balance(lw_e.address) == 13.5

    # If marker lacks post_wallet and no legacy set exists on disk, marker is cleaned and None returned.
    marker_e.write_text(json.dumps({"owned": []}), encoding="utf-8")
    assert node_state.migrate_legacy_node_state() is None
    assert not marker_e.exists()
