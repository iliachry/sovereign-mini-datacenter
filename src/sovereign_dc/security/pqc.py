"""Post-Quantum Cryptography (PQC) Security Engine.

Implements NIST FIPS 204 (ML-DSA / Dilithium) and FIPS 203 (ML-KEM / Kyber)
cryptographic operations for RFC 9171 / RFC 9172 (BPSec) DTN bundle
signing, mesh consensus authentication, and zero-trust key encapsulation.

Backed by pure-Python lattice implementations (`dilithium-py` for FIPS 204 ML-DSA
and `kyber-py` for FIPS 203 ML-KEM) over Module-LWE / Module-SIS polynomial rings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from dilithium_py.ml_dsa import ML_DSA_65, ML_DSA_87
from kyber_py.ml_kem import ML_KEM_768, ML_KEM_1024


class PQCAlgorithm(StrEnum):
    """NIST-standardized Post-Quantum Cryptographic Algorithms."""

    ML_DSA_65 = "ML-DSA-65"  # FIPS 204 (Dilithium3 equivalent, Category 3)
    ML_DSA_87 = "ML-DSA-87"  # FIPS 204 (Dilithium5 equivalent, Category 5)
    ML_KEM_768 = "ML-KEM-768"  # FIPS 203 (Kyber768 equivalent, Category 3)
    ML_KEM_1024 = "ML-KEM-1024"  # FIPS 203 (Kyber1024 equivalent, Category 5)
    SPHINCS_PLUS = "SLH-DSA-SHA2-256s"  # FIPS 205 alias (routed via ML-DSA-87)


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
        else:
            self._impl = ML_DSA_87
            self._key_len = 2592
            self._sk_len = 4896
            self._sig_len = 4627

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

    def sign_bundle(self, bundle_dict: dict[str, Any], keypair: PQCKeyPair) -> dict[str, Any]:
        """Signs an RFC 9171 DTN Space Bundle, appending a BPSec (RFC 9172) signature block."""
        canonical_payload = json.dumps(
            {
                "id": bundle_dict.get("id"),
                "src": bundle_dict.get("src"),
                "dst": bundle_dict.get("dst"),
                "ts": bundle_dict.get("ts"),
                "payload_b64": bundle_dict.get("payload_b64"),
            },
            sort_keys=True,
        ).encode("utf-8")

        sig = self.sign(canonical_payload, keypair.private_key)

        signed_bundle = dict(bundle_dict)
        signed_bundle["bpsec"] = {
            "alg": keypair.algorithm.value,
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
        pinned trust anchor before verifying the ML-DSA signature.
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

            canonical_payload = json.dumps(
                {
                    "id": signed_bundle_dict.get("id"),
                    "src": signed_bundle_dict.get("src"),
                    "dst": signed_bundle_dict.get("dst"),
                    "ts": signed_bundle_dict.get("ts"),
                    "payload_b64": signed_bundle_dict.get("payload_b64"),
                },
                sort_keys=True,
            ).encode("utf-8")

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
        else:
            self._impl = ML_KEM_1024
            self._key_len = 1568
            self._dk_len = 3168
            self._ct_len = 1568

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


_CACHED_NODE_KEYPAIRS: dict[PQCAlgorithm, PQCKeyPair] = {}


def get_node_pqc_keypair(algorithm: PQCAlgorithm = PQCAlgorithm.ML_DSA_65) -> PQCKeyPair:
    """Returns a process-cached PQC keypair for status/telemetry attestation without per-request churn."""
    if algorithm not in _CACHED_NODE_KEYPAIRS:
        _CACHED_NODE_KEYPAIRS[algorithm] = PQCSigner(algorithm).generate_keypair()
    return _CACHED_NODE_KEYPAIRS[algorithm]
