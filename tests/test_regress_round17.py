"""Regression tests for tri-council review round 17.

1. ``EnterpriseRegistry._exclusive_lock`` Windows (``fcntl=None``) stale-lock
   recovery serializes reaping and verifies ownership on release so concurrent
   writers cannot lose a registration when taking over an expired ``.lock.excl``.
2. ``EnterpriseManager._on_load_shedding_event`` keeps skipped shedding actions
   eligible for reconsideration when a later callback reclassifies the app back.
3. ``DTNRouter.get_queue_stats()`` reads queue totals and per-priority counts in
   one SQLite read transaction so concurrent enqueues from another router
   instance cannot mix revisions.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import sovereign_dc.enterprise.registry as registry_mod
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import AppManifest, PowerPolicy, PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter


def _manifest_with_floor(app_id: str, tier: PowerPriority, min_soc: float) -> AppManifest:
    base = EnterpriseRegistry.scaffold_manifest(app_id, app_id, power_tier=tier)
    base.power = PowerPolicy(tier=tier, min_battery_soc=min_soc, min_solar_watts=0.0)
    return base


def test_windows_registry_stale_lock_recovery_serializes_concurrent_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry_mod, "fcntl", None)
    reg_file = tmp_path / "registry.json"
    excl_path = tmp_path / "registry.json.lock.excl"
    excl_path.write_text("99999999\n", encoding="utf-8")
    stale_time = time.time() - (registry_mod.LOCK_STALE_SECONDS + 30.0)
    os.utime(excl_path, (stale_time, stale_time))

    writer_a = EnterpriseRegistry(registry_file=reg_file)
    writer_b = EnterpriseRegistry(registry_file=reg_file)

    orig_open = os.open
    a_saw_stale = threading.Event()
    b_done = threading.Event()
    b_result: list[tuple[bool, list[str]]] = []

    def intercepted_open(path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        # Pause writer A right after it observed stale .lock.excl, before claiming .reap.
        if threading.current_thread().name == "writer-a" and ".reap." in os.fspath(path) and not a_saw_stale.is_set():
            a_saw_stale.set()
            assert b_done.wait(timeout=5.0)
        return orig_open(path, flags, mode)

    monkeypatch.setattr(registry_mod.os, "open", intercepted_open)

    a_result: list[tuple[bool, list[str]]] = []

    def run_a() -> None:
        a_result.append(writer_a.register_app(EnterpriseRegistry.scaffold_manifest("App A", "app-a")))

    def run_b() -> None:
        assert a_saw_stale.wait(timeout=5.0)
        try:
            b_result.append(writer_b.register_app(EnterpriseRegistry.scaffold_manifest("App B", "app-b")))
        finally:
            b_done.set()

    t_a = threading.Thread(target=run_a, name="writer-a")
    t_b = threading.Thread(target=run_b, name="writer-b")
    t_a.start()
    t_b.start()
    t_a.join(timeout=10.0)
    t_b.join(timeout=10.0)

    assert a_result == [(True, [])]
    assert b_result == [(True, [])]
    reloaded = EnterpriseRegistry(registry_file=reg_file)
    assert sorted(app.app_id for app in reloaded.list_apps()) == ["app-a", "app-b"]
    assert not list(tmp_path.glob("registry.json.lock.*"))


def test_shedding_convergence_reconsiders_skipped_app_after_later_callback(tmp_path: Path) -> None:
    # Pause path: B starts L4_IDLE -> A's pause callback upgrades B to L0_CRITICAL
    # (B's pause skips) -> C's pause callback downgrades B back to L4_IDLE -> B must be paused.
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "reg_pause.json")
    for app_id in ("app-a", "app-b", "app-c"):
        assert registry.register_app(_manifest_with_floor(app_id, PowerPriority.L4_IDLE, 10.0))[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    for app_id in ("app-a", "app-b", "app-c"):
        assert manager.start_app(app_id)[0]

    bus.subscribe(
        "enterprise.app-a.paused",
        lambda _e: registry.register_app(_manifest_with_floor("app-b", PowerPriority.L0_CRITICAL, 10.0)),
    )
    bus.subscribe(
        "enterprise.app-c.paused",
        lambda _e: registry.register_app(_manifest_with_floor("app-b", PowerPriority.L4_IDLE, 10.0)),
    )

    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))
    states = {s.manifest.app_id: s.status for s in manager.list_runtime_states()}
    assert states == {"app-a": AppStatus.PAUSED, "app-b": AppStatus.PAUSED, "app-c": AppStatus.PAUSED}

    # Resume path: B is shedding-paused -> A's resume callback raises B's SoC floor to 95
    # (B's resume skips) -> C's resume callback lowers B's SoC floor back to 10 -> B must resume.
    bus2 = SovereignEventBus()
    registry2 = EnterpriseRegistry(registry_file=tmp_path / "reg_resume.json")
    for app_id in ("app-a", "app-b", "app-c"):
        assert registry2.register_app(_manifest_with_floor(app_id, PowerPriority.L4_IDLE, 10.0))[0]
    manager2 = EnterpriseManager(registry=registry2, event_bus=bus2)
    for app_id in ("app-a", "app-b", "app-c"):
        assert manager2.start_app(app_id)[0]

    bus2.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))
    bus2.subscribe(
        "enterprise.app-a.resumed",
        lambda _e: registry2.register_app(_manifest_with_floor("app-b", PowerPriority.L4_IDLE, 95.0)),
    )
    bus2.subscribe(
        "enterprise.app-c.resumed",
        lambda _e: registry2.register_app(_manifest_with_floor("app-b", PowerPriority.L4_IDLE, 10.0)),
    )

    bus2.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 0, "battery_soc": 90.0}))
    states2 = {s.manifest.app_id: s.status for s in manager2.list_runtime_states()}
    assert states2 == {"app-a": AppStatus.RUNNING, "app-b": AppStatus.RUNNING, "app-c": AppStatus.RUNNING}


def test_dtn_queue_stats_reads_consistent_snapshot_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "dtn_spool.db")
    writer = DTNRouter(db_path=db_path, local_node_eid="dtn://node-w/")
    reader = DTNRouter(db_path=db_path, local_node_eid="dtn://node-r/")
    try:
        assert writer.queue_bundle(
            Bundle(
                source_eid="dtn://node-w/",
                destination_eid="dtn://sat/",
                payload=b"normal-1",
                priority=BundlePriority.NORMAL,
            )
        )

        orig_get_conn = reader._get_connection
        enqueued_concurrent = False
        seen_statements: list[str] = []

        @contextlib.contextmanager
        def tracing_conn() -> Iterator[sqlite3.Connection]:
            with orig_get_conn() as conn:

                def on_trace(sql: str) -> None:
                    nonlocal enqueued_concurrent
                    seen_statements.append(sql.strip())
                    if "GROUP BY priority" in sql and not enqueued_concurrent:
                        enqueued_concurrent = True
                        assert writer.queue_bundle(
                            Bundle(
                                source_eid="dtn://node-w/",
                                destination_eid="dtn://sat/",
                                payload=b"critical-2",
                                priority=BundlePriority.CRITICAL,
                            )
                        )

                conn.set_trace_callback(on_trace)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        monkeypatch.setattr(reader, "_get_connection", tracing_conn)
        stats = reader.get_queue_stats()

        assert enqueued_concurrent
        assert "BEGIN" in seen_statements
        assert stats["queued_bundle_count"] == sum(stats["priority_counts"].values()) == 1
        assert stats["priority_counts"]["normal"] == 1
        assert stats["priority_counts"]["critical"] == 0

        latest = reader.get_queue_stats()
        assert latest["queued_bundle_count"] == sum(latest["priority_counts"].values()) == 2
        assert latest["priority_counts"]["normal"] == 1
        assert latest["priority_counts"]["critical"] == 1
    finally:
        reader.close()
        writer.close()
