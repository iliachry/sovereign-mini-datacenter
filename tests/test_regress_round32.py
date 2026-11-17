"""Regression tests for tri-council review round 32.

1. ``software/backup/backup.sh`` keeps ``(pid, boot)`` ownership transitions
   crash-consistent across ``SIGKILL`` mid-takeover and before fresh ``boot``
   rename, preventing reused live PIDs from blocking takeover recovery.
2. ``smdc economy audit [--anchor]`` reports distinct reasons for empty ledgers,
   unavailable node identity keys, and invalid chains, and suppresses the
   ``--anchor`` recommendation when the ledger has no transactions.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from sovereign_dc import cli
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.node_state import NodeWallet
from tests.test_regress_round14 import _cleanup_and_lock_sources
from tests.test_regress_round15 import _backup_lock_preamble


def _lock_harness(body: str, *, hooks: str = "") -> str:
    return (
        _backup_lock_preamble(with_traps=False)
        + _cleanup_and_lock_sources()
        + "\ntrap cleanup EXIT\ntrap 'exit 129' HUP\ntrap 'exit 130' INT\ntrap 'exit 143' TERM\n"
        + hooks
        + body
    )


def test_backup_lock_recovers_after_sigkill_mid_takeover_and_fresh_link(tmp_path: Path) -> None:
    normal = tmp_path / "normal.sh"
    normal.write_text(
        _lock_harness(
            'acquire_lock\nprintf "held=%s pid=%s boot=%s\\n" "$LOCK_HELD" "$(cat "$LOCK_DIR/pid")" "$(cat "$LOCK_DIR/boot" 2>/dev/null || true)"\n'
        ),
        encoding="utf-8",
    )
    crash_mv = tmp_path / "crash_mv.sh"
    crash_mv.write_text(
        _lock_harness(
            "acquire_lock\n",
            hooks="""
mv() {
    command mv "$@"
    mv_rc=$?
    if [ "${TAKEOVER_HELD:-false}" = "true" ] && [ "${3:-}" = "$LOCK_DIR/boot" ]; then
        kill -9 "$$"
    fi
    return "$mv_rc"
}
""",
        ),
        encoding="utf-8",
    )

    # Case A: Astra 32 exact scenario — pre-reboot lock with live reused PID,
    # killed via SIGKILL right after updating boot and before renaming pid (twice).
    dumps_a = tmp_path / "dumps_a"
    lock_a = dumps_a / ".smdc-backup.lock"
    lock_a.mkdir(parents=True)
    reused_pid = str(os.getpid())
    (lock_a / "pid").write_text(f"{reused_pid}\n", encoding="utf-8")
    (lock_a / "boot").write_text(f"{reused_pid}:pre-powercut-boot-id\n", encoding="utf-8")

    for _ in range(2):
        res_kill = subprocess.run(["/bin/sh", str(crash_mv), str(dumps_a)], capture_output=True, text=True, check=False)  # noqa: S603, S607
        assert res_kill.returncode in (-9, 137)
        assert (lock_a / "pid").read_text(encoding="utf-8").strip() == reused_pid
        assert (lock_a / ".takeover").is_symlink()

    res_recover = subprocess.run(["/bin/sh", str(normal), str(dumps_a)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_recover.returncode == 0, res_recover.stderr
    lines = res_recover.stdout.strip().splitlines()
    held_idx = next(i for i, line in enumerate(lines) if line.startswith("held="))
    assert lines[held_idx:] == [lines[held_idx]]
    assert lines[held_idx].startswith("held=true pid=")
    assert "pre-powercut-boot-id" not in res_recover.stdout
    assert not lock_a.exists()

    # Case B: Fresh lock killed right after hardlinking pid, before moving .boot.$$ -> boot.
    dumps_b = tmp_path / "dumps_b"
    lock_b = dumps_b / ".smdc-backup.lock"
    lock_b.mkdir(parents=True)
    (lock_b / "pid").write_text(f"{reused_pid}\n", encoding="utf-8")
    (lock_b / f".boot.{reused_pid}").write_text(f"{reused_pid}:pre-powercut-boot-id\n", encoding="utf-8")

    res_b = subprocess.run(["/bin/sh", str(normal), str(dumps_b)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_b.returncode == 0, res_b.stderr
    assert "held=true" in res_b.stdout
    assert not lock_b.exists()

    crash_ln = tmp_path / "crash_ln.sh"
    crash_ln.write_text(
        _lock_harness(
            "acquire_lock\n",
            hooks="""
ln() {
    command ln "$@"
    ln_rc=$?
    if [ "${2:-}" = "$LOCK_DIR/pid" ]; then
        kill -9 "$$"
    fi
    return "$ln_rc"
}
""",
        ),
        encoding="utf-8",
    )
    dumps_c = tmp_path / "dumps_c"
    lock_c = dumps_c / ".smdc-backup.lock"
    proc = subprocess.Popen(["/bin/sh", str(crash_ln), str(dumps_c)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
    rc = proc.wait(timeout=5)
    assert rc in (-9, 137)
    assert (lock_c / "pid").read_text(encoding="utf-8").strip() == str(proc.pid)
    assert not (lock_c / "boot").exists()
    staged_boot = (lock_c / f".boot.{proc.pid}").read_text(encoding="utf-8").strip()
    assert staged_boot.startswith(f"{proc.pid}:")


def test_economy_audit_empty_and_no_key_and_corrupt_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    db = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db)

    # Empty ledger: valid and absent, hint suppressed, --anchor reports empty (not invalid chain).
    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 0
    out_empty = capsys.readouterr().out
    assert "VALID" in out_empty
    assert "ABSENT" in out_empty
    assert "Empty ledger:" in out_empty
    assert "Run with --anchor" not in out_empty

    assert cli.cmd_economy_history(Namespace(db_path=db, limit=5)) is None
    assert "No transactions recorded yet in ledger." in capsys.readouterr().out

    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=True)) == 1
    out_empty_anchor = capsys.readouterr().out
    assert "nothing to anchor: the ledger has no transactions" in out_empty_anchor
    assert "the chain does not verify" not in out_empty_anchor
    assert ledger.anchor_chain() is False
    assert ledger.anchor_chain_with_reason() == (False, "empty")

    # Non-empty unanchored ledger: recommends --anchor, fails with no_key when key unavailable.
    wallet = NodeWallet.create(node_id="audit-node")
    ledger.mint(wallet.address, 5.0, memo="genesis")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM chain_anchor")
    conn.commit()
    conn.close()

    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 0
    out_unanchored = capsys.readouterr().out
    assert "Run with --anchor to anchor it." in out_unanchored

    db2 = str(tmp_path / "anchored_nokey.db")
    ledger2 = Ledger(db_path=db2)
    ledger2.mint(wallet.address, 1.0, memo="anchored")

    with monkeypatch.context() as mp:
        mp.setattr("sovereign_dc.economy.ledger._node_anchor_key", lambda: None)
        assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=True)) == 1
        out_nokey = capsys.readouterr().out
        assert "refused: node identity key unavailable" in out_nokey
        assert "the chain does not verify" not in out_nokey
        ledger2.mint(wallet.address, 1.0, memo="unadvanced")
        assert ledger2.verify_chain(require_anchor=True) is False
    assert ledger2.verify_chain() is False

    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=True)) == 0
    out_anchored = capsys.readouterr().out
    assert "chain verified and anchored with the node identity key" in out_anchored

    # Tampered ledger: --anchor refuses with invalid-chain reason.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE transactions SET memo = 'tampered'")
    conn.commit()
    conn.close()

    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=True)) == 1
    out_tampered = capsys.readouterr().out
    assert "refused: the chain does not verify" in out_tampered
