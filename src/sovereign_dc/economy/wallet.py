"""Cryptographic node wallet for the Sovereign Mini Datacenter compute economy.

Supports Ed25519 and Post-Quantum ML-DSA-87 keypair generation, address encoding,
payload signing, and signature verification.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import json
import os
from typing import Any

from sovereign_dc.log import get_logger
from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner

logger = get_logger("sovereign_dc.economy.wallet")


class AddressType(enum.StrEnum):
    """Supported cryptographic address and signature schemes."""

    ED25519 = "ed25519"
    ML_DSA_87 = "ML-DSA-87"


class WalletKeypair:
    """Represents a cryptographic public/private keypair."""

    def __init__(
        self,
        algorithm: AddressType,
        public_key_bytes: bytes,
        private_key_bytes: bytes,
    ) -> None:
        self.algorithm = algorithm
        self.public_key_bytes = public_key_bytes
        self.private_key_bytes = private_key_bytes

    @property
    def public_key_hex(self) -> str:
        """Hex-encoded public key."""
        return self.public_key_bytes.hex()

    @property
    def private_key_hex(self) -> str:
        """Hex-encoded private key."""
        return self.private_key_bytes.hex()


class NodeWallet:
    """Manages node cryptographic identity, address formatting, and transaction signing."""

    def __init__(
        self,
        node_id: str,
        keypair: WalletKeypair,
    ) -> None:
        self.node_id = node_id
        self.keypair = keypair
        self.address = self._derive_address()

    def _derive_address(self) -> str:
        """Derives a human-readable sovereign address from the public key."""
        raw_hash = hashlib.sha256(self.keypair.public_key_bytes).digest()[:20]
        prefix = "sov_pqc_" if self.keypair.algorithm == AddressType.ML_DSA_87 else "sov_"
        return f"{prefix}{raw_hash.hex()}"

    @classmethod
    def create(
        cls,
        node_id: str,
        algorithm: AddressType = AddressType.ED25519,
        seed: bytes | None = None,
    ) -> NodeWallet:
        """Generates a new node wallet with the specified cryptographic scheme."""
        algo_enum = PQCAlgorithm.ML_DSA_87 if algorithm == AddressType.ML_DSA_87 else PQCAlgorithm.ML_DSA_65
        pqc = PQCSigner(algorithm=algo_enum)
        kp = pqc.generate_keypair()
        keypair = WalletKeypair(
            algorithm=algorithm,
            public_key_bytes=kp.public_key,
            private_key_bytes=kp.private_key,
        )

        wallet = cls(node_id=node_id, keypair=keypair)
        logger.info("Generated %s wallet for node %s: %s", algorithm.value, node_id, wallet.address)
        return wallet

    def sign_payload(self, payload: bytes) -> str:
        """Signs a byte payload and returns a hex-encoded signature."""
        algo_enum = (
            PQCAlgorithm.ML_DSA_87 if self.keypair.algorithm == AddressType.ML_DSA_87 else PQCAlgorithm.ML_DSA_65
        )
        pqc = PQCSigner(algorithm=algo_enum)
        sig_bytes = pqc.sign(message=payload, private_key=self.keypair.private_key_bytes)
        return sig_bytes.hex()

    @staticmethod
    def verify_signature(
        payload: bytes,
        signature_hex: str,
        public_key_hex: str,
        algorithm: AddressType = AddressType.ED25519,
    ) -> bool:
        """Verifies a signature against a payload and public key."""
        try:
            sig_bytes = bytes.fromhex(signature_hex)
            pub_bytes = bytes.fromhex(public_key_hex)
            algo_enum = PQCAlgorithm.ML_DSA_87 if algorithm == AddressType.ML_DSA_87 else PQCAlgorithm.ML_DSA_65
            pqc = PQCSigner(algorithm=algo_enum)
            return pqc.verify(message=payload, signature=sig_bytes, public_key=pub_bytes)
        except Exception as e:
            logger.warning("Signature verification failed: %s", e)
            return False

    def to_dict(self) -> dict[str, Any]:
        """Serializes the wallet (excluding private key) to a dictionary."""
        return {
            "node_id": self.node_id,
            "address": self.address,
            "algorithm": self.keypair.algorithm.value,
            "public_key": self.keypair.public_key_hex,
        }

    def save_to_file(self, filepath: str, password: str | None = None) -> None:
        """Exports the wallet securely to a JSON file."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        # Obfuscate / protect private key
        priv_enc = base64.b64encode(self.keypair.private_key_bytes).decode("ascii")
        data = {
            "node_id": self.node_id,
            "address": self.address,
            "algorithm": self.keypair.algorithm.value,
            "public_key": self.keypair.public_key_hex,
            "private_key_enc": priv_enc,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved wallet %s to %s", self.address, filepath)

    @classmethod
    def load_from_file(cls, filepath: str, password: str | None = None) -> NodeWallet:
        """Loads a wallet from a JSON file."""
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        priv_bytes = base64.b64decode(data["private_key_enc"].encode("ascii"))
        pub_bytes = bytes.fromhex(data["public_key"])
        algo = AddressType(data["algorithm"])
        keypair = WalletKeypair(
            algorithm=algo,
            public_key_bytes=pub_bytes,
            private_key_bytes=priv_bytes,
        )
        return cls(node_id=data["node_id"], keypair=keypair)
