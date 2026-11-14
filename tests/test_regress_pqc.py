"""Regression tests for the PQC engine: algorithm mislabelling, bundle coverage, node identity."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.security import pqc
from sovereign_dc.security.pqc import PQCKEM, PQCAlgorithm, PQCSigner, get_node_pqc_keypair


@pytest.mark.parametrize("algorithm", [PQCAlgorithm.ML_KEM_768, PQCAlgorithm.ML_KEM_1024, PQCAlgorithm.SPHINCS_PLUS])
def test_signer_refuses_algorithms_it_does_not_implement(algorithm: PQCAlgorithm) -> None:
    # Previously these silently signed with ML-DSA-87 while labelling the output as `algorithm`.
    with pytest.raises(ValueError, match="does not implement"):
        PQCSigner(algorithm)


@pytest.mark.parametrize("algorithm", [PQCAlgorithm.ML_DSA_65, PQCAlgorithm.ML_DSA_87, PQCAlgorithm.SPHINCS_PLUS])
def test_kem_refuses_algorithms_it_does_not_implement(algorithm: PQCAlgorithm) -> None:
    with pytest.raises(ValueError, match="does not implement"):
        PQCKEM(algorithm)


def _bundle() -> dict[str, Any]:
    return {
        "v": 7,
        "id": "bundle-1",
        "src": "dtn://a.space",
        "dst": "dtn://b.earth/telemetry",
        "ts": 1724400000.0,
        "priority": 1,
        "lifetime": 3600,
        "payload_b64": base64.b64encode(b"hello").decode("ascii"),
    }


def test_bundle_signature_covers_every_field() -> None:
    signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
    keypair = signer.generate_keypair()
    signed = signer.sign_bundle(_bundle(), keypair)
    assert signed["bpsec"]["cov"] == 2
    assert signer.verify_bundle(signed) is True

    for field, value in (("priority", 3), ("lifetime", 10**9), ("v", 6), ("dst", "dtn://evil/")):
        tampered = dict(signed, **{field: value})
        assert signer.verify_bundle(tampered) is False, field

    injected = dict(signed, route_override="dtn://evil/")
    assert signer.verify_bundle(injected) is False

    # Dropping the coverage marker must not downgrade a full-coverage signature.
    downgraded = dict(signed, bpsec={k: v for k, v in signed["bpsec"].items() if k != "cov"})
    assert signer.verify_bundle(downgraded) is False


def test_bundles_signed_with_the_legacy_coverage_still_verify() -> None:
    signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
    keypair = signer.generate_keypair()
    bundle = _bundle()
    legacy_input = json.dumps(
        {k: bundle.get(k) for k in ("id", "src", "dst", "ts", "payload_b64")}, sort_keys=True
    ).encode("utf-8")
    legacy_signed = dict(
        bundle,
        bpsec={
            "alg": keypair.algorithm.value,
            "key_id": keypair.key_id,
            "sig_b64": base64.b64encode(signer.sign(legacy_input, keypair.private_key)).decode("ascii"),
            "pubkey_b64": base64.b64encode(keypair.public_key).decode("ascii"),
        },
    )
    assert signer.verify_bundle(legacy_signed) is True
    assert signer.verify_bundle(dict(legacy_signed, payload_b64="AAAA")) is False


def test_sign_bundle_rejects_keypair_of_another_algorithm() -> None:
    kp87 = PQCSigner(PQCAlgorithm.ML_DSA_87).generate_keypair()
    with pytest.raises(ValueError):
        PQCSigner(PQCAlgorithm.ML_DSA_65).sign_bundle(_bundle(), kp87)


def test_node_keypair_is_persisted_owner_only_and_stable(tmp_path: Path) -> None:
    state_dir = str(tmp_path / "state")
    first = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87, state_dir=state_dir)

    key_file = Path(pqc.node_pqc_key_path(PQCAlgorithm.ML_DSA_87, state_dir))
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert [p.name for p in key_file.parent.iterdir()] == [key_file.name]  # no temp files left behind

    # A "new process" (empty cache) must load the same identity instead of minting a new one.
    pqc._CACHED_NODE_KEYPAIRS.clear()
    second = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87, state_dir=state_dir)
    assert second.key_id == first.key_id
    assert second.public_key == first.public_key
    assert second.private_key == first.private_key

    signer = PQCSigner(PQCAlgorithm.ML_DSA_87)
    assert signer.verify(b"msg", signer.sign(b"msg", second.private_key), first.public_key)


def test_node_keypair_file_that_is_tampered_or_exposed_is_not_trusted(tmp_path: Path) -> None:
    state_dir = str(tmp_path / "state")
    original = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_65, state_dir=state_dir)
    key_file = Path(pqc.node_pqc_key_path(PQCAlgorithm.ML_DSA_65, state_dir))

    # World-readable key file: refuse to use it as the node identity.
    os.chmod(key_file, 0o644)
    pqc._CACHED_NODE_KEYPAIRS.clear()
    exposed = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_65, state_dir=state_dir)
    assert exposed.public_key != original.public_key
    os.chmod(key_file, 0o600)

    # Public key swapped for somebody else's: inconsistent file is rejected, not half-loaded.
    data = json.loads(key_file.read_text(encoding="utf-8"))
    other = PQCSigner(PQCAlgorithm.ML_DSA_65).generate_keypair()
    data["public_key_b64"] = base64.b64encode(other.public_key).decode("ascii")
    key_file.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(key_file, 0o600)
    pqc._CACHED_NODE_KEYPAIRS.clear()
    tampered = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_65, state_dir=state_dir)
    assert tampered.public_key not in (other.public_key, original.public_key)
    assert PQCSigner(PQCAlgorithm.ML_DSA_65).public_key_from_private(tampered.private_key) == tampered.public_key
