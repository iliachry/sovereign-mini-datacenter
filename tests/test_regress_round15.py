"""Regression tests for tri-council review round 15.

1. Concurrent dead-``.takeover`` reaping in ``backup.sh`` ``acquire_lock``
   serializes contenders and admits at most one writer, including recovery from
   an interrupted prior reaper.
2. An interrupted round-13 directory-form ``.takeover`` marker left on disk
   does not permanently block subsequent backups and serializes concurrent
   directory cleanup.
3. A pending load-shedding pause plan does not pause an application whose
   power tier was reclassified to ``L0_CRITICAL`` during an earlier app's pause
   callback.
4. ``EnterpriseRegistry`` captures its ``_loaded_signature`` from
   ``os.fstat(f.fileno())`` during ``_read_registry_file()`` rather than a
   post-load ``stat()`` so concurrent replacements during ``__init__`` are
   detected on the next ``refresh()``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import sovereign_dc.enterprise.registry as registry_mod
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round14 import _cleanup_and_lock_sources


def _backup_lock_preamble(*, with_traps: bool = True) -> str:
    base = (
        'set -eu\nDUMP_PARENT="$1"\nLOCK_DIR="$DUMP_PARENT/.smdc-backup.lock"\n'
        'CURRENT_LINK="$DUMP_PARENT/current"\nGEN_NEW=""\n'
        'LOCK_HELD=false\nTAKEOVER_HELD=false\nREAP_LINK=""\n'
        "nc_maintenance_off() { return 0; }\n"
        'warn() { printf "%s\\n" "$*"; }\n'
        'err() { printf "%s\\n" "$*" >&2; exit 1; }\n'
    )
    if with_traps:
        base += "trap cleanup EXIT\ntrap 'exit 129' HUP\ntrap 'exit 130' INT\ntrap 'exit 143' TERM\n"
    return base


def test_concurrent_dead_takeover_reap_admits_only_one_writer(tmp_path: Path) -> None:
    fns = _cleanup_and_lock_sources()
    race_preamble = _backup_lock_preamble(with_traps=False)
    trap_preamble = _backup_lock_preamble(with_traps=True)

    second = tmp_path / "second.sh"
    second.write_text(
        race_preamble + fns + '\nacquire_lock\nprintf "second pid=%s held=%s\\n" "$$" "$LOCK_HELD"\n',
        encoding="utf-8",
    )

    first = tmp_path / "first.sh"
    first.write_text(
        race_preamble
        + fns
        + """
readlink() {
    rl_out="$(command readlink "$@")"
    rl_rc=$?
    if [ "${1:-}" = "$LOCK_DIR/.takeover" ] && [ ! -f "$INJECT_FLAG" ]; then
        touch "$INJECT_FLAG"
        /bin/sh "$SECOND" "$DUMP_PARENT" >> "$SECOND_OUT"
    fi
    printf "%s\\n" "$rl_out"
    return "$rl_rc"
}
acquire_lock
printf "first pid=%s held=%s\\n" "$$" "$LOCK_HELD"
""",
        encoding="utf-8",
    )

    dumps = tmp_path / "dumps_race"
    (dumps / ".smdc-backup.lock").mkdir(parents=True)
    (dumps / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    os.symlink("999998", dumps / ".smdc-backup.lock" / ".takeover")
    inject_flag = tmp_path / "injected.flag"
    second_out = tmp_path / "second.out"

    res = subprocess.run(  # noqa: S603
        ["/bin/sh", str(first), str(dumps)],  # noqa: S607
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SECOND": str(second),
            "INJECT_FLAG": str(inject_flag),
            "SECOND_OUT": str(second_out),
        },
        check=False,
    )
    assert res.returncode != 0
    assert "first pid=" not in res.stdout
    second_lines = [
        line for line in second_out.read_text(encoding="utf-8").splitlines() if line.startswith("second pid=")
    ]
    assert len(second_lines) == 1 and "held=true" in second_lines[0]
    second_pid = second_lines[0].split("pid=")[1].split()[0]
    assert (dumps / ".smdc-backup.lock" / "pid").read_text(encoding="utf-8").strip() == second_pid
    assert not list((dumps / ".smdc-backup.lock").glob(".takeover*"))

    normal = tmp_path / "normal.sh"
    normal.write_text(
        trap_preamble + fns + '\nacquire_lock\nprintf "normal pid=%s held=%s\\n" "$$" "$LOCK_HELD"\n',
        encoding="utf-8",
    )

    # Case A2: A reads dead tk_pid while B stops holding live .takeover (pid still 999999);
    # when A resumes, B's live reaper claim blocks A rather than clobbering B's marker.
    dumps_mid = tmp_path / "dumps_mid_race"
    (dumps_mid / ".smdc-backup.lock").mkdir(parents=True)
    (dumps_mid / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    os.symlink("999998", dumps_mid / ".smdc-backup.lock" / ".takeover")
    a_paused = tmp_path / "a_paused.flag"
    a_go = tmp_path / "a_go.flag"
    b_holding = tmp_path / "b_holding.flag"
    b_go = tmp_path / "b_go.flag"

    first_mid = tmp_path / "first_mid.sh"
    first_mid.write_text(
        race_preamble
        + fns
        + """
readlink() {
    rl_out="$(command readlink "$@")"
    rl_rc=$?
    if [ "${1:-}" = "$LOCK_DIR/.takeover" ] && [ ! -f "$A_PAUSED" ]; then
        touch "$A_PAUSED"
        while [ ! -f "$A_GO" ]; do
            sleep 0.01
        done
    fi
    printf "%s\n" "$rl_out"
    return "$rl_rc"
}
acquire_lock
printf "first_mid pid=%s held=%s\n" "$$" "$LOCK_HELD"
""",
        encoding="utf-8",
    )
    second_mid = tmp_path / "second_mid.sh"
    second_mid.write_text(
        race_preamble
        + fns
        + """
cat() {
    command cat "$@"
    cat_rc=$?
    if [ "${1:-}" = "$LOCK_DIR/pid" ] && [ "${TAKEOVER_HELD:-false}" = "true" ] && [ ! -f "$B_HOLDING" ]; then
        touch "$B_HOLDING"
        while [ ! -f "$B_GO" ]; do
            sleep 0.01
        done
    fi
    return "$cat_rc"
}
acquire_lock
printf "second_mid pid=%s held=%s\n" "$$" "$LOCK_HELD"
""",
        encoding="utf-8",
    )
    proc_a = subprocess.Popen(  # noqa: S603
        ["/bin/sh", str(first_mid), str(dumps_mid)],  # noqa: S607
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "A_PAUSED": str(a_paused), "A_GO": str(a_go)},
    )
    try:
        deadline = time.monotonic() + 5.0
        while not a_paused.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert a_paused.exists()
        proc_b = subprocess.Popen(  # noqa: S603
            ["/bin/sh", str(second_mid), str(dumps_mid)],  # noqa: S607
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "B_HOLDING": str(b_holding), "B_GO": str(b_go)},
        )
        try:
            deadline = time.monotonic() + 5.0
            while not b_holding.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert b_holding.exists()
            a_go.touch()
            a_out, _a_err = proc_a.communicate(timeout=5.0)
            assert proc_a.returncode != 0
            assert "first_mid pid=" not in a_out
            b_go.touch()
            b_out, _b_err = proc_b.communicate(timeout=5.0)
            assert proc_b.returncode == 0
            assert f"second_mid pid={proc_b.pid} held=true" in b_out
            assert (dumps_mid / ".smdc-backup.lock" / "pid").read_text(encoding="utf-8").strip() == str(proc_b.pid)
        finally:
            b_go.touch()
            if proc_b.poll() is None:
                proc_b.kill()
                proc_b.wait(timeout=5.0)
    finally:
        a_go.touch()
        if proc_a.poll() is None:
            proc_a.kill()
            proc_a.wait(timeout=5.0)

    # Case B: dead reaper claim symlink left by SIGKILL also recovers cleanly.
    dumps_reap = tmp_path / "dumps_dead_reap"
    (dumps_reap / ".smdc-backup.lock").mkdir(parents=True)
    (dumps_reap / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    os.symlink("999998", dumps_reap / ".smdc-backup.lock" / ".takeover")
    os.symlink("999997", dumps_reap / ".smdc-backup.lock" / ".takeover.reap.999999.tk.999998")
    res_dead_reap = subprocess.run(
        ["/bin/sh", str(normal), str(dumps_reap)], capture_output=True, text=True, check=False
    )  # noqa: S603, S607
    assert res_dead_reap.returncode == 0 and "held=true" in res_dead_reap.stdout
    assert not (dumps_reap / ".smdc-backup.lock").exists()

    # Case C: live reaper claim symlink held by an active PID blocks contenders.
    dumps_live_reap = tmp_path / "dumps_live_reap"
    (dumps_live_reap / ".smdc-backup.lock").mkdir(parents=True)
    (dumps_live_reap / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    os.symlink("999998", dumps_live_reap / ".smdc-backup.lock" / ".takeover")
    os.symlink(str(os.getpid()), dumps_live_reap / ".smdc-backup.lock" / ".takeover.reap.999999.tk.999998")
    res_live_reap = subprocess.run(  # noqa: S603
        ["/bin/sh", str(normal), str(dumps_live_reap)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_live_reap.returncode != 0


def test_legacy_round13_takeover_directory_does_not_block_recovery(tmp_path: Path) -> None:
    fns = _cleanup_and_lock_sources()
    preamble = _backup_lock_preamble(with_traps=True)

    contender = tmp_path / "contender.sh"
    contender.write_text(
        preamble + fns + '\nacquire_lock\nprintf "contender pid=%s held=%s\\n" "$$" "$LOCK_HELD"\n',
        encoding="utf-8",
    )

    first = tmp_path / "first_dir.sh"
    first.write_text(
        preamble
        + fns
        + """
rm() {
    if [ "${1:-}" = "-rf" ] && [ "${2:-}" = "$LOCK_DIR/.takeover" ] && [ ! -f "$INJECT_FLAG" ]; then
        touch "$INJECT_FLAG"
        /bin/sh "$CONTENDER" "$DUMP_PARENT" >> "$CONTENDER_OUT" || true
    fi
    command rm "$@"
}
acquire_lock
printf "first pid=%s held=%s\\n" "$$" "$LOCK_HELD"
""",
        encoding="utf-8",
    )

    dumps = tmp_path / "dumps_legacy_dir"
    (dumps / ".smdc-backup.lock" / ".takeover").mkdir(parents=True)
    (dumps / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    # Non-empty legacy directory (e.g., an unpatched ln -s created .takeover/12345 inside it).
    os.symlink("12345", dumps / ".smdc-backup.lock" / ".takeover" / "12345")
    inject_flag = tmp_path / "dir_injected.flag"
    contender_out = tmp_path / "contender.out"

    res = subprocess.run(  # noqa: S603
        ["/bin/sh", str(first), str(dumps)],  # noqa: S607
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CONTENDER": str(contender),
            "INJECT_FLAG": str(inject_flag),
            "CONTENDER_OUT": str(contender_out),
        },
        check=False,
    )
    assert res.returncode == 0
    assert "first pid=" in res.stdout and "held=true" in res.stdout
    assert "contender pid=" not in contender_out.read_text(encoding="utf-8") if contender_out.exists() else True
    assert not (dumps / ".smdc-backup.lock").exists()

    # Subsequent stale lock after cleanup also acquires cleanly.
    (dumps / ".smdc-backup.lock").mkdir(parents=True)
    (dumps / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    res_second = subprocess.run(["/bin/sh", str(contender), str(dumps)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_second.returncode == 0 and "held=true" in res_second.stdout


def test_shedding_pause_skips_app_reclassified_to_critical_during_callback(tmp_path: Path) -> None:
    for sync_via_start_app in (True, False):
        reg_file = tmp_path / f"registry_{sync_via_start_app}.json"
        bus = SovereignEventBus()
        registry = EnterpriseRegistry(registry_file=reg_file)
        for app_id in ("app-a", "app-b"):
            assert registry.register_app(
                EnterpriseRegistry.scaffold_manifest(app_id, app_id, power_tier=PowerPriority.L4_IDLE)
            )[0]
        manager = EnterpriseManager(registry=registry, event_bus=bus)
        for app_id in ("app-a", "app-b"):
            assert manager.start_app(app_id)[0]

        paused_b_events: list[Event] = []
        bus.subscribe("enterprise.app-b.paused", paused_b_events.append)

        fired: list[bool] = []

        def upgrade_b_to_critical(
            _event: Event,
            *,
            use_start: bool = sync_via_start_app,
            rf: Path = reg_file,
            mgr: EnterpriseManager = manager,
            reg: EnterpriseRegistry = registry,
            seen: list[bool] = fired,
        ) -> None:
            if not seen:
                seen.append(True)
                writer = reg if use_start else EnterpriseRegistry(registry_file=rf)
                assert writer.register_app(
                    EnterpriseRegistry.scaffold_manifest("app-b", "app-b", power_tier=PowerPriority.L0_CRITICAL)
                )[0]
                if use_start:
                    assert mgr.start_app("app-b")[0]

        bus.subscribe("enterprise.app-a.paused", upgrade_b_to_critical)
        bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))

        assert fired
        state_a = manager.get_runtime_state("app-a")
        state_b = manager.get_runtime_state("app-b")
        assert state_a is not None and state_a.status == AppStatus.PAUSED
        assert state_b is not None
        assert state_b.status == AppStatus.RUNNING
        assert state_b.manifest.power.tier == PowerPriority.L0_CRITICAL
        assert "app-b" not in manager._shed_paused
        assert not paused_b_events


def test_registry_captures_signature_from_open_fd_during_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg_file = tmp_path / "registry.json"
    writer = EnterpriseRegistry(registry_file=reg_file)
    assert writer.register_app(EnterpriseRegistry.scaffold_manifest("App A", "app-a"))[0]

    real_json_load = json.load
    injected = False

    def racing_json_load(fp: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal injected
        data = real_json_load(fp, *args, **kwargs)
        if not injected:
            injected = True
            other = EnterpriseRegistry(registry_file=reg_file)
            assert other.register_app(EnterpriseRegistry.scaffold_manifest("App B", "app-b"))[0]
        return data

    monkeypatch.setattr(registry_mod.json, "load", racing_json_load)
    reader = EnterpriseRegistry(registry_file=reg_file)
    assert injected

    # The open fd captured the pre-replacement signature; refresh() detects app-b on next lookup.
    assert reader.get_app("app-a") is not None
    app_b = reader.get_app("app-b")
    assert app_b is not None and app_b.name == "App B"
