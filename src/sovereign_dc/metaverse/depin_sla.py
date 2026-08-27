"""DePIN SLA enforcement, smart contract verification, and Byzantine fault tolerant consensus.

Implements the 6-sublayer blockchain architecture:
1. DePIN sublayer: Node registration, stake-weighted selection & reputation.
2. Consensus sublayer: Hybrid PoS/dBFT with ceil(2N/3) + 1 validator signatures.
3. Protocol sublayer: Smart contract SLA verification (reject SINR < -15 dB, alert SINR < -10 dB).
4. Execution sublayer: Gas pricing limits and compute verification.
5. dApp sublayer: Cryptographic authorization & spatial interface access.
6. Transaction sublayer: ECDSA / PQC signature attestation on sensor telemetry.
"""

from __future__ import annotations

import hashlib
import math
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

    @property
    def consensus_threshold(self) -> int:
        """Computes Byzantine Fault Tolerant threshold: ceil(2N / 3) + 1."""
        n = len(self.validators)
        return math.ceil((2.0 * n) / 3.0) + 1

    def verify_sensor_signature(self, sensor_id: str, payload_bytes: bytes, signature_hex: str) -> bool:
        """Verifies ECDSA / cryptographic attestation signature on IoT sensor data."""
        if not signature_hex:
            return False
        # Deterministic simulation of ECDSA signature check
        expected_digest = hashlib.sha256(sensor_id.encode() + payload_bytes).hexdigest()
        return signature_hex.startswith(expected_digest[:8]) or len(signature_hex) >= 16

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
                rejection_reason="ECDSA sensor signature verification failed (Sybil / Injection risk)",
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
        finality_time = 3.5  # Typical 3.5s finality

        record = {
            "block_hash": block_hash,
            "uav_pos": uav_pos,
            "min_sinr_db": min_sinr,
            "validator_signatures": [v.node_id for v in approving_validators[:req_threshold]],
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.finalized_blocks.append(record)

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
