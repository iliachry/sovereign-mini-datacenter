"""DePIN SLA enforcement, smart contract verification, and Byzantine fault tolerant consensus.

Implements the 6-sublayer blockchain architecture:
1. DePIN sublayer: Node registration, stake-weighted selection & reputation.
2. Consensus sublayer: Hybrid PoS/dBFT with floor(2N/3) + 1 validator signatures.
3. Protocol sublayer: Smart contract SLA verification (reject SINR < -15 dB, alert SINR < -10 dB).
4. Execution sublayer: Gas pricing limits and compute verification.
5. dApp sublayer: Cryptographic authorization & spatial interface access.
6. Transaction sublayer: keyed HMAC-SHA256 attestation on sensor telemetry (simulation
   stand-in for ECDSA / PQC signatures: symmetric per-sensor / per-validator keys).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ValidatorNode:
    """Represents a validator node in the decentralized DePIN network."""

    node_id: str
    wallet_address: str
    stake_amount: float
    uptime_pct: float
    reputation_score: float = 1.0
    is_active: bool = True
    signing_key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)

    def sign(self, message: bytes) -> str:
        """HMAC-SHA256 signature of ``message`` under this validator's key (hex)."""
        return hmac.new(self.signing_key, message, hashlib.sha256).hexdigest()

    @property
    def consensus_weight(self) -> float:
        """Calculates stake-weighted consensus voting power."""
        return self.stake_amount * (self.uptime_pct / 100.0) * self.reputation_score


@dataclass
class SLAVerificationResult:
    """Outcome of smart contract business logic execution and multi-sig validation."""

    valid: bool
    position_approved: bool
    rejection_reason: str | None
    sinr_check_passed: bool
    min_sinr_db: float
    optimization_event_triggered: bool
    validator_signatures_count: int
    required_threshold: int
    block_hash: str
    finality_time_sec: float
    gas_consumed_gwei: float
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class DePINSLAValidator:
    """Enforces smart-contract SLA constraints and Byzantine multi-sig consensus."""

    # Programmatic SLA Thresholds (from IEEE IoT Magazine spec)
    MIN_SINR_THRESHOLD_DB = -15.0  # Minimum SINR for reliable 5G NR QPSK modulation
    OPTIMIZATION_ALERT_THRESHOLD_DB = -10.0  # Emits trigger event when SINR degrades
    SIMULATED_FINALITY_SEC = 3.5  # Modelled constant (no real chain); reported as finality_time_sec
    MAX_FINALIZED_BLOCKS_RETAINED = 1000  # In-memory history cap for long-running processes

    def __init__(self, num_validators: int = 7) -> None:
        self.validators: list[ValidatorNode] = [
            ValidatorNode(
                node_id=f"depin-val-{i:02d}",
                wallet_address=f"sov_val_{i:02d}_{hashlib.sha256(str(i).encode()).hexdigest()[:8]}",
                stake_amount=1000.0 + (i * 250.0),
                uptime_pct=99.2 + (i * 0.1),
                reputation_score=1.0,
            )
            for i in range(1, num_validators + 1)
        ]
        self.finalized_blocks: list[dict[str, Any]] = []
        self.total_finalized_blocks: int = 0
        self._sensor_keys: dict[str, bytes] = {}

    @property
    def consensus_threshold(self) -> int:
        """Computes the Byzantine Fault Tolerant quorum: floor(2N / 3) + 1.

        With N = 3f + 1 validators this is 2f + 1 (e.g. N=7 tolerates f=2 -> quorum 5)
        and it never exceeds N.
        """
        n = len(self.validators)
        return (2 * n) // 3 + 1

    def register_sensor(self, sensor_id: str, key: bytes | None = None) -> bytes:
        """Registers (or rotates) a sensor's attestation key and returns it."""
        sensor_key = key if key is not None else secrets.token_bytes(32)
        if len(sensor_key) < 16:
            raise ValueError("sensor attestation key must be at least 16 bytes")
        self._sensor_keys[sensor_id] = sensor_key
        return sensor_key

    @staticmethod
    def sign_sensor_payload(sensor_key: bytes, sensor_id: str, payload_bytes: bytes) -> str:
        """Sensor-side helper: HMAC-SHA256 over ``sensor_id || 0x00 || payload`` (hex)."""
        return hmac.new(sensor_key, sensor_id.encode() + b"\x00" + payload_bytes, hashlib.sha256).hexdigest()

    def verify_sensor_signature(self, sensor_id: str, payload_bytes: bytes, signature_hex: str) -> bool:
        """Verifies the keyed attestation signature on IoT sensor data.

        The full HMAC-SHA256 tag is compared in constant time against the key registered
        for ``sensor_id``. Unknown sensors and malformed signatures are rejected.
        """
        sensor_key = self._sensor_keys.get(sensor_id)
        if sensor_key is None or not signature_hex:
            return False
        expected = self.sign_sensor_payload(sensor_key, sensor_id, payload_bytes)
        return hmac.compare_digest(expected, signature_hex.strip().lower())

    def evaluate_uav_position_sla(
        self,
        uav_pos: tuple[float, float, float],
        per_receiver_sinr: dict[str, float],
        sensor_signatures_valid: bool = True,
    ) -> SLAVerificationResult:
        """Evaluates UAV position update against DePIN smart contracts and consensus."""
        if not per_receiver_sinr:
            return SLAVerificationResult(
                valid=False,
                position_approved=False,
                rejection_reason="No receiver SINR measurements provided",
                sinr_check_passed=False,
                min_sinr_db=-99.0,
                optimization_event_triggered=True,
                validator_signatures_count=0,
                required_threshold=self.consensus_threshold,
                block_hash="",
                finality_time_sec=0.0,
                gas_consumed_gwei=0.0,
            )

        min_sinr = min(per_receiver_sinr.values())

        # 1. Cryptographic Sensor Attestation check
        if not sensor_signatures_valid:
            return SLAVerificationResult(
                valid=False,
                position_approved=False,
                rejection_reason="Sensor attestation signature verification failed (Sybil / Injection risk)",
                sinr_check_passed=False,
                min_sinr_db=min_sinr,
                optimization_event_triggered=True,
                validator_signatures_count=0,
                required_threshold=self.consensus_threshold,
                block_hash="",
                finality_time_sec=0.0,
                gas_consumed_gwei=0.0,
            )

        # 2. Smart Contract Business Logic: Reject if any receiver SINR < -15 dB
        sinr_passed = min_sinr >= self.MIN_SINR_THRESHOLD_DB
        if not sinr_passed:
            return SLAVerificationResult(
                valid=False,
                position_approved=False,
                rejection_reason=f"Position rejected by Smart Contract: Minimum SINR {min_sinr:.2f} dB < -15.0 dB threshold",
                sinr_check_passed=False,
                min_sinr_db=min_sinr,
                optimization_event_triggered=True,
                validator_signatures_count=0,
                required_threshold=self.consensus_threshold,
                block_hash="",
                finality_time_sec=0.0,
                gas_consumed_gwei=21000.0,
            )

        # 3. Check Optimization Alert Trigger (SINR < -10 dB)
        opt_triggered = min_sinr < self.OPTIMIZATION_ALERT_THRESHOLD_DB

        # 4. Multi-Signature Byzantine Consensus Voting
        req_threshold = self.consensus_threshold
        approving_validators = [v for v in self.validators if v.is_active]
        sigs_count = len(approving_validators)

        if sigs_count < req_threshold:
            return SLAVerificationResult(
                valid=False,
                position_approved=False,
                rejection_reason=f"Insufficient validator multi-sig agreement: {sigs_count}/{req_threshold}",
                sinr_check_passed=True,
                min_sinr_db=min_sinr,
                optimization_event_triggered=opt_triggered,
                validator_signatures_count=sigs_count,
                required_threshold=req_threshold,
                block_hash="",
                finality_time_sec=0.0,
                gas_consumed_gwei=21000.0,
            )

        # 5. Commit to Blockchain with Asynchronous Finality (3-6s simulated)
        block_content = f"uav_pos:{uav_pos}:{per_receiver_sinr}:{time.time()}"
        block_hash = hashlib.sha256(block_content.encode()).hexdigest()
        finality_time = self.SIMULATED_FINALITY_SEC

        signers = approving_validators[:req_threshold]
        record = {
            "block_hash": block_hash,
            "uav_pos": uav_pos,
            "min_sinr_db": min_sinr,
            "validator_signatures": [v.node_id for v in signers],
            "validator_signature_digests": {v.node_id: v.sign(block_hash.encode()) for v in signers},
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.finalized_blocks.append(record)
        self.total_finalized_blocks += 1
        if len(self.finalized_blocks) > self.MAX_FINALIZED_BLOCKS_RETAINED:
            del self.finalized_blocks[: -self.MAX_FINALIZED_BLOCKS_RETAINED]

        return SLAVerificationResult(
            valid=True,
            position_approved=True,
            rejection_reason=None,
            sinr_check_passed=True,
            min_sinr_db=min_sinr,
            optimization_event_triggered=opt_triggered,
            validator_signatures_count=sigs_count,
            required_threshold=req_threshold,
            block_hash=block_hash,
            finality_time_sec=finality_time,
            gas_consumed_gwei=54200.0,
        )
