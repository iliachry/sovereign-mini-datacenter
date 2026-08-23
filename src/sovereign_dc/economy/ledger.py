"""Append-only transaction ledger and offline state channels for sovereign compute tokens.

Provides replay-protected transaction chains, balance state management,
and off-chain micropayment state channels for machine-to-machine coordination.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.log import get_logger

logger = get_logger("sovereign_dc.economy.ledger")


@dataclass
class Transaction:
    """Represents an immutable value transfer or compute credit transaction."""

    sender: str
    recipient: str
    amount: float
    fee: float = 0.0
    nonce: int = 0
    timestamp: str = ""
    memo: str = ""
    signature: str = ""
    signature_algorithm: str = "ed25519"
    tx_id: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()
        if not self.tx_id:
            self.tx_id = self.compute_hash()

    def compute_hash(self) -> str:
        """Computes deterministic SHA-256 hash of transaction content."""
        canonical_str = (
            f"{self.sender}:{self.recipient}:{self.amount:.6f}:{self.fee:.6f}:{self.nonce}:{self.timestamp}:{self.memo}"
        )
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def sign(self, wallet: NodeWallet) -> None:
        """Signs the transaction hash with the sender's wallet keypair."""
        self.tx_id = self.compute_hash()
        self.signature_algorithm = wallet.keypair.algorithm.value
        self.signature = wallet.sign_payload(self.tx_id.encode("utf-8"))

    def is_valid(self, public_key_hex: str | None = None) -> bool:
        """Validates hash integrity and optional cryptographic signature."""
        expected_hash = self.compute_hash()
        if self.tx_id != expected_hash:
            return False
        if self.sender in ("GENESIS", "SYSTEM", "MINT"):
            return True
        if not self.signature or not public_key_hex:
            return False
        algo = AddressType(self.signature_algorithm)
        return NodeWallet.verify_signature(
            payload=self.tx_id.encode("utf-8"),
            signature_hex=self.signature,
            public_key_hex=public_key_hex,
            algorithm=algo,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes transaction to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        """Constructs transaction from dictionary."""
        return cls(**data)


@dataclass
class StateChannelPromise:
    """Signed promissory state update within an offline micropayment channel."""

    channel_id: str
    sequence: int
    amount_transferred: float
    timestamp: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateChannel:
    """Offline bidirectional micropayment channel for continuous micro-transfers."""

    def __init__(
        self,
        channel_id: str,
        sender_address: str,
        peer_address: str,
        deposit_amount: float,
        sequence: int = 0,
        transferred_amount: float = 0.0,
    ) -> None:
        self.channel_id = channel_id
        self.sender_address = sender_address
        self.peer_address = peer_address
        self.deposit_amount = deposit_amount
        self.sequence = sequence
        self.transferred_amount = transferred_amount
        self.is_closed = False

    def stream_micropayment(self, amount: float, wallet: NodeWallet) -> StateChannelPromise:
        """Generates a signed state update promise allocating micro-credits to peer."""
        if self.is_closed:
            raise ValueError(f"State channel {self.channel_id} is already closed")
        if self.transferred_amount + amount > self.deposit_amount:
            raise ValueError("State channel collateral exhausted")

        self.transferred_amount += amount
        self.sequence += 1
        ts = datetime.now(UTC).isoformat()

        payload = f"{self.channel_id}:{self.sequence}:{self.transferred_amount:.6f}:{ts}".encode()
        sig = wallet.sign_payload(payload)

        return StateChannelPromise(
            channel_id=self.channel_id,
            sequence=self.sequence,
            amount_transferred=self.transferred_amount,
            timestamp=ts,
            signature=sig,
        )

    def close(self) -> tuple[float, float]:
        """Closes the state channel and returns (settled_to_peer, refund_to_sender)."""
        self.is_closed = True
        settled_to_peer = self.transferred_amount
        refund_to_sender = max(0.0, self.deposit_amount - self.transferred_amount)
        logger.info(
            "Closed channel %s: %.2f credits settled to %s, %.2f refunded to %s",
            self.channel_id,
            settled_to_peer,
            self.peer_address,
            refund_to_sender,
            self.sender_address,
        )
        return settled_to_peer, refund_to_sender


class Ledger:
    """Persistent, thread-safe, append-only compute credits ledger."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or ":memory:"
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Initializes ledger SQLite tables."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    amount REAL NOT NULL,
                    fee REAL NOT NULL,
                    nonce INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    memo TEXT,
                    signature TEXT,
                    signature_algorithm TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS balances (
                    address TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    nonce INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def get_balance(self, address: str) -> float:
        """Returns current balance for the given address."""
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT balance FROM balances WHERE address = ?", (address,)).fetchone()
            return float(row["balance"]) if row else 0.0

    def get_nonce(self, address: str) -> int:
        """Returns the next expected transaction nonce for the given address."""
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT nonce FROM balances WHERE address = ?", (address,)).fetchone()
            return int(row["nonce"]) if row else 0

    def mint(self, recipient: str, amount: float, memo: str = "GENESIS_MINT") -> Transaction:
        """Mints initial compute credits to an address."""
        if amount <= 0:
            raise ValueError("Mint amount must be strictly positive")

        tx = Transaction(
            sender="MINT",
            recipient=recipient,
            amount=amount,
            fee=0.0,
            nonce=0,
            memo=memo,
        )

        with self._lock, self._get_connection() as conn:
            # Update balance
            curr_bal = self.get_balance(recipient)
            conn.execute(
                """
                INSERT INTO balances (address, balance, nonce) VALUES (?, ?, 0)
                ON CONFLICT(address) DO UPDATE SET balance = balance + ?
                """,
                (recipient, amount, amount),
            )
            # Record tx
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

        logger.info("Minted %.2f credits to %s (New balance: %.2f)", amount, recipient, curr_bal + amount)
        return tx

    def transfer(
        self,
        sender_wallet: NodeWallet,
        recipient: str,
        amount: float,
        fee: float = 0.0,
        memo: str = "",
    ) -> Transaction:
        """Executes a signed transfer of compute credits between nodes."""
        if amount <= 0:
            raise ValueError("Transfer amount must be strictly positive")
        if sender_wallet.address == recipient:
            raise ValueError("Cannot transfer credits to self")

        with self._lock:
            sender_bal = self.get_balance(sender_wallet.address)
            total_debit = amount + fee
            if sender_bal < total_debit:
                raise ValueError(
                    f"Insufficient balance: {sender_wallet.address} has {sender_bal:.2f} credits, requires {total_debit:.2f}"
                )

            nonce = self.get_nonce(sender_wallet.address)
            tx = Transaction(
                sender=sender_wallet.address,
                recipient=recipient,
                amount=amount,
                fee=fee,
                nonce=nonce,
                memo=memo,
            )
            tx.sign(sender_wallet)

            with self._get_connection() as conn:
                # Debit sender
                conn.execute(
                    "UPDATE balances SET balance = balance - ?, nonce = nonce + 1 WHERE address = ?",
                    (total_debit, sender_wallet.address),
                )
                # Credit recipient
                conn.execute(
                    """
                    INSERT INTO balances (address, balance, nonce) VALUES (?, ?, 0)
                    ON CONFLICT(address) DO UPDATE SET balance = balance + ?
                    """,
                    (recipient, amount, amount),
                )
                # Insert tx
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

        logger.info(
            "Transferred %.2f credits from %s to %s (Tx: %s)",
            amount,
            sender_wallet.address,
            recipient,
            tx.tx_id[:12],
        )
        return tx

    def get_history(self, address: str | None = None, limit: int = 50) -> list[Transaction]:
        """Retrieves transaction history optionally filtered by address."""
        with self._lock, self._get_connection() as conn:
            if address:
                cursor = conn.execute(
                    """
                    SELECT * FROM transactions
                    WHERE sender = ? OR recipient = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (address, address, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            rows = cursor.fetchall()

        return [
            Transaction(
                tx_id=r["tx_id"],
                sender=r["sender"],
                recipient=r["recipient"],
                amount=float(r["amount"]),
                fee=float(r["fee"]),
                nonce=int(r["nonce"]),
                timestamp=r["timestamp"],
                memo=r["memo"] or "",
                signature=r["signature"] or "",
                signature_algorithm=r["signature_algorithm"] or "ed25519",
            )
            for r in rows
        ]

    def export_state(self) -> dict[str, Any]:
        """Exports ledger snapshot state."""
        with self._lock, self._get_connection() as conn:
            bal_rows = conn.execute("SELECT address, balance, nonce FROM balances").fetchall()
            balances = {r["address"]: {"balance": float(r["balance"]), "nonce": int(r["nonce"])} for r in bal_rows}
        return {
            "balances": balances,
            "total_transactions": len(self.get_history(limit=10000)),
        }
