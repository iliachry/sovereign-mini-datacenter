"""Cryptographic node wallet for the Sovereign Mini Datacenter compute economy.

Supports RFC 8032 Ed25519 and Post-Quantum NIST FIPS 204 ML-DSA-87 keypair generation,
deterministic seed derivation, authenticated AES-256-GCM keystore encryption,
address encoding, payload signing, and signature verification.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import json
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

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
        if algorithm == AddressType.ED25519:
            if seed is not None:
                seed_32 = seed if len(seed) == 32 else hashlib.sha256(seed).digest()
                sk = ed25519.Ed25519PrivateKey.from_private_bytes(seed_32)
            else:
                sk = ed25519.Ed25519PrivateKey.generate()
            pk = sk.public_key()
            priv_bytes = sk.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            pub_bytes = pk.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            keypair = WalletKeypair(
                algorithm=AddressType.ED25519,
                public_key_bytes=pub_bytes,
                private_key_bytes=priv_bytes,
            )
        else:
            pqc = PQCSigner(algorithm=PQCAlgorithm.ML_DSA_87)
            kp = pqc.generate_keypair(seed=seed)
            keypair = WalletKeypair(
                algorithm=AddressType.ML_DSA_87,
                public_key_bytes=kp.public_key,
                private_key_bytes=kp.private_key,
            )

        wallet = cls(node_id=node_id, keypair=keypair)
        logger.info("Generated %s wallet for node %s: %s", algorithm.value, node_id, wallet.address)
        return wallet

    def sign_payload(self, payload: bytes) -> str:
        """Signs a byte payload and returns a hex-encoded signature."""
        if self.keypair.algorithm == AddressType.ED25519:
            sk = ed25519.Ed25519PrivateKey.from_private_bytes(self.keypair.private_key_bytes)
            sig_bytes = sk.sign(payload)
            return sig_bytes.hex()

        pqc = PQCSigner(algorithm=PQCAlgorithm.ML_DSA_87)
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
            if algorithm == AddressType.ED25519:
                if len(pub_bytes) != 32 or len(sig_bytes) != 64:
                    return False
                pk = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
                pk.verify(sig_bytes, payload)
                return True

            pqc = PQCSigner(algorithm=PQCAlgorithm.ML_DSA_87)
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
        """Exports the wallet to a JSON file (with AES-256-GCM encryption if password supplied)."""
        abs_path = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        data: dict[str, Any] = {
            "node_id": self.node_id,
            "address": self.address,
            "algorithm": self.keypair.algorithm.value,
            "public_key": self.keypair.public_key_hex,
        }

        if password:
            salt = secrets.token_bytes(16)
            nonce = secrets.token_bytes(12)
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=200_000,
            )
            aes_key = kdf.derive(password.encode("utf-8"))
            ct = AESGCM(aes_key).encrypt(
                nonce,
                self.keypair.private_key_bytes,
                self.address.encode("utf-8"),
            )
            data["encrypted"] = True
            data["kdf"] = "pbkdf2-sha256-aes256gcm"
            data["salt_b64"] = base64.b64encode(salt).decode("ascii")
            data["nonce_b64"] = base64.b64encode(nonce).decode("ascii")
            data["private_key_enc"] = base64.b64encode(ct).decode("ascii")
        else:
            data["encrypted"] = False
            data["private_key_enc"] = base64.b64encode(self.keypair.private_key_bytes).decode("ascii")

        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(abs_path, 0o600)
        except OSError:
            pass
        logger.info("Saved wallet %s to %s", self.address, abs_path)

    @classmethod
    def load_from_file(cls, filepath: str, password: str | None = None) -> NodeWallet:
        """Loads a wallet from a JSON file, decrypting with AES-256-GCM if password-protected."""
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("encrypted"):
            if not password:
                raise ValueError("Wallet file is password-encrypted; a password is required to unlock it.")
            salt = base64.b64decode(data["salt_b64"].encode("ascii"))
            nonce = base64.b64decode(data["nonce_b64"].encode("ascii"))
            ct = base64.b64decode(data["private_key_enc"].encode("ascii"))
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=200_000,
            )
            aes_key = kdf.derive(password.encode("utf-8"))
            try:
                priv_bytes = AESGCM(aes_key).decrypt(nonce, ct, data["address"].encode("utf-8"))
            except Exception as err:
                raise ValueError("Invalid wallet password or corrupted keystore.") from err
        else:
            priv_bytes = base64.b64decode(data["private_key_enc"].encode("ascii"))

        pub_bytes = bytes.fromhex(data["public_key"])
        algo = AddressType(data["algorithm"])
        keypair = WalletKeypair(
            algorithm=algo,
            public_key_bytes=pub_bytes,
            private_key_bytes=priv_bytes,
        )
        return cls(node_id=data["node_id"], keypair=keypair)
