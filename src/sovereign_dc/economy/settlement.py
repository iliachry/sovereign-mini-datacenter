"""Cryptographic settlement and proof-of-workload verification for sovereign compute economy.

Bridges offline state channel reconciliations and Proof-of-Compute/Proof-of-Relay
receipts with RFC 9171 Delay-Tolerant Networking (DTN) space bundles.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.economy.ledger import Ledger, Transaction
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.log import get_logger
from sovereign_dc.space.dtn.bundle import Bundle

logger = get_logger("sovereign_dc.economy.settlement")


@dataclass
class ProofOfCompute:
    """Cryptographic execution receipt proving local AI / compute task completion."""

    task_id: str
    service_type: str
    units_processed: float
    client_address: str
    worker_node_id: str
    worker_address: str
    total_credits_due: float
    result_digest: str
    execution_timestamp: str = ""
    signature: str = ""
    signature_algorithm: str = "ed25519"

    def __post_init__(self) -> None:
        if not self.execution_timestamp:
            self.execution_timestamp = datetime.now(UTC).isoformat()

    def compute_hash(self) -> str:
        """Calculates deterministic hash of the proof receipt."""
        raw = f"{self.task_id}:{self.service_type}:{self.units_processed:.4f}:{self.client_address}:{self.worker_address}:{self.total_credits_due:.4f}:{self.result_digest}:{self.execution_timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def sign(self, worker_wallet: NodeWallet) -> None:
        """Signs the compute proof with the executing worker node's wallet."""
        digest = self.compute_hash()
        self.signature_algorithm = worker_wallet.keypair.algorithm.value
        self.signature = worker_wallet.sign_payload(digest.encode("utf-8"))

    def verify(self, worker_public_key_hex: str) -> bool:
        """Verifies proof signature and digest integrity."""
        if not self.signature or not worker_public_key_hex:
            return False
        digest = self.compute_hash()
        algo = AddressType(self.signature_algorithm)
        return NodeWallet.verify_signature(
            payload=digest.encode("utf-8"),
            signature_hex=self.signature,
            public_key_hex=worker_public_key_hex,
            algorithm=algo,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProofOfCompute:
        return cls(**data)


@dataclass
class ProofOfRelay:
    """Cryptographic receipt proving Space DTN / LoRa bundle forwarding."""

    bundle_id: str
    source_eid: str
    destination_eid: str
    bytes_relayed: int
    relay_node_id: str
    relay_address: str
    credits_due: float
    relay_timestamp: str = ""
    signature: str = ""
    signature_algorithm: str = "ed25519"

    def __post_init__(self) -> None:
        if not self.relay_timestamp:
            self.relay_timestamp = datetime.now(UTC).isoformat()

    def compute_hash(self) -> str:
        raw = f"{self.bundle_id}:{self.source_eid}:{self.destination_eid}:{self.bytes_relayed}:{self.relay_address}:{self.credits_due:.4f}:{self.relay_timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def sign(self, relay_wallet: NodeWallet) -> None:
        digest = self.compute_hash()
        self.signature_algorithm = relay_wallet.keypair.algorithm.value
        self.signature = relay_wallet.sign_payload(digest.encode("utf-8"))

    def verify(self, relay_public_key_hex: str) -> bool:
        if not self.signature or not relay_public_key_hex:
            return False
        digest = self.compute_hash()
        algo = AddressType(self.signature_algorithm)
        return NodeWallet.verify_signature(
            payload=digest.encode("utf-8"),
            signature_hex=self.signature,
            public_key_hex=relay_public_key_hex,
            algorithm=algo,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettlementEngine:
    """Automates receipt verification, state channel settlement, and DTN spool encapsulation."""

    @staticmethod
    def settle_proof_of_compute(
        proof: ProofOfCompute,
        client_wallet: NodeWallet,
        ledger: Ledger,
        worker_public_key_hex: str | None = None,
    ) -> Transaction:
        """Validates a ProofOfCompute receipt and transfers credits to the worker node."""
        if worker_public_key_hex and not proof.verify(worker_public_key_hex):
            raise ValueError("Invalid ProofOfCompute signature or corrupted payload")

        memo = f"compute_settle:{proof.service_type}:{proof.task_id}"
        tx = ledger.transfer(
            sender_wallet=client_wallet,
            recipient=proof.worker_address,
            amount=proof.total_credits_due,
            memo=memo,
        )
        logger.info(
            "Settled ProofOfCompute %s: %.4f credits -> %s (Tx: %s)",
            proof.task_id,
            proof.total_credits_due,
            proof.worker_address,
            tx.tx_id[:12],
        )
        return tx

    @staticmethod
    def create_dtn_settlement_bundle(
        source_eid: str,
        destination_eid: str,
        transactions: list[Transaction],
        wallet: NodeWallet,
        priority: int = 2,
    ) -> Bundle:
        """Serializes transactions into an RFC 9171 Space DTN bundle for satellite burst settlement."""
        payload_dict = {
            "type": "dtn_economy_settlement_v1",
            "sender_address": wallet.address,
            "created_at": datetime.now(UTC).isoformat(),
            "transactions": [tx.to_dict() for tx in transactions],
        }
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        bundle = Bundle(
            source_eid=source_eid,
            destination_eid=destination_eid,
            payload=payload_bytes,
            priority=priority,
        )
        logger.info("Created DTN settlement bundle %s containing %d transactions", bundle.bundle_id, len(transactions))
        return bundle

    @staticmethod
    def reconcile_dtn_settlement_bundle(
        bundle: Bundle,
        ledger: Ledger,
    ) -> list[Transaction]:
        """Unpacks and applies validated transactions received via satellite DTN pass."""
        try:
            data = json.loads(bundle.payload.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Failed to parse DTN settlement payload: {e}") from e

        if data.get("type") != "dtn_economy_settlement_v1":
            raise ValueError("Unsupported DTN bundle payload type")

        settled_txs: list[Transaction] = []
        for tx_data in data.get("transactions", []):
            tx = Transaction.from_dict(tx_data)
            # Check if transaction already applied
            existing = [t for t in ledger.get_history(limit=1000) if t.tx_id == tx.tx_id]
            if not existing:
                # Apply transaction
                with ledger._lock, ledger._get_connection() as conn:
                    # Debit sender
                    conn.execute(
                        "UPDATE balances SET balance = balance - ?, nonce = nonce + 1 WHERE address = ?",
                        (tx.amount + tx.fee, tx.sender),
                    )
                    # Credit recipient
                    conn.execute(
                        """
                        INSERT INTO balances (address, balance, nonce) VALUES (?, ?, 0)
                        ON CONFLICT(address) DO UPDATE SET balance = balance + ?
                        """,
                        (tx.recipient, tx.amount, tx.amount),
                    )
                    conn.execute(
                        """
                        INSERT INTO transactions (tx_id, sender, recipient, amount, fee, nonce, timestamp, memo, signature, signature_algorithm)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx.tx_id,
                            tx.sender,
                            tx.recipient,
                            tx.amount,
                            tx.fee,
                            tx.nonce,
                            tx.timestamp,
                            tx.memo,
                            tx.signature,
                            tx.signature_algorithm,
                        ),
                    )
                    conn.commit()
                settled_txs.append(tx)

        logger.info("Reconciled %d transactions from DTN bundle %s", len(settled_txs), bundle.bundle_id)
        return settled_txs
