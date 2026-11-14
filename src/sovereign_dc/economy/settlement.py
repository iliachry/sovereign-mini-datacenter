"""Cryptographic settlement and proof-of-workload verification for sovereign compute economy.

Bridges offline state channel reconciliations and Proof-of-Compute/Proof-of-Relay
receipts with RFC 9171 Delay-Tolerant Networking (DTN) space bundles.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.economy.ledger import Ledger, Transaction, TransactionRejected, credits_to_micro
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.log import get_logger
from sovereign_dc.space.dtn.bundle import Bundle

logger = get_logger("sovereign_dc.economy.settlement")

# Receipt hash schemes. Version 1 joins the fields with ":" and rounds numbers to 4 decimals;
# it is only unambiguous while the joined text fields contain no ":" themselves, so legacy
# receipts are verified only under that condition. Version 2 hashes canonical JSON and is
# what every newly signed receipt uses.
RECEIPT_HASH_LEGACY = 1
RECEIPT_HASH_CURRENT = 2


def _is_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _canonical_receipt_hash(domain: bytes, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(domain + b"\n" + canonical.encode("utf-8")).hexdigest()


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
    hash_version: int = RECEIPT_HASH_CURRENT

    def __post_init__(self) -> None:
        if not self.execution_timestamp:
            self.execution_timestamp = datetime.now(UTC).isoformat()

    def compute_hash(self) -> str:
        """Calculates the deterministic hash of the receipt for its ``hash_version``.

        Raises ValueError for an unknown hash version or non-finite numbers.
        """
        if self.hash_version == RECEIPT_HASH_LEGACY:
            raw = f"{self.task_id}:{self.service_type}:{self.units_processed:.4f}:{self.client_address}:{self.worker_address}:{self.total_credits_due:.4f}:{self.result_digest}:{self.execution_timestamp}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if self.hash_version != RECEIPT_HASH_CURRENT:
            raise ValueError(f"Unsupported ProofOfCompute hash version: {self.hash_version!r}")
        return _canonical_receipt_hash(
            b"smdc-proof-of-compute-v2",
            {
                "v": RECEIPT_HASH_CURRENT,
                "task_id": self.task_id,
                "service_type": self.service_type,
                "units_processed": float(self.units_processed),
                "client_address": self.client_address,
                "worker_node_id": self.worker_node_id,
                "worker_address": self.worker_address,
                "total_credits_due": float(self.total_credits_due),
                "result_digest": self.result_digest,
                "execution_timestamp": self.execution_timestamp,
            },
        )

    def is_unambiguous(self) -> bool:
        """True if the signed hash commits to exactly this receipt's text fields.

        Requires well-typed fields for both schemes. A legacy (version 1) receipt additionally
        must not contain ":" in any joined text field except the trailing timestamp: otherwise
        a different split of the same signed string (``task_id="job:42"`` versus
        ``task_id="job"``, ``service_type="42:..."``) would verify under one signature.
        """
        joined = (self.task_id, self.service_type, self.client_address, self.worker_address, self.result_digest)
        text_ok = all(isinstance(v, str) for v in (*joined, self.worker_node_id, self.execution_timestamp))
        if not text_ok or not _is_number(self.units_processed) or not _is_number(self.total_credits_due):
            return False
        if self.hash_version == RECEIPT_HASH_CURRENT:
            return True
        return self.hash_version == RECEIPT_HASH_LEGACY and not any(":" in v for v in joined)

    def signed_credits_due(self) -> float:
        """The amount the signature actually commits to (a legacy hash rounds it to 4 decimals)."""
        if self.hash_version == RECEIPT_HASH_LEGACY:
            return float(f"{self.total_credits_due:.4f}")
        return float(self.total_credits_due)

    def sign(self, worker_wallet: NodeWallet) -> None:
        """Signs the compute proof with the executing worker node's wallet."""
        digest = self.compute_hash()
        self.signature_algorithm = worker_wallet.keypair.algorithm.value
        self.signature = worker_wallet.sign_payload(digest.encode("utf-8"))

    def settlement_key(self) -> str:
        """Stable identifier of the debt this receipt represents (one payment per client/worker/task).

        Only meaningful for a receipt that passed ``verify()``: verification guarantees that
        ``task_id`` and both addresses are exactly the values the worker signed.
        """
        raw = json.dumps(
            ["proof_of_compute", self.client_address, self.worker_address, self.task_id],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "poc:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def verify(self, worker_public_key_hex: str) -> bool:
        """Verifies digest integrity, that the key controls ``worker_address``, and the signature."""
        if not self.signature or not worker_public_key_hex:
            return False
        if not self.is_unambiguous():
            return False
        try:
            algo = AddressType(self.signature_algorithm)
            digest = self.compute_hash()
        except (TypeError, ValueError, OverflowError):
            return False
        # A valid signature by an arbitrary key proves nothing: the key must be the one
        # that owns the address the credits are paid to.
        if not NodeWallet.public_key_matches_address(worker_public_key_hex, self.worker_address, algo):
            return False
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
        """Constructs a receipt from a dictionary.

        A dictionary without ``hash_version`` was produced by a release that only knew the
        legacy hash scheme and is therefore read as version 1.
        """
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        filtered.setdefault("hash_version", RECEIPT_HASH_LEGACY)
        return cls(**filtered)


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
    hash_version: int = RECEIPT_HASH_CURRENT

    def __post_init__(self) -> None:
        if not self.relay_timestamp:
            self.relay_timestamp = datetime.now(UTC).isoformat()

    def compute_hash(self) -> str:
        """Calculates the deterministic hash of the receipt for its ``hash_version``."""
        if self.hash_version == RECEIPT_HASH_LEGACY:
            raw = f"{self.bundle_id}:{self.source_eid}:{self.destination_eid}:{self.bytes_relayed}:{self.relay_address}:{self.credits_due:.4f}:{self.relay_timestamp}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if self.hash_version != RECEIPT_HASH_CURRENT:
            raise ValueError(f"Unsupported ProofOfRelay hash version: {self.hash_version!r}")
        return _canonical_receipt_hash(
            b"smdc-proof-of-relay-v2",
            {
                "v": RECEIPT_HASH_CURRENT,
                "bundle_id": self.bundle_id,
                "source_eid": self.source_eid,
                "destination_eid": self.destination_eid,
                "bytes_relayed": int(self.bytes_relayed),
                "relay_node_id": self.relay_node_id,
                "relay_address": self.relay_address,
                "credits_due": float(self.credits_due),
                "relay_timestamp": self.relay_timestamp,
            },
        )

    def is_unambiguous(self) -> bool:
        """True if the signed hash commits to exactly this receipt's text fields.

        Requires well-typed fields for both schemes. Endpoint IDs contain a ":" by nature
        (``dtn://node``, ``ipn:1.2``), so a legacy (version 1) receipt is accepted only in the
        one shape whose ":"-joined encoding splits a single way: no ":" in ``bundle_id`` or
        ``relay_address`` and exactly one ":" in each endpoint ID.
        """
        text = (self.bundle_id, self.source_eid, self.destination_eid, self.relay_node_id, self.relay_address)
        if not all(isinstance(v, str) for v in (*text, self.relay_timestamp)):
            return False
        if not isinstance(self.bytes_relayed, int) or isinstance(self.bytes_relayed, bool):
            return False
        if not _is_number(self.credits_due):
            return False
        if self.hash_version == RECEIPT_HASH_CURRENT:
            return True
        return (
            self.hash_version == RECEIPT_HASH_LEGACY
            and ":" not in self.bundle_id
            and ":" not in self.relay_address
            and self.source_eid.count(":") == 1
            and self.destination_eid.count(":") == 1
        )

    def settlement_key(self) -> str:
        """Stable identifier of the debt this receipt represents (one payment per relay/bundle).

        Only meaningful for a receipt that passed ``verify()``.
        """
        raw = json.dumps(
            ["proof_of_relay", self.relay_address, self.bundle_id],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "por:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def sign(self, relay_wallet: NodeWallet) -> None:
        digest = self.compute_hash()
        self.signature_algorithm = relay_wallet.keypair.algorithm.value
        self.signature = relay_wallet.sign_payload(digest.encode("utf-8"))

    def verify(self, relay_public_key_hex: str) -> bool:
        """Verifies digest integrity, that the key controls ``relay_address``, and the signature."""
        if not self.signature or not relay_public_key_hex:
            return False
        if not self.is_unambiguous():
            return False
        try:
            algo = AddressType(self.signature_algorithm)
            digest = self.compute_hash()
        except (TypeError, ValueError, OverflowError):
            return False
        if not NodeWallet.public_key_matches_address(relay_public_key_hex, self.relay_address, algo):
            return False
        return NodeWallet.verify_signature(
            payload=digest.encode("utf-8"),
            signature_hex=self.signature,
            public_key_hex=relay_public_key_hex,
            algorithm=algo,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProofOfRelay:
        """Constructs a receipt from a dictionary (no ``hash_version`` means the legacy scheme)."""
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        filtered.setdefault("hash_version", RECEIPT_HASH_LEGACY)
        return cls(**filtered)


class SettlementResult(list[Transaction]):
    """Outcome of reconciling a DTN settlement bundle.

    The list itself holds the transactions that were newly applied (so existing callers that
    treat the result as ``list[Transaction]`` keep working). ``rejected`` lists every
    transaction that was refused because of ledger state, ``duplicates`` counts transactions
    that were already recorded.
    """

    def __init__(self, applied: list[Transaction] | None = None) -> None:
        super().__init__(applied or [])
        self.rejected: list[dict[str, str]] = []
        self.duplicates: int = 0

    @property
    def applied_count(self) -> int:
        return len(self)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": [tx.tx_id for tx in self],
            "applied_count": self.applied_count,
            "duplicates": self.duplicates,
            "rejected_count": self.rejected_count,
            "rejected": list(self.rejected),
        }


class SettlementEngine:
    """Automates receipt verification, state channel settlement, and DTN spool encapsulation."""

    @staticmethod
    def settle_proof_of_compute(
        proof: ProofOfCompute,
        client_wallet: NodeWallet,
        ledger: Ledger,
        worker_public_key_hex: str | None = None,
    ) -> Transaction:
        """Validates a ProofOfCompute receipt and transfers credits to the worker node.

        Raises ValueError if the receipt is not signed by the key that owns
        ``proof.worker_address``, if it was issued to a different client than
        ``client_wallet``, if this client/worker/task has already been settled on ``ledger``,
        or if the signed amount is not a multiple of 0.000001 credits (the ledger's unit of
        account): such a debt cannot be paid exactly and is never rounded silently.

        The receipt's settlement key is signed into the payment transaction, so a ledger that
        later reconciles that transaction also refuses to settle the same receipt again.
        """
        if not worker_public_key_hex or not proof.verify(worker_public_key_hex):
            raise ValueError("Invalid ProofOfCompute signature or missing worker public key")
        if proof.client_address != client_wallet.address:
            raise ValueError(
                f"ProofOfCompute {proof.task_id} was issued to {proof.client_address}, "
                f"not to the paying wallet {client_wallet.address}"
            )

        amount_due = proof.signed_credits_due()
        try:
            credits_to_micro(amount_due)
        except ValueError as err:
            raise ValueError(
                f"ProofOfCompute {proof.task_id} claims {amount_due!r} credits, which the ledger cannot "
                "settle exactly (amounts must be multiples of 0.000001 credits)"
            ) from err

        memo = f"compute_settle:{proof.service_type}:{proof.task_id}"
        try:
            tx = ledger.transfer(
                sender_wallet=client_wallet,
                recipient=proof.worker_address,
                amount=amount_due,
                memo=memo,
                settlement_key=proof.settlement_key(),
            )
        except TransactionRejected as err:
            if err.reason == "already_settled":
                raise ValueError(f"ProofOfCompute {proof.task_id} has already been settled") from err
            raise
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
        tx_dicts = []
        for tx in transactions:
            d = tx.to_dict()
            if not d.get("sender_public_key") and tx.sender == wallet.address:
                d["sender_public_key"] = wallet.keypair.public_key_hex
            tx_dicts.append(d)

        payload_dict: dict[str, Any] = {
            "type": "dtn_economy_settlement_v1",
            "sender_address": wallet.address,
            "sender_public_key": wallet.keypair.public_key_hex,
            "sender_algorithm": wallet.keypair.algorithm.value,
            "created_at": datetime.now(UTC).isoformat(),
            "transactions": tx_dicts,
        }
        canonical_body = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        payload_dict["bundle_signature"] = wallet.sign_payload(canonical_body)

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
    def _verify_bundle_envelope(data: dict[str, Any]) -> None:
        """Checks the settlement envelope signature and that its key controls ``sender_address``."""
        sender_address = data.get("sender_address")
        sender_pubkey = data.get("sender_public_key")
        bundle_signature = data.get("bundle_signature")
        if not isinstance(sender_address, str) or not isinstance(sender_pubkey, str):
            raise ValueError("DTN settlement bundle is missing sender_address / sender_public_key")
        if not isinstance(bundle_signature, str) or not bundle_signature:
            raise ValueError("DTN settlement bundle is not signed (missing bundle_signature)")
        try:
            algorithm = AddressType(data.get("sender_algorithm", AddressType.ED25519.value))
        except ValueError as err:
            raise ValueError("DTN settlement bundle declares an unsupported sender_algorithm") from err
        if not NodeWallet.public_key_matches_address(sender_pubkey, sender_address, algorithm):
            raise ValueError("DTN settlement bundle sender_public_key does not derive sender_address")

        body = {k: v for k, v in data.items() if k != "bundle_signature"}
        canonical_body = json.dumps(body, sort_keys=True).encode("utf-8")
        if not NodeWallet.verify_signature(
            payload=canonical_body,
            signature_hex=bundle_signature,
            public_key_hex=sender_pubkey,
            algorithm=algorithm,
        ):
            raise ValueError("DTN settlement bundle envelope signature verification failed")

    @staticmethod
    def reconcile_dtn_settlement_bundle(
        bundle: Bundle,
        ledger: Ledger,
    ) -> SettlementResult:
        """Unpacks, cryptographically validates, and applies transactions received via satellite DTN pass.

        The whole bundle is refused with ValueError (nothing is applied) if the payload cannot
        be parsed, the envelope signature does not verify against the key that owns
        ``sender_address``, or any contained transaction is unsigned, forged or claims a
        privileged sender (MINT/GENESIS/SYSTEM).

        Authentic transactions are then applied one by one through the ledger's validated
        apply path. A transaction whose nonce is not the sender's next nonce on this ledger,
        or whose sender lacks the balance on this ledger, is not applied and is reported in
        ``result.rejected`` (``{"tx_id", "reason", "detail"}``); already-recorded transactions
        are counted in ``result.duplicates``. The returned object is the list of newly
        applied transactions.
        """
        try:
            data = json.loads(bundle.payload.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Failed to parse DTN settlement payload: {e}") from e

        if not isinstance(data, dict) or data.get("type") != "dtn_economy_settlement_v1":
            raise ValueError("Unsupported DTN bundle payload type")

        SettlementEngine._verify_bundle_envelope(data)

        raw_txs = data.get("transactions", [])
        if not isinstance(raw_txs, list):
            raise ValueError("DTN settlement bundle 'transactions' must be a list")

        bundle_pubkey = str(data.get("sender_public_key", ""))
        transactions: list[Transaction] = []
        for tx_data in raw_txs:
            if not isinstance(tx_data, dict):
                raise ValueError("Rejected malformed transaction entry in DTN settlement bundle")
            try:
                tx = Transaction.from_dict(tx_data)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError(f"Rejected malformed transaction in DTN settlement bundle: {err}") from err
            if not tx.sender_public_key:
                # Older bundles omit the per-transaction key when the bundle sender signed it.
                tx.sender_public_key = bundle_pubkey
            # Stateless checks first, for every transaction: one forged entry voids the bundle
            # before anything is written. Hostile field types (e.g. a list as sender, an integer
            # too large for a float) must surface as ValueError like every other rejection.
            try:
                Ledger.check_foreign_transaction(tx)
            except (TypeError, OverflowError) as err:
                raise ValueError(f"Rejected malformed transaction in DTN settlement bundle: {err}") from err
            transactions.append(tx)

        result = SettlementResult()
        for tx in transactions:
            try:
                if ledger.apply_signed_transaction(tx):
                    result.append(tx)
                else:
                    result.duplicates += 1
            except TransactionRejected as err:
                logger.warning("Rejected transaction %s from DTN bundle %s: %s", tx.tx_id, bundle.bundle_id, err)
                result.rejected.append({"tx_id": tx.tx_id, "reason": err.reason, "detail": str(err)})

        logger.info(
            "Reconciled DTN bundle %s: %d applied, %d duplicate, %d rejected",
            bundle.bundle_id,
            len(result),
            result.duplicates,
            len(result.rejected),
        )
        return result
