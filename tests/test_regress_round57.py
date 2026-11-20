"""Regression tests for tri-council review round 57.

1. ``_reconcile_destination_ledger`` preserves the shared legacy sync baseline digest
   (``leg_digest``) rather than storing an advanced canonical digest when canonical
   advanced ahead of an unchanged legacy ledger, preventing stale legacy overwrites across
   repeated pre-wallet or post-wallet interruptions while still detecting later split-brain
   legacy writes. Also covers ``owned_ledger_digest`` fallback when crashing after wallet
   publication before writing ``post_wallet``.
2. ``_lock_or_replace_destination_ledger`` routes 0-byte destination files (such as those
   pre-created by ``Ledger._secure_db_file()``) through ``BEGIN EXCLUSIVE`` + inode
   verification rather than falling back to ``os.replace``.
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
from sovereign_dc.economy.wallet import NodeWallet


def test_round57_baseline_digest_stays_at_sync_point_across_repeated_interruptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_set = node_state.LEGACY_SETS[0]
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)
    real_publish = node_state._publish_staged_item

    def _setup_pre_wallet_interruption(name: str) -> tuple[Path, node_state.NodeStatePaths, NodeWallet, Ledger, Path]:
        sdir = tmp_path / name
        sdir.mkdir()
        os.chmod(sdir, 0o700)
        monkeypatch.setenv("SMDC_STATE_DIR", str(sdir))
        paths = node_state.node_state_paths()

        lw = NodeWallet.create(node_id=f"r57-{name}")
        lw.save_to_file(str(sdir / cli_set.wallet))
        lp = sdir / cli_set.ledger
        leg = Ledger(db_path=str(lp))
        leg.mint(lw.address, 12.5, memo=f"R57_{name}_BASE")

        armed = {"flag": True}

        def fail_wallet_once(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
            if armed["flag"] and dst == paths.wallet:
                armed["flag"] = False
                raise OSError(errno.EIO, "simulated pre-wallet failure #1")
            real_publish(tmp_dst, dst, overwrite=overwrite)

        with monkeypatch.context() as mp:
            mp.setattr(node_state, "_publish_staged_item", fail_wallet_once)
            with pytest.raises(OSError, match="simulated pre-wallet failure #1"):
                node_state.migrate_legacy_node_state()
        return sdir, paths, lw, leg, lp

    # Subcase A: Canonical advances (+50.0 -> 62.5); Attempt 2 fails at wallet again;
    # Attempt 3 must keep canonical's 62.5 rather than overwriting with legacy's 12.5.
    state_a, paths_a, wallet_a, leg_a, lp_a = _setup_pre_wallet_interruption("subcase_a")
    del leg_a
    marker_a = state_a / node_state._MIGRATING_FILENAME
    baseline_a = json.loads(marker_a.read_text(encoding="utf-8"))["owned_ledger_digest"]

    canon_a = Ledger(db_path=paths_a.ledger)
    late_tx_a = canon_a.mint(wallet_a.address, 50.0, memo="R57_A_LATE")

    armed_a2 = {"flag": True}

    def fail_wallet_second(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
        if armed_a2["flag"] and dst == paths_a.wallet:
            armed_a2["flag"] = False
            raise OSError(errno.EIO, "simulated pre-wallet failure #2")
        real_publish(tmp_dst, dst, overwrite=overwrite)

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_publish_staged_item", fail_wallet_second)
        with pytest.raises(OSError, match="simulated pre-wallet failure #2"):
            node_state.migrate_legacy_node_state()

    marker_after_a2 = json.loads(marker_a.read_text(encoding="utf-8"))
    assert marker_after_a2["owned"] == ["ledger"]
    assert marker_after_a2["owned_ledger_digest"] == baseline_a

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not marker_a.exists()
    assert not lp_a.exists()
    assert canon_a.get_balance(wallet_a.address) == 62.5
    assert canon_a.has_transaction(late_tx_a.tx_id) is True
    assert canon_a.verify_chain() is True

    # Subcase B1: Canonical advances (+50.0 -> 62.5); Attempt 2 publishes wallet & post_wallet
    # marker, then fails inside _retire_legacy_ledger; Attempt 3 recovers & preserves 62.5.
    state_b1, paths_b1, wallet_b1, leg_b1, lp_b1 = _setup_pre_wallet_interruption("subcase_b1")
    del leg_b1
    marker_b1 = state_b1 / node_state._MIGRATING_FILENAME
    baseline_b1 = json.loads(marker_b1.read_text(encoding="utf-8"))["owned_ledger_digest"]

    canon_b1 = Ledger(db_path=paths_b1.ledger)
    late_tx_b1 = canon_b1.mint(wallet_b1.address, 50.0, memo="R57_B1_LATE")

    def fail_retire(src_conn: sqlite3.Connection, legacy_ledger: str) -> None:
        raise OSError(errno.EIO, "simulated post-wallet retire failure")

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_retire_legacy_ledger", fail_retire)
        with pytest.raises(OSError, match="simulated post-wallet retire failure"):
            node_state.migrate_legacy_node_state()

    marker_data_b1 = json.loads(marker_b1.read_text(encoding="utf-8"))
    assert marker_data_b1["post_wallet"]["published_ledger_digest"] == baseline_b1

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not marker_b1.exists()
    assert not lp_b1.exists()
    assert canon_b1.get_balance(wallet_b1.address) == 62.5
    assert canon_b1.has_transaction(late_tx_b1.tx_id) is True
    assert canon_b1.verify_chain() is True

    # Subcase B2: Crash right after paths.wallet publish before writing post_wallet marker;
    # fallback recovery reads owned_ledger_digest and preserves canonical's 62.5.
    state_b2, paths_b2, wallet_b2, leg_b2, lp_b2 = _setup_pre_wallet_interruption("subcase_b2")
    del leg_b2
    canon_b2 = Ledger(db_path=paths_b2.ledger)
    late_tx_b2 = canon_b2.mint(wallet_b2.address, 50.0, memo="R57_B2_LATE")

    real_write_marker = node_state._write_migrating_owned

    def fail_post_wallet_marker(marker_path: str, owned: set[str], **kwargs: Any) -> None:
        if kwargs.get("post_wallet") is not None:
            raise OSError(errno.EIO, "simulated post-wallet marker failure")
        real_write_marker(marker_path, owned, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_write_migrating_owned", fail_post_wallet_marker)
        with pytest.raises(OSError, match="simulated post-wallet marker failure"):
            node_state.migrate_legacy_node_state()

    marker_b2 = state_b2 / node_state._MIGRATING_FILENAME
    assert "post_wallet" not in json.loads(marker_b2.read_text(encoding="utf-8"))
    assert Path(paths_b2.wallet).exists()

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not marker_b2.exists()
    assert not lp_b2.exists()
    assert canon_b2.get_balance(wallet_b2.address) == 62.5
    assert canon_b2.has_transaction(late_tx_b2.tx_id) is True

    # Subcase C: Canonical advances before Attempt 2, Attempt 2 fails at wallet, THEN legacy
    # also advances (+2.5 -> 15.0) before Attempt 3 -> Attempt 3 detects split-brain and keeps both.
    state_c, paths_c, wallet_c, leg_c, lp_c = _setup_pre_wallet_interruption("subcase_c")
    canon_c = Ledger(db_path=paths_c.ledger)
    canon_c.mint(wallet_c.address, 50.0, memo="R57_C_CANON")

    armed_c2 = {"flag": True}

    def fail_wallet_c2(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
        if armed_c2["flag"] and dst == paths_c.wallet:
            armed_c2["flag"] = False
            raise OSError(errno.EIO, "simulated pre-wallet failure c2")
        real_publish(tmp_dst, dst, overwrite=overwrite)

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_publish_staged_item", fail_wallet_c2)
        with pytest.raises(OSError, match="simulated pre-wallet failure c2"):
            node_state.migrate_legacy_node_state()

    leg_c.mint(wallet_c.address, 2.5, memo="R57_C_LEGACY")
    del leg_c

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not (state_c / node_state._MIGRATING_FILENAME).exists()
    assert lp_c.exists()
    assert canon_c.get_balance(wallet_c.address) == 62.5
    assert Ledger(db_path=str(lp_c)).get_balance(wallet_c.address) == 15.0


def test_round57_zero_byte_destination_file_uses_locked_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_set = node_state.LEGACY_SETS[0]
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    # Subcase A: 0-byte canonical file (as created by Ledger._secure_db_file) initialized and
    # minted concurrently after plan-time check -> locked BEGIN EXCLUSIVE detects & preserves it.
    state_a = tmp_path / "zero_a"
    state_a.mkdir()
    os.chmod(state_a, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_a))
    paths_a = node_state.node_state_paths()

    with open(paths_a.ledger, "wb"):
        pass
    os.chmod(paths_a.ledger, 0o600)
    assert os.path.getsize(paths_a.ledger) == 0

    lw_a = NodeWallet.create(node_id="r57-zero-leg-a")
    lw_a.save_to_file(str(state_a / cli_set.wallet))
    lp_a = state_a / cli_set.ledger
    Ledger(db_path=str(lp_a)).mint(lw_a.address, 1.0, memo="R57_ZERO_LEG_A")

    canon_wallet_a = NodeWallet.create(node_id="r57-zero-canon-a")
    real_stage = node_state._stage_legacy_ledger_snapshot
    armed = {"flag": True}

    @contextlib.contextmanager
    def stage_then_init_zero_byte(*args: Any, **kwargs: Any) -> Iterator[tuple[str, sqlite3.Connection, str]]:
        with real_stage(*args, **kwargs) as staged_tuple:
            if armed["flag"]:
                armed["flag"] = False
                Ledger(db_path=paths_a.ledger).mint(canon_wallet_a.address, 500.0, memo="R57_ZERO_CONCURRENT")
            yield staged_tuple

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_stage_legacy_ledger_snapshot", stage_then_init_zero_byte)
        with pytest.raises(sqlite3.OperationalError, match="became established concurrently"):
            node_state.migrate_legacy_node_state()

    assert not (state_a / node_state._MIGRATING_FILENAME).exists()
    assert not Path(paths_a.wallet).exists()
    assert node_state.migrate_legacy_node_state() == "CLI"
    assert lp_a.exists()
    assert node_state.open_node_ledger().get_balance(canon_wallet_a.address) == 500.0

    # Subcase B: Uncontended 0-byte canonical file is populated in place without replacing inode.
    state_b = tmp_path / "zero_b"
    state_b.mkdir()
    os.chmod(state_b, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_b))
    paths_b = node_state.node_state_paths()

    with open(paths_b.ledger, "wb"):
        pass
    os.chmod(paths_b.ledger, 0o600)
    orig_ino = os.stat(paths_b.ledger).st_ino

    lw_b = NodeWallet.create(node_id="r57-zero-leg-b")
    lw_b.save_to_file(str(state_b / cli_set.wallet))
    lp_b = state_b / cli_set.ledger
    Ledger(db_path=str(lp_b)).mint(lw_b.address, 12.5, memo="R57_ZERO_LEG_B")

    assert node_state.migrate_legacy_node_state() == "CLI"
    assert not lp_b.exists()
    assert os.stat(paths_b.ledger).st_ino == orig_ino
    assert node_state.open_node_ledger().get_balance(lw_b.address) == 12.5
