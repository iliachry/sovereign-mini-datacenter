"""Regression tests for tri-council review round 19.

1. ``node_state._state_lock()`` propagates inner ``OSError`` exceptions cleanly
   without wrapping them in ``RuntimeError: generator didn't stop after throw()``.
2. Windows (``fcntl=None``) ``exclusive_file_lock`` refuses to steal aged locks
   or ``.reap`` markers whose holding process/thread is still alive, and resets
   the lock mtime upon acquisition.
3. ``EnterpriseManager.start_app()`` revalidates load shedding against the
   current shedding policy and manifest after ``enterprise.<app_id>.started``
   even when the callback both changes the shedding level and re-registers the app.
4. Legacy wallet migration, state directory fallback, and DTN spool discovery
   tolerate the absence of POSIX ``os.getuid()`` on Windows.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

import sovereign_dc.config as config_mod
import sovereign_dc.economy.node_state as node_state
import sovereign_dc.enterprise.registry as registry_mod
from sovereign_dc.config import get_state_dir
from sovereign_dc.economy.node_state import load_node_wallet
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry, _claim_reap_file
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from sovereign_dc.space.dtn.bundle import Bundle
from sovereign_dc.space.dtn.router import DTNRouter, default_spool_path


def test_state_lock_propagates_inner_oserror_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    for fcntl_val in (node_state.fcntl, None):
        monkeypatch.setattr(node_state, "fcntl", fcntl_val)
        with pytest.raises(PermissionError, match="wallet is read-only"):
            with node_state._state_lock():
                raise PermissionError("wallet is read-only")


def test_live_windows_lock_and_reap_are_not_stolen_on_timestamp_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry_mod, "fcntl", None)
    monkeypatch.setattr(registry_mod, "LOCK_STALE_SECONDS", 0.0)
    monkeypatch.setattr(registry_mod, "LOCK_POLL_SECONDS", 0.01)

    reg_file = tmp_path / "registry.json"
    excl_path = tmp_path / "registry.json.lock.excl"
    writer_a = EnterpriseRegistry(registry_file=reg_file)
    writer_b = EnterpriseRegistry(registry_file=reg_file)

    orig_read_reg = EnterpriseRegistry._read_registry_file
    a_holding = threading.Event()
    b_timed_out = threading.Event()

    def paused_read(self: EnterpriseRegistry):  # type: ignore[no-untyped-def]
        if threading.current_thread().name == "live-a" and excl_path.exists():
            old_ts = time.time() - 120.0
            os.utime(excl_path, (old_ts, old_ts))
            a_holding.set()
            assert b_timed_out.wait(timeout=5.0)
        return orig_read_reg(self)

    monkeypatch.setattr(EnterpriseRegistry, "_read_registry_file", paused_read)
    monkeypatch.setattr(registry_mod, "LOCK_TIMEOUT_SECONDS", 0.12)

    a_res: list[tuple[bool, list[str]]] = []
    t_a = threading.Thread(
        target=lambda: a_res.append(writer_a.register_app(EnterpriseRegistry.scaffold_manifest("App A", "app-a"))),
        name="live-a",
    )
    t_a.start()
    assert a_holding.wait(timeout=5.0)

    # Even with LOCK_STALE_SECONDS=0 and backdated excl mtime, live writer-a cannot be stolen.
    ok_b, errs_b = writer_b.register_app(EnterpriseRegistry.scaffold_manifest("App B", "app-b"))
    assert ok_b is False
    assert errs_b and "held by another process" in errs_b[0]
    with pytest.raises(TimeoutError):
        with registry_mod.exclusive_file_lock(reg_file.with_name(f"{reg_file.name}.lock"), use_fcntl=None):
            pass
    b_timed_out.set()
    t_a.join(timeout=5.0)
    assert a_res == [(True, [])]

    monkeypatch.setattr(registry_mod, "LOCK_TIMEOUT_SECONDS", 5.0)
    assert writer_b.register_app(EnterpriseRegistry.scaffold_manifest("App B", "app-b")) == (True, [])
    assert sorted(a.app_id for a in EnterpriseRegistry(registry_file=reg_file).list_apps()) == ["app-a", "app-b"]

    # Live .reap marker aged beyond stale threshold is not bypassed via .r1; dead one is.
    reap_base = tmp_path / "marker.lock.reap.deadbeef"
    live_token = f"{os.getpid()}:{threading.get_ident()}:live\n".encode()
    reap_base.write_bytes(live_token)
    old_ts = time.time() - 120.0
    os.utime(reap_base, (old_ts, old_ts))
    assert _claim_reap_file(reap_base, 0.05, 0.0, 0.01, owner_token=b"other") is None

    reap_base.write_bytes(b"99999999:1:dead\n")
    os.utime(reap_base, (old_ts, old_ts))
    hopped = _claim_reap_file(reap_base, 0.2, 0.0, 0.01, owner_token=live_token)
    assert hopped is not None and hopped.name.endswith(".r1")
    for p in tmp_path.glob("marker.lock.reap.*"):
        p.unlink()

    # mtime is refreshed upon acquisition even when contention delayed acquiring the lock.
    excl_lock_path = tmp_path / "fresh.lock"
    orig_link = os.link
    seen_utime: list[Path] = []
    orig_utime = os.utime

    def track_utime(path: os.PathLike[str] | str, times: object = None) -> None:
        if os.fspath(path).endswith(".excl"):
            seen_utime.append(Path(os.fspath(path)))
        orig_utime(path, times)  # type: ignore[arg-type]

    monkeypatch.setattr(registry_mod.os, "utime", track_utime)
    monkeypatch.setattr(registry_mod.os, "link", orig_link)
    with registry_mod.exclusive_file_lock(excl_lock_path, use_fcntl=None):
        assert any(p.name == "fresh.lock.excl" for p in seen_utime)


@pytest.mark.parametrize("initial_level", [None, 0])
def test_start_app_revalidates_against_post_callback_shedding_policy(initial_level: int | None, tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App Crit", "app-crit", power_tier=PowerPriority.L0_CRITICAL)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    if initial_level is not None:
        bus.publish(
            Event(
                event_type="load_shedding.changed", source="test", payload={"level": initial_level, "battery_soc": 90.0}
            )
        )

    paused_events: list[Event] = []
    bus.subscribe("enterprise.app-crit.paused", paused_events.append)

    fired: list[bool] = []

    def escalate_and_downgrade(_event: Event) -> None:
        if not fired:
            fired.append(True)
            bus.publish(
                Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0})
            )
            assert registry.register_app(
                EnterpriseRegistry.scaffold_manifest("App Crit", "app-crit", power_tier=PowerPriority.L4_IDLE)
            )[0]

    bus.subscribe("enterprise.app-crit.started", escalate_and_downgrade)
    ok, msg = manager.start_app("app-crit")
    assert ok is True
    assert "paused by load shedding" in msg

    state = manager.get_runtime_state("app-crit")
    assert state is not None
    assert state.status == AppStatus.PAUSED
    assert "app-crit" in manager._shed_paused
    assert len(paused_events) == 1


def test_windows_legacy_migration_and_dtn_spool_work_without_os_getuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_tmp = tmp_path / "win_temp"
    legacy_tmp.mkdir()
    legacy_wallet_path = legacy_tmp / "smdc_default_wallet.json"
    legacy_wallet = NodeWallet.create(node_id="win-legacy")
    legacy_wallet.save_to_file(str(legacy_wallet_path))

    legacy_spool = legacy_tmp / "dtn_spool.db"
    router = DTNRouter(db_path=str(legacy_spool), local_node_eid="dtn://win/")
    router.queue_bundle(Bundle(source_eid="dtn://win/", destination_eid="dtn://sat/", payload=b"hi"))
    router.close()

    state_dir = tmp_path / "state"
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setenv("TEMP", str(legacy_tmp))
    monkeypatch.setattr(node_state, "_legacy_cli_tmp_dir", lambda: str(legacy_tmp))
    monkeypatch.setattr(config_mod, "_FALLBACK_STATE_DIR", None)
    monkeypatch.delattr(os, "getuid", raising=False)

    adopted = load_node_wallet()
    assert adopted.address == legacy_wallet.address

    dtn_state_dir = tmp_path / "fresh_dtn_state"
    monkeypatch.setenv("SMDC_STATE_DIR", str(dtn_state_dir))
    resolved_spool = default_spool_path()
    assert os.path.realpath(resolved_spool) == os.path.realpath(legacy_spool)

    # get_state_dir() fallback works when os.getuid is absent and SMDC_STATE_DIR fails mkdir.
    monkeypatch.setenv("SMDC_STATE_DIR", "/dev/null/impossible")
    fb = get_state_dir()
    assert Path(fb).is_dir()
