"""Regression tests for tri-council review round 14.

1. Interrupted stale-lock takeover (``SIGTERM`` cleanup or ``SIGKILL`` leaving
   a dead ``.takeover`` marker) does not permanently block subsequent backups.
2. A pending shedding-recovery plan does not lift a newer operator pause on a
   later application in the batch.
3. ``ARCHITECTURE.md`` and ``COMPLIANCE.md`` align on the unimplemented
   Meshtastic serial/TCP radio transport.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round7 import ROOT
from tests.test_regress_round12 import _acquire_lock_source


def _cleanup_and_lock_sources() -> str:
    text = (ROOT / "software" / "backup" / "backup.sh").read_text(encoding="utf-8")
    c_start = text.index("cleanup() {")
    c_end = text.index("\ntrap cleanup EXIT", c_start)
    return text[c_start:c_end] + "\n" + _acquire_lock_source()


def test_interrupted_stale_lock_takeover_does_not_block_subsequent_backups(tmp_path: Path) -> None:
    fns = _cleanup_and_lock_sources()
    preamble = (
        'set -eu\nDUMP_PARENT="$1"\nLOCK_DIR="$DUMP_PARENT/.smdc-backup.lock"\n'
        'CURRENT_LINK="$DUMP_PARENT/current"\nGEN_NEW=""\n'
        "LOCK_HELD=false\nTAKEOVER_HELD=false\n"
        "nc_maintenance_off() { return 0; }\n"
        'warn() { printf "%s\\n" "$*"; }\n'
        'err() { printf "%s\\n" "$*" >&2; exit 1; }\n'
    )
    traps = "trap cleanup EXIT\ntrap 'exit 129' HUP\ntrap 'exit 130' INT\ntrap 'exit 143' TERM\n"

    crashed = tmp_path / "crashed.sh"
    crashed.write_text(
        preamble
        + fns
        + traps
        + """
ln() {
    command ln "$@" || return $?
    if [ "$1" = "-s" ] && [ "${3:-}" = "$LOCK_DIR/.takeover" ]; then
        kill -TERM "$$"
    fi
}
acquire_lock
""",
        encoding="utf-8",
    )
    normal = tmp_path / "normal.sh"
    normal.write_text(
        preamble + fns + traps + '\nacquire_lock\nprintf "normal pid=%s held=%s\\n" "$$" "$LOCK_HELD"\n',
        encoding="utf-8",
    )

    # Case A: interrupted with SIGTERM right after creating .takeover.
    dumps_a = tmp_path / "dumps_a"
    (dumps_a / ".smdc-backup.lock").mkdir(parents=True)
    (dumps_a / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    res_crash = subprocess.run(["/bin/sh", str(crashed), str(dumps_a)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_crash.returncode == 143
    assert not (dumps_a / ".smdc-backup.lock" / ".takeover").exists()
    res_next = subprocess.run(["/bin/sh", str(normal), str(dumps_a)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_next.returncode == 0 and "held=true" in res_next.stdout

    # Case B: orphaned dead .takeover left behind (e.g., SIGKILL).
    dumps_b = tmp_path / "dumps_b"
    (dumps_b / ".smdc-backup.lock").mkdir(parents=True)
    (dumps_b / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    os.symlink("999998", dumps_b / ".smdc-backup.lock" / ".takeover")
    res_b = subprocess.run(["/bin/sh", str(normal), str(dumps_b)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_b.returncode == 0 and "held=true" in res_b.stdout

    # Case C: live .takeover owned by another active process still blocks.
    dumps_c = tmp_path / "dumps_c"
    (dumps_c / ".smdc-backup.lock").mkdir(parents=True)
    (dumps_c / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    os.symlink(str(os.getpid()), dumps_c / ".smdc-backup.lock" / ".takeover")
    res_c = subprocess.run(["/bin/sh", str(normal), str(dumps_c)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert res_c.returncode != 0


def test_shedding_recovery_does_not_lift_newer_operator_pause(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    for app_id in ("app-a", "app-b"):
        assert registry.register_app(
            EnterpriseRegistry.scaffold_manifest(app_id, app_id, power_tier=PowerPriority.L4_IDLE)
        )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    for app_id in ("app-a", "app-b"):
        assert manager.start_app(app_id)[0]

    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))
    assert {s.status for s in manager.list_runtime_states()} == {AppStatus.PAUSED}

    fired: list[bool] = []

    def operator_pause_b(_event: Event) -> None:
        if not fired:
            fired.append(True)
            assert manager.resume_app("app-b")[0]
            assert manager.pause_app("app-b", reason="operator maintenance hold")[0]

    bus.subscribe("enterprise.app-a.resumed", operator_pause_b)
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 0, "battery_soc": 90.0}))

    assert fired
    state_a = manager.get_runtime_state("app-a")
    state_b = manager.get_runtime_state("app-b")
    assert state_a is not None and state_a.status == AppStatus.RUNNING
    assert state_b is not None and state_b.status == AppStatus.PAUSED
    assert "app-b" not in manager._shed_paused


def test_round14_lora_doc_alignment() -> None:
    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "bridged on the host by" not in arch
    assert "serial/TCP radio attach is not implemented" in arch

    comp = (ROOT / "COMPLIANCE.md").read_text(encoding="utf-8")
    assert "serial/TCP radio transport is not implemented" in comp
