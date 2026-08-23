"""Unit tests for Post-Quantum Cryptography (PQC) Security Engine."""

from __future__ import annotations

import base64

import pytest

from sovereign_dc.security.pqc import (
    PQCKEM,
    PQCAlgorithm,
    PQCKeyPair,
    PQCSigner,
)


class TestPQCKeyPair:
    """Test PQCKeyPair serialization and deserialization."""

    def test_keypair_to_dict_and_from_dict(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        kp = signer.generate_keypair()

        d = kp.to_dict()
        assert d["algorithm"] == "ML-DSA-65"
        assert "key_id" in d
        assert "public_key_b64" in d

        recovered = PQCKeyPair.from_public_dict(d)
        assert recovered.algorithm == PQCAlgorithm.ML_DSA_65
        assert recovered.public_key == kp.public_key
        assert recovered.key_id == kp.key_id
        assert recovered.private_key == b""


class TestPQCSigner:
    """Test ML-DSA Post-Quantum Digital Signature algorithms."""

    @pytest.mark.parametrize("alg", [PQCAlgorithm.ML_DSA_65, PQCAlgorithm.ML_DSA_87])
    def test_sign_and_verify_success(self, alg: PQCAlgorithm):
        signer = PQCSigner(alg)
        keypair = signer.generate_keypair()

        msg = b"CRITICAL_LOAD_SHED_L3: SHUTDOWN_GPU_NODE_02"
        sig = signer.sign(msg, keypair.private_key)

        assert len(sig) > 0
        assert signer.verify(msg, sig, keypair.public_key) is True

    def test_verify_rejects_tampered_message(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        keypair = signer.generate_keypair()

        msg = b"AUTHENTIC_STATE_SYNC"
        sig = signer.sign(msg, keypair.private_key)

        tampered_msg = b"TAMPERED_STATE_SYNC"
        assert signer.verify(tampered_msg, sig, keypair.public_key) is False

    def test_verify_rejects_tampered_signature(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        keypair = signer.generate_keypair()

        msg = b"AUTHENTIC_STATE_SYNC"
        sig = bytearray(signer.sign(msg, keypair.private_key))
        sig[10] ^= 0xFF  # Flip bits

        assert signer.verify(msg, bytes(sig), keypair.public_key) is False

    def test_verify_rejects_wrong_public_key(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        kp1 = signer.generate_keypair()
        kp2 = signer.generate_keypair()

        msg = b"AUTHENTIC_TELEMETRY"
        sig = signer.sign(msg, kp1.private_key)

        assert signer.verify(msg, sig, kp2.public_key) is False

    def test_verify_invalid_length(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        assert signer.verify(b"msg", b"short_sig", b"short_pk") is False


class TestPQCBPSecBundleSigning:
    """Test RFC 9172 BPSec DTN Bundle Signing and Verification."""

    def test_sign_and_verify_dtn_bundle(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        keypair = signer.generate_keypair()

        raw_bundle = {
            "v": 7,
            "id": "bundle-uuid-1234",
            "src": "dtn://smdc-dgx-01.sovereign.space",
            "dst": "dtn://ground-station-alpha.earth/telemetry",
            "ts": 1724400000.0,
            "payload_b64": base64.b64encode(b'{"solar_w": 1420.5, "soc": 92.1}').decode("ascii"),
        }

        signed_bundle = signer.sign_bundle(raw_bundle, keypair)
        assert "bpsec" in signed_bundle
        assert signed_bundle["bpsec"]["alg"] == "ML-DSA-65"
        assert signed_bundle["bpsec"]["key_id"] == keypair.key_id

        # Verify authentic bundle
        assert signer.verify_bundle(signed_bundle) is True

    def test_verify_dtn_bundle_tampered_payload(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        keypair = signer.generate_keypair()

        raw_bundle = {
            "v": 7,
            "id": "bundle-uuid-1234",
            "src": "dtn://smdc-dgx-01.sovereign.space",
            "dst": "dtn://ground-station-alpha.earth/telemetry",
            "ts": 1724400000.0,
            "payload_b64": base64.b64encode(b'{"solar_w": 1420.5}').decode("ascii"),
        }

        signed_bundle = signer.sign_bundle(raw_bundle, keypair)

        # Alter payload
        signed_bundle["payload_b64"] = base64.b64encode(b'{"solar_w": 0.0}').decode("ascii")
        assert signer.verify_bundle(signed_bundle) is False

    def test_verify_dtn_bundle_missing_bpsec(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        assert signer.verify_bundle({"id": "no_bpsec"}) is False

    def test_verify_dtn_bundle_invalid_base64(self):
        signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
        assert (
            signer.verify_bundle(
                {
                    "id": "bad",
                    "bpsec": {"sig_b64": "invalid!!", "pubkey_b64": "invalid!!", "alg": "ML-DSA-65"},
                }
            )
            is False
        )


class TestPQCKEM:
    """Test ML-KEM Key Encapsulation Mechanism."""

    @pytest.mark.parametrize("alg", [PQCAlgorithm.ML_KEM_768, PQCAlgorithm.ML_KEM_1024])
    def test_encapsulate_and_decapsulate(self, alg: PQCAlgorithm):
        kem = PQCKEM(alg)
        recipient_kp = kem.generate_keypair()

        # Sender encapsulates symmetric key under recipient's public key
        ciphertext, shared_secret_sender = kem.encapsulate(recipient_kp.public_key)

        assert len(ciphertext) > 0
        assert len(shared_secret_sender) == 32  # 256-bit symmetric key

        # Recipient decapsulates
        shared_secret_recipient = kem.decapsulate(ciphertext, recipient_kp.private_key)
        assert len(shared_secret_recipient) == 32
        assert isinstance(shared_secret_recipient, bytes)
