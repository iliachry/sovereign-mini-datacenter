"""Regression tests for tri-council review round 13.

1. Concurrent stale-lock takeover in ``backup.sh`` ``acquire_lock`` admits at
   most one writer even when two contenders read the same dead PID.
2. ``backup.sh`` and ``restore.sh`` refuse a carriage return before a multiline
   opening quote (and a carriage return inside a key).
3. ``AppLifecycleHandler`` subscribes to ``.started`` / ``.stopped`` and
   serializes state-event checks under a lock so ``restart_app()`` or
   concurrent publisher threads cannot invert ``is_paused``.
4. ``ARCHITECTURE.md`` and ``COMMERCIALIZATION.md`` match shipped Meshtastic,
   telemetry, shedding, and DTN capabilities.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round7 import ROOT, _env_file_value
from tests.test_regress_round12 import _acquire_lock_source


def test_concurrent_stale_lock_takeover_admits_only_one_writer(tmp_path: Path) -> None:
    fn = _acquire_lock_source()
    preamble = (
        'set -eu\nDUMP_PARENT="$1"\nLOCK_DIR="$DUMP_PARENT/.smdc-backup.lock"\n'
        'LOCK_HELD=false\nwarn() { printf "%s\\n" "$*"; }\n'
        'err() { printf "%s\\n" "$*" >&2; exit 1; }\n'
    )
    second = tmp_path / "second.sh"
    second.write_text(
        preamble + fn + '\nacquire_lock\nprintf "second pid=%s acquired=%s\\n" "$$" "$LOCK_HELD"\n',
        encoding="utf-8",
    )
    first = tmp_path / "first.sh"
    first.write_text(
        preamble
        + fn
        + """
ln() {
    if [ "$1" = "-s" ] && [ "${3:-}" = "$LOCK_DIR/.takeover" ] && [ "${INJECTED:-0}" = 0 ]; then
        INJECTED=1
        /bin/sh "$SECOND" "$DUMP_PARENT"
    fi
    command ln "$@"
}
acquire_lock
printf "first pid=%s acquired=%s\\n" "$$" "$LOCK_HELD"
""",
        encoding="utf-8",
    )

    dumps = tmp_path / "dumps"
    (dumps / ".smdc-backup.lock").mkdir(parents=True)
    (dumps / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")

    res = subprocess.run(  # noqa: S603
        ["/bin/sh", str(first), str(dumps)],  # noqa: S607
        capture_output=True,
        text=True,
        env={**os.environ, "SECOND": str(second)},
        check=False,
    )
    assert res.returncode != 0
    second_lines = [line for line in res.stdout.splitlines() if line.startswith("second pid=")]
    assert len(second_lines) == 1 and "acquired=true" in second_lines[0]
    second_pid = second_lines[0].split("pid=")[1].split()[0]
    assert not any(line.startswith("first pid=") and "acquired=true" in line for line in res.stdout.splitlines())
    assert (dumps / ".smdc-backup.lock" / "pid").read_text(encoding="utf-8").strip() == second_pid


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
@pytest.mark.parametrize(
    ("name", "raw_bytes"),
    [
        ("cr_before_multiline_quote", b'RESTIC_PASSWORD=actual\nOTHER=\r"multiline\nRESTIC_PASSWORD=wrong\nend"\n'),
        ("cr_in_key", b"RESTIC_PASSWORD\r=wrong\n"),
    ],
)
def test_backup_scripts_refuse_cr_before_multiline_opening_quote(
    script: str, name: str, raw_bytes: bytes, tmp_path: Path
) -> None:
    env_file = tmp_path / f"{name}.env"
    env_file.write_bytes(raw_bytes)
    result = _env_file_value(script, env_file, "RESTIC_PASSWORD")
    assert result.returncode == 2 and result.stdout == ""


def test_restart_during_pause_delivery_keeps_sdk_unpaused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = SovereignEventBus()
    monkeypatch.setattr("sovereign_dc.events.get_event_bus", lambda: bus)

    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App", "app-x", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    assert manager.start_app("app-x")[0]

    restarted: list[bool] = []

    def restart_on_pause(_event: Event) -> None:
        if not restarted:
            restarted.append(True)
            manager.restart_app("app-x")

    bus.subscribe("enterprise.app-x.paused", restart_on_pause)

    handler = AppLifecycleHandler("app-x", client=SMDCClient(base_url="http://127.0.0.1:1"))
    try:
        assert manager.pause_app("app-x")[0]
        state = manager.get_runtime_state("app-x")
        assert state is not None and state.status == AppStatus.RUNNING
        assert handler.is_paused is False
    finally:
        handler.stop()


def test_round13_doc_alignment() -> None:
    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "AES-256-GCM" not in arch
    assert "AES-256-CTR" in arch

    comm = (ROOT / "COMMERCIALIZATION.md").read_text(encoding="utf-8")
    assert "RS485 Native" not in comm
    assert "dynamically negotiates GPU batch priorities" not in comm
    assert "without dropping packets" not in comm
