"""Regression tests for tri-council review round 12.

1. ``backup.sh`` ``acquire_lock`` admits at most one dump writer even across the
   gap between ``mkdir "$LOCK_DIR"`` and writing ``"${LOCK_DIR}/pid"``.
2. Re-entrant lifecycle notifications do not invert ``AppLifecycleHandler.is_paused``
   against ``EnterpriseManager`` runtime state.
3. ``backup.sh`` and ``restore.sh`` refuse UTF-8 BOM and non-ASCII Unicode
   whitespace in ``.env`` rather than differing from Docker Compose.
4. ``ARCHITECTURE.md`` describes LoRa hardware and unencrypted DTN bundles
   consistently with the codebase, ``README.md``, and ``COMPLIANCE.md``.
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


def _acquire_lock_source() -> str:
    text = (ROOT / "software" / "backup" / "backup.sh").read_text(encoding="utf-8")
    start = text.index("acquire_lock() {")
    return text[start : text.index("\n}\n", start) + 3]


def test_backup_lock_acquisition_admits_only_one_writer_across_mkdir_pid_gap(tmp_path: Path) -> None:
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
mkdir() {
    command mkdir "$@" || return $?
    if [ "$*" = "$LOCK_DIR" ] && [ "${INJECTED:-0}" = 0 ]; then
        INJECTED=1
        /bin/sh "$SECOND" "$DUMP_PARENT"
    fi
}
acquire_lock
printf "first pid=%s acquired=%s\\n" "$$" "$LOCK_HELD"
""",
        encoding="utf-8",
    )
    dumps = tmp_path / "dumps"
    res = subprocess.run(  # noqa: S603
        ["/bin/sh", str(first), str(dumps)],  # noqa: S607
        capture_output=True,
        text=True,
        env={**os.environ, "SECOND": str(second)},
        check=False,
    )
    assert res.returncode != 0
    assert "acquired=true" in res.stdout
    second_lines = [line for line in res.stdout.splitlines() if line.startswith("second pid=")]
    assert len(second_lines) == 1
    second_pid = second_lines[0].split("pid=")[1].split()[0]
    assert not any(line.startswith("first pid=") and "acquired=true" in line for line in res.stdout.splitlines())
    assert (dumps / ".smdc-backup.lock" / "pid").read_text(encoding="utf-8").strip() == second_pid

    # A genuine stale lock with a dead PID is still taken over cleanly.
    stale_dir = tmp_path / "stale_dumps"
    (stale_dir / ".smdc-backup.lock").mkdir(parents=True)
    (stale_dir / ".smdc-backup.lock" / "pid").write_text("999999\n", encoding="utf-8")
    stale_res = subprocess.run(  # noqa: S603
        ["/bin/sh", str(second), str(stale_dir)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert stale_res.returncode == 0
    assert "second pid=" in stale_res.stdout and "acquired=true" in stale_res.stdout


@pytest.mark.parametrize("direction", ["recover_on_pause", "shed_on_resume"])
def test_reentrant_lifecycle_delivery_keeps_sdk_aligned_with_manager(
    direction: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = SovereignEventBus()
    monkeypatch.setattr("sovereign_dc.events.get_event_bus", lambda: bus)

    registry = EnterpriseRegistry(registry_file=tmp_path / f"{direction}.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App", "app-x", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    assert manager.start_app("app-x")[0]

    def shed(level: int, soc: float) -> None:
        bus.publish(
            Event(event_type="load_shedding.changed", source="test", payload={"level": level, "battery_soc": soc})
        )

    fired: list[bool] = []

    def react(_event: Event) -> None:
        if not fired:
            fired.append(True)
            if direction == "recover_on_pause":
                shed(0, 90.0)
            else:
                shed(4, 5.0)

    target_event = "enterprise.app-x.paused" if direction == "recover_on_pause" else "enterprise.app-x.resumed"
    bus.subscribe(target_event, react)

    handler = AppLifecycleHandler("app-x", client=SMDCClient(base_url="http://127.0.0.1:1"))
    try:
        shed(4, 5.0)
        if direction == "shed_on_resume":
            shed(0, 90.0)
        state = manager.get_runtime_state("app-x")
        assert state is not None
        if direction == "recover_on_pause":
            assert state.status == AppStatus.RUNNING and handler.is_paused is False
        else:
            assert state.status == AppStatus.PAUSED and handler.is_paused is True
    finally:
        handler.stop()


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
@pytest.mark.parametrize(
    ("name", "env_text"),
    [
        ("utf8_bom", "\ufeffRESTIC_PASSWORD=secret\n"),
        ("trailing_nbsp", "RESTIC_PASSWORD=secret\u00a0\n"),
        (
            "nbsp_before_multiline_quote",
            'RESTIC_PASSWORD=actual\nOTHER=\u00a0"multiline\nRESTIC_PASSWORD=wrong\nend"\n',
        ),
        ("vertical_tab", "RESTIC_PASSWORD=\x0bsecret\n"),
    ],
)
def test_backup_scripts_refuse_bom_and_unicode_whitespace(
    script: str, name: str, env_text: str, tmp_path: Path
) -> None:
    env_file = tmp_path / f"{name}.env"
    env_file.write_text(env_text, encoding="utf-8")
    result = _env_file_value(script, env_file, "RESTIC_PASSWORD")
    assert result.returncode == 2 and result.stdout == ""

    normal = tmp_path / "utf8_value.env"
    normal.write_text("RESTIC_PASSWORD=café_π_€\n", encoding="utf-8")
    ok = _env_file_value(script, normal, "RESTIC_PASSWORD")
    assert ok.returncode == 0 and ok.stdout == "café_π_€"


def test_architecture_md_matches_dtn_and_lora_reality() -> None:
    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "Encrypted bundles are queued" not in arch
    assert "driven by the ESP32 bridge" not in arch
    assert "Unencrypted, SHA-256 digest-verified bundles" in arch
