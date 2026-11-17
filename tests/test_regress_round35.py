"""Regression tests for tri-council review round 35.

1. ``Ledger._row_to_transaction()``, ``Transaction._well_formed()``,
   ``Ledger._anchor_matches()``, and ``Ledger._check_chain()`` avoid lossy
   ``int()``/``float()`` coercion on persisted SQLite rows and ``chain_anchor`` so
   fractional or malformed SQLite storage-class values (such as ``nonce = 0.5`` or
   ``hash_version = 2.5``) fail structural verification while still evaluating the
   head anchor cleanly.
"""

from __future__ import annotations

import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from sovereign_dc import cli
from sovereign_dc.economy.ledger import Ledger, Transaction
from sovereign_dc.economy.node_state import NodeWallet


def _sqlite_copy(src_db: str, dst_db: Path) -> str:
    src = sqlite3.connect(src_db)
    dst = sqlite3.connect(str(dst_db))
    src.backup(dst)
    src.close()
    dst.close()
    return str(dst_db)


def test_ledger_rejects_fractional_and_malformed_sqlite_fields_while_evaluating_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    alice = NodeWallet.create(node_id="alice-r35")
    bob = NodeWallet.create(node_id="bob-r35")
    base_db = str(tmp_path / "base.db")
    ledger = Ledger(db_path=base_db)
    ledger.mint(alice.address, 20.0, memo="mint")
    tx0 = ledger.transfer(alice, bob.address, 3.0, memo="tx0")
    ledger.transfer(alice, bob.address, 4.0, memo="tx1")

    assert ledger.chain_report() == (True, "valid", 3)

    mutations = (
        ("frac_nonce", "UPDATE transactions SET nonce = 0.5 WHERE tx_id = ?", (tx0.tx_id,)),
        ("frac_ver", "UPDATE transactions SET hash_version = 2.5 WHERE tx_id = ?", (tx0.tx_id,)),
        ("str_nonce", "UPDATE transactions SET nonce = 'not-an-int' WHERE tx_id = ?", (tx0.tx_id,)),
        ("str_amount", "UPDATE transactions SET amount = '1.0' WHERE tx_id = ?", (tx0.tx_id,)),
        ("int_memo", "UPDATE transactions SET memo = 0 WHERE tx_id = ?", (tx0.tx_id,)),
        ("null_memo", "UPDATE transactions SET memo = NULL WHERE tx_id = ?", (tx0.tx_id,)),
        ("null_sig_alg", "UPDATE transactions SET signature_algorithm = NULL WHERE tx_id = ?", (tx0.tx_id,)),
        ("null_settlement", "UPDATE transactions SET settlement_key = NULL WHERE tx_id = ?", (tx0.tx_id,)),
    )
    for name, sql, params in mutations:
        copy_db = _sqlite_copy(base_db, tmp_path / f"{name}.db")
        conn = sqlite3.connect(copy_db)
        conn.execute(sql, params)
        conn.commit()
        conn.close()

        tampered = Ledger(db_path=copy_db)
        assert tampered.chain_report() == (False, "valid", 3), name
        assert tampered.verify_chain() is False, name
        assert tampered.verify_chain(require_anchor=True) is False, name
        assert cli.cmd_economy_verify(Namespace(db_path=copy_db, anchor=False)) == 1, name
        out = capsys.readouterr().out
        links_line = next(line for line in out.splitlines() if "Chain links:" in line)
        anchor_line = next(line for line in out.splitlines() if "Head anchor:" in line)
        assert "INVALID" in links_line, name
        assert "VALID" in anchor_line and "INVALID" not in anchor_line, name

    anchor_copy = _sqlite_copy(base_db, tmp_path / "frac_anchor.db")
    conn = sqlite3.connect(anchor_copy)
    conn.execute("UPDATE chain_anchor SET tx_count = 3.5 WHERE id = 1")
    conn.commit()
    conn.close()
    bad_anchor = Ledger(db_path=anchor_copy)
    assert bad_anchor.chain_report() == (True, "invalid", 3)
    assert bad_anchor.verify_chain() is False

    tx_dict = tx0.to_dict()
    assert Transaction.from_dict({**tx_dict, "hash_version": 2.0}).is_valid() is False
    assert Transaction.from_dict({**tx_dict, "hash_version": True}).is_valid() is False
