"""Regression tests for tri-council review round 51.

1. Shared-temp legacy CLI migration validates SQLite sidecar files (``-wal``/``-shm``/``-journal``)
   and re-verifies ownership/mode on the open staging file descriptor (``O_NOFOLLOW`` + ``fstat``)
   so untrusted local sidecars cannot inject ledger pages.
2. Interrupted-migration marker ``.smdc_node_state.migrating`` records which destination components
   the migration owns so retries never overwrite a pre-established destination ledger or an existing
   node ML-DSA-87 wallet that the first attempt preserved.
3. ``EnterpriseManager.package_app()`` preserves any previous package/sidecar pair and rolls back if
   publishing the replacement sidecar fails.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy import node_state
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.enterprise import manager as manager_mod
from sovereign_dc.enterprise.manager import EnterpriseManager, package_sidecar_path, verify_package
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.security.pqc import PQCAlgorithm, get_node_pqc_keypair


def test_round51_untrusted_tmp_sqlite_sidecar_blocks_legacy_cli_adoption(
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
    victim_wallet = NodeWallet.create(node_id="r51-victim")
    victim_wallet_path = tmp_cli / cli_set.wallet
    victim_wallet.save_to_file(str(victim_wallet_path))
    os.chmod(victim_wallet_path, 0o600)

    victim_ledger_path = tmp_cli / cli_set.ledger
    victim_ledger = Ledger(db_path=str(victim_ledger_path))
    victim_ledger.mint(victim_wallet.address, 100.0, memo="R51_VICTIM")
    del victim_ledger
    os.chmod(victim_ledger_path, 0o600)
    for side in ("-wal", "-shm", "-journal"):
        p = tmp_cli / f"{cli_set.ledger}{side}"
        if p.exists():
            p.unlink()

    # Craft a valid SQLite WAL holding an attacker mint and plant it world-writable next to victim ledger.
    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir()
    attacker_db = attacker_dir / "att.db"
    attacker_wallet = NodeWallet.create(node_id="r51-attacker")
    Ledger(db_path=str(attacker_db))
    reader = sqlite3.connect(str(attacker_db))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM transactions").fetchall()
    Ledger(db_path=str(attacker_db)).mint(attacker_wallet.address, 999999.0, memo="R51_ATTACKER")
    wal_bytes = (attacker_dir / "att.db-wal").read_bytes()
    reader.close()

    planted_wal = tmp_cli / f"{cli_set.ledger}-wal"
    planted_wal.write_bytes(wal_bytes)
    os.chmod(planted_wal, 0o666)  # noqa: S103 - deliberate world-writable fixture
    assert node_state.is_trusted_legacy_file(str(planted_wal)) is False

    # Trusted legacy migration must ignore the poisoned legacy set and never credit attacker.
    assert node_state.migrate_legacy_node_state() is None
    identity = node_state.get_node_identity()
    assert identity.wallet.address != victim_wallet.address
    assert identity.balance == 0.0
    assert identity.ledger.get_balance(attacker_wallet.address) == 0.0

    # Defense in depth: direct _adopt and _stage_copy(require_trusted=True) reject untrusted files.
    with pytest.raises(PermissionError):
        node_state._adopt(cli_set, str(tmp_cli), node_state.node_state_paths())
    with pytest.raises(PermissionError):
        node_state._stage_copy(str(planted_wal), str(state_dir), ".probe.", require_trusted=True)


def test_round51_interrupted_migration_marker_preserves_established_ledger_and_pqc_wallet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    os.chmod(state_dir, 0o700)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: None)

    paths = node_state.node_state_paths()
    custom_wallet = NodeWallet.create(node_id="r51-custom-ledger")
    established_ledger = Ledger(db_path=paths.ledger)
    established_ledger.mint(custom_wallet.address, 500.0, memo="R51_EXISTING_LEDGER")
    del established_ledger

    existing_pqc = NodeWallet.create(node_id="r51-existing-pqc", algorithm=AddressType.ML_DSA_87)
    existing_pqc.save_to_file(paths.pqc_wallet, overwrite=False)

    mcp_set = node_state.LEGACY_SETS[2]
    assert mcp_set.pqc_wallet is not None
    legacy_wallet = NodeWallet.create(node_id="r51-legacy-mcp")
    legacy_wallet.save_to_file(str(state_dir / mcp_set.wallet))
    legacy_ledger = Ledger(db_path=str(state_dir / mcp_set.ledger))
    legacy_ledger.mint(legacy_wallet.address, 1.0, memo="R51_LEGACY_MCP")
    del legacy_ledger
    legacy_pqc = NodeWallet.create(node_id="r51-legacy-mcp-pqc", algorithm=AddressType.ML_DSA_87)
    legacy_pqc.save_to_file(str(state_dir / mcp_set.pqc_wallet))

    marker_path = state_dir / node_state._MIGRATING_FILENAME
    marker_path.write_text("{corrupt-json", encoding="utf-8")
    assert node_state._read_migrating_owned(str(marker_path)) == set()
    marker_path.unlink()

    real_publish = node_state._publish_staged_item
    fail_once = {"armed": True}

    def fail_wallet_publish(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
        if fail_once["armed"] and dst == paths.wallet:
            fail_once["armed"] = False
            raise OSError(errno.EIO, "simulated wallet publish failure")
        real_publish(tmp_dst, dst, overwrite=overwrite)

    with monkeypatch.context() as mp:
        mp.setattr(node_state, "_publish_staged_item", fail_wallet_publish)
        with pytest.raises(OSError, match="simulated wallet publish failure"):
            node_state.migrate_legacy_node_state()

    assert marker_path.exists()
    assert json.loads(marker_path.read_text(encoding="utf-8")) == {"owned": []}

    assert node_state.migrate_legacy_node_state() == "MCP"
    assert not marker_path.exists()

    identity = node_state.get_node_identity()
    assert identity.wallet.address == legacy_wallet.address
    assert identity.pqc_wallet.address == existing_pqc.address
    assert identity.ledger.get_balance(custom_wallet.address) == 500.0
    assert identity.ledger.verify_chain() is True


def test_round51_failed_package_sidecar_publish_restores_previous_valid_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager = EnterpriseManager(registry=registry)

    manifest = EnterpriseRegistry.scaffold_manifest(name="Demo App", app_id="demo-app")
    app_dir = EnterpriseRegistry.create_project_scaffold(tmp_path / "demo-app", manifest)
    (app_dir / "main.py").write_text("print('v1')\n", encoding="utf-8")

    out_dir = tmp_path / "pkg_out"
    out = out_dir / "demo-app.smdc-app"
    sidecar = package_sidecar_path(out)

    ok_v1, msg_v1, _ = manager.package_app(app_dir, output_path=out, sign_pqc=True)
    assert ok_v1, msg_v1
    trusted = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key
    assert verify_package(out, trusted) == (True, "ok")
    before = (out.read_bytes(), sidecar.read_bytes())

    (app_dir / "main.py").write_text("print('v2')\n", encoding="utf-8")
    real_replace = manager_mod.os.replace

    def fail_sidecar_replace(src: Any, dst: Any) -> None:
        if Path(dst) == sidecar:
            raise OSError(errno.EIO, "simulated sidecar replace failure")
        real_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(manager_mod.os, "replace", fail_sidecar_replace)
        mp.setattr(
            manager_mod.os,
            "link",
            lambda _s, _d: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, "no link")),
        )
        ok_v2, msg_v2, meta_v2 = manager.package_app(app_dir, output_path=out, sign_pqc=True)

    assert ok_v2 is False
    assert "simulated sidecar replace failure" in msg_v2
    assert meta_v2 == {}
    assert (out.read_bytes(), sidecar.read_bytes()) == before
    assert verify_package(out, trusted) == (True, "ok")
    assert sorted(p.name for p in out_dir.iterdir()) == ["demo-app.smdc-app", "demo-app.smdc-app.sig"]

    # Also verify a fresh package path leaves no orphan archive if sidecar replace fails.
    fresh_dir = tmp_path / "pkg_fresh"
    fresh_out = fresh_dir / "fresh-app.smdc-app"
    fresh_sidecar = package_sidecar_path(fresh_out)

    def fail_fresh_sidecar_replace(src: Any, dst: Any) -> None:
        if Path(dst) == fresh_sidecar:
            raise OSError(errno.EIO, "simulated fresh sidecar replace failure")
        real_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(manager_mod.os, "replace", fail_fresh_sidecar_replace)
        ok_fresh, _, _ = manager.package_app(app_dir, output_path=fresh_out, sign_pqc=True)

    assert ok_fresh is False
    assert list(fresh_dir.iterdir()) == []
