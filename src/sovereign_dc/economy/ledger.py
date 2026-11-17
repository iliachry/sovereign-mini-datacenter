"""Append-only transaction ledger and offline state channels for sovereign compute tokens.

Provides hash-linked, replay-protected transaction chains, balance state management,
and off-chain micropayment state channels for machine-to-machine coordination.
"""

from __future__ import annotations

import base64
import contextlib
import fractions
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import stat
import threading
from collections.abc import Collection, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.economy.wallet import AddressType, NodeWallet, WalletKeypair
from sovereign_dc.log import get_logger
from sovereign_dc.security.pqc import PQCAlgorithm, get_node_pqc_keypair, node_pqc_key_path

logger = get_logger("sovereign_dc.economy.ledger")

GENESIS_PREV_HASH = "0" * 64

# Transaction hash schemes. Version 1 (":"-joined, amounts rounded to 6 decimals) is ambiguous
# and is only kept so rows written by older releases still verify. Version 2 hashes an
# unambiguous canonical JSON encoding and is what every new transaction uses.
HASH_VERSION_LEGACY = 1
HASH_VERSION_CURRENT = 2
_SUPPORTED_HASH_VERSIONS = (HASH_VERSION_LEGACY, HASH_VERSION_CURRENT)

# Sender names reserved for credit creation. Such transactions carry no signature and may
# only be created by the local ``Ledger.mint()``; they are never accepted from a peer.
PRIVILEGED_SENDERS = frozenset({"GENESIS", "SYSTEM", "MINT"})

_HEX64 = re.compile(r"[0-9a-f]{64}")
PROMISE_VERSION_CURRENT = "smdc-promise-v2"
_LEGACY_PROMISE_CHANNEL_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
_LEGACY_PROMISE_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})?")
# Unit of account: one micro-credit (0.000001 credits). Balances are stored and updated as
# integer micro-credits (``balances.balance_micro``); floats only appear at the API and in the
# REAL ``balance`` column that is kept in step for readers from older releases.
_MICRO_CREDITS = 1_000_000
# Largest amount and largest balance the ledger accepts: 2,000,000,000 credits (2e15
# micro-credits). The bound is what makes the float API exact: below 2**31 credits a double
# resolves 2**-22 credits (0.24 micro-credits), so every micro-credit count up to the bound
# has its own float and ``_to_micro(_from_micro(m)) == m`` holds for all of them. (2**53
# micro-credits, the previous bound, is not enough: near 9e9 credits adjacent doubles are
# 1.9 micro-credits apart, so a balance read back as a float could move by a micro-credit.)
MAX_CREDITS = 2_000_000_000
_MAX_MICRO = MAX_CREDITS * _MICRO_CREDITS

# Chain anchor: the head of the local chain digest is authenticated with a MAC key derived
# from this node's persistent identity key, which lives in the SMDC state directory and not
# in the ledger database. Someone who can only edit the database file cannot recompute it.
ANCHOR_VERSION = 1
ANCHOR_KEY_ALGORITHM = PQCAlgorithm.ML_DSA_65
ANCHOR_ABSENT = "absent"
ANCHOR_VALID = "valid"
ANCHOR_INVALID = "invalid"
ANCHOR_UNVERIFIABLE = "unverifiable"

_ANCHOR_KEYS: dict[str, tuple[str, bytes]] = {}
_ANCHOR_KEYS_LOCK = threading.Lock()


def _chain_digest(prev_digest: str, tx_id: str) -> str:
    """Running digest of the local chain: commits to every tx_id and its position."""
    return hashlib.sha256(f"smdc-chain-v1\n{prev_digest}\n{tx_id}".encode()).hexdigest()


def _node_anchor_key() -> tuple[str, bytes] | None:
    """Returns ``(key_id, mac_key)`` derived from this node's persistent identity key.

    Returns None when the node has no persistent identity (``get_node_pqc_keypair`` fell back
    to a key that only lives in this process): a MAC under such a key could never be checked
    again, so nothing is anchored with it.
    """
    path = node_pqc_key_path(ANCHOR_KEY_ALGORITHM)
    with _ANCHOR_KEYS_LOCK:
        cached = _ANCHOR_KEYS.get(path)
    if cached is not None:
        return cached
    try:
        keypair = get_node_pqc_keypair(ANCHOR_KEY_ALGORITHM)
        with open(path, encoding="utf-8") as f:
            persisted_public = base64.b64decode(json.load(f)["public_key_b64"], validate=True)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not keypair.private_key or not hmac.compare_digest(persisted_public, keypair.public_key):
        return None
    mac_key = hashlib.sha256(b"smdc-ledger-anchor-v1\n" + keypair.private_key).digest()
    with _ANCHOR_KEYS_LOCK:
        _ANCHOR_KEYS[path] = (keypair.key_id, mac_key)
    return keypair.key_id, mac_key


def _anchor_mac(mac_key: bytes, key_id: str, tx_count: int, head_tx_id: str, head_digest: str) -> str:
    message = json.dumps(
        {
            "smdc_ledger_anchor": ANCHOR_VERSION,
            "key_id": key_id,
            "tx_count": int(tx_count),
            "head_tx_id": head_tx_id,
            "head_digest": head_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(mac_key, message, hashlib.sha256).hexdigest()


def _to_micro(amount: float) -> int:
    """Converts a credit amount to integer micro-credits, rounding to 6 decimal places.

    This is the lenient conversion, used to quantise values that are already part of the
    record (amounts of rows written by older releases, channel totals that were validated
    when they were set). New amounts go through ``_exact_micro``.
    """
    return round(amount * _MICRO_CREDITS)


def _real_to_micro(value: float) -> int:
    """Nearest micro-credit count to a stored REAL credit value, by exact rational rounding.

    Used only to migrate REAL balances written by older releases: the double is taken at its
    exact value (no intermediate float multiplication), so ``0.19999999999999998`` becomes
    200000 and a large balance is not moved by a second rounding error.
    """
    return round(fractions.Fraction(value) * _MICRO_CREDITS)


def _micro_text(micro: int) -> str:
    """Canonical 6-decimal text of a micro-credit count (``1000001`` -> ``"1.000001"``).

    Built from the integer, so two different counts never share a text. For a count within
    the accepted range it is the same string ``f"{credits:.6f}"`` produces for its float.
    """
    sign = "-" if micro < 0 else ""
    whole, fraction = divmod(abs(micro), _MICRO_CREDITS)
    return f"{sign}{whole}.{fraction:06d}"


def _from_micro(micro: int) -> float:
    """The canonical float for a micro-credit count (the double nearest to ``micro / 1e6``)."""
    return micro / _MICRO_CREDITS


def _exact_micro(amount: float) -> int | None:
    """Micro-credits for ``amount``, or None if it is not a whole number of micro-credits.

    An amount is accepted when it is exactly the float a 6-decimal literal parses to
    (``0.1``, ``0.25``, ``12.5``, ``0.000001``). ``0.1234567`` or ``0.1 + 0.2`` are not:
    accounting never rounds an amount silently. Magnitudes above ``MAX_CREDITS`` are outside
    the range in which a float identifies one micro-credit count and are refused as well.
    """
    if not _is_real_number(amount) or not math.isfinite(amount) or abs(amount) > MAX_CREDITS:
        return None
    micro = _to_micro(amount)
    if abs(micro) > _MAX_MICRO:
        return None
    return micro if _from_micro(micro) == amount else None


def credits_to_micro(amount: float) -> int:
    """Converts a credit amount to integer micro-credits (1 credit = 1,000,000).

    Raises ValueError if ``amount`` is not a finite real number, has more than 6 decimal
    places, or exceeds ``MAX_CREDITS`` (2,000,000,000 credits). This is the validation every
    new ledger amount goes through.
    """
    if not _is_real_number(amount) or not math.isfinite(amount):
        raise ValueError("Credit amount must be a finite real number")
    micro = _exact_micro(amount)
    if micro is None:
        raise ValueError(
            f"Credit amount {amount!r} is not a multiple of 0.000001 credits (or exceeds the representable range)"
        )
    return micro


def micro_to_credits(micro: int) -> float:
    """Converts integer micro-credits back to the canonical float credit amount."""
    return _from_micro(micro)


def _is_real_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class TransactionRejected(ValueError):
    """Raised when a transaction fails validation; ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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
    hash_version: int = HASH_VERSION_CURRENT
    # Identifier of the debt this transfer settles (e.g. ``ProofOfCompute.settlement_key()``).
    # Signed as part of the version-2 hash, so every ledger that applies the transaction
    # learns that the debt is paid. Empty for ordinary transfers.
    settlement_key: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()
        if not self.tx_id:
            self.tx_id = self.compute_hash()

    @property
    def is_privileged(self) -> bool:
        """True if the sender is a reserved credit-creation identity (MINT/GENESIS/SYSTEM)."""
        return self.sender in PRIVILEGED_SENDERS

    def compute_hash(self) -> str:
        """Computes the deterministic SHA-256 transaction hash for ``hash_version``.

        Raises ValueError for an unknown hash version or non-finite amounts.
        """
        if self.hash_version == HASH_VERSION_LEGACY:
            canonical_str = (
                f"{self.prev_hash}:{self.sender}:{self.recipient}:"
                f"{self.amount:.6f}:{self.fee:.6f}:{self.nonce}:{self.timestamp}:{self.memo}"
            )
            return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
        if self.hash_version != HASH_VERSION_CURRENT:
            raise ValueError(f"Unsupported transaction hash version: {self.hash_version!r}")
        # Canonical JSON: every field is a separately quoted/typed value, so no choice of
        # memo/recipient can make two different transactions share one encoding.
        fields: dict[str, Any] = {
            "v": HASH_VERSION_CURRENT,
            "prev_hash": self.prev_hash,
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": float(self.amount),
            "fee": float(self.fee),
            "nonce": int(self.nonce),
            "timestamp": self.timestamp,
            "memo": self.memo,
        }
        if self.settlement_key:
            # Only present when set, so transactions signed before the field existed keep
            # their hash; an object with the key can never equal one without it.
            fields["settlement_key"] = self.settlement_key
        canonical = json.dumps(
            fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(b"smdc-tx-v2\n" + canonical.encode("utf-8")).hexdigest()

    def _well_formed(self) -> bool:
        """Checks field types and numeric ranges (no cryptography)."""
        text_fields = (
            self.sender,
            self.recipient,
            self.timestamp,
            self.memo,
            self.signature,
            self.signature_algorithm,
            self.tx_id,
            self.prev_hash,
            self.sender_public_key,
            self.settlement_key,
        )
        if not all(isinstance(v, str) for v in text_fields):
            return False
        if self.settlement_key and self.hash_version != HASH_VERSION_CURRENT:
            # The legacy hash does not cover the field, so it would be unsigned there.
            return False
        if not _is_real_number(self.amount) or not _is_real_number(self.fee):
            return False
        if not math.isfinite(self.amount) or not math.isfinite(self.fee):
            return False
        if self.amount <= 0 or self.fee < 0:
            return False
        if not isinstance(self.nonce, int) or isinstance(self.nonce, bool) or self.nonce < 0:
            return False
        return (
            isinstance(self.hash_version, int)
            and not isinstance(self.hash_version, bool)
            and bool(self.timestamp)
            and bool(self.tx_id)
            and self.hash_version in _SUPPORTED_HASH_VERSIONS
        )

    def is_unambiguous(self) -> bool:
        """True if the signed hash commits to exactly this transaction's value fields.

        Always true for hash version 2. A legacy (version 1) hash only pins sender, recipient,
        amount, fee and nonce when the addresses are well-formed and the amounts survive the
        6-decimal rounding of the legacy encoding.
        """
        if self.hash_version == HASH_VERSION_CURRENT:
            return True
        try:
            return (
                _HEX64.fullmatch(self.prev_hash) is not None
                and NodeWallet.is_valid_address(self.sender)
                and NodeWallet.is_valid_address(self.recipient)
                and float(f"{self.amount:.6f}") == float(self.amount)
                and float(f"{self.fee:.6f}") == float(self.fee)
            )
        except (TypeError, ValueError):
            return False

    def sign(self, wallet: NodeWallet) -> None:
        """Signs the transaction hash with the sender's wallet keypair."""
        self.tx_id = self.compute_hash()
        self.signature_algorithm = wallet.keypair.algorithm.value
        self.sender_public_key = wallet.keypair.public_key_hex
        self.signature = wallet.sign_payload(self.tx_id.encode("utf-8"))

    def is_valid(self, public_key_hex: str | None = None) -> bool:
        """Validates hash integrity, address ownership, and cryptographic signature.

        Transactions from a privileged sender (MINT/GENESIS/SYSTEM) are unsigned and only
        pass the structural checks here: that is sufficient for re-verifying rows the local
        ledger minted itself, but callers handling transactions from a peer MUST reject
        ``is_privileged`` transactions (``Ledger.apply_signed_transaction`` does).
        """
        if not self._well_formed():
            return False
        try:
            expected_hash = self.compute_hash()
        except (TypeError, ValueError):
            return False
        if self.tx_id != expected_hash:
            return False
        if self.is_privileged:
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
        """Constructs transaction from dictionary.

        A dictionary without ``hash_version`` was produced by a release that only knew the
        legacy hash scheme and is therefore read as version 1.
        """
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
            "hash_version",
            "settlement_key",
        }
        filtered = {k: v for k, v in data.items() if k in allowed}
        filtered.setdefault("hash_version", HASH_VERSION_LEGACY)
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
    """Offline unidirectional micropayment channel: ``sender_address`` pays ``peer_address``.

    Each party keeps its own ``StateChannel`` object for the same ``channel_id``:

    * The **sender** calls ``stream_micropayment()``. Every call signs a promise for the new
      *cumulative* amount; ``transferred_amount`` is the highest amount this side has signed.
    * The **receiver** calls ``verify_promise()`` on each promise it is handed.
      ``last_verified_amount`` / ``last_verified_promise`` are the highest cumulative amount
      (and its signed evidence) this side has authenticated against the sender's key.

    ``close()`` settles the highest cumulative amount this object knows the sender signed
    for: what it signed itself (sender side) or what it verified (receiver side). All
    comparisons and sums are done in integer micro-credits.

    A promise commits to one integer micro-credit count: the signed text and the settled
    amount are both derived from that integer (``_micro_text``). A promise whose
    ``amount_transferred`` is not exactly a whole number of micro-credits is rejected by
    ``verify_promise``; it is never rounded to a neighbouring amount.
    """

    def __init__(
        self,
        channel_id: str,
        sender_address: str,
        peer_address: str,
        deposit_amount: float,
        sequence: int = 0,
        transferred_amount: float = 0.0,
    ) -> None:
        if not isinstance(channel_id, str) or not channel_id:
            raise ValueError("State channel channel_id must be a non-empty string")
        if not _is_real_number(deposit_amount) or not math.isfinite(deposit_amount) or deposit_amount <= 0:
            raise ValueError("State channel deposit_amount must be strictly positive")
        if not _is_real_number(transferred_amount) or not math.isfinite(transferred_amount) or transferred_amount < 0:
            raise ValueError("State channel transferred_amount must be a finite, non-negative number")
        if _exact_micro(deposit_amount) is None or _exact_micro(transferred_amount) is None:
            raise ValueError("State channel amounts must not have more than 6 decimal places")
        if _to_micro(transferred_amount) > _to_micro(deposit_amount):
            raise ValueError("State channel transferred_amount exceeds the deposit")
        self.channel_id = channel_id
        self.sender_address = sender_address
        self.peer_address = peer_address
        self.deposit_amount = deposit_amount
        self.sequence = sequence
        self.transferred_amount = transferred_amount
        self.is_closed = False
        # Receiver-side replay protection: highest promise accepted by verify_promise().
        self.last_verified_sequence = 0
        self.last_verified_amount = 0.0
        self._last_verified_micro = 0
        # The promise behind last_verified_amount: the receiver's evidence for settlement.
        self.last_verified_promise: StateChannelPromise | None = None

    @staticmethod
    def _promise_payload(channel_id: str, sequence: int, amount_micro: int, timestamp: str) -> bytes:
        """Canonical versioned signed bytes of a promise for ``amount_micro`` micro-credits."""
        return json.dumps(
            [PROMISE_VERSION_CURRENT, channel_id, sequence, _micro_text(amount_micro), timestamp],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @staticmethod
    def _legacy_v1_promise_payload(channel_id: str, sequence: int, amount_micro: int, timestamp: str) -> bytes | None:
        """Unambiguous legacy v1 payload, or None when fields could cross-splice at ':'."""
        if not _LEGACY_PROMISE_CHANNEL_ID_RE.fullmatch(channel_id):
            return None
        if sequence < 1 or amount_micro <= 0:
            return None
        if not _LEGACY_PROMISE_ISO_TS_RE.fullmatch(timestamp):
            return None
        return f"{channel_id}:{sequence}:{_micro_text(amount_micro)}:{timestamp}".encode()

    def stream_micropayment(self, amount: float, wallet: NodeWallet) -> StateChannelPromise:
        """Generates a signed state update promise allocating micro-credits to peer."""
        if self.is_closed:
            raise ValueError(f"State channel {self.channel_id} is already closed")
        if not _is_real_number(amount) or amount <= 0:
            raise ValueError("Micropayment amount must be strictly positive")
        if not math.isfinite(amount):
            raise ValueError("Micropayment amount must be a finite number")
        amount_micro = _exact_micro(amount)
        if amount_micro is None or amount_micro <= 0:
            raise ValueError("Micropayment amount must not have more than 6 decimal places")
        # Sum in integer micro-credits: 0.1 + 0.2 must be able to spend a 0.3 deposit.
        total_micro = _to_micro(self.transferred_amount) + amount_micro
        if total_micro > _to_micro(self.deposit_amount):
            raise ValueError("State channel collateral exhausted")

        self.transferred_amount = _from_micro(total_micro)
        self.sequence += 1
        ts = datetime.now(UTC).isoformat()

        sig = wallet.sign_payload(self._promise_payload(self.channel_id, self.sequence, total_micro, ts))

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
        """Verifies a peer's signed state channel promise and records it as the latest state.

        The promise must be signed by the key that controls ``sender_address``, and both its
        sequence number and cumulative amount must advance past the last accepted promise, so
        a stale or replayed promise is rejected (returns False). The amount must be exactly a
        whole number of micro-credits: the signature is checked against the text of that
        integer, and that integer is what ``close()`` settles.
        """
        if self.is_closed or promise.channel_id != self.channel_id:
            return False
        if not isinstance(promise.sequence, int) or isinstance(promise.sequence, bool):
            return False
        if promise.sequence <= self.last_verified_sequence:
            return False
        amount = promise.amount_transferred
        if not _is_real_number(amount) or not math.isfinite(amount) or amount <= 0:
            return False
        amount_micro = _exact_micro(amount)
        if amount_micro is None:
            # 1.0000015 renders as "1.000001" with 6 decimals: it is not the signed amount.
            return False
        if amount_micro > _to_micro(self.deposit_amount):
            return False
        if amount_micro < self._last_verified_micro:
            return False
        if not isinstance(promise.channel_id, str) or not isinstance(promise.timestamp, str) or not promise.timestamp:
            return False
        try:
            promise.channel_id.encode("utf-8")
            promise.timestamp.encode("utf-8")
        except UnicodeEncodeError:
            return False
        if not NodeWallet.public_key_matches_address(sender_public_key_hex, self.sender_address, algorithm):
            return False
        payload = self._promise_payload(promise.channel_id, promise.sequence, amount_micro, promise.timestamp)
        verified = NodeWallet.verify_signature(
            payload=payload,
            signature_hex=promise.signature,
            public_key_hex=sender_public_key_hex,
            algorithm=algorithm,
        )
        if not verified:
            legacy_payload = self._legacy_v1_promise_payload(
                promise.channel_id, promise.sequence, amount_micro, promise.timestamp
            )
            if legacy_payload is None or not NodeWallet.verify_signature(
                payload=legacy_payload,
                signature_hex=promise.signature,
                public_key_hex=sender_public_key_hex,
                algorithm=algorithm,
            ):
                return False
        self.last_verified_sequence = promise.sequence
        # The signature covers exactly these micro-credits.
        self._last_verified_micro = amount_micro
        self.last_verified_amount = _from_micro(amount_micro)
        self.last_verified_promise = promise
        return True

    def settled_amount(self) -> float:
        """Cumulative credits owed to the peer according to this side's channel state.

        The larger of ``transferred_amount`` (promises this object signed as the sender) and
        ``last_verified_amount`` (the latest promise this object authenticated as the
        receiver). On a receiver-side object ``transferred_amount`` stays 0 and on a
        sender-side object ``last_verified_amount`` stays 0, so each side reports the latest
        state it can prove; a receiver backs its claim with ``last_verified_promise``.
        """
        return _from_micro(max(_to_micro(self.transferred_amount), self._last_verified_micro))

    def close(self) -> tuple[float, float]:
        """Closes the state channel and returns ``(settled_to_peer, refund_to_sender)``.

        ``settled_to_peer`` is ``settled_amount()``; the refund is the rest of the deposit.
        Both sides of a channel therefore agree once the receiver has verified the sender's
        latest promise. Promises the receiver never verified are not counted on its side.
        """
        self.is_closed = True
        settled_micro = _to_micro(self.settled_amount())
        settled_to_peer = _from_micro(settled_micro)
        refund_to_sender = _from_micro(max(0, _to_micro(self.deposit_amount) - settled_micro))
        logger.info(
            "Closed channel %s: %.2f credits settled to %s, %.2f refunded to %s",
            self.channel_id,
            settled_to_peer,
            self.peer_address,
            refund_to_sender,
            self.sender_address,
        )
        return settled_to_peer, refund_to_sender


_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _secure_db_file(db_path: str) -> None:
    """Makes the SQLite file at ``db_path`` (and its side files) private to the owner (0600).

    A missing database is created empty with mode 0600 before SQLite opens it; SQLite gives
    the ``-wal``/``-shm``/``-journal`` files it creates later the mode of the main file. An
    existing database or side file that is a regular file owned by the current user and
    accessible to group or others is chmod-ed to 0600. Files owned by someone else and
    symlinks are left alone. Errors are logged, never raised: the caller's open decides.
    """
    try:
        fd = os.open(db_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
    except OSError as err:
        logger.debug("Could not pre-create %s with mode 0600: %s", db_path, err)
    getuid = getattr(os, "getuid", None)
    for path in (db_path, *(db_path + suffix for suffix in _SQLITE_SIDECARS)):
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or (getuid is not None and st.st_uid != getuid()):
            continue
        if stat.S_IMODE(st.st_mode) & 0o077:
            try:
                os.chmod(path, 0o600)
            except OSError as err:
                logger.warning("Could not restrict %s to mode 0600: %s", path, err)


class Ledger:
    """Persistent, thread-safe, hash-linked append-only compute credits ledger.

    Every state change (mint, transfer, reconciled foreign transaction) goes through one
    validated apply path that runs inside a single ``BEGIN IMMEDIATE`` SQLite transaction,
    so the balance check, nonce check, chain-tip read and the writes are atomic even when
    several ``Ledger`` objects or processes share one database file.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or ":memory:"
        self._lock = threading.RLock()
        self._memory_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
        else:
            _secure_db_file(self.db_path)
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

    @contextlib.contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Yields a connection holding the database write lock (``BEGIN IMMEDIATE``).

        Everything read inside the block is guaranteed to still be true when the block's
        writes commit; any exception rolls the whole block back.
        """
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def _init_db(self) -> None:
        """Initializes ledger SQLite tables and migrates older schemas in place."""
        with self._write_transaction() as conn:
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
                    sender_public_key TEXT DEFAULT '',
                    hash_version INTEGER NOT NULL DEFAULT 1,
                    local_prev TEXT,
                    settlement_key TEXT DEFAULT '',
                    chain_digest TEXT
                )
                """
            )
            # Ensure columns exist on older DB schemas. Rows written before these columns
            # existed keep hash_version 1 (legacy hash) and local_prev NULL (the signed
            # prev_hash doubles as the local chain link, as it did when they were written).
            cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
            if "prev_hash" not in cols:
                conn.execute(
                    f"ALTER TABLE transactions ADD COLUMN prev_hash TEXT NOT NULL DEFAULT '{GENESIS_PREV_HASH}'"
                )
            if "sender_public_key" not in cols:
                conn.execute("ALTER TABLE transactions ADD COLUMN sender_public_key TEXT DEFAULT ''")
            if "hash_version" not in cols:
                conn.execute(
                    f"ALTER TABLE transactions ADD COLUMN hash_version INTEGER NOT NULL DEFAULT {HASH_VERSION_LEGACY}"
                )
            if "local_prev" not in cols:
                conn.execute("ALTER TABLE transactions ADD COLUMN local_prev TEXT")
            if "settlement_key" not in cols:
                conn.execute("ALTER TABLE transactions ADD COLUMN settlement_key TEXT DEFAULT ''")
            if "chain_digest" not in cols:
                conn.execute("ALTER TABLE transactions ADD COLUMN chain_digest TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_no_chain_digest "
                "ON transactions(tx_id) WHERE chain_digest IS NULL"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS balances (
                    address TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    nonce INTEGER NOT NULL,
                    balance_micro INTEGER
                )
                """
            )
            # ``balance_micro`` (integer micro-credits) is the balance of record. Ledgers from
            # releases that only had the REAL ``balance`` column are migrated in place: the
            # column is added and filled by exact rounding of each REAL to the nearest
            # micro-credit. ``balance`` stays and is rewritten with every change, so a reader
            # from an older release still sees the balance.
            balance_cols = {row[1] for row in conn.execute("PRAGMA table_info(balances)").fetchall()}
            if "balance_micro" not in balance_cols:
                conn.execute("ALTER TABLE balances ADD COLUMN balance_micro INTEGER")
            self._backfill_balance_micro(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settled_proofs (
                    proof_key TEXT PRIMARY KEY,
                    tx_id TEXT NOT NULL,
                    settled_at TEXT NOT NULL,
                    payer TEXT
                )
                """
            )
            # Rows written before ``payer`` existed keep the bare settlement key as
            # ``proof_key`` (payer NULL) and block that key for every payer, as they did then.
            # Newer rows are scoped: ``proof_key`` is "<payer address>|<settlement key>".
            proof_cols = {row[1] for row in conn.execute("PRAGMA table_info(settled_proofs)").fetchall()}
            if "payer" not in proof_cols:
                conn.execute("ALTER TABLE settled_proofs ADD COLUMN payer TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chain_anchor (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    tx_count INTEGER NOT NULL,
                    head_tx_id TEXT NOT NULL,
                    head_digest TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    mac TEXT NOT NULL,
                    anchored_at TEXT NOT NULL
                )
                """
            )
            # Lazy migration: rows written before the running chain digest existed get theirs
            # on first open. Existing digests are never rewritten.
            if conn.execute("SELECT 1 FROM transactions WHERE chain_digest IS NULL LIMIT 1").fetchone():
                self._backfill_chain_digests(conn)

    @staticmethod
    def _backfill_chain_digests(conn: sqlite3.Connection) -> None:
        """Fills in missing ``chain_digest`` values in chain order (stored ones are kept)."""
        digest = GENESIS_PREV_HASH
        rows = conn.execute("SELECT rowid AS rid, tx_id, chain_digest FROM transactions ORDER BY rowid ASC").fetchall()
        for r in rows:
            digest = _chain_digest(digest, str(r["tx_id"]))
            if r["chain_digest"] is None:
                conn.execute("UPDATE transactions SET chain_digest = ? WHERE rowid = ?", (digest, r["rid"]))

    @staticmethod
    def _backfill_balance_micro(conn: sqlite3.Connection) -> None:
        """Gives every balance row without ``balance_micro`` the micro-credits of its REAL balance."""
        rows = conn.execute("SELECT address, balance FROM balances WHERE balance_micro IS NULL").fetchall()
        for r in rows:
            conn.execute(
                "UPDATE balances SET balance_micro = ? WHERE address = ?",
                (_real_to_micro(float(r["balance"])), r["address"]),
            )

    @staticmethod
    def _scoped_proof_key(payer: str, settlement_key: str) -> str:
        return f"{payer}|{settlement_key}"

    @staticmethod
    def _settlement_recorded(conn: sqlite3.Connection, payer: str, settlement_key: str) -> bool:
        """True if ``payer`` has already paid ``settlement_key`` on this ledger."""
        row = conn.execute(
            "SELECT 1 FROM settled_proofs WHERE proof_key = ? OR proof_key = ? LIMIT 1",
            (settlement_key, Ledger._scoped_proof_key(payer, settlement_key)),
        ).fetchone()
        return row is not None

    def _get_last_tx_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT tx_id FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()
        return str(row["tx_id"]) if row else GENESIS_PREV_HASH

    @staticmethod
    def _account_micro(conn: sqlite3.Connection, address: str) -> tuple[int, int]:
        """Returns (balance in micro-credits, next_nonce) for ``address`` as seen by ``conn``.

        The integer ``balance_micro`` column is the balance of record. The REAL ``balance``
        column is only consulted for a row this release did not write last: one inserted by
        an older release (``balance_micro`` NULL), or one whose REAL no longer equals the
        float of its ``balance_micro`` because an older release updated it. Such a REAL is
        converted by exact rounding to the nearest micro-credit, so a value left behind by
        float arithmetic (``0.19999999999999998``) reads as the balance it stands for.
        """
        row = conn.execute(
            "SELECT balance, balance_micro, nonce FROM balances WHERE address = ?", (address,)
        ).fetchone()
        if row is None:
            return 0, 0
        return Ledger._row_micro(row), int(row["nonce"])

    @staticmethod
    def _row_micro(row: sqlite3.Row) -> int:
        """Micro-credit balance of a ``balances`` row (see ``_account_micro``)."""
        real = float(row["balance"])
        stored = row["balance_micro"]
        if stored is not None and _from_micro(int(stored)) == real:
            return int(stored)
        return _real_to_micro(real)

    @staticmethod
    def _set_balance(conn: sqlite3.Connection, address: str, micro: int, *, bump_nonce: bool = False) -> None:
        """Writes ``micro`` as the balance of ``address`` (integer column and its REAL mirror)."""
        conn.execute(
            """
            INSERT INTO balances (address, balance, nonce, balance_micro) VALUES (?, ?, 0, ?)
            ON CONFLICT(address) DO UPDATE SET balance = excluded.balance, balance_micro = excluded.balance_micro
            """,
            (address, _from_micro(micro), micro),
        )
        if bump_nonce:
            conn.execute("UPDATE balances SET nonce = nonce + 1 WHERE address = ?", (address,))

    @staticmethod
    def _account(conn: sqlite3.Connection, address: str) -> tuple[float, int]:
        """Returns (balance, next_nonce) for ``address`` as seen by ``conn``."""
        micro, nonce = Ledger._account_micro(conn, address)
        return _from_micro(micro), nonce

    @staticmethod
    def _tx_micro(tx: Transaction) -> tuple[int, int]:
        """``(amount, fee)`` of ``tx`` in micro-credits; TransactionRejected if either is finer than that."""
        amount_micro = _exact_micro(tx.amount)
        fee_micro = _exact_micro(tx.fee)
        if amount_micro is None or fee_micro is None or amount_micro <= 0 or fee_micro < 0:
            raise TransactionRejected(
                "amount_precision",
                f"Rejected transaction {tx.tx_id}: amount and fee must be positive multiples of "
                f"0.000001 credits (got amount={tx.amount!r}, fee={tx.fee!r})",
            )
        return amount_micro, fee_micro

    @staticmethod
    def _cell(r: sqlite3.Row, keys: Collection[str], col: str, default: Any = "") -> Any:
        return r[col] if col in keys else default

    @classmethod
    def _row_to_transaction(cls, r: sqlite3.Row) -> Transaction:
        keys = r.keys()
        raw_amount, raw_fee = r["amount"], r["fee"]
        tx = object.__new__(Transaction)
        tx.tx_id = r["tx_id"]
        tx.prev_hash = cls._cell(r, keys, "prev_hash", GENESIS_PREV_HASH)
        tx.sender, tx.recipient, tx.timestamp = r["sender"], r["recipient"], r["timestamp"]
        tx.amount = float(raw_amount) if _is_real_number(raw_amount) else raw_amount
        tx.fee = float(raw_fee) if _is_real_number(raw_fee) else raw_fee
        tx.nonce = r["nonce"]
        tx.memo, tx.signature = cls._cell(r, keys, "memo"), cls._cell(r, keys, "signature")
        tx.signature_algorithm = cls._cell(r, keys, "signature_algorithm", "ed25519")
        tx.sender_public_key = cls._cell(r, keys, "sender_public_key")
        tx.hash_version = cls._cell(r, keys, "hash_version", HASH_VERSION_LEGACY)
        tx.settlement_key = cls._cell(r, keys, "settlement_key")
        return tx

    def has_transaction(self, tx_id: str) -> bool:
        """Checks whether a transaction ID is already recorded in the ledger."""
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM transactions WHERE tx_id = ? LIMIT 1", (tx_id,)).fetchone()
            return row is not None

    def has_settled_proof(self, proof_key: str, payer: str | None = None) -> bool:
        """Checks whether a settlement key (e.g. a ProofOfCompute) has already been paid.

        A settlement is recorded per paying address. With ``payer`` the answer is whether that
        address has paid the key; without it, whether any address has.
        """
        with self._lock, self._get_connection() as conn:
            if payer is not None:
                return self._settlement_recorded(conn, payer, proof_key)
            row = conn.execute(
                "SELECT 1 FROM settled_proofs WHERE proof_key = ?"
                " OR (payer IS NOT NULL AND proof_key = payer || '|' || ?) LIMIT 1",
                (proof_key, proof_key),
            ).fetchone()
            return row is not None

    def get_balance(self, address: str) -> float:
        """Returns current balance for the given address, in credits.

        The float is exact for every balance the ledger can create (up to ``MAX_CREDITS``):
        ``credits_to_micro(get_balance(a)) == get_balance_micro(a)``.
        """
        with self._lock, self._get_connection() as conn:
            return self._account(conn, address)[0]

    def get_balance_micro(self, address: str) -> int:
        """Returns current balance for the given address in integer micro-credits."""
        with self._lock, self._get_connection() as conn:
            return self._account_micro(conn, address)[0]

    def get_nonce(self, address: str) -> int:
        """Returns the next expected transaction nonce for the given address."""
        with self._lock, self._get_connection() as conn:
            return self._account(conn, address)[1]

    # ------------------------------------------------------------------
    # Validated apply path (shared by mint, transfer and reconciliation)
    # ------------------------------------------------------------------

    @staticmethod
    def check_foreign_transaction(tx: Transaction) -> None:
        """Statelessly validates a transaction received from a peer.

        Raises TransactionRejected if the transaction claims a privileged sender, is
        malformed, names an invalid recipient, or does not carry a valid signature by the
        key that controls the sender address.
        """
        if tx.is_privileged:
            raise TransactionRejected(
                "privileged_sender",
                f"Rejected transaction {tx.tx_id}: sender {tx.sender!r} is reserved for local minting",
            )
        if not NodeWallet.is_valid_address(tx.recipient):
            raise TransactionRejected(
                "invalid_recipient", f"Rejected transaction {tx.tx_id}: malformed recipient address"
            )
        if tx.sender == tx.recipient:
            raise TransactionRejected("self_transfer", f"Rejected transaction {tx.tx_id}: sender equals recipient")
        if not tx.is_valid():
            raise TransactionRejected(
                "invalid_signature", f"Rejected invalid or unsigned transaction {tx.tx_id} in DTN settlement bundle"
            )
        if not tx.is_unambiguous():
            raise TransactionRejected(
                "ambiguous_legacy_hash",
                f"Rejected transaction {tx.tx_id}: legacy hash does not uniquely commit to its fields",
            )

    def _apply(
        self,
        conn: sqlite3.Connection,
        tx: Transaction,
        *,
        allow_privileged: bool = False,
    ) -> None:
        """Applies one already-authenticated transaction inside an open write transaction.

        Enforces uniqueness, the sender's expected nonce and sufficient balance, then debits,
        credits and appends the transaction to the local hash chain. Balances are validated
        and updated in integer micro-credits (``balance_micro``; the REAL ``balance`` column
        mirrors it), and an amount or fee finer than one micro-credit is refused (reason
        ``amount_precision``)
        instead of being rounded. A transaction that
        carries a signed ``settlement_key`` is refused if its sender has already paid that key
        here, and otherwise records the key as paid, whether the transaction was created
        locally or reconciled from another ledger. Raises TransactionRejected (and changes
        nothing) if any check fails.
        """
        if conn.execute("SELECT 1 FROM transactions WHERE tx_id = ? LIMIT 1", (tx.tx_id,)).fetchone():
            raise TransactionRejected("duplicate", f"Transaction {tx.tx_id} is already recorded")
        settlement_key = tx.settlement_key
        if settlement_key and self._settlement_recorded(conn, tx.sender, settlement_key):
            raise TransactionRejected("already_settled", f"Settlement {settlement_key} has already been paid")

        amount_micro, fee_micro = self._tx_micro(tx)

        if tx.is_privileged:
            if not allow_privileged:
                raise TransactionRejected("privileged_sender", f"Sender {tx.sender!r} is reserved for local minting")
        else:
            balance_micro, expected_nonce = self._account_micro(conn, tx.sender)
            if tx.nonce != expected_nonce:
                raise TransactionRejected(
                    "bad_nonce",
                    f"Unexpected nonce for {tx.sender}: transaction has {tx.nonce}, ledger expects {expected_nonce}",
                )
            debit_micro = amount_micro + fee_micro
            if balance_micro < debit_micro:
                raise TransactionRejected(
                    "insufficient_balance",
                    f"Insufficient balance: {tx.sender} has {_from_micro(balance_micro):.6f} credits, "
                    f"requires {_from_micro(debit_micro):.6f}",
                )
            self._set_balance(conn, tx.sender, balance_micro - debit_micro, bump_nonce=True)

        # Read after the debit: the write lock is held, so this is the recipient's current balance.
        recipient_micro, _ = self._account_micro(conn, tx.recipient)
        if recipient_micro + amount_micro > _MAX_MICRO:
            raise TransactionRejected(
                "balance_overflow",
                f"Rejected transaction {tx.tx_id}: balance of {tx.recipient} would exceed {MAX_CREDITS:,} credits",
            )
        self._set_balance(conn, tx.recipient, recipient_micro + amount_micro)
        # ``prev_hash`` is part of the signed hash and, for a foreign transaction, points into
        # its origin ledger. ``local_prev`` is this ledger's own chain link.
        tip = conn.execute("SELECT tx_id, chain_digest FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()
        if tip is not None and tip["chain_digest"] is None:
            # A process running an older release appended rows after this ledger was opened.
            self._backfill_chain_digests(conn)
            tip = conn.execute("SELECT tx_id, chain_digest FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()
        local_prev = str(tip["tx_id"]) if tip is not None else GENESIS_PREV_HASH
        prev_digest = str(tip["chain_digest"]) if tip is not None else GENESIS_PREV_HASH
        chain_digest = _chain_digest(prev_digest, tx.tx_id)
        conn.execute(
            """
            INSERT INTO transactions (tx_id, prev_hash, sender, recipient, amount, fee, nonce, timestamp, memo, signature, signature_algorithm, sender_public_key, hash_version, local_prev, settlement_key, chain_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.tx_id,
                tx.prev_hash,
                tx.sender,
                tx.recipient,
                float(tx.amount),
                float(tx.fee),
                tx.nonce,
                tx.timestamp,
                tx.memo,
                tx.signature,
                tx.signature_algorithm,
                tx.sender_public_key,
                tx.hash_version,
                local_prev,
                settlement_key,
                chain_digest,
            ),
        )
        if settlement_key:
            conn.execute(
                "INSERT INTO settled_proofs (proof_key, tx_id, settled_at, payer) VALUES (?, ?, ?, ?)",
                (
                    self._scoped_proof_key(tx.sender, settlement_key),
                    tx.tx_id,
                    datetime.now(UTC).isoformat(),
                    tx.sender,
                ),
            )
        self._advance_anchor(conn, tip is None, local_prev, prev_digest, tx.tx_id, chain_digest)

    # ------------------------------------------------------------------
    # Chain anchor (head digest authenticated with the node identity key)
    # ------------------------------------------------------------------

    @staticmethod
    def _anchor_matches(
        anchor: sqlite3.Row, key: tuple[str, bytes], tx_count: int | None, head_tx_id: str, head_digest: str
    ) -> bool:
        """True if ``anchor`` was produced by this node for exactly the given chain head."""
        key_id, mac_key = key
        count = anchor["tx_count"]
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or not isinstance(anchor["mac"], str)
            or not anchor["mac"].isascii()
            or anchor["key_id"] != key_id
            or anchor["head_tx_id"] != head_tx_id
            or anchor["head_digest"] != head_digest
        ):
            return False
        if tx_count is not None and count != tx_count:
            return False
        expected = _anchor_mac(mac_key, key_id, count, head_tx_id, head_digest)
        return hmac.compare_digest(anchor["mac"], expected)

    @staticmethod
    def _write_anchor(
        conn: sqlite3.Connection, key: tuple[str, bytes], tx_count: int, head_tx_id: str, head_digest: str
    ) -> None:
        key_id, mac_key = key
        conn.execute(
            "INSERT OR REPLACE INTO chain_anchor (id, tx_count, head_tx_id, head_digest, key_id, mac, anchored_at)"
            " VALUES (1, ?, ?, ?, ?, ?, ?)",
            (
                tx_count,
                head_tx_id,
                head_digest,
                key_id,
                _anchor_mac(mac_key, key_id, tx_count, head_tx_id, head_digest),
                datetime.now(UTC).isoformat(),
            ),
        )

    def _advance_anchor(
        self,
        conn: sqlite3.Connection,
        was_empty: bool,
        prev_tx_id: str,
        prev_digest: str,
        new_tx_id: str,
        new_digest: str,
    ) -> None:
        """Moves the anchor to the row just appended, but only from a head it already vouches for.

        A new ledger is anchored from its first transaction. An anchor is never created for
        an existing unanchored chain and never repaired here: doing so would silently bless
        whatever the database currently holds. ``anchor_chain()`` is the explicit way.
        """
        if self._memory_conn is not None:
            return  # nothing persists, and no key file should be created for a throwaway ledger
        anchor = conn.execute("SELECT * FROM chain_anchor WHERE id = 1").fetchone()
        if anchor is None and not was_empty:
            return
        key = _node_anchor_key()
        if key is None:
            if anchor is not None:
                logger.warning(
                    "Ledger %s: node identity key unavailable, chain anchor not advanced; "
                    "verify_chain() will fail until anchor_chain() is run",
                    self.db_path,
                )
            return
        if anchor is None:
            self._write_anchor(conn, key, 1, new_tx_id, new_digest)
        elif not was_empty and self._anchor_matches(anchor, key, None, prev_tx_id, prev_digest):
            self._write_anchor(conn, key, int(anchor["tx_count"]) + 1, new_tx_id, new_digest)
        else:
            logger.warning("Ledger %s: chain anchor does not match the chain head; not advancing it", self.db_path)

    def anchor_chain_with_reason(self) -> tuple[bool, str]:
        """Verifies the chain structure and (re)anchors its current head with the node identity key.

        Returns ``(True, "anchored")`` on success, or ``(False, reason)`` where ``reason`` is
        ``"invalid_chain"``, ``"empty"``, or ``"no_key"``, changing nothing on failure.
        """
        with self._write_transaction() as conn:
            ok, _, count, head_tx_id, head_digest = self._check_chain(conn, None)
            if not ok:
                return False, "invalid_chain"
            if count == 0:
                return False, "empty"
            key = _node_anchor_key()
            if key is None:
                return False, "no_key"
            self._write_anchor(conn, key, count, head_tx_id, head_digest)
        logger.info("Anchored ledger %s at transaction %d (%s)", self.db_path, count, head_tx_id[:12])
        return True, "anchored"

    def anchor_chain(self) -> bool:
        """Verifies the chain structure and (re)anchors its current head with the node identity key.

        This is the explicit operator action for adopting a ledger that has no anchor (one
        created by an older release) or whose anchor is no longer valid (the node identity key
        was replaced, or the file came from another node). Any existing anchor is ignored and
        replaced. Returns False, changing nothing, if the chain itself does not verify, the
        ledger is empty, or the node has no persistent identity key.
        """
        return self.anchor_chain_with_reason()[0]

    def apply_signed_transaction(self, tx: Transaction) -> bool:
        """Validates and applies a signed transaction that originated on another ledger.

        Returns True if the transaction was applied and False if it was already recorded
        (idempotent replay). Raises TransactionRejected when it is privileged, malformed,
        badly signed, carries an unexpected nonce, exceeds the sender's balance, or settles a
        debt (signed ``settlement_key``) its sender has already paid on this ledger
        (reason ``already_settled``). Applying it records its settlement key as paid here.
        """
        self.check_foreign_transaction(tx)
        with self._write_transaction() as conn:
            if conn.execute("SELECT 1 FROM transactions WHERE tx_id = ? LIMIT 1", (tx.tx_id,)).fetchone():
                return False
            self._apply(conn, tx)
        return True

    def mint(self, recipient: str, amount: float, memo: str = "GENESIS_MINT") -> Transaction:
        """Mints initial compute credits to an address.

        ``amount`` must be a positive multiple of 0.000001 credits, at most ``MAX_CREDITS``
        (ValueError otherwise). Credits minted here are issued by this node for itself: they
        are local test credits and carry no value outside this ledger.
        """
        if not _is_real_number(amount) or not math.isfinite(amount) or amount <= 0:
            raise ValueError("Mint amount must be a finite, strictly positive number")
        if _exact_micro(amount) is None:
            raise ValueError(f"Mint amount must not have more than 6 decimal places or exceed {MAX_CREDITS:,} credits")
        if not recipient:
            raise ValueError("Recipient address must not be empty")
        if not NodeWallet.is_valid_address(recipient):
            raise ValueError(f"Recipient {recipient!r} is not a valid sovereign wallet address")

        with self._write_transaction() as conn:
            tx = Transaction(
                sender="MINT",
                recipient=recipient,
                amount=float(amount),
                fee=0.0,
                nonce=0,
                memo=memo,
                prev_hash=self._get_last_tx_hash(conn),
            )
            self._apply(conn, tx, allow_privileged=True)
            new_balance = self._account(conn, recipient)[0]

        logger.info("Minted %.2f credits to %s (New balance: %.2f)", amount, recipient, new_balance)
        return tx

    def transfer(
        self,
        sender_wallet: NodeWallet,
        recipient: str,
        amount: float,
        fee: float = 0.0,
        memo: str = "",
        settlement_key: str | None = None,
    ) -> Transaction:
        """Executes a signed transfer of compute credits between nodes.

        If ``settlement_key`` is given, it is signed into the transaction and the transfer is
        recorded as this sender's one and only payment for that key; a second transfer with
        the same key raises ValueError, on this ledger and on every ledger the transaction
        is reconciled into.

        ``amount`` and ``fee`` must be multiples of 0.000001 credits; a finer value raises
        ValueError rather than being rounded. The balance check and the debit/credit are
        exact (integer micro-credits), so a sender can always spend its whole balance.
        """
        if settlement_key is not None and not isinstance(settlement_key, str):
            raise ValueError("settlement_key must be a string")
        if not _is_real_number(amount) or not math.isfinite(amount) or amount <= 0:
            raise ValueError("Transfer amount must be strictly positive")
        if not _is_real_number(fee) or not math.isfinite(fee) or fee < 0:
            raise ValueError("Transfer fee cannot be negative")
        amount_micro = _exact_micro(amount)
        fee_micro = _exact_micro(fee)
        if amount_micro is None or fee_micro is None or amount_micro <= 0:
            raise ValueError(
                f"Transfer amount and fee must not have more than 6 decimal places or exceed {MAX_CREDITS:,} credits"
            )
        if sender_wallet.address == recipient:
            raise ValueError("Cannot transfer credits to self")
        if not NodeWallet.is_valid_address(recipient):
            raise ValueError(f"Recipient {recipient!r} is not a valid sovereign wallet address")

        with self._write_transaction() as conn:
            balance_micro, nonce = self._account_micro(conn, sender_wallet.address)
            debit_micro = amount_micro + fee_micro
            if balance_micro < debit_micro:
                raise TransactionRejected(
                    "insufficient_balance",
                    f"Insufficient balance: {sender_wallet.address} has {_from_micro(balance_micro):.6f} credits, "
                    f"requires {_from_micro(debit_micro):.6f}",
                )
            tx = Transaction(
                sender=sender_wallet.address,
                recipient=recipient,
                amount=float(amount),
                fee=float(fee),
                nonce=nonce,
                memo=memo,
                prev_hash=self._get_last_tx_hash(conn),
                settlement_key=settlement_key or "",
            )
            tx.sign(sender_wallet)
            if not tx.is_valid(sender_wallet.keypair.public_key_hex):
                raise ValueError("Transaction signature self-verification failed")
            self._apply(conn, tx)

        logger.info(
            "Transferred %.2f credits from %s to %s (Tx: %s)",
            amount,
            sender_wallet.address,
            recipient,
            tx.tx_id[:12],
        )
        return tx

    def _check_chain(
        self, conn: sqlite3.Connection, anchor_key: tuple[str, bytes] | None
    ) -> tuple[bool, str, int, str, str]:
        """Walks the chain; returns ``(structure_ok, anchor_status, tx_count, head_tx_id, head_digest)``."""
        # One read transaction for both reads: another ledger instance or process may commit a
        # valid transaction (rows plus a new anchor) between them, and an old row list
        # compared with a new anchor would look like tampering.
        began = not conn.in_transaction
        if began:
            conn.execute("BEGIN")
        try:
            rows = conn.execute("SELECT * FROM transactions ORDER BY rowid ASC").fetchall()
            anchor = conn.execute("SELECT * FROM chain_anchor WHERE id = 1").fetchone()
        finally:
            if began:
                conn.commit()
        expected_prev = GENESIS_PREV_HASH
        digest = GENESIS_PREV_HASH
        # Per sender: (nonce of its latest row, whether that row came through the validated apply path).
        last_nonce: dict[str, tuple[int, bool]] = {}
        structure_ok = True
        for r in rows:
            tx = self._row_to_transaction(r)
            local_prev = r["local_prev"]
            if local_prev is None:
                # Written by a release that had no local link: the signed link is the chain
                # link. Only legacy-hash rows can be that old.
                if tx.hash_version != HASH_VERSION_LEGACY or tx.prev_hash != expected_prev:
                    structure_ok = False
            elif local_prev != expected_prev:
                structure_ok = False
            elif tx.prev_hash != expected_prev and tx.is_privileged:
                # Mints are only ever created locally, so their own link must be the local one.
                structure_ok = False
            if not tx.is_valid():
                structure_ok = False
            if not tx.is_privileged and isinstance(tx.nonce, int) and not isinstance(tx.nonce, bool):
                applied = local_prev is not None
                previous = last_nonce.get(tx.sender)
                if applied:
                    # The apply path only ever accepts the sender's next nonce.
                    if previous is None:
                        if tx.nonce != 0:
                            structure_ok = False
                    elif previous[1] and tx.nonce != previous[0] + 1:
                        structure_ok = False
                last_nonce[tx.sender] = (tx.nonce, applied)
            row_tx_id = tx.tx_id if isinstance(tx.tx_id, str) else ""
            digest = _chain_digest(digest, row_tx_id)
            stored_digest = r["chain_digest"]
            if stored_digest is not None and stored_digest != digest:
                structure_ok = False
            expected_prev = row_tx_id

        if anchor is None:
            status = ANCHOR_ABSENT
        elif anchor_key is None:
            status = ANCHOR_UNVERIFIABLE
        elif self._anchor_matches(anchor, anchor_key, len(rows), expected_prev, digest):
            status = ANCHOR_VALID
        else:
            status = ANCHOR_INVALID
        if not structure_ok:
            return False, status, len(rows), "", ""
        return True, status, len(rows), expected_prev, digest

    def _chain_report_on_conn(self, conn: sqlite3.Connection) -> tuple[bool, str, int]:
        began = not conn.in_transaction
        if began:
            conn.execute("BEGIN")
        try:
            has_anchor = conn.execute("SELECT 1 FROM chain_anchor WHERE id = 1").fetchone() is not None
            # Only consult (and possibly create) the node identity key when there is an anchor to check.
            key = _node_anchor_key() if has_anchor else None
            ok, status, count, _, _ = self._check_chain(conn, key)
        finally:
            if began:
                conn.commit()
        if status == ANCHOR_UNVERIFIABLE:
            logger.warning(
                "Ledger %s carries a chain anchor but this node has no persistent identity key to check it",
                self.db_path,
            )
        return ok, status, count

    def _chain_report(self) -> tuple[bool, str, int]:
        with self._lock, self._get_connection() as conn:
            return self._chain_report_on_conn(conn)

    def chain_report(self) -> tuple[bool, str, int]:
        """Returns ``(links_ok, anchor_status, tx_count)`` from one consistent snapshot."""
        return self._chain_report()

    def chain_anchor_status(self) -> str:
        """Returns the state of the head anchor: "valid", "absent", "invalid" or "unverifiable".

        "absent" means the ledger has no anchor (created by an older release, in memory, or
        the anchor row was removed); "unverifiable" means an anchor exists but this node has
        no persistent identity key to check it with.
        """
        return self._chain_report()[1]

    def verify_chain(self, require_anchor: bool = False) -> bool:
        """Verifies chain linkage, signatures, sender nonces, chain digests and the head anchor.

        Row by row, in order:

        * the row must pass ``Transaction.is_valid()`` under the hash scheme it was written with;
        * its local link (``local_prev``) must name the previous row. Rows from releases
          without that column must be legacy-hash rows whose *signed* ``prev_hash`` names the
          previous row. A mint must always link through its own ``prev_hash``; only a signed
          transfer may have a ``prev_hash`` that points elsewhere (a transaction reconciled
          from another ledger, whose signed link refers to its origin chain);
        * a sender's transactions that came through the validated apply path must carry
          consecutive nonces starting at 0. Nonces are signed, so removing a signed
          transaction is detected whenever its sender has a later transaction;
        * the stored running digest (``sha256`` over the previous digest and the ``tx_id``)
          must match.

        Finally, if the ledger has a head anchor (a MAC over the transaction count, head
        ``tx_id`` and head digest under a key derived from this node's persistent identity
        key, which is kept in the SMDC state directory, not in the database) it must be
        valid for exactly the current head and this node's key. File ledgers created by this
        release are anchored from their first transaction; older ledgers stay unanchored until
        ``anchor_chain()`` is called.

        What this cannot detect, against someone able to rewrite the database file:

        * With a valid anchor: replacing the whole database (rows and anchor) with an earlier
          copy of itself; and anything done by someone who can also read the node identity key.
        * Deleting the anchor row. The ledger then verifies as an unanchored one, with the
          limits below. Pass ``require_anchor=True`` (or check ``chain_anchor_status()``) when
          the ledger is known to be anchored: a missing or unverifiable anchor then fails.
        * Without an anchor (digests and ``local_prev`` can be recomputed by the editor):
          truncating the tail; inserting forged mint rows (mints are unsigned) at the tail or
          before a signed transfer made to look reconciled; removing a mint, or a sender's
          most recent transfer(s), when the following row is a signed transfer that can be
          made to look reconciled; reordering such rows where every sender's nonce order is
          preserved.
        * Edits to the ``balances`` and ``settled_proofs`` tables, which are not re-derived here.
        """
        links_ok, status, _ = self.chain_report()
        if not links_ok or status == ANCHOR_INVALID:
            return False
        return status == ANCHOR_VALID or not require_anchor

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

        return [self._row_to_transaction(r) for r in rows]

    def export_state(self) -> dict[str, Any]:
        """Exports ledger snapshot state."""
        with self._lock, self._get_connection() as conn:
            began = not conn.in_transaction
            if began:
                conn.execute("BEGIN")
            try:
                bal_rows = conn.execute("SELECT address, balance, balance_micro, nonce FROM balances").fetchall()
                total_transactions = int(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
                chain_ok, anchor_status, _ = self._chain_report_on_conn(conn)
            finally:
                if began:
                    conn.commit()
            balances = {
                r["address"]: {
                    "balance": _from_micro(self._row_micro(r)),
                    "balance_micro": self._row_micro(r),
                    "nonce": int(r["nonce"]),
                }
                for r in bal_rows
            }
        return {
            "balances": balances,
            "total_transactions": total_transactions,
            "chain_verified": chain_ok and anchor_status != ANCHOR_INVALID,
            "chain_anchor": anchor_status,
        }
