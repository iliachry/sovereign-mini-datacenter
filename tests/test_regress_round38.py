"""Regression tests for tri-council review round 38.

1. ``_exact_micro()`` rejects finite magnitudes exceeding ``MAX_CREDITS`` before
   multiplying by ``_MICRO_CREDITS``, so signed oversized transactions (e.g.
   ``amount=1e308`` or ``fee=1e308``) are recorded in ``result.rejected`` as
   ``amount_precision`` rather than aborting ``reconcile_dtn_settlement_bundle()``
   mid-bundle with ``OverflowError``.
2. MCP ``tool_scaffold_enterprise_app()`` rejects invalid ``category``, ``runtime``,
   ``power_tier``, and non-boolean/non-string inputs explicitly instead of silently
   substituting defaults and reporting ``valid: true``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_dc.economy.ledger import Ledger, Transaction, credits_to_micro
from sovereign_dc.economy.node_state import NodeWallet
from sovereign_dc.economy.settlement import SettlementEngine
from sovereign_dc.mcp.server import MCPServer


def test_settlement_reconciles_oversized_float_amounts_and_fees_as_rejected_without_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "dtn.db"))

    alice = NodeWallet.create(node_id="alice-r38")
    bob = NodeWallet.create(node_id="bob-r38")
    bundle_signer = NodeWallet.create(node_id="sender-node")

    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    ledger.mint(alice.address, 10.0, memo="fund alice")

    tx_ok1 = Transaction(sender=alice.address, recipient=bob.address, amount=1.0, fee=0.0, nonce=0)
    tx_ok1.sign(alice)

    tx_huge_amt = Transaction(sender=alice.address, recipient=bob.address, amount=1e308, fee=0.0, nonce=1)
    tx_huge_amt.sign(alice)

    tx_ok2 = Transaction(sender=alice.address, recipient=bob.address, amount=2.0, fee=0.0, nonce=1)
    tx_ok2.sign(alice)

    tx_huge_fee = Transaction(sender=alice.address, recipient=bob.address, amount=1.0, fee=1e308, nonce=2)
    tx_huge_fee.sign(alice)

    bundle = SettlementEngine.create_dtn_settlement_bundle(
        source_eid="dtn://origin/economy",
        destination_eid="dtn://dest/economy",
        transactions=[tx_ok1, tx_huge_amt, tx_ok2, tx_huge_fee],
        wallet=bundle_signer,
    )

    res = SettlementEngine.reconcile_dtn_settlement_bundle(bundle, ledger)
    assert len(res) == 2
    assert [t.tx_id for t in res] == [tx_ok1.tx_id, tx_ok2.tx_id]
    assert ledger.get_balance(bob.address) == 3.0
    assert ledger.get_balance(alice.address) == 7.0
    assert [r["tx_id"] for r in res.rejected] == [tx_huge_amt.tx_id, tx_huge_fee.tx_id]
    assert all(r["reason"] == "amount_precision" for r in res.rejected)

    with pytest.raises(ValueError):
        credits_to_micro(1e308)
    with pytest.raises(ValueError):
        credits_to_micro(-1e308)


def _call_scaffold(arguments: dict[str, object]) -> dict[str, object]:
    server = MCPServer()
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "scaffold_enterprise_app", "arguments": arguments},
        }
    )
    assert resp is not None
    content = resp["result"]["content"][0]["text"]
    return json.loads(content)


def test_mcp_scaffold_enterprise_app_rejects_invalid_enum_and_type_arguments() -> None:
    bad_cases: list[dict[str, object]] = [
        {"name": "X", "category": "iot", "power_tier": "L9"},
        {"name": "X", "category": "iot", "power_tier": "l0_critical"},
        {"name": "X", "category": "bogus"},
        {"name": "X", "runtime": "bogus"},
        {"name": "X", "gpu_required": "false"},
        {"name": "", "category": "iot"},
        {"name": "X", "app_id": 123},
        {"name": "X", "entrypoint": 123},
        {"name": "X", "power_tier": 1},
    ]
    for bad in bad_cases:
        out = _call_scaffold(bad)
        assert out["valid"] is False, bad
        assert out["success"] is False, bad
        assert out["manifest"] is None, bad
        assert out["yaml_preview"] == "", bad
        assert isinstance(out["validation_errors"], list) and len(out["validation_errors"]) >= 1, bad

    ok_iot = _call_scaffold({"name": "X", "category": "iot"})
    assert ok_iot["valid"] is True
    assert ok_iot["manifest"]["power"]["tier"] == "L0_CRITICAL"  # type: ignore[index]

    empty_id = _call_scaffold({"name": "X", "app_id": ""})
    assert empty_id["valid"] is False
    assert empty_id["manifest"]["app_id"] == ""  # type: ignore[index]
