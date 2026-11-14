"""Post-Quantum Cryptography (PQC) Security Engine.

Implements NIST FIPS 204 (ML-DSA / Dilithium) and FIPS 203 (ML-KEM / Kyber)
cryptographic operations for RFC 9171 / RFC 9172 (BPSec) DTN bundle
signing, mesh consensus authentication, and zero-trust key encapsulation.

Backed by pure-Python lattice implementations (`dilithium-py` for FIPS 204 ML-DSA
and `kyber-py` for FIPS 203 ML-KEM) over Module-LWE / Module-SIS polynomial rings.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from dilithium_py.ml_dsa import ML_DSA_65, ML_DSA_87
from kyber_py.ml_kem import ML_KEM_768, ML_KEM_1024

logger = logging.getLogger("smdc.security.pqc")

# Version tag of the BPSec signature coverage emitted by ``PQCSigner.sign_bundle``.
# Coverage 2 signs every bundle field except the ``bpsec`` block itself; bundles without a
# ``cov`` marker were signed by older releases over (id, src, dst, ts, payload_b64) only.
BPSEC_COVERAGE_VERSION = 2


class PQCAlgorithm(StrEnum):
    """NIST-standardized Post-Quantum Cryptographic Algorithms."""

    ML_DSA_65 = "ML-DSA-65"  # FIPS 204 (Dilithium3 equivalent, Category 3)
    ML_DSA_87 = "ML-DSA-87"  # FIPS 204 (Dilithium5 equivalent, Category 5)
    ML_KEM_768 = "ML-KEM-768"  # FIPS 203 (Kyber768 equivalent, Category 3)
    ML_KEM_1024 = "ML-KEM-1024"  # FIPS 203 (Kyber1024 equivalent, Category 5)
    SPHINCS_PLUS = "SLH-DSA-SHA2-256s"  # FIPS 205 identifier (reserved; no engine implements it)


@dataclass(frozen=True)
class PQCKeyPair:
    """Represents a public/private post-quantum keypair."""

    algorithm: PQCAlgorithm
    public_key: bytes
    private_key: bytes
    key_id: str

    def to_dict(self) -> dict[str, str]:
        """Serializes public components for certificate / mesh distribution."""
        return {
            "algorithm": self.algorithm.value,
            "key_id": self.key_id,
            "public_key_b64": base64.b64encode(self.public_key).decode("ascii"),
        }

    @classmethod
    def from_public_dict(cls, data: dict[str, str]) -> PQCKeyPair:
        """Constructs a public-only keypair container from serialized metadata."""
        return cls(
            algorithm=PQCAlgorithm(data["algorithm"]),
            public_key=base64.b64decode(data["public_key_b64"]),
            private_key=b"",
            key_id=data["key_id"],
        )


class PQCSigner:
    """Post-Quantum Digital Signature Engine (NIST FIPS 204 / ML-DSA).

    Signs and verifies arbitrary messages, Raft consensus log entries, and
    RFC 9171 DTN Space Bundles using Module-Lattice Digital Signature Standard
    (ML-DSA-65 and ML-DSA-87).
    """

    def __init__(self, algorithm: PQCAlgorithm = PQCAlgorithm.ML_DSA_65) -> None:
        self.algorithm = algorithm
        if algorithm == PQCAlgorithm.ML_DSA_65:
            self._impl: Any = ML_DSA_65
            self._key_len = 1952
            self._sk_len = 4032
            self._sig_len = 3309
        elif algorithm == PQCAlgorithm.ML_DSA_87:
            self._impl = ML_DSA_87
            self._key_len = 2592
            self._sk_len = 4896
            self._sig_len = 4627
        else:
            # Never sign with one algorithm while labelling the output as another.
            raise ValueError(
                f"PQCSigner does not implement {getattr(algorithm, 'value', algorithm)!r}; "
                f"supported signature algorithms: {PQCAlgorithm.ML_DSA_65.value}, {PQCAlgorithm.ML_DSA_87.value}"
            )

    def generate_keypair(self, seed: bytes | None = None) -> PQCKeyPair:
        """Generates a new Post-Quantum ML-DSA signing keypair."""
        if seed is not None:
            digest = hashlib.shake_256(b"SMDC-ML-DSA-KEYGEN-SEED:" + seed).digest(32)
            pubkey, privkey = self._impl._keygen_internal(digest)
        else:
            pubkey, privkey = self._impl.keygen()

        key_id = hashlib.sha256(pubkey).hexdigest()[:16]
        return PQCKeyPair(
            algorithm=self.algorithm,
            public_key=pubkey,
            private_key=privkey,
            key_id=f"pqc-{self.algorithm.value.lower()}-{key_id}",
        )

    def public_key_from_private(self, private_key: bytes) -> bytes:
        """Recomputes the ML-DSA public key that belongs to ``private_key``."""
        if len(private_key) != self._sk_len:
            raise ValueError(
                f"Invalid {self.algorithm.value} private key length: expected {self._sk_len}, got {len(private_key)}"
            )
        return bytes(self._impl.pk_from_sk(private_key))

    def sign(self, message: bytes, private_key: bytes) -> bytes:
        """Signs a message using the ML-DSA private key.

        Raises ValueError if the private key length does not match FIPS 204 specifications.
        """
        if len(private_key) != self._sk_len:
            raise ValueError(
                f"Invalid {self.algorithm.value} private key length: expected {self._sk_len}, got {len(private_key)}"
            )
        return bytes(self._impl.sign(private_key, message))

    def verify(self, message: bytes, signature: bytes, public_key: bytes) -> bool:
        """Verifies an ML-DSA signature against a public key.

        Returns True if and only if the lattice signature is mathematically valid.
        """
        if len(signature) != self._sig_len or len(public_key) != self._key_len:
            return False
        try:
            return bool(self._impl.verify(public_key, message, signature))
        except Exception:
            return False

    @staticmethod
    def _bundle_signing_input(bundle_dict: dict[str, Any], coverage: int) -> bytes:
        """Builds the canonical byte string covered by a BPSec signature of the given coverage version."""
        if coverage == BPSEC_COVERAGE_VERSION:
            covered = {k: v for k, v in bundle_dict.items() if k != "bpsec"}
            return json.dumps(
                {"bpsec_cov": BPSEC_COVERAGE_VERSION, "bundle": covered},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        # Legacy coverage (bundles signed before the ``cov`` marker existed).
        return json.dumps(
            {
                "id": bundle_dict.get("id"),
                "src": bundle_dict.get("src"),
                "dst": bundle_dict.get("dst"),
                "ts": bundle_dict.get("ts"),
                "payload_b64": bundle_dict.get("payload_b64"),
            },
            sort_keys=True,
        ).encode("utf-8")

    def sign_bundle(self, bundle_dict: dict[str, Any], keypair: PQCKeyPair) -> dict[str, Any]:
        """Signs an RFC 9171 DTN Space Bundle, appending a BPSec (RFC 9172) signature block.

        The signature covers every field of ``bundle_dict`` except ``bpsec`` (coverage version 2).
        """
        if keypair.algorithm != self.algorithm:
            raise ValueError(
                f"Keypair algorithm {keypair.algorithm.value} does not match signer algorithm {self.algorithm.value}"
            )
        canonical_payload = self._bundle_signing_input(bundle_dict, BPSEC_COVERAGE_VERSION)

        sig = self.sign(canonical_payload, keypair.private_key)

        signed_bundle = dict(bundle_dict)
        signed_bundle["bpsec"] = {
            "alg": keypair.algorithm.value,
            "cov": BPSEC_COVERAGE_VERSION,
            "key_id": keypair.key_id,
            "sig_b64": base64.b64encode(sig).decode("ascii"),
            "pubkey_b64": base64.b64encode(keypair.public_key).decode("ascii"),
        }
        return signed_bundle

    def verify_bundle(
        self,
        signed_bundle_dict: dict[str, Any],
        trusted_public_key: bytes | None = None,
    ) -> bool:
        """Verifies the BPSec Post-Quantum signature block on an RFC 9171 DTN bundle.

        If ``trusted_public_key`` is supplied, requires the bundle's key to match the
        pinned trust anchor before verifying the ML-DSA signature. Without it the result
        only proves the bundle is self-consistent, not who signed it.

        Bundles signed by this release carry ``bpsec.cov == 2`` and are verified over every
        bundle field except ``bpsec``. Bundles without the marker were signed by older
        releases and are verified over the legacy field set (id, src, dst, ts, payload_b64)
        only, so their remaining fields are NOT authenticated.
        """
        bpsec = signed_bundle_dict.get("bpsec")
        if not isinstance(bpsec, dict):
            return False

        try:
            sig = base64.b64decode(bpsec["sig_b64"], validate=True)
            pubkey = base64.b64decode(bpsec["pubkey_b64"], validate=True)
            alg_name = bpsec["alg"]
            if alg_name != self.algorithm.value:
                return False
            if trusted_public_key is not None and not hmac.compare_digest(pubkey, trusted_public_key):
                return False

            coverage = bpsec.get("cov", 1)
            if coverage not in (1, BPSEC_COVERAGE_VERSION):
                return False
            canonical_payload = self._bundle_signing_input(signed_bundle_dict, coverage)

            return self.verify(canonical_payload, sig, pubkey)
        except Exception:
            return False


class PQCKEM:
    """Post-Quantum Key Encapsulation Mechanism (NIST FIPS 203 / ML-KEM).

    Establishes 256-bit symmetric encryption keys between sovereign nodes
    and satellite ground stations using Module-Lattice Key Encapsulation
    (ML-KEM-768 and ML-KEM-1024).
    """

    def __init__(self, algorithm: PQCAlgorithm = PQCAlgorithm.ML_KEM_768) -> None:
        self.algorithm = algorithm
        if algorithm == PQCAlgorithm.ML_KEM_768:
            self._impl: Any = ML_KEM_768
            self._key_len = 1184
            self._dk_len = 2400
            self._ct_len = 1088
        elif algorithm == PQCAlgorithm.ML_KEM_1024:
            self._impl = ML_KEM_1024
            self._key_len = 1568
            self._dk_len = 3168
            self._ct_len = 1568
        else:
            raise ValueError(
                f"PQCKEM does not implement {getattr(algorithm, 'value', algorithm)!r}; "
                f"supported KEM algorithms: {PQCAlgorithm.ML_KEM_768.value}, {PQCAlgorithm.ML_KEM_1024.value}"
            )

    def generate_keypair(self) -> PQCKeyPair:
        """Generates an ML-KEM Post-Quantum encapsulation keypair."""
        ek, dk = self._impl.keygen()
        key_id = hashlib.sha256(ek).hexdigest()[:16]
        return PQCKeyPair(
            algorithm=self.algorithm,
            public_key=ek,
            private_key=dk,
            key_id=f"kem-{self.algorithm.value.lower()}-{key_id}",
        )

    def encapsulate(self, recipient_public_key: bytes) -> tuple[bytes, bytes]:
        """Encapsulates a 256-bit shared secret under the recipient's ML-KEM public key.

        Returns:
            Tuple of (ciphertext, 256-bit shared_secret_key).
        """
        if len(recipient_public_key) != self._key_len:
            raise ValueError(
                f"Invalid {self.algorithm.value} encapsulation key length: "
                f"expected {self._key_len}, got {len(recipient_public_key)}"
            )
        shared_secret, ciphertext = self._impl.encaps(recipient_public_key)
        return bytes(ciphertext), bytes(shared_secret)

    def decapsulate(self, ciphertext: bytes, recipient_private_key: bytes) -> bytes:
        """Decapsulates the 256-bit shared secret using recipient's ML-KEM private key."""
        if len(ciphertext) != self._ct_len:
            raise ValueError(
                f"Invalid {self.algorithm.value} ciphertext length: expected {self._ct_len}, got {len(ciphertext)}"
            )
        if len(recipient_private_key) != self._dk_len:
            raise ValueError(
                f"Invalid {self.algorithm.value} decapsulation key length: "
                f"expected {self._dk_len}, got {len(recipient_private_key)}"
            )
        return bytes(self._impl.decaps(recipient_private_key, ciphertext))


_CACHED_NODE_KEYPAIRS: dict[tuple[PQCAlgorithm, str], PQCKeyPair] = {}

NODE_KEY_FILE_VERSION = 1


def _node_state_dir() -> str:
    """Directory holding the persisted node identity keys (``SMDC_STATE_DIR`` or ``~/.smdc``)."""
    return os.environ.get("SMDC_STATE_DIR") or os.path.expanduser("~/.smdc")


def node_pqc_key_path(algorithm: PQCAlgorithm, state_dir: str | None = None) -> str:
    """Returns the path of the persisted node signing key for ``algorithm``."""
    return os.path.join(state_dir or _node_state_dir(), f"node_pqc_{algorithm.value.lower()}.key.json")


def _load_node_keypair(path: str, algorithm: PQCAlgorithm) -> PQCKeyPair:
    """Loads and validates a persisted node keypair. Raises ValueError/OSError if unusable."""
    st = os.stat(path)
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise ValueError(f"node key file {path} is not owned by the current user")
    # POSIX permission bits are meaningless on Windows (NTFS reports 0o666); ACLs apply there.
    if os.name != "nt" and st.st_mode & 0o077:
        raise ValueError(f"node key file {path} is group/world accessible (mode {st.st_mode & 0o777:o})")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("algorithm") != algorithm.value:
        raise ValueError(f"node key file {path} does not hold a {algorithm.value} key")
    public_key = base64.b64decode(data["public_key_b64"], validate=True)
    private_key = base64.b64decode(data["private_key_b64"], validate=True)
    signer = PQCSigner(algorithm)
    if not hmac.compare_digest(signer.public_key_from_private(private_key), public_key):
        raise ValueError(f"node key file {path} is inconsistent (public key does not match private key)")
    key_id = f"pqc-{algorithm.value.lower()}-{hashlib.sha256(public_key).hexdigest()[:16]}"
    return PQCKeyPair(algorithm=algorithm, public_key=public_key, private_key=private_key, key_id=key_id)


def _persist_node_keypair(path: str, keypair: PQCKeyPair) -> bool:
    """Atomically creates the 0600 node key file. Returns False if another process created it first."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    payload = json.dumps(
        {
            "version": NODE_KEY_FILE_VERSION,
            "algorithm": keypair.algorithm.value,
            "key_id": keypair.key_id,
            "public_key_b64": base64.b64encode(keypair.public_key).decode("ascii"),
            "private_key_b64": base64.b64encode(keypair.private_key).decode("ascii"),
        },
        indent=2,
    )
    # mkstemp creates the temp file 0600 with O_EXCL; link() publishes it only if the
    # destination does not exist yet, so the key is never visible partially written or
    # with a wider mode, and a concurrent creator is never overwritten.
    fd, tmp_path = tempfile.mkstemp(prefix=".node_pqc_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            return False
        return True
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def get_node_pqc_keypair(
    algorithm: PQCAlgorithm = PQCAlgorithm.ML_DSA_65,
    state_dir: str | None = None,
) -> PQCKeyPair:
    """Returns this node's persistent PQC signing identity for ``algorithm``.

    The keypair is stored owner-only (0600) under the SMDC state directory
    (``SMDC_STATE_DIR`` or ``~/.smdc``) and created atomically on first use, so signatures
    produced by different processes of the same node verify against one stable public key.
    If the state directory cannot be written, the function logs a warning and falls back to
    a key that lives only for the current process.
    """
    path = node_pqc_key_path(algorithm, state_dir)
    cache_key = (algorithm, path)
    cached = _CACHED_NODE_KEYPAIRS.get(cache_key)
    if cached is not None:
        return cached

    keypair: PQCKeyPair | None = None
    try:
        if not os.path.exists(path):
            candidate = PQCSigner(algorithm).generate_keypair()
            if _persist_node_keypair(path, candidate):
                keypair = candidate
                logger.info("Created persistent node %s identity %s at %s", algorithm.value, candidate.key_id, path)
        if keypair is None:
            keypair = _load_node_keypair(path, algorithm)
    except (OSError, ValueError, KeyError, TypeError) as err:
        logger.warning(
            "Persistent node %s key unavailable at %s (%s); using an ephemeral in-process key",
            algorithm.value,
            path,
            err,
        )
        keypair = PQCSigner(algorithm).generate_keypair()

    _CACHED_NODE_KEYPAIRS[cache_key] = keypair
    return keypair
