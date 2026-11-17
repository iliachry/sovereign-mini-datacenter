"""Regression tests for tri-council review round 24.

1. ``exclusive_file_lock`` Windows (``fcntl=None``) release retries transient sharing
   violations (``winerror in (5, 32, 33)``) instead of leaving the holder's live
   ``.excl`` file behind indefinitely.
2. Concurrent ``register_app()`` calls on the same ``EnterpriseRegistry`` instance do not
   lose committed registrations by clearing post-snapshot ``_pending`` entries.
3. Failed ``register_app()`` / ``unregister_app()`` reloads current disk state and never
   caches a rolled-back obsolete manifest under a newer ``_loaded_signature``.
"""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path

import pytest

import sovereign_dc.enterprise.registry as registry_mod
from sovereign_dc.enterprise.registry import EnterpriseRegistry, exclusive_file_lock
from sovereign_dc.enterprise.schema import PowerPriority


def test_windows_lock_release_retries_sharing_violation_and_removes_excl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "share.lock"
    excl_path = tmp_path / "share.lock.excl"

    orig_unlink = Path.unlink
    sharing_attempts = 0

    def transient_sharing_unlink(self: Path, missing_ok: bool = False) -> None:
        nonlocal sharing_attempts
        if self == excl_path and sharing_attempts < 2:
            sharing_attempts += 1
            err = PermissionError("sharing violation")
            err.winerror = 32  # type: ignore[attr-defined]
            raise err
        orig_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", transient_sharing_unlink)
    with exclusive_file_lock(lock_path, use_fcntl=None, timeout_seconds=1.0, poll_seconds=0.002):
        assert excl_path.exists()

    assert sharing_attempts == 2
    assert not excl_path.exists()


def test_concurrent_registrations_on_single_registry_instance_preserve_both_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg_file = tmp_path / "registry.json"
    shared_reg = EnterpriseRegistry(registry_file=reg_file)

    orig_replace = registry_mod.os.replace
    a_before_replace = threading.Event()
    b_started = threading.Event()

    def pause_a_before_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if threading.current_thread().name == "reg-a" and Path(dst) == reg_file and not a_before_replace.is_set():
            a_before_replace.set()
            b_started.wait(timeout=0.3)
        orig_replace(src, dst)

    monkeypatch.setattr(registry_mod.os, "replace", pause_a_before_replace)

    res_a: list[tuple[bool, list[str]]] = []
    res_b: list[tuple[bool, list[str]]] = []

    def do_a() -> None:
        res_a.append(shared_reg.register_app(EnterpriseRegistry.scaffold_manifest("App A", "app-a")))

    def do_b() -> None:
        assert a_before_replace.wait(timeout=5.0)
        b_started.set()
        res_b.append(shared_reg.register_app(EnterpriseRegistry.scaffold_manifest("App B", "app-b")))

    ta = threading.Thread(target=do_a, name="reg-a")
    tb = threading.Thread(target=do_b, name="reg-b")
    ta.start()
    tb.start()
    ta.join(timeout=10.0)
    tb.join(timeout=10.0)

    assert res_a == [(True, [])]
    assert res_b == [(True, [])]
    fresh_ids = sorted(app.app_id for app in EnterpriseRegistry(registry_file=reg_file).list_apps())
    assert fresh_ids == ["app-a", "app-b"]


def test_failed_registration_rollback_does_not_cache_stale_tier_under_new_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg_file = tmp_path / "registry.json"
    reg_a = EnterpriseRegistry(registry_file=reg_file)
    reg_b = EnterpriseRegistry(registry_file=reg_file)

    assert reg_a.register_app(
        EnterpriseRegistry.scaffold_manifest("Worker", "worker", power_tier=PowerPriority.L4_IDLE)
    ) == (True, [])
    # Writer B upgrades worker on disk to L0_CRITICAL while A still remembers L4_IDLE.
    assert reg_b.register_app(
        EnterpriseRegistry.scaffold_manifest("Worker", "worker", power_tier=PowerPriority.L0_CRITICAL)
    ) == (True, [])

    # Writer A attempts to update worker while save_registry fails with ENOSPC at os.replace.
    orig_replace = registry_mod.os.replace

    def fail_enospc(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if Path(dst) == reg_file:
            raise OSError(errno.ENOSPC, "No space left on device")
        orig_replace(src, dst)

    monkeypatch.setattr(registry_mod.os, "replace", fail_enospc)
    ok, errs = reg_a.register_app(
        EnterpriseRegistry.scaffold_manifest("Worker", "worker", power_tier=PowerPriority.L3_DEFERRABLE)
    )
    assert ok is False and errs

    seen = reg_a.get_app("worker")
    assert seen is not None
    assert seen.power.tier == PowerPriority.L0_CRITICAL
