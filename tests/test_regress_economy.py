"""Regression tests for the compute-economy settlement, ledger, wallet and state-channel fixes.

Every test here reproduces a defect that previously allowed credits to be created,
re-spent or mis-attributed, and asserts the hardened behaviour.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.economy import (
    AddressType,
    Ledger,
    NodeWallet,
    ProofOfCompute,
    ProofOfRelay,
    SettlementEngine,
    StateChannel,
    Transaction,
)
from sovereign_dc.economy.ledger import GENESIS_PREV_HASH
from sovereign_dc.space.dtn.bundle import Bundle

SRC = "dtn://node-alpha.space"
DST = "dtn://node-beta.space"


def _raw_bundle(payload: dict[str, Any]) -> Bundle:
    return Bundle(source_eid=SRC, destination_eid=DST, payload=json.dumps(payload, sort_keys=True).encode("utf-8"))


def _signed_envelope(wallet: NodeWallet, tx_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Builds a correctly signed settlement envelope around arbitrary transaction dicts."""
    body: dict[str, Any] = {
        "type": "dtn_economy_settlement_v1",
        "sender_address": wallet.address,
        "sender_public_key": wallet.keypair.public_key_hex,
        "sender_algorithm": wallet.keypair.algorithm.value,
        "created_at": "2026-01-01T00:00:00+00:00",
        "transactions": tx_dicts,
    }
    body["bundle_signature"] = wallet.sign_payload(json.dumps(body, sort_keys=True).encode("utf-8"))
    return body


def _signed_tx(wallet: NodeWallet, recipient: str, amount: float, nonce: int, memo: str = "") -> Transaction:
    tx = Transaction(sender=wallet.address, recipient=recipient, amount=amount, nonce=nonce, memo=memo)
    tx.sign(wallet)
    return tx


# --------------------------------------------------------------------------------------
# Finding 1: privileged (MINT/GENESIS/SYSTEM) transactions must never arrive via a bundle
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("privileged_sender", ["MINT", "GENESIS", "SYSTEM"])
def test_unsigned_bundle_cannot_mint_credits(tmp_path: Path, privileged_sender: str) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    attacker = NodeWallet.create(node_id="attacker")
    forged = Transaction(sender=privileged_sender, recipient=attacker.address, amount=1e9, memo="free money")

    # Original exploit: no envelope signature at all.
    unsigned = _raw_bundle({"type": "dtn_economy_settlement_v1", "transactions": [forged.to_dict()]})
    with pytest.raises(ValueError):
        SettlementEngine.reconcile_dtn_settlement_bundle(unsigned, ledger)

    # Even inside an envelope the attacker signed correctly, a privileged sender is refused.
    signed = _raw_bundle(_signed_envelope(attacker, [forged.to_dict()]))
    with pytest.raises(ValueError, match="reserved for local minting"):
        SettlementEngine.reconcile_dtn_settlement_bundle(signed, ledger)

    assert ledger.get_balance(attacker.address) == 0.0
    assert ledger.get_history() == []


def test_apply_signed_transaction_refuses_privileged_sender() -> None:
    ledger = Ledger()
    attacker = NodeWallet.create(node_id="attacker")
    forged = Transaction(sender="MINT", recipient=attacker.address, amount=5.0)
    with pytest.raises(ValueError):
        ledger.apply_signed_transaction(forged)
    assert ledger.get_balance(attacker.address) == 0.0


# --------------------------------------------------------------------------------------
# Finding 2: reconcile must enforce balance and nonce and report what it rejected
# --------------------------------------------------------------------------------------


def test_reconcile_rejects_overdraft_and_reused_nonce(tmp_path: Path) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    attacker = NodeWallet.create(node_id="attacker")
    accomplice = NodeWallet.create(node_id="accomplice")

    txs = [_signed_tx(attacker, accomplice.address, 1e6, nonce=999, memo=f"spend-{i}") for i in range(3)]
    bundle = SettlementEngine.create_dtn_settlement_bundle(SRC, DST, txs, attacker)

    result = SettlementEngine.reconcile_dtn_settlement_bundle(bundle, ledger)

    assert list(result) == []
    assert result.rejected_count == 3
    assert {r["reason"] for r in result.rejected} == {"bad_nonce"}
    assert {r["tx_id"] for r in result.rejected} == {tx.tx_id for tx in txs}
    assert ledger.get_balance(accomplice.address) == 0.0
    assert ledger.get_balance(attacker.address) == 0.0  # never negative
    assert ledger.get_nonce(attacker.address) == 0


def test_reconcile_rejects_spend_beyond_balance_with_correct_nonce(tmp_path: Path) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    sender = NodeWallet.create(node_id="sender")
    peer = NodeWallet.create(node_id="peer")
    ledger.mint(sender.address, 10.0)

    overdraft = _signed_tx(sender, peer.address, 1e6, nonce=0)
    result = SettlementEngine.reconcile_dtn_settlement_bundle(
        SettlementEngine.create_dtn_settlement_bundle(SRC, DST, [overdraft], sender), ledger
    )
    assert len(result) == 0
    assert [r["reason"] for r in result.rejected] == ["insufficient_balance"]
    assert ledger.get_balance(sender.address) == 10.0
    assert ledger.get_balance(peer.address) == 0.0


def test_reconcile_applies_each_nonce_once(tmp_path: Path) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    sender = NodeWallet.create(node_id="sender")
    peer = NodeWallet.create(node_id="peer")
    ledger.mint(sender.address, 100.0)

    first = _signed_tx(sender, peer.address, 60.0, nonce=0, memo="a")
    double_spend = _signed_tx(sender, peer.address, 60.0, nonce=0, memo="b")
    second = _signed_tx(sender, peer.address, 30.0, nonce=1, memo="c")
    bundle = SettlementEngine.create_dtn_settlement_bundle(SRC, DST, [first, double_spend, second], sender)

    result = SettlementEngine.reconcile_dtn_settlement_bundle(bundle, ledger)

    assert [tx.tx_id for tx in result] == [first.tx_id, second.tx_id]
    assert [(r["tx_id"], r["reason"]) for r in result.rejected] == [(double_spend.tx_id, "bad_nonce")]
    assert ledger.get_balance(peer.address) == 90.0
    assert ledger.get_balance(sender.address) == 10.0
    assert result.to_dict()["applied_count"] == 2

    replay = SettlementEngine.reconcile_dtn_settlement_bundle(bundle, ledger)
    assert len(replay) == 0
    assert replay.duplicates == 2
    assert ledger.get_balance(peer.address) == 90.0


# --------------------------------------------------------------------------------------
# Finding 3: the envelope signature and sender binding are verified
# --------------------------------------------------------------------------------------


def test_reconcile_verifies_envelope_signature_and_sender_binding(tmp_path: Path) -> None:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    mallory = NodeWallet.create(node_id="mallory")
    origin = Ledger(db_path=str(tmp_path / "origin.db"))
    origin.mint(alice.address, 100.0)
    tx = origin.transfer(alice, bob.address, 5.0)
    good = json.loads(SettlementEngine.create_dtn_settlement_bundle(SRC, DST, [tx], alice).payload)

    def fresh_ledger(name: str) -> Ledger:
        replica = Ledger(db_path=str(tmp_path / name))
        replica.mint(alice.address, 100.0)
        return replica

    # Tampered body under the original signature.
    tampered = dict(good, created_at="1999-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="envelope signature"):
        SettlementEngine.reconcile_dtn_settlement_bundle(_raw_bundle(tampered), fresh_ledger("r1.db"))

    # Signature stripped.
    stripped = {k: v for k, v in good.items() if k != "bundle_signature"}
    with pytest.raises(ValueError, match="not signed"):
        SettlementEngine.reconcile_dtn_settlement_bundle(_raw_bundle(stripped), fresh_ledger("r2.db"))

    # Envelope validly signed by mallory's key but claiming alice's address.
    spoofed = _signed_envelope(mallory, good["transactions"])
    spoofed["sender_address"] = alice.address
    spoofed["bundle_signature"] = mallory.sign_payload(
        json.dumps({k: v for k, v in spoofed.items() if k != "bundle_signature"}, sort_keys=True).encode("utf-8")
    )
    with pytest.raises(ValueError, match="does not derive sender_address"):
        SettlementEngine.reconcile_dtn_settlement_bundle(_raw_bundle(spoofed), fresh_ledger("r3.db"))

    # The untouched bundle still reconciles.
    replica = fresh_ledger("r4.db")
    assert len(SettlementEngine.reconcile_dtn_settlement_bundle(_raw_bundle(good), replica)) == 1
    assert replica.get_balance(bob.address) == 5.0


# --------------------------------------------------------------------------------------
# Finding 4: ProofOfCompute replay, client mismatch and key/address binding
# --------------------------------------------------------------------------------------


def _proof(client: NodeWallet, worker: NodeWallet, task_id: str = "task-1", due: float = 0.25) -> ProofOfCompute:
    proof = ProofOfCompute(
        task_id=task_id,
        service_type="llm_inference",
        units_processed=5.0,
        client_address=client.address,
        worker_node_id=worker.node_id,
        worker_address=worker.address,
        total_credits_due=due,
        result_digest="digest",
    )
    proof.sign(worker)
    return proof


def test_proof_of_compute_cannot_be_settled_twice(tmp_path: Path) -> None:
    db = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db)
    client = NodeWallet.create(node_id="client")
    worker = NodeWallet.create(node_id="worker")
    ledger.mint(client.address, 100.0)
    proof = _proof(client, worker)

    SettlementEngine.settle_proof_of_compute(proof, client, ledger, worker.keypair.public_key_hex)
    with pytest.raises(ValueError, match="already been settled"):
        SettlementEngine.settle_proof_of_compute(proof, client, ledger, worker.keypair.public_key_hex)

    # The settled-proof record is persistent, not per-object, and survives a re-signed copy.
    resigned = _proof(client, worker)
    with pytest.raises(ValueError, match="already been settled"):
        SettlementEngine.settle_proof_of_compute(resigned, client, Ledger(db_path=db), worker.keypair.public_key_hex)

    assert ledger.get_balance(worker.address) == 0.25
    assert ledger.get_balance(client.address) == 99.75
    assert ledger.has_settled_proof(proof.settlement_key())


def test_proof_of_compute_must_name_the_paying_client(tmp_path: Path) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    client = NodeWallet.create(node_id="client")
    bystander = NodeWallet.create(node_id="bystander")
    worker = NodeWallet.create(node_id="worker")
    ledger.mint(bystander.address, 100.0)
    proof = _proof(client, worker)

    with pytest.raises(ValueError, match="not to the paying wallet"):
        SettlementEngine.settle_proof_of_compute(proof, bystander, ledger, worker.keypair.public_key_hex)
    assert ledger.get_balance(bystander.address) == 100.0
    assert ledger.get_balance(worker.address) == 0.0


def test_proof_verification_binds_public_key_to_payee_address() -> None:
    client = NodeWallet.create(node_id="client")
    honest_worker = NodeWallet.create(node_id="worker")
    attacker = NodeWallet.create(node_id="attacker")

    # Attacker signs a receipt that pays the honest worker's address... with the attacker key,
    # and one that pays the attacker while being "verified" with the attacker's own key is fine.
    forged = _proof(client, honest_worker)
    forged.sign(attacker)
    assert forged.verify(attacker.keypair.public_key_hex) is False
    assert forged.verify(honest_worker.keypair.public_key_hex) is False

    relay = ProofOfRelay(
        bundle_id="b1",
        source_eid=SRC,
        destination_eid=DST,
        bytes_relayed=10,
        relay_node_id="relay",
        relay_address=honest_worker.address,
        credits_due=0.1,
    )
    relay.sign(attacker)
    assert relay.verify(attacker.keypair.public_key_hex) is False

    genuine = _proof(client, honest_worker)
    assert genuine.verify(honest_worker.keypair.public_key_hex) is True


# --------------------------------------------------------------------------------------
# Finding 5: an honest reconcile keeps verify_chain() true
# --------------------------------------------------------------------------------------


def test_verify_chain_survives_reconciling_foreign_transactions(tmp_path: Path) -> None:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    carol = NodeWallet.create(node_id="carol")

    origin = Ledger(db_path=str(tmp_path / "origin.db"))
    origin.mint(carol.address, 1.0)  # origin history diverges from the replica's
    origin.mint(alice.address, 100.0)
    tx1 = origin.transfer(alice, bob.address, 12.5)
    tx2 = origin.transfer(alice, bob.address, 2.5)

    replica = Ledger(db_path=str(tmp_path / "replica.db"))
    replica.mint(alice.address, 100.0)
    replica.mint(bob.address, 1.0)
    assert replica.verify_chain()

    bundle = SettlementEngine.create_dtn_settlement_bundle(SRC, DST, [tx1, tx2], alice)
    assert len(SettlementEngine.reconcile_dtn_settlement_bundle(bundle, replica)) == 2

    assert replica.verify_chain() is True
    # The signed origin link is preserved verbatim so the signature still verifies.
    stored = {tx.tx_id: tx for tx in replica.get_history()}
    assert stored[tx1.tx_id].prev_hash == tx1.prev_hash
    assert stored[tx1.tx_id].is_valid()

    # The replica can keep appending its own transactions afterwards.
    replica.transfer(bob, carol.address, 3.0)
    assert replica.verify_chain() is True
    assert replica.export_state()["chain_verified"] is True


# --------------------------------------------------------------------------------------
# Finding 6: unambiguous, versioned transaction hash + recipient validation
# --------------------------------------------------------------------------------------


def test_transaction_hash_is_unambiguous() -> None:
    sender = NodeWallet.create(node_id="s").address
    recipient = NodeWallet.create(node_id="r").address
    common: dict[str, Any] = {"sender": sender, "recipient": recipient, "nonce": 0}

    # Timestamp/memo boundary shift: identical under the old ":"-joined encoding.
    a = Transaction(amount=1.0, timestamp="2026-01-01T00:00", memo="00:x", **common)
    b = Transaction(amount=1.0, timestamp="2026-01-01T00:00:00", memo="x", **common)
    assert a.tx_id != b.tx_id

    # Sub-micro-credit amounts: identical under the old 6-decimal formatting.
    c = Transaction(amount=1.0000001, timestamp="t", **common)
    d = Transaction(amount=1.0000004, timestamp="t", **common)
    assert c.tx_id != d.tx_id

    # Recipient/amount boundary shift.
    e = Transaction(sender=sender, recipient="bob", amount=1.0, nonce=0, timestamp="t")
    f = Transaction(sender=sender, recipient="bob:1.000000", amount=1.0, nonce=0, timestamp="t")
    assert e.tx_id != f.tx_id


def test_transfer_and_mint_reject_malformed_recipient() -> None:
    ledger = Ledger()
    alice = NodeWallet.create(node_id="alice")
    ledger.mint(alice.address, 10.0)
    for bad in ["bob", "sov_xyz", alice.address + ":1.000000", alice.address + "\n", ""]:
        with pytest.raises(ValueError):
            ledger.transfer(alice, bad, 1.0)
        with pytest.raises(ValueError):
            ledger.mint(bad, 1.0)
    assert ledger.get_balance(alice.address) == 10.0


def _create_legacy_ledger(db_path: str, alice: NodeWallet, bob: NodeWallet) -> list[str]:
    """Writes a ledger file exactly as the pre-versioning release did (old schema, v1 hashes)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE transactions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_id TEXT UNIQUE NOT NULL,
            prev_hash TEXT NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000',
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            amount REAL NOT NULL,
            fee REAL NOT NULL,
            nonce INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            memo TEXT,
            signature TEXT,
            signature_algorithm TEXT,
            sender_public_key TEXT DEFAULT ''
        )
        """
    )
    conn.execute("CREATE TABLE balances (address TEXT PRIMARY KEY, balance REAL NOT NULL, nonce INTEGER NOT NULL)")

    mint = Transaction(
        sender="MINT",
        recipient=alice.address,
        amount=100.0,
        memo="GENESIS_MINT",
        prev_hash=GENESIS_PREV_HASH,
        hash_version=1,
    )
    pay = Transaction(
        sender=alice.address,
        recipient=bob.address,
        amount=40.0,
        nonce=0,
        memo="legacy:memo:with:colons",
        prev_hash=mint.tx_id,
        hash_version=1,
    )
    pay.sign(alice)
    for tx in (mint, pay):
        conn.execute(
            "INSERT INTO transactions (tx_id, prev_hash, sender, recipient, amount, fee, nonce, timestamp, memo,"
            " signature, signature_algorithm, sender_public_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tx.tx_id,
                tx.prev_hash,
                tx.sender,
                tx.recipient,
                tx.amount,
                tx.fee,
                tx.nonce,
                tx.timestamp,
                tx.memo,
                tx.signature,
                tx.signature_algorithm,
                tx.sender_public_key,
            ),
        )
    conn.execute("INSERT INTO balances VALUES (?, 60.0, 1)", (alice.address,))
    conn.execute("INSERT INTO balances VALUES (?, 40.0, 0)", (bob.address,))
    conn.commit()
    conn.close()
    return [mint.tx_id, pay.tx_id]


def test_existing_ledger_with_legacy_hashes_still_verifies(tmp_path: Path) -> None:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    db_path = str(tmp_path / "legacy.db")
    legacy_ids = _create_legacy_ledger(db_path, alice, bob)

    ledger = Ledger(db_path=db_path)  # migrates in place
    assert ledger.verify_chain() is True
    history = {tx.tx_id: tx for tx in ledger.get_history()}
    assert set(history) == set(legacy_ids)
    assert all(tx.hash_version == 1 for tx in history.values())

    # New activity on the migrated file uses the new scheme and the chain stays intact.
    new_tx = ledger.transfer(alice, bob.address, 10.0, memo="post:upgrade")
    assert new_tx.hash_version == 2
    assert ledger.verify_chain() is True
    assert Ledger(db_path=db_path).verify_chain() is True  # idempotent re-open
    assert ledger.get_balance(bob.address) == 50.0

    # A dict from an older peer (no hash_version) is read as the legacy scheme.
    legacy_dict = history[legacy_ids[1]].to_dict()
    legacy_dict.pop("hash_version")
    assert Transaction.from_dict(legacy_dict).is_valid()


# --------------------------------------------------------------------------------------
# Finding 7: read-check-write is atomic across Ledger objects sharing one file
# --------------------------------------------------------------------------------------


def test_two_ledger_objects_on_one_file_do_not_fork_the_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "shared.db")
    ledger_a = Ledger(db_path=db_path)
    ledger_b = Ledger(db_path=db_path)
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    ledger_a.mint(alice.address, 100.0)

    a_is_signing = threading.Event()
    b_finished = threading.Event()
    original_sign = Transaction.sign

    def slow_sign(self: Transaction, wallet: NodeWallet) -> None:
        # Hold thread A between "read nonce / chain tip" and "write" while B tries to commit.
        if threading.current_thread().name == "ledger-a":
            a_is_signing.set()
            b_finished.wait(timeout=1.0)
        original_sign(self, wallet)

    monkeypatch.setattr(Transaction, "sign", slow_sign)
    errors: list[BaseException] = []

    def run_a() -> None:
        try:
            ledger_a.transfer(alice, bob.address, 10.0, memo="from-a")
        except BaseException as exc:  # noqa: BLE001 - surfaced through the assertion below
            errors.append(exc)

    def run_b() -> None:
        try:
            a_is_signing.wait(timeout=5.0)
            ledger_b.transfer(alice, bob.address, 10.0, memo="from-b")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            b_finished.set()

    threads = [threading.Thread(target=run_a, name="ledger-a"), threading.Thread(target=run_b, name="ledger-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    assert errors == []
    assert ledger_a.verify_chain() is True
    spends = [tx for tx in ledger_a.get_history(limit=10) if tx.sender == alice.address]
    assert sorted(tx.nonce for tx in spends) == [0, 1]
    assert len({tx.prev_hash for tx in spends}) == 2
    assert ledger_a.get_balance(alice.address) == 80.0
    assert ledger_a.get_nonce(alice.address) == 2


# --------------------------------------------------------------------------------------
# Finding 14: wallet key file permissions and keystore consistency
# --------------------------------------------------------------------------------------


def test_wallet_file_is_owner_only_while_key_material_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sovereign_dc.economy import wallet as wallet_module

    wallet = NodeWallet.create(node_id="node")
    modes_during_write: list[int] = []
    real_dump = json.dump

    def recording_dump(obj: Any, fp: Any, **kwargs: Any) -> None:
        # The mode that matters is the one in force at the moment the private key hits disk.
        modes_during_write.append(stat.S_IMODE(os.fstat(fp.fileno()).st_mode))
        real_dump(obj, fp, **kwargs)

    monkeypatch.setattr(wallet_module.json, "dump", recording_dump)
    old_umask = os.umask(0o022)
    try:
        fresh = tmp_path / "fresh.json"
        wallet.save_to_file(str(fresh))

        existing = tmp_path / "existing.json"
        existing.write_text("{}", encoding="utf-8")
        os.chmod(existing, 0o644)
        wallet.save_to_file(str(existing))
    finally:
        os.umask(old_umask)

    assert modes_during_write == [0o600, 0o600]
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600
    assert NodeWallet.load_from_file(str(existing)).address == wallet.address


@pytest.mark.parametrize("algorithm", [AddressType.ED25519, AddressType.ML_DSA_87])
def test_wallet_load_rejects_public_key_or_address_not_matching_private_key(
    tmp_path: Path, algorithm: AddressType
) -> None:
    wallet = NodeWallet.create(node_id="node", algorithm=algorithm)
    other = NodeWallet.create(node_id="other", algorithm=algorithm)
    path = tmp_path / "wallet.json"
    wallet.save_to_file(str(path))
    original = json.loads(path.read_text(encoding="utf-8"))

    swapped_key = dict(original, public_key=other.keypair.public_key_hex, address=other.address)
    path.write_text(json.dumps(swapped_key), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the private key"):
        NodeWallet.load_from_file(str(path))

    swapped_address = dict(original, address=other.address)
    path.write_text(json.dumps(swapped_address), encoding="utf-8")
    with pytest.raises(ValueError, match="stored address"):
        NodeWallet.load_from_file(str(path))

    path.write_text(json.dumps(original), encoding="utf-8")
    assert NodeWallet.load_from_file(str(path)).address == wallet.address


# --------------------------------------------------------------------------------------
# Finding 15: state channel float exhaustion, signer binding, monotonic sequence
# --------------------------------------------------------------------------------------


def test_state_channel_can_spend_exact_deposit_despite_float_rounding() -> None:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    channel = StateChannel("chan", alice.address, bob.address, deposit_amount=0.3)
    channel.stream_micropayment(0.1, alice)
    channel.stream_micropayment(0.2, alice)  # 0.1 + 0.2 == 0.30000000000000004
    with pytest.raises(ValueError, match="collateral exhausted"):
        channel.stream_micropayment(0.000001, alice)
    settled, refund = channel.close()
    assert settled == pytest.approx(0.3)
    assert refund == 0.0


def test_state_channel_promise_requires_channel_sender_key_and_fresh_sequence() -> None:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    mallory = NodeWallet.create(node_id="mallory")

    sender_side = StateChannel("chan", alice.address, bob.address, deposit_amount=50.0)
    receiver_side = StateChannel("chan", alice.address, bob.address, deposit_amount=50.0)

    # A promise signed by someone other than the channel's sender proves nothing.
    forged_side = StateChannel("chan", alice.address, bob.address, deposit_amount=50.0)
    forged = forged_side.stream_micropayment(40.0, mallory)
    assert receiver_side.verify_promise(forged, mallory.keypair.public_key_hex) is False

    p1 = sender_side.stream_micropayment(5.0, alice)
    p2 = sender_side.stream_micropayment(10.0, alice)
    assert receiver_side.verify_promise(p2, alice.keypair.public_key_hex) is True
    # Stale (lower sequence) and replayed (same sequence) promises are refused.
    assert receiver_side.verify_promise(p1, alice.keypair.public_key_hex) is False
    assert receiver_side.verify_promise(p2, alice.keypair.public_key_hex) is False
    p3 = sender_side.stream_micropayment(1.0, alice)
    assert receiver_side.verify_promise(p3, alice.keypair.public_key_hex) is True
    assert receiver_side.last_verified_sequence == 3


# --------------------------------------------------------------------------------------
# Finding 18 (ledger part): non-finite amounts
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad_amount", [float("inf"), float("-inf"), float("nan"), 0.0, -1.0])
def test_mint_and_transfer_reject_non_finite_or_non_positive_amounts(bad_amount: float) -> None:
    ledger = Ledger()
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    ledger.mint(alice.address, 10.0)

    with pytest.raises(ValueError):
        ledger.mint(alice.address, bad_amount)
    with pytest.raises(ValueError):
        ledger.transfer(alice, bob.address, bad_amount)
    with pytest.raises(ValueError):
        ledger.transfer(alice, bob.address, 1.0, fee=float("nan"))

    assert ledger.get_balance(alice.address) == 10.0
    assert ledger.verify_chain() is True


@pytest.mark.parametrize(
    "hostile",
    [
        {"sender": []},
        {"amount": 10**400},
        {"fee": 10**400},
        {"recipient": {"x": 1}},
        {"nonce": "NaN"},
    ],
)
def test_reconcile_hostile_field_types_raise_value_error(tmp_path: Path, hostile: dict[str, Any]) -> None:
    """A validly signed envelope carrying malformed fields must be rejected with ValueError only."""
    mallory = NodeWallet.create(node_id="mallory")
    bob = NodeWallet.create(node_id="bob")
    tx_dict = {**_signed_tx(mallory, bob.address, 1.0, nonce=0).to_dict(), **hostile}
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    with pytest.raises(ValueError):
        SettlementEngine.reconcile_dtn_settlement_bundle(_raw_bundle(_signed_envelope(mallory, [tx_dict])), ledger)
    assert ledger.get_balance(bob.address) == 0.0


# --------------------------------------------------------------------------------------
# Review round 2, finding 1: the settlement key must be bound to exactly what was signed
# --------------------------------------------------------------------------------------


def _legacy_proof(client: NodeWallet, worker: NodeWallet, task_id: str, service_type: str) -> ProofOfCompute:
    return ProofOfCompute(
        task_id=task_id,
        service_type=service_type,
        units_processed=1.0,
        client_address=client.address,
        worker_node_id=worker.node_id,
        worker_address=worker.address,
        total_credits_due=10.0,
        result_digest="digest",
        execution_timestamp="2026-01-01T00:00:00+00:00",
        hash_version=1,
    )


def test_one_signed_receipt_cannot_be_resplit_into_two_settlements(tmp_path: Path) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    client = NodeWallet.create(node_id="client")
    worker = NodeWallet.create(node_id="worker")
    ledger.mint(client.address, 100.0)
    worker_key = worker.keypair.public_key_hex

    signed = _legacy_proof(client, worker, "job:42", "llm_inference")
    signed.sign(worker)
    resplit = _legacy_proof(client, worker, "job", "42:llm_inference")
    resplit.signature = signed.signature
    # Same ":"-joined string, hence the same signed hash, but a different task_id.
    assert resplit.compute_hash() == signed.compute_hash()
    assert resplit.settlement_key() != signed.settlement_key()

    for proof in (signed, resplit):
        assert proof.verify(worker_key) is False
        with pytest.raises(ValueError, match="Invalid ProofOfCompute"):
            SettlementEngine.settle_proof_of_compute(proof, client, ledger, worker_key)
    assert ledger.get_balance(worker.address) == 0.0

    # A receipt signed by this release has no such second reading, whatever its fields hold.
    current = _proof(client, worker, task_id="job:42", due=10.0)
    assert current.hash_version == 2
    moved = ProofOfCompute.from_dict({**current.to_dict(), "task_id": "job", "service_type": "42:llm_inference"})
    assert moved.compute_hash() != current.compute_hash()
    assert moved.verify(worker_key) is False
    SettlementEngine.settle_proof_of_compute(current, client, ledger, worker_key)
    with pytest.raises(ValueError, match="already been settled"):
        SettlementEngine.settle_proof_of_compute(current, client, ledger, worker_key)
    assert ledger.get_balance(worker.address) == 10.0


def test_unambiguous_legacy_receipts_still_verify_and_settle(tmp_path: Path) -> None:
    ledger = Ledger(db_path=str(tmp_path / "ledger.db"))
    client = NodeWallet.create(node_id="client")
    worker = NodeWallet.create(node_id="worker")
    ledger.mint(client.address, 100.0)

    legacy = _legacy_proof(client, worker, "task-1", "llm_inference")
    legacy.sign(worker)
    stored = legacy.to_dict()
    stored.pop("hash_version")  # exactly what an older release serialized

    loaded = ProofOfCompute.from_dict(stored)
    assert loaded.hash_version == 1
    assert loaded.verify(worker.keypair.public_key_hex) is True
    assert loaded.settlement_key() == _proof(client, worker, task_id="task-1").settlement_key()
    SettlementEngine.settle_proof_of_compute(loaded, client, ledger, worker.keypair.public_key_hex)
    assert ledger.get_balance(worker.address) == 10.0

    # Relabelling a signed receipt with the other hash scheme invalidates it.
    assert ProofOfCompute.from_dict({**stored, "hash_version": 2}).verify(worker.keypair.public_key_hex) is False
    assert ProofOfCompute.from_dict({**stored, "hash_version": 3}).verify(worker.keypair.public_key_hex) is False


def test_proof_of_relay_hash_is_unambiguous_and_legacy_receipts_are_restricted() -> None:
    relay = NodeWallet.create(node_id="relay")
    key = relay.keypair.public_key_hex

    def receipt(bundle_id: str, source: str, destination: str, **extra: Any) -> ProofOfRelay:
        return ProofOfRelay(
            bundle_id=bundle_id,
            source_eid=source,
            destination_eid=destination,
            bytes_relayed=10,
            relay_node_id="relay",
            relay_address=relay.address,
            credits_due=0.5,
            relay_timestamp="2026-01-01T00:00:00+00:00",
            **extra,
        )

    # Legacy encoding: moving a ":" boundary keeps the signed hash but changes the bundle id.
    signed = receipt("b1:dtn", "//a.x", DST, hash_version=1)
    signed.sign(relay)
    resplit = receipt("b1", "dtn://a.x", DST, hash_version=1)
    resplit.signature = signed.signature
    assert resplit.compute_hash() == signed.compute_hash()
    assert resplit.settlement_key() != signed.settlement_key()
    assert signed.verify(key) is False

    # The well-formed legacy shape keeps verifying (also when loaded from an old dict).
    good = receipt("b1", SRC, DST, hash_version=1)
    good.sign(relay)
    old_dict = good.to_dict()
    old_dict.pop("hash_version")
    assert ProofOfRelay.from_dict(old_dict).verify(key) is True

    # Receipts signed now commit to every field separately.
    a = receipt("b1:dtn", "//a.x", DST)
    b = receipt("b1", "dtn://a.x", DST)
    assert a.hash_version == 2
    assert a.compute_hash() != b.compute_hash()
    a.sign(relay)
    assert a.verify(key) is True
    b.signature = a.signature
    assert b.verify(key) is False


# --------------------------------------------------------------------------------------
# Review round 2, finding 2: the settlement key travels with the signed transaction
# --------------------------------------------------------------------------------------


def test_receipt_settled_on_one_ledger_cannot_be_settled_again_on_a_reconciled_replica(tmp_path: Path) -> None:
    client = NodeWallet.create(node_id="client")
    worker = NodeWallet.create(node_id="worker")
    worker_key = worker.keypair.public_key_hex
    ledger_a = Ledger(db_path=str(tmp_path / "a.db"))
    ledger_b = Ledger(db_path=str(tmp_path / "b.db"))
    ledger_a.mint(client.address, 100.0)
    ledger_b.mint(client.address, 100.0)
    proof = _proof(client, worker, due=10.0)

    tx = SettlementEngine.settle_proof_of_compute(proof, client, ledger_a, worker_key)
    assert tx.settlement_key == proof.settlement_key()
    bundle = SettlementEngine.create_dtn_settlement_bundle(SRC, DST, [tx], client)
    assert len(SettlementEngine.reconcile_dtn_settlement_bundle(bundle, ledger_b)) == 1

    assert ledger_b.has_settled_proof(proof.settlement_key())
    with pytest.raises(ValueError, match="already been settled"):
        SettlementEngine.settle_proof_of_compute(proof, client, ledger_b, worker_key)
    assert ledger_b.get_balance(worker.address) == 10.0
    assert ledger_b.get_balance(client.address) == 90.0
    assert ledger_b.verify_chain() is True

    # The other order: B settles first, then A's payment for the same receipt arrives.
    ledger_c = Ledger(db_path=str(tmp_path / "c.db"))
    ledger_c.mint(client.address, 100.0)
    SettlementEngine.settle_proof_of_compute(proof, client, ledger_c, worker_key)
    result = SettlementEngine.reconcile_dtn_settlement_bundle(bundle, ledger_c)
    assert len(result) == 0
    assert [r["reason"] for r in result.rejected] == ["already_settled"]
    assert ledger_c.get_balance(worker.address) == 10.0


def test_settlement_key_is_signed_and_scoped_to_the_payer(tmp_path: Path) -> None:
    client = NodeWallet.create(node_id="client")
    worker = NodeWallet.create(node_id="worker")
    mallory = NodeWallet.create(node_id="mallory")
    proof = _proof(client, worker, due=10.0)
    key = proof.settlement_key()

    origin = Ledger(db_path=str(tmp_path / "origin.db"))
    origin.mint(client.address, 100.0)
    tx = SettlementEngine.settle_proof_of_compute(proof, client, origin, worker.keypair.public_key_hex)

    # Stripping or changing the key breaks the signature, so it cannot be hidden from a replica.
    for forged_key in ("", "poc:" + "0" * 64):
        forged = Transaction.from_dict({**tx.to_dict(), "settlement_key": forged_key})
        assert forged.is_valid() is False
    # The legacy hash does not cover the field at all, so it is refused there.
    legacy = Transaction(sender=client.address, recipient=worker.address, amount=1.0, hash_version=1)
    legacy.settlement_key = key
    legacy.sign(client)
    assert legacy.is_valid() is False

    # Transactions without a key hash exactly as they did before the field existed.
    plain = origin.transfer(client, worker.address, 1.0)
    plain_dict = plain.to_dict()
    plain_dict.pop("settlement_key")
    assert Transaction.from_dict(plain_dict).tx_id == plain.tx_id
    assert Transaction.from_dict(plain_dict).is_valid()

    # Someone else naming the client's settlement key in their own transfer does not block the client.
    replica = Ledger(db_path=str(tmp_path / "replica.db"))
    replica.mint(client.address, 100.0)
    replica.mint(mallory.address, 1.0)
    replica.transfer(mallory, worker.address, 0.000001, settlement_key=key)
    assert replica.has_settled_proof(key, payer=client.address) is False
    SettlementEngine.settle_proof_of_compute(proof, client, replica, worker.keypair.public_key_hex)
    assert replica.has_settled_proof(key, payer=client.address) is True


def test_settled_proofs_recorded_by_the_previous_release_still_block(tmp_path: Path) -> None:
    client = NodeWallet.create(node_id="client")
    worker = NodeWallet.create(node_id="worker")
    proof = _proof(client, worker, due=10.0)
    db_path = str(tmp_path / "ledger.db")
    Ledger(db_path=db_path).mint(client.address, 100.0)

    # The previous release stored the bare key in a three-column table.
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE settled_proofs")
    conn.execute(
        "CREATE TABLE settled_proofs (proof_key TEXT PRIMARY KEY, tx_id TEXT NOT NULL, settled_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO settled_proofs VALUES (?, 'old-tx', '2026-01-01T00:00:00+00:00')", (proof.settlement_key(),)
    )
    conn.commit()
    conn.close()

    ledger = Ledger(db_path=db_path)
    assert ledger.has_settled_proof(proof.settlement_key())
    with pytest.raises(ValueError, match="already been settled"):
        SettlementEngine.settle_proof_of_compute(proof, client, ledger, worker.keypair.public_key_hex)
    assert ledger.get_balance(worker.address) == 0.0


# --------------------------------------------------------------------------------------
# Review round 2, finding 3: verify_chain must not trust the unsigned local_prev column
# --------------------------------------------------------------------------------------


def _chain_digest_for(prev: str, tx_id: str) -> str:
    import hashlib

    return hashlib.sha256(f"smdc-chain-v1\n{prev}\n{tx_id}".encode()).hexdigest()


def _relink(conn: sqlite3.Connection) -> None:
    """What a careful editor does after removing rows: recompute every unsigned link column."""
    prev, digest = GENESIS_PREV_HASH, GENESIS_PREV_HASH
    for rowid, tx_id in conn.execute("SELECT rowid, tx_id FROM transactions ORDER BY rowid").fetchall():
        digest = _chain_digest_for(digest, tx_id)
        conn.execute("UPDATE transactions SET local_prev = ?, chain_digest = ? WHERE rowid = ?", (prev, digest, rowid))
        prev = tx_id


def _populated_ledger(path: str) -> tuple[Ledger, NodeWallet, NodeWallet, list[Transaction]]:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    ledger = Ledger(db_path=path)
    txs = [ledger.mint(alice.address, 100.0)]
    txs += [ledger.transfer(alice, bob.address, float(i), memo=f"pay-{i}") for i in (1, 2, 3)]
    return ledger, alice, bob, txs


def test_deleting_an_interior_signed_transaction_breaks_verify_chain(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ledger.db")
    ledger, _, _, txs = _populated_ledger(db_path)
    assert ledger.verify_chain() is True
    assert ledger.chain_anchor_status() == "valid"

    # The reviewer's reproduction: drop an interior signed row, repoint the next row's local_prev.
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM transactions WHERE tx_id = ?", (txs[2].tx_id,))
    conn.execute("UPDATE transactions SET local_prev = ? WHERE tx_id = ?", (txs[1].tx_id, txs[3].tx_id))
    conn.commit()
    assert Ledger(db_path=db_path).verify_chain() is False

    # Recomputing every digest does not help: the anchor no longer matches ...
    _relink(conn)
    conn.commit()
    assert Ledger(db_path=db_path).verify_chain() is False
    assert Ledger(db_path=db_path).chain_anchor_status() == "invalid"

    # ... and with the anchor removed too, the signed nonce gap (0, then 2) still gives it away.
    conn.execute("DELETE FROM chain_anchor")
    conn.commit()
    conn.close()
    reopened = Ledger(db_path=db_path)
    assert reopened.chain_anchor_status() == "absent"
    assert reopened.verify_chain() is False
    assert reopened.export_state()["chain_verified"] is False


def test_anchor_detects_tampering_that_unsigned_columns_cannot(tmp_path: Path) -> None:
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    db_path = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db_path)
    ledger.mint(alice.address, 100.0)
    tail = ledger.transfer(alice, bob.address, 5.0)
    assert ledger.verify_chain(require_anchor=True) is True

    # Tail truncation and a forged trailing mint leave every remaining row individually valid.
    forged = Transaction(sender="MINT", recipient=bob.address, amount=1e9, prev_hash=tail.tx_id)
    for statement, params in (
        ("DELETE FROM transactions WHERE tx_id = ?", (tail.tx_id,)),
        (
            "INSERT INTO transactions (tx_id, prev_hash, sender, recipient, amount, fee, nonce, timestamp, memo,"
            " signature, signature_algorithm, sender_public_key, hash_version, local_prev) VALUES"
            " (?, ?, 'MINT', ?, 1e9, 0, 0, ?, '', '', 'ed25519', '', 2, ?)",
            (forged.tx_id, tail.tx_id, bob.address, forged.timestamp, tail.tx_id),
        ),
    ):
        tampered = str(tmp_path / f"tampered-{statement[:6]}.db")
        src = sqlite3.connect(db_path)  # copy through SQLite so the WAL contents are included
        dst = sqlite3.connect(tampered)
        src.backup(dst)
        src.close()
        dst.execute(statement, params)
        _relink(dst)
        dst.commit()
        assert Ledger(db_path=tampered).verify_chain() is False

        # Removing the anchor hides it from the default check only; a caller that knows the
        # ledger is anchored asks for the anchor and is told.
        dst.execute("DELETE FROM chain_anchor")
        dst.commit()
        dst.close()
        assert Ledger(db_path=tampered).verify_chain(require_anchor=True) is False

    assert ledger.verify_chain(require_anchor=True) is True


def test_forged_or_foreign_anchor_is_rejected_and_never_laundered_by_an_append(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ledger.db")
    ledger, alice, bob, txs = _populated_ledger(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM transactions WHERE tx_id = ?", (txs[3].tx_id,))
    _relink(conn)
    head_digest = conn.execute("SELECT chain_digest FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    # The editor cannot compute the MAC, so it writes its best guess at a matching anchor.
    conn.execute("UPDATE chain_anchor SET tx_count = 3, head_tx_id = ?, head_digest = ?", (txs[2].tx_id, head_digest))
    conn.commit()
    conn.close()

    tampered = Ledger(db_path=db_path)
    assert tampered.verify_chain() is False
    # Appending to the tampered chain must not produce a fresh, valid anchor over it.
    tampered.mint(bob.address, 1.0)
    assert tampered.chain_anchor_status() == "invalid"
    assert tampered.verify_chain() is False

    # An unanchored chain is likewise not anchored implicitly, only by the explicit operator action.
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM chain_anchor")
    conn.commit()
    conn.close()
    unanchored = Ledger(db_path=db_path)
    unanchored.mint(bob.address, 1.0)
    assert unanchored.chain_anchor_status() == "absent"
    assert unanchored.verify_chain(require_anchor=True) is False
    assert unanchored.anchor_chain() is True
    assert unanchored.verify_chain(require_anchor=True) is True
    unanchored.transfer(bob, alice.address, 1.0)
    assert Ledger(db_path=db_path).verify_chain(require_anchor=True) is True


def test_mint_cannot_be_passed_off_as_a_reconciled_row(tmp_path: Path) -> None:
    alice = NodeWallet.create(node_id="alice")
    db_path = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db_path)
    first = ledger.mint(alice.address, 1.0)
    second = ledger.mint(alice.address, 2.0)
    ledger.mint(alice.address, 3.0)

    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM transactions WHERE tx_id = ?", (second.tx_id,))
    conn.execute("DELETE FROM chain_anchor")
    _relink(conn)
    conn.commit()
    conn.close()
    assert first.tx_id != second.tx_id
    assert Ledger(db_path=db_path).verify_chain() is False


def test_ledger_written_by_the_previous_release_migrates_and_verifies(tmp_path: Path) -> None:
    """Schema of the release before chain digests: local_prev present, no digest/anchor/settlement key."""
    alice = NodeWallet.create(node_id="alice")
    bob = NodeWallet.create(node_id="bob")
    carol = NodeWallet.create(node_id="carol")
    db_path = str(tmp_path / "previous.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE transactions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, tx_id TEXT UNIQUE NOT NULL, prev_hash TEXT NOT NULL,
            sender TEXT NOT NULL, recipient TEXT NOT NULL, amount REAL NOT NULL, fee REAL NOT NULL,
            nonce INTEGER NOT NULL, timestamp TEXT NOT NULL, memo TEXT, signature TEXT, signature_algorithm TEXT,
            sender_public_key TEXT DEFAULT '', hash_version INTEGER NOT NULL DEFAULT 1, local_prev TEXT
        )
        """
    )
    conn.execute("CREATE TABLE balances (address TEXT PRIMARY KEY, balance REAL NOT NULL, nonce INTEGER NOT NULL)")
    conn.execute(
        "CREATE TABLE settled_proofs (proof_key TEXT PRIMARY KEY, tx_id TEXT NOT NULL, settled_at TEXT NOT NULL)"
    )

    mint = Transaction(sender="MINT", recipient=alice.address, amount=100.0, memo="GENESIS_MINT")
    pay = Transaction(sender=alice.address, recipient=bob.address, amount=40.0, nonce=0, prev_hash=mint.tx_id)
    pay.sign(alice)
    # A transaction reconciled from another ledger: its signed prev_hash points into the origin chain.
    foreign = Transaction(sender=carol.address, recipient=bob.address, amount=1.0, nonce=0, prev_hash="ab" * 32)
    foreign.sign(carol)
    local_prev = GENESIS_PREV_HASH
    for tx in (mint, pay, foreign):
        conn.execute(
            "INSERT INTO transactions (tx_id, prev_hash, sender, recipient, amount, fee, nonce, timestamp, memo,"
            " signature, signature_algorithm, sender_public_key, hash_version, local_prev)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tx.tx_id,
                tx.prev_hash,
                tx.sender,
                tx.recipient,
                tx.amount,
                tx.fee,
                tx.nonce,
                tx.timestamp,
                tx.memo,
                tx.signature,
                tx.signature_algorithm,
                tx.sender_public_key,
                tx.hash_version,
                local_prev,
            ),
        )
        local_prev = tx.tx_id
    conn.execute("INSERT INTO balances VALUES (?, 60.0, 1)", (alice.address,))
    conn.execute("INSERT INTO balances VALUES (?, 41.0, 0)", (bob.address,))
    conn.execute("INSERT INTO balances VALUES (?, 0.0, 1)", (carol.address,))
    conn.commit()
    conn.close()

    ledger = Ledger(db_path=db_path)  # migrates in place, computing the digests lazily
    assert ledger.verify_chain() is True
    assert ledger.chain_anchor_status() == "absent"
    assert {tx.tx_id for tx in ledger.get_history()} == {mint.tx_id, pay.tx_id, foreign.tx_id}

    check = sqlite3.connect(db_path)
    digests = [row[0] for row in check.execute("SELECT chain_digest FROM transactions ORDER BY rowid")]
    check.close()
    assert all(digests) and len(set(digests)) == 3

    ledger.transfer(alice, bob.address, 10.0)
    assert Ledger(db_path=db_path).verify_chain() is True  # idempotent re-open
    assert ledger.anchor_chain() is True
    assert ledger.verify_chain(require_anchor=True) is True


def test_in_memory_ledger_is_not_anchored_and_creates_no_key_file() -> None:
    ledger = Ledger()
    alice = NodeWallet.create(node_id="alice")
    ledger.mint(alice.address, 1.0)
    assert ledger.verify_chain() is True
    assert ledger.chain_anchor_status() == "absent"
    assert not os.path.exists(os.environ["SMDC_STATE_DIR"])
