"""Append-only transaction ledger and offline state channels for sovereign compute tokens.

Provides hash-linked, replay-protected transaction chains, balance state management,
and off-chain micropayment state channels for machine-to-machine coordination.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.economy.wallet import AddressType, NodeWallet, WalletKeypair
from sovereign_dc.log import get_logger

logger = get_logger("sovereign_dc.economy.ledger")

GENESIS_PREV_HASH = "0" * 64


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
    prev_hash: str = GENESIS_PREV_HASH
    sender_public_key: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()
        if not self.tx_id:
            self.tx_id = self.compute_hash()

    def compute_hash(self) -> str:
        """Computes deterministic SHA-256 hash of transaction content including prev_hash."""
        canonical_str = (
            f"{self.prev_hash}:{self.sender}:{self.recipient}:"
            f"{self.amount:.6f}:{self.fee:.6f}:{self.nonce}:{self.timestamp}:{self.memo}"
        )
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def sign(self, wallet: NodeWallet) -> None:
        """Signs the transaction hash with the sender's wallet keypair."""
        self.tx_id = self.compute_hash()
        self.signature_algorithm = wallet.keypair.algorithm.value
        self.sender_public_key = wallet.keypair.public_key_hex
        self.signature = wallet.sign_payload(self.tx_id.encode("utf-8"))

    def is_valid(self, public_key_hex: str | None = None) -> bool:
        """Validates hash integrity, address ownership, and cryptographic signature."""
        if self.amount <= 0 or self.fee < 0:
            return False
        expected_hash = self.compute_hash()
        if self.tx_id != expected_hash:
            return False
        if self.sender in ("GENESIS", "SYSTEM", "MINT"):
            return True
        effective_pubkey = public_key_hex or self.sender_public_key
        if not self.signature or not effective_pubkey:
            return False
        try:
            algo = AddressType(self.signature_algorithm)
            derived = NodeWallet(
                node_id="verify",
                keypair=WalletKeypair(
                    algorithm=algo,
                    public_key_bytes=bytes.fromhex(effective_pubkey),
                    private_key_bytes=b"",
                ),
            ).address
            if derived != self.sender:
                return False
        except Exception:
            return False
        return NodeWallet.verify_signature(
            payload=self.tx_id.encode("utf-8"),
            signature_hex=self.signature,
            public_key_hex=effective_pubkey,
            algorithm=algo,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes transaction to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        """Constructs transaction from dictionary."""
        allowed = {
            "sender",
            "recipient",
            "amount",
            "fee",
            "nonce",
            "timestamp",
            "memo",
            "signature",
            "signature_algorithm",
            "tx_id",
            "prev_hash",
            "sender_public_key",
        }
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


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
        if deposit_amount <= 0:
            raise ValueError("State channel deposit_amount must be strictly positive")
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
        if amount <= 0:
            raise ValueError("Micropayment amount must be strictly positive")
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

    def verify_promise(
        self, promise: StateChannelPromise, sender_public_key_hex: str, algorithm: AddressType = AddressType.ED25519
    ) -> bool:
        """Verifies a peer's signed state channel promise."""
        if promise.channel_id != self.channel_id or promise.sequence <= 0:
            return False
        if promise.amount_transferred <= 0 or promise.amount_transferred > self.deposit_amount:
            return False
        payload = (
            f"{promise.channel_id}:{promise.sequence}:{promise.amount_transferred:.6f}:{promise.timestamp}".encode()
        )
        return NodeWallet.verify_signature(
            payload=payload,
            signature_hex=promise.signature,
            public_key_hex=sender_public_key_hex,
            algorithm=algorithm,
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
    """Persistent, thread-safe, hash-linked append-only compute credits ledger."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or ":memory:"
        self._lock = threading.RLock()
        self._memory_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        """Closes any persistent in-memory database connection."""
        with self._lock:
            if self._memory_conn is not None:
                self._memory_conn.close()
                self._memory_conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @contextlib.contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            try:
                yield self._memory_conn
                self._memory_conn.commit()
            except Exception:
                self._memory_conn.rollback()
                raise
            return

        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initializes ledger SQLite tables."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
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
            # Ensure columns exist on older DB schemas
            cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
            if "prev_hash" not in cols:
                conn.execute(
                    f"ALTER TABLE transactions ADD COLUMN prev_hash TEXT NOT NULL DEFAULT '{GENESIS_PREV_HASH}'"
                )
            if "sender_public_key" not in cols:
                conn.execute("ALTER TABLE transactions ADD COLUMN sender_public_key TEXT DEFAULT ''")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS balances (
                    address TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    nonce INTEGER NOT NULL
                )
                """
            )

    def _get_last_tx_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT tx_id FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()
        return str(row["tx_id"]) if row else GENESIS_PREV_HASH

    def has_transaction(self, tx_id: str) -> bool:
        """Checks whether a transaction ID is already recorded in the ledger."""
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM transactions WHERE tx_id = ? LIMIT 1", (tx_id,)).fetchone()
            return row is not None

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
        if not recipient:
            raise ValueError("Recipient address must not be empty")

        with self._lock, self._get_connection() as conn:
            prev_hash = self._get_last_tx_hash(conn)
            tx = Transaction(
                sender="MINT",
                recipient=recipient,
                amount=amount,
                fee=0.0,
                nonce=0,
                memo=memo,
                prev_hash=prev_hash,
            )
            curr_bal = self.get_balance(recipient)
            conn.execute(
                """
                INSERT INTO balances (address, balance, nonce) VALUES (?, ?, 0)
                ON CONFLICT(address) DO UPDATE SET balance = balance + ?
                """,
                (recipient, amount, amount),
            )
            conn.execute(
                """
                INSERT INTO transactions (tx_id, prev_hash, sender, recipient, amount, fee, nonce, timestamp, memo, signature, signature_algorithm, sender_public_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
        if fee < 0:
            raise ValueError("Transfer fee cannot be negative")
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
            with self._get_connection() as conn:
                prev_hash = self._get_last_tx_hash(conn)
                tx = Transaction(
                    sender=sender_wallet.address,
                    recipient=recipient,
                    amount=amount,
                    fee=fee,
                    nonce=nonce,
                    memo=memo,
                    prev_hash=prev_hash,
                )
                tx.sign(sender_wallet)
                if not tx.is_valid(sender_wallet.keypair.public_key_hex):
                    raise ValueError("Transaction signature self-verification failed")

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
                    INSERT INTO transactions (tx_id, prev_hash, sender, recipient, amount, fee, nonce, timestamp, memo, signature, signature_algorithm, sender_public_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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

        logger.info(
            "Transferred %.2f credits from %s to %s (Tx: %s)",
            amount,
            sender_wallet.address,
            recipient,
            tx.tx_id[:12],
        )
        return tx

    def verify_chain(self) -> bool:
        """Verifies hash-chain linkage and cryptographic signatures across the entire ledger."""
        with self._lock, self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM transactions ORDER BY rowid ASC").fetchall()
        expected_prev = GENESIS_PREV_HASH
        for r in rows:
            tx = Transaction(
                tx_id=r["tx_id"],
                prev_hash=r["prev_hash"] if "prev_hash" in r.keys() else GENESIS_PREV_HASH,
                sender=r["sender"],
                recipient=r["recipient"],
                amount=float(r["amount"]),
                fee=float(r["fee"]),
                nonce=int(r["nonce"]),
                timestamp=r["timestamp"],
                memo=r["memo"] or "",
                signature=r["signature"] or "",
                signature_algorithm=r["signature_algorithm"] or "ed25519",
                sender_public_key=r["sender_public_key"] if "sender_public_key" in r.keys() else "",
            )
            if tx.prev_hash != expected_prev:
                return False
            if not tx.is_valid():
                return False
            expected_prev = tx.tx_id
        return True

    def get_history(self, address: str | None = None, limit: int = 50) -> list[Transaction]:
        """Retrieves transaction history optionally filtered by address."""
        with self._lock, self._get_connection() as conn:
            if address:
                cursor = conn.execute(
                    """
                    SELECT * FROM transactions
                    WHERE sender = ? OR recipient = ?
                    ORDER BY timestamp DESC, rowid DESC LIMIT ?
                    """,
                    (address, address, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM transactions ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                    (limit,),
                )
            rows = cursor.fetchall()

        return [
            Transaction(
                tx_id=r["tx_id"],
                prev_hash=r["prev_hash"] if "prev_hash" in r.keys() else GENESIS_PREV_HASH,
                sender=r["sender"],
                recipient=r["recipient"],
                amount=float(r["amount"]),
                fee=float(r["fee"]),
                nonce=int(r["nonce"]),
                timestamp=r["timestamp"],
                memo=r["memo"] or "",
                signature=r["signature"] or "",
                signature_algorithm=r["signature_algorithm"] or "ed25519",
                sender_public_key=r["sender_public_key"] if "sender_public_key" in r.keys() else "",
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
            "chain_verified": self.verify_chain(),
        }
