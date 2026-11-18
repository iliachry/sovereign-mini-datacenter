"""Regression tests for tri-council review round 52.

1. ``migrate_legacy_node_state()`` emits an accurate info log and an ``untouched`` warning when an
   already-established destination ledger keeps the chosen legacy set's ledger on disk.
2. ``_has_established_ledger()`` checks the chain strictly read-only without ``Ledger.__init__``
   write locks, and treats SQLite lock/I/O errors as indeterminate rather than replacing an
   unreadable established destination ledger.
3. Overlapping ``EnterpriseManager.package_app()`` calls targeting the same output serialize
   backup and pair publication cleanly via ``exclusive_file_lock``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.enterprise import manager as manager_mod
from sovereign_dc.enterprise.manager import EnterpriseManager, verify_package
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.security.pqc import PQCAlgorithm, get_node_pqc_keypair


def test_round52_migration_logs_and_warns_accurately_when_established_ledger_skips_legacy_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths = node_state.node_state_paths()
    custom_wallet = NodeWallet.create(node_id="r52-custom")
    est_ledger = Ledger(db_path=paths.ledger)
    est_ledger.mint(custom_wallet.address, 500.0, memo="R52_ESTABLISHED")
    del est_ledger

    cli_set = node_state.LEGACY_SETS[0]
    legacy_wallet = NodeWallet.create(node_id="r52-cli")
    legacy_wallet.save_to_file(str(state_dir / cli_set.wallet))
    legacy_ledger = Ledger(db_path=str(state_dir / cli_set.ledger))
    legacy_ledger.mint(legacy_wallet.address, 1.0, memo="R52_LEGACY")
    del legacy_ledger

    caplog.set_level(logging.INFO, logger="sovereign_dc.economy.node_state")
    assert node_state.migrate_legacy_node_state() == "CLI"

    info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "Adopted" in r.getMessage()]
    warn_msgs = [
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "left untouched" in r.getMessage()
    ]
    assert len(info_msgs) == 1
    assert "wallet and ledger" not in info_msgs[0]
    assert "kept existing node ledger" in info_msgs[0]
    assert len(warn_msgs) == 1
    assert cli_set.ledger in warn_msgs[0]
    assert (state_dir / cli_set.ledger).exists()
    assert node_state.get_node_identity().ledger.get_balance(custom_wallet.address) == 500.0


def test_round52_busy_canonical_ledger_is_never_overwritten_during_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Subcase A: WAL BEGIN IMMEDIATE active writer on established canonical ledger does not block or overwrite.
    state_a = tmp_path / "state_a"
    state_a.mkdir()
    os.chmod(state_a, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_a))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths_a = node_state.node_state_paths()
    custom_a = NodeWallet.create(node_id="r52-busy-a")
    Ledger(db_path=paths_a.ledger).mint(custom_a.address, 500.0, memo="R52_BUSY_A")

    cli_set = node_state.LEGACY_SETS[0]
    legacy_a = NodeWallet.create(node_id="r52-leg-a")
    legacy_a.save_to_file(str(state_a / cli_set.wallet))
    Ledger(db_path=str(state_a / cli_set.ledger)).mint(legacy_a.address, 1.0, memo="R52_LEG_A")

    writer = sqlite3.connect(paths_a.ledger, timeout=0.05)
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert node_state.migrate_legacy_node_state() == "CLI"
    finally:
        writer.rollback()
        writer.close()

    assert node_state.get_node_identity().ledger.get_balance(custom_a.address) == 500.0

    # Subcase B: Rollback-journal BEGIN EXCLUSIVE lock causes indeterminate SQLite error instead of overwrite.
    state_b = tmp_path / "state_b"
    state_b.mkdir()
    os.chmod(state_b, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_b))

    paths_b = node_state.node_state_paths()
    custom_b = NodeWallet.create(node_id="r52-busy-b")
    Ledger(db_path=paths_b.ledger).mint(custom_b.address, 500.0, memo="R52_BUSY_B")
    del_mode = sqlite3.connect(paths_b.ledger)
    del_mode.execute("PRAGMA journal_mode=DELETE")
    del_mode.close()

    legacy_b = NodeWallet.create(node_id="r52-leg-b")
    legacy_b.save_to_file(str(state_b / cli_set.wallet))
    Ledger(db_path=str(state_b / cli_set.ledger)).mint(legacy_b.address, 1.0, memo="R52_LEG_B")

    excl = sqlite3.connect(paths_b.ledger, timeout=0.05)
    excl.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            node_state.migrate_legacy_node_state()
    finally:
        excl.rollback()
        excl.close()

    assert not (state_b / node_state._MIGRATING_FILENAME).exists()
    assert (state_b / cli_set.wallet).exists()
    assert Ledger(db_path=paths_b.ledger).get_balance(custom_b.address) == 500.0


def test_round52_concurrent_package_app_publications_serialize_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager_a = EnterpriseManager(registry=registry)
    manager_b = EnterpriseManager(registry=registry)

    app_a = EnterpriseRegistry.create_project_scaffold(
        tmp_path / "app-a", EnterpriseRegistry.scaffold_manifest(name="Demo A", app_id="demo-app")
    )
    (app_a / "main.py").write_text("print('pkg-a')\n", encoding="utf-8")

    app_b = EnterpriseRegistry.create_project_scaffold(
        tmp_path / "app-b", EnterpriseRegistry.scaffold_manifest(name="Demo B", app_id="demo-app")
    )
    (app_b / "main.py").write_text("print('pkg-b')\n", encoding="utf-8")

    out_dir = tmp_path / "pkg_out"
    out = out_dir / "demo-app.smdc-app"
    trusted = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key

    a_out_replaced = threading.Event()
    b_started = threading.Event()
    release_a = threading.Event()
    real_replace = manager_mod.os.replace
    first_out = {"armed": True}
    lock = threading.Lock()

    def paused_replace(src: Any, dst: Any) -> None:
        real_replace(src, dst)
        with lock:
            if first_out["armed"] and Path(dst) == out:
                first_out["armed"] = False
                pause = True
            else:
                pause = False
        if pause:
            a_out_replaced.set()
            assert b_started.wait(timeout=5.0)
            assert release_a.wait(timeout=5.0)

    monkeypatch.setattr(manager_mod.os, "replace", paused_replace)

    def run_a() -> tuple[bool, str, dict[str, Any]]:
        return manager_a.package_app(app_a, output_path=out, sign_pqc=True)

    def run_b() -> tuple[bool, str, dict[str, Any]]:
        assert a_out_replaced.wait(timeout=5.0)
        b_started.set()
        threading.Timer(0.15, release_a.set).start()
        return manager_b.package_app(app_b, output_path=out, sign_pqc=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(run_a)
        fut_b = pool.submit(run_b)
        res_a = fut_a.result(timeout=15.0)
        res_b = fut_b.result(timeout=15.0)

    assert res_a[0] is True and res_b[0] is True
    assert verify_package(out, trusted) == (True, "ok")
    assert sorted(p.name for p in out_dir.iterdir()) == ["demo-app.smdc-app", "demo-app.smdc-app.sig"]
