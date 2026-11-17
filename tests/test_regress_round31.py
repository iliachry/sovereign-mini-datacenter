"""Regression tests for tri-council review round 31.

1. ``software/backup/backup.sh`` qualifies lock/takeover markers with OS boot identity
   so a pre-reboot stale lock (even whose PID or ``.takeover`` PID was reused by a
   live post-reboot process) is cleanly taken over, while a live same-boot holder still
   blocks concurrent runs.
2. ``hardware/ASSEMBLY_MANUAL.md`` uses a single apostrophe in ``app's``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.test_regress_round14 import _cleanup_and_lock_sources
from tests.test_regress_round15 import _backup_lock_preamble

ROOT = Path(__file__).resolve().parent.parent


def _lock_harness(body: str) -> str:
    return (
        _backup_lock_preamble(with_traps=False)
        + _cleanup_and_lock_sources()
        + "\ntrap cleanup EXIT\ntrap 'exit 129' HUP\ntrap 'exit 130' INT\ntrap 'exit 143' TERM\n"
        + body
    )


def test_backup_lock_takes_over_stale_boot_lock_even_when_pid_is_reused_by_live_process(tmp_path: Path) -> None:
    normal = tmp_path / "normal.sh"
    normal.write_text(
        _lock_harness(
            'acquire_lock\nprintf "held=%s pid=%s boot=%s\n" "$LOCK_HELD" "$(cat "$LOCK_DIR/pid")" "$(cat "$LOCK_DIR/boot" 2>/dev/null || true)"\n'
        ),
        encoding="utf-8",
    )

    # Case A: stale lock from a prior OS boot whose pid and .takeover symlink collide
    # with currently live PIDs on the new boot must be taken over cleanly.
    dumps_stale = tmp_path / "dumps_stale"
    lock_stale = dumps_stale / ".smdc-backup.lock"
    lock_stale.mkdir(parents=True)
    reused_pid = str(os.getpid())
    (lock_stale / "pid").write_text(f"{reused_pid}\n", encoding="utf-8")
    (lock_stale / "boot").write_text(f"{reused_pid}:pre-powercut-boot-id\n", encoding="utf-8")
    os.symlink("1:boot=pre-powercut-boot-id", lock_stale / ".takeover")

    res_stale = subprocess.run(["/bin/sh", str(normal), str(dumps_stale)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_stale.returncode == 0, res_stale.stderr
    assert "held=true" in res_stale.stdout
    assert "pre-powercut-boot-id" not in res_stale.stdout
    assert not lock_stale.exists()

    # Case B: live child holding a current-boot lock blocks a second concurrent run.
    holder = tmp_path / "holder.sh"
    holder.write_text(
        _lock_harness('acquire_lock\nprintf "READY\n"\nsleep 15\n'),
        encoding="utf-8",
    )
    dumps_live = tmp_path / "dumps_live"
    lock_live = dumps_live / ".smdc-backup.lock"
    proc = subprocess.Popen(  # noqa: S603
        ["/bin/sh", str(holder), str(dumps_live)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        ready = proc.stdout.readline().strip()
        assert ready == "READY"
        assert (lock_live / "pid").read_text(encoding="utf-8").strip() == str(proc.pid)
        boot_marker = (lock_live / "boot").read_text(encoding="utf-8").strip()
        assert boot_marker.startswith(f"{proc.pid}:")

        res_contend = subprocess.run(  # noqa: S603, S607
            ["/bin/sh", str(normal), str(dumps_live)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_contend.returncode != 0
        assert "Another backup.sh dump run" in res_contend.stderr
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_assembly_manual_apostrophe_clean() -> None:
    manual = (ROOT / "hardware" / "ASSEMBLY_MANUAL.md").read_text(encoding="utf-8")
    assert "app'''s" not in manual
    assert "app's `min_battery_soc`" in manual
