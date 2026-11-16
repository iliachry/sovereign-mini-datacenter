"""Regression tests for tri-council review round 16.

1. Planned load-shedding recovery re-syncs the registry and re-evaluates shedding
   causes under the lock so an app whose manifest now requires shedding (e.g.
   raised ``min_battery_soc`` during an earlier callback) stays paused, and
   cross-app manifest reclassifications converge within one event.
2. ``Ledger.export_state()`` reads balances, transaction count, and chain
   verification in a single SQLite read transaction so concurrent writes from
   another ``Ledger`` instance cannot produce a mixed-revision export.
3. ``backup.sh`` and ``restore.sh`` accept single-line quoted ``.env`` values
   followed by tabs or mixed whitespace and a ``#`` comment, matching Docker
   Compose.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import AppManifest, PowerPolicy, PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round7 import _env_file_value


def _manifest_with_floor(app_id: str, tier: PowerPriority, min_soc: float) -> AppManifest:
    base = EnterpriseRegistry.scaffold_manifest(app_id, app_id, power_tier=tier)
    base.power = PowerPolicy(tier=tier, min_battery_soc=min_soc, min_solar_watts=0.0)
    return base


def test_shedding_recovery_skips_app_whose_manifest_now_requires_shedding(tmp_path: Path) -> None:
    for sync_via_same_registry in (True, False):
        reg_file = tmp_path / f"registry_{sync_via_same_registry}.json"
        bus = SovereignEventBus()
        registry = EnterpriseRegistry(registry_file=reg_file)
        for app_id in ("app-a", "app-b"):
            assert registry.register_app(_manifest_with_floor(app_id, PowerPriority.L4_IDLE, 10.0))[0]
        manager = EnterpriseManager(registry=registry, event_bus=bus)
        for app_id in ("app-a", "app-b"):
            assert manager.start_app(app_id)[0]

        bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))
        assert {s.status for s in manager.list_runtime_states()} == {AppStatus.PAUSED}

        resumed_b_events: list[Event] = []
        bus.subscribe("enterprise.app-b.resumed", resumed_b_events.append)

        fired: list[bool] = []

        def raise_b_floor(
            _event: Event,
            *,
            same_reg: bool = sync_via_same_registry,
            rf: Path = reg_file,
            reg: EnterpriseRegistry = registry,
            seen: list[bool] = fired,
        ) -> None:
            if not seen:
                seen.append(True)
                writer = reg if same_reg else EnterpriseRegistry(registry_file=rf)
                assert writer.register_app(_manifest_with_floor("app-b", PowerPriority.L4_IDLE, 95.0))[0]

        bus.subscribe("enterprise.app-a.resumed", raise_b_floor)
        bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 0, "battery_soc": 90.0}))

        assert fired
        state_a = manager.get_runtime_state("app-a")
        state_b = manager.get_runtime_state("app-b")
        assert state_a is not None and state_a.status == AppStatus.RUNNING
        assert state_b is not None
        assert state_b.status == AppStatus.PAUSED
        assert state_b.manifest.power.min_battery_soc == 95.0
        assert manager._shed_paused.get("app-b") == {"soc_floor"}
        assert not resumed_b_events


def test_shedding_callback_reclassification_of_other_app_converges(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(_manifest_with_floor("app-a", PowerPriority.L4_IDLE, 10.0))[0]
    assert registry.register_app(_manifest_with_floor("app-b", PowerPriority.L0_CRITICAL, 0.0))[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    assert manager.start_app("app-a")[0]
    assert manager.start_app("app-b")[0]

    fired: list[bool] = []

    def downgrade_b(_event: Event) -> None:
        if not fired:
            fired.append(True)
            assert registry.register_app(_manifest_with_floor("app-b", PowerPriority.L4_IDLE, 20.0))[0]

    bus.subscribe("enterprise.app-a.paused", downgrade_b)
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))

    assert fired
    state_a = manager.get_runtime_state("app-a")
    state_b = manager.get_runtime_state("app-b")
    assert state_a is not None and state_a.status == AppStatus.PAUSED
    assert state_b is not None and state_b.status == AppStatus.PAUSED


def test_ledger_export_state_reads_snapshot_atomically_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    db_path = str(tmp_path / "ledger.db")
    wallet = NodeWallet.create("round16-node")

    writer = Ledger(db_path=db_path)
    reader = Ledger(db_path=db_path)
    try:
        writer.mint(wallet.address, 1.0, memo="mint-1")
        orig_get_conn = reader._get_connection
        committed_concurrent = False
        seen_statements: list[str] = []

        @contextlib.contextmanager
        def tracing_conn() -> Iterator[sqlite3.Connection]:
            with orig_get_conn() as conn:

                def on_trace(sql: str) -> None:
                    nonlocal committed_concurrent
                    seen_statements.append(sql.strip())
                    if "COUNT(*) FROM transactions" in sql and not committed_concurrent:
                        committed_concurrent = True
                        writer.mint(wallet.address, 1.0, memo="mint-2")

                conn.set_trace_callback(on_trace)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        monkeypatch.setattr(reader, "_get_connection", tracing_conn)
        exported = reader.export_state()

        assert committed_concurrent
        # Export sees a mutually consistent snapshot from revision 1, never balance=1 with tx_count=2.
        assert exported["balances"][wallet.address]["balance"] == 1.0
        assert exported["total_transactions"] == 1
        assert exported["chain_verified"] is True
        assert exported["chain_anchor"] == "valid"
        assert "BEGIN" in seen_statements
        assert reader.get_balance(wallet.address) == 2.0

        latest = reader.export_state()
        assert latest["balances"][wallet.address]["balance"] == 2.0
        assert latest["total_transactions"] == 2
        assert latest["chain_verified"] is True
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_scripts_accept_quoted_values_with_mixed_whitespace_before_comment(script: str, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'A="hi"\t #comment\n'
        'B="hi"\t#comment\n'
        "C='hi'\t #comment\n"
        "D='hi' \t #comment\n"
        'E="hi"#comment\n'
        'F="hi"tail\n'
        'G="hi"\tgarbage\n',
        encoding="utf-8",
    )
    for key in ("A", "B", "C", "D", "E"):
        res = _env_file_value(script, env_file, key)
        assert res.returncode == 0 and res.stdout == "hi", (key, res.stdout, res.stderr)
    for bad_key in ("F", "G"):
        res_bad = _env_file_value(script, env_file, bad_key)
        assert res_bad.returncode == 2 and res_bad.stdout == "", (bad_key, res_bad.stdout)
