"""Regression tests for tri-council review round 47.

1. ``NodeWallet.save_to_file(..., overwrite=True)`` stages into an exclusive 0o600 temporary
   file before ``os.replace``, so mid-write errors (e.g. ENOSPC) preserve the existing keystore.
2. Signing failures in ``StateChannel.stream_micropayment``, ``Transaction.sign``,
   ``ProofOfCompute.sign``, and ``ProofOfRelay.sign`` leave object state untouched.
3. Periodic Qdrant reconciliation via ``seed_indexed_sources`` checks deterministic chunk
   point IDs per source so partial chunk loss in multi-chunk documents triggers repair.
"""

from __future__ import annotations

import errno
import stat
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.economy.ledger import StateChannel, Transaction
from sovereign_dc.economy.settlement import ProofOfCompute, ProofOfRelay
from sovereign_dc.economy.wallet import AddressType, NodeWallet


def test_round47_wallet_save_to_file_preserves_existing_keystore_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wallet_v1 = NodeWallet.create(node_id="r47-v1", algorithm=AddressType.ED25519)
    wallet_v2 = NodeWallet.create(node_id="r47-v2", algorithm=AddressType.ED25519)
    wallet_dir = tmp_path / "keys"
    wallet_dir.mkdir()
    keystore = wallet_dir / "node.wallet"
    wallet_v1.save_to_file(str(keystore), password="strong-passphrase", overwrite=True)  # noqa: S106
    assert stat.S_IMODE(keystore.stat().st_mode) == 0o600
    original_bytes = keystore.read_bytes()

    def failing_dump(_obj: Any, fp: Any, **_kwargs: Any) -> None:
        fp.write('{"partial":')
        fp.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as mp:
        mp.setattr("sovereign_dc.economy.wallet.json.dump", failing_dump)
        with pytest.raises(OSError, match="No space left on device"):
            wallet_v2.save_to_file(str(keystore), password="strong-passphrase", overwrite=True)  # noqa: S106

    assert [p.name for p in wallet_dir.iterdir()] == ["node.wallet"]
    assert keystore.read_bytes() == original_bytes
    assert stat.S_IMODE(keystore.stat().st_mode) == 0o600
    reloaded_v1 = NodeWallet.load_from_file(str(keystore), password="strong-passphrase")  # noqa: S106
    assert reloaded_v1.address == wallet_v1.address

    wallet_v2.save_to_file(str(keystore), password="strong-passphrase", overwrite=True)  # noqa: S106
    assert [p.name for p in wallet_dir.iterdir()] == ["node.wallet"]
    assert stat.S_IMODE(keystore.stat().st_mode) == 0o600
    reloaded_v2 = NodeWallet.load_from_file(str(keystore), password="strong-passphrase")  # noqa: S106
    assert reloaded_v2.address == wallet_v2.address


def test_round47_failed_signing_does_not_mutate_channel_transaction_or_receipt_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = NodeWallet.create(node_id="r47-sender", algorithm=AddressType.ML_DSA_87)
    receiver = NodeWallet.create(node_id="r47-receiver", algorithm=AddressType.ED25519)
    channel = StateChannel("ch-r47", sender.address, receiver.address, deposit_amount=10.0)

    p1 = channel.stream_micropayment(1.5, sender)
    assert p1.sequence == 1
    assert channel.transferred_amount == 1.5
    assert channel.sequence == 1

    def fail_sign(_payload: bytes) -> str:
        raise RuntimeError("HSM unavailable")

    tx = Transaction(sender=sender.address, recipient=receiver.address, amount=1.0, fee=0.0, nonce=1)
    tx_id_before = tx.tx_id
    poc = ProofOfCompute(
        task_id="task-r47",
        service_type="llm_inference",
        units_processed=2.0,
        client_address=receiver.address,
        worker_node_id=sender.node_id,
        worker_address=sender.address,
        total_credits_due=0.5,
        result_digest="digest-r47",
    )
    por = ProofOfRelay(
        bundle_id="bundle-r47",
        source_eid="dtn://src",
        destination_eid="dtn://dst",
        bytes_relayed=512,
        relay_node_id=sender.node_id,
        relay_address=sender.address,
        credits_due=0.1,
    )

    with monkeypatch.context() as mp:
        mp.setattr(sender, "sign_payload", fail_sign)
        with pytest.raises(RuntimeError, match="HSM unavailable"):
            channel.stream_micropayment(2.0, sender)
        assert channel.transferred_amount == 1.5
        assert channel.sequence == 1

        with pytest.raises(RuntimeError, match="HSM unavailable"):
            tx.sign(sender)
        assert tx.tx_id == tx_id_before
        assert tx.signature == ""
        assert tx.sender_public_key == ""
        assert tx.signature_algorithm == "ed25519"

        with pytest.raises(RuntimeError, match="HSM unavailable"):
            poc.sign(sender)
        assert poc.signature == ""
        assert poc.signature_algorithm == "ed25519"

        with pytest.raises(RuntimeError, match="HSM unavailable"):
            por.sign(sender)
        assert por.signature == ""
        assert por.signature_algorithm == "ed25519"

    p2 = channel.stream_micropayment(2.0, sender)
    assert p2.sequence == 2
    assert p2.amount_transferred == 3.5
    assert channel.verify_promise(p2, sender.keypair.public_key_hex, sender.keypair.algorithm) is True

    tx.sign(sender)
    poc.sign(sender)
    por.sign(sender)
    assert tx.is_valid(sender.keypair.public_key_hex) is True
    assert poc.verify(sender.keypair.public_key_hex) is True
    assert por.verify(sender.keypair.public_key_hex) is True


def test_round47_qdrant_reconciliation_repairs_partially_lost_multichunk_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    multi = docs / "multi.md"
    single = docs / "single.md"
    multi.write_text(" ".join(f"token{i}" for i in range(1200)), encoding="utf-8")
    single.write_text("compact reference note", encoding="utf-8")

    points: dict[str, dict[str, Any]] = {}

    def fake_qdrant(method: str, suffix: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        if suffix.startswith("/points/delete") and body is not None:
            must = body.get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            keep = {hid for cond in body.get("filter", {}).get("must_not", []) for hid in cond.get("has_id", [])}
            for pid in [
                k
                for k, v in points.items()
                if v["payload"].get(knowledge_indexer.SOURCE_PATH_FIELD) in paths and k not in keep
            ]:
                del points[pid]
        elif suffix.startswith("/points/scroll"):
            return {
                "result": {
                    "points": [{"id": pid, "payload": pt["payload"]} for pid, pt in points.items()],
                    "next_page_offset": None,
                },
                "status": "ok",
            }
        elif method == "PUT" and suffix.startswith("/points") and body is not None:
            for pt in body.get("points", []):
                points[str(pt["id"])] = {"id": str(pt["id"]), "payload": pt["payload"]}
        return {"result": {}, "status": "ok"}

    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ())
    monkeypatch.setattr(knowledge_indexer, "_collection_created", True)
    monkeypatch.setattr(knowledge_indexer, "_qdrant_request", fake_qdrant)
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda _text: [0.1, 0.2])

    indexed: set[tuple[str, float]] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (2, 0)
    expected_multi_ids = {knowledge_indexer.chunk_point_id(str(multi), idx) for idx in range(3)}
    assert {pid for pid, pt in points.items() if pt["payload"]["path"] == str(multi)} == expected_multi_ids
    assert knowledge_indexer.list_indexed_sources() == {str(multi), str(single)}

    single_entry = next(entry for entry in indexed if entry[0] == str(single))
    assert single_entry[1] != knowledge_indexer.UNKNOWN_MTIME

    # Simulate partial chunk loss in Qdrant: remove middle chunk only while keeping 2/3 chunks.
    lost_id = knowledge_indexer.chunk_point_id(str(multi), 1)
    del points[lost_id]
    assert knowledge_indexer.list_indexed_sources() == {str(multi), str(single)}

    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert (str(multi), knowledge_indexer.UNKNOWN_MTIME) in indexed
    assert single_entry in indexed

    # Only multi.md is re-indexed; single.md remains cached and untouched.
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert {pid for pid, pt in points.items() if pt["payload"]["path"] == str(multi)} == expected_multi_ids
    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
