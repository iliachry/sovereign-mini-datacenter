"""Regression tests for tri-council review round 18.

1. Concurrent Windows (``fcntl=None``) node-wallet first-use initialization
   serializes across processes and uses non-overwriting atomic creation so an
   already-persisted private key cannot be truncated.
2. Registry fallback lock release serializes on the same ``.reap.<tag>`` mutex
   as stale-lock reaping so a releasing holder cannot unlink a replacement
   owner's live lock.
3. ``EnterpriseManager.start_app()`` revalidates load shedding after publishing
   ``enterprise.<app_id>.started`` even when initial causes before the
   callback were empty.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import sovereign_dc.economy.node_state as node_state
import sovereign_dc.enterprise.registry as registry_mod
from sovereign_dc.economy.node_state import load_node_wallet, mint_local_test_credits
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus


def _child_windows_load_wallet(state_dir: str, out_q: multiprocessing.Queue[str]) -> None:
    os.environ["SMDC_STATE_DIR"] = state_dir
    sys.modules["fcntl"] = None  # type: ignore[assignment]
    node_state.fcntl = None  # type: ignore[assignment]
    wallet = load_node_wallet()
    out_q.put(wallet.address)


def test_concurrent_windows_wallet_startup_preserves_first_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1) Cross-process Windows fallback race on fresh state dir: both children agree on one wallet.
    state_dir = tmp_path / "proc_state"
    ctx = multiprocessing.get_context("spawn")
    q: multiprocessing.Queue[str] = ctx.Queue()
    p1 = ctx.Process(target=_child_windows_load_wallet, args=(str(state_dir), q))
    p2 = ctx.Process(target=_child_windows_load_wallet, args=(str(state_dir), q))
    p1.start()
    p2.start()
    p1.join(timeout=15.0)
    p2.join(timeout=15.0)
    assert p1.exitcode == 0 and p2.exitcode == 0
    addrs = [q.get(timeout=2.0), q.get(timeout=2.0)]
    assert addrs[0] == addrs[1]
    assert not list(state_dir.glob("*.tmp.*"))
    assert not list(state_dir.glob("*.excl*"))

    # 2) Direct creation-file non-overwrite guard even if two callers reach _load_or_create_wallet.
    race_state = tmp_path / "create_guard"
    race_state.mkdir(parents=True)
    monkeypatch.setenv("SMDC_STATE_DIR", str(race_state))
    monkeypatch.setattr(node_state, "fcntl", None)
    wallet_path = race_state / "smdc_node_wallet.json"

    orig_create = NodeWallet.create
    a_created = threading.Event()
    b_done = threading.Event()
    wallets_seen: list[str] = []

    def intercepted_create(*args: object, **kwargs: object) -> NodeWallet:
        w = orig_create(*args, **kwargs)  # type: ignore[arg-type]
        if threading.current_thread().name == "wallet-a" and not a_created.is_set():
            a_created.set()
            assert b_done.wait(timeout=5.0)
        return w

    monkeypatch.setattr(node_state.NodeWallet, "create", staticmethod(intercepted_create))

    def run_a() -> None:
        w = node_state._load_or_create_wallet(str(wallet_path), AddressType.ED25519, None)
        wallets_seen.append(w.address)

    def run_b() -> None:
        assert a_created.wait(timeout=5.0)
        try:
            w = node_state._load_or_create_wallet(str(wallet_path), AddressType.ED25519, None)
            mint_local_test_credits(10.0)
            wallets_seen.append(w.address)
        finally:
            b_done.set()

    ta = threading.Thread(target=run_a, name="wallet-a")
    tb = threading.Thread(target=run_b, name="wallet-b")
    ta.start()
    tb.start()
    ta.join(timeout=10.0)
    tb.join(timeout=10.0)

    assert len(wallets_seen) == 2 and wallets_seen[0] == wallets_seen[1]
    from sovereign_dc.economy.node_state import open_node_ledger

    assert open_node_ledger().get_balance(wallets_seen[0]) == 10.0


def test_windows_registry_lock_release_never_deletes_replacement_owner_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry_mod, "fcntl", None)
    reg_file = tmp_path / "registry.json"
    excl_path = tmp_path / "registry.json.lock.excl"

    writer_a = EnterpriseRegistry(registry_file=reg_file)
    writer_b = EnterpriseRegistry(registry_file=reg_file)
    writer_c = EnterpriseRegistry(registry_file=reg_file)

    orig_read_reg = EnterpriseRegistry._read_registry_file
    orig_claim = registry_mod._claim_reap_file
    orig_alive = registry_mod._is_token_owner_alive
    dead_tokens: set[bytes] = set()
    a_at_release = threading.Event()
    b_replaced = threading.Event()
    a_released = threading.Event()
    c_inside = threading.Event()
    events_order: list[str] = []

    def wrapped_read(self: EnterpriseRegistry):  # type: ignore[no-untyped-def]
        name = threading.current_thread().name
        if name == "writer-a" and excl_path.exists():
            # Simulate writer A having stalled & appearing dead past LOCK_STALE_SECONDS before reaching finally:.
            dead_tokens.add(excl_path.read_bytes())
            stale_ts = time.time() - (registry_mod.LOCK_STALE_SECONDS + 30.0)
            os.utime(excl_path, (stale_ts, stale_ts))
        elif name == "writer-b":
            events_order.append("b_enter")
            b_replaced.set()
            assert a_released.wait(timeout=5.0)
            # Yield briefly so writer C has already attempted acquisition while B holds the lock.
            time.sleep(0.08)
        elif name == "writer-c":
            events_order.append("c_enter")
            c_inside.set()
        res = orig_read_reg(self)
        if name == "writer-b":
            events_order.append("b_exit")
        return res

    def wrapped_claim(
        base_reap: Path,
        timeout_s: float,
        stale_seconds: float,
        poll_seconds: float,
        owner_token: bytes = b"",
    ) -> Path | None:
        if threading.current_thread().name == "writer-a" and not a_at_release.is_set():
            # Pause A right at release before claiming its reap lock.
            a_at_release.set()
            assert b_replaced.wait(timeout=5.0)
        return orig_claim(base_reap, timeout_s, stale_seconds, poll_seconds, owner_token)

    monkeypatch.setattr(EnterpriseRegistry, "_read_registry_file", wrapped_read)
    monkeypatch.setattr(registry_mod, "_claim_reap_file", wrapped_claim)
    monkeypatch.setattr(
        registry_mod,
        "_is_token_owner_alive",
        lambda raw: False if raw in dead_tokens else orig_alive(raw),
    )

    results: dict[str, tuple[bool, list[str]]] = {}

    def run_a() -> None:
        results["a"] = writer_a.register_app(EnterpriseRegistry.scaffold_manifest("App A", "app-a"))
        a_released.set()

    def run_b() -> None:
        assert a_at_release.wait(timeout=5.0)
        results["b"] = writer_b.register_app(EnterpriseRegistry.scaffold_manifest("App B", "app-b"))

    def run_c() -> None:
        assert b_replaced.wait(timeout=5.0)
        assert a_released.wait(timeout=5.0)
        results["c"] = writer_c.register_app(EnterpriseRegistry.scaffold_manifest("App C", "app-c"))

    ta = threading.Thread(target=run_a, name="writer-a")
    tb = threading.Thread(target=run_b, name="writer-b")
    tc = threading.Thread(target=run_c, name="writer-c")
    ta.start()
    tb.start()
    tc.start()
    ta.join(timeout=10.0)
    tb.join(timeout=10.0)
    tc.join(timeout=10.0)

    assert results == {"a": (True, []), "b": (True, []), "c": (True, [])}
    assert c_inside.is_set()
    # C must only enter its critical section after B completed (never while B held the lock).
    assert events_order.index("b_exit") < events_order.index("c_enter")
    reloaded = EnterpriseRegistry(registry_file=reg_file)
    assert sorted(a.app_id for a in reloaded.list_apps()) == ["app-a", "app-b", "app-c"]
    assert not list(tmp_path.glob("registry.json.lock.*"))


def test_start_app_revalidates_shedding_when_started_callback_reclassifies_app(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("Critical App", "app-crit", power_tier=PowerPriority.L0_CRITICAL)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)

    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))

    paused_events: list[Event] = []
    bus.subscribe("enterprise.app-crit.paused", paused_events.append)
    bus.subscribe(
        "enterprise.app-crit.started",
        lambda _e: registry.register_app(
            EnterpriseRegistry.scaffold_manifest("Critical App", "app-crit", power_tier=PowerPriority.L4_IDLE)
        ),
    )

    ok, msg = manager.start_app("app-crit")
    assert ok is True
    assert "paused by load shedding" in msg

    state = manager.get_runtime_state("app-crit")
    assert state is not None
    assert state.status == AppStatus.PAUSED
    assert "app-crit" in manager._shed_paused
    assert len(paused_events) == 1
