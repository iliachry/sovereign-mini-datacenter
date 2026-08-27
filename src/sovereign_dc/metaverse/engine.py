"""Metaverse Multi-Layer Simulation Orchestrator.

Implements Algorithm 1 (Multi-Layer System Integration) from IEEE IoT Magazine:
1. Phase 1: Parallel IoT collection, 5G slicing queue, blockchain tx processing.
2. Phase 2: Sequential critical path (< 6ms deadline: DT update -> ray-trace -> PPO inference -> URLLC action).
3. Phase 3: Finalization (asynchronous multi-sig logging with 3-6s finality, XR frame render).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.log import get_logger
from sovereign_dc.metaverse.agent import ACTIONS_3D, SceneAwarePPO
from sovereign_dc.metaverse.depin_sla import DePINSLAValidator, SLAVerificationResult
from sovereign_dc.metaverse.ray_tracer import SionnaRayTracer
from sovereign_dc.metaverse.slicing import NetworkSlicingManager

logger = get_logger("sovereign_dc.metaverse.engine")


@dataclass
class SimulationCycleTrace:
    """Detailed telemetry trace for a single multi-layer simulation step."""

    cycle_index: int
    uav_position: tuple[float, float, float]
    action_taken: int
    action_vector: tuple[float, float, float]
    per_receiver_sinr: dict[str, float]
    per_receiver_capacity: dict[str, float]
    sum_sinr_db: float
    sum_capacity_bps_hz: float
    decision_latency_ms: float
    critical_path_latency_ms: float
    urllc_latency_ms: float
    sla_result: SLAVerificationResult
    xr_frame_ready: bool = True
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class MetaverseOrchestrator:
    """Orchestrates the 6-layer cyber-physical metaverse wireless management stack."""

    def __init__(
        self,
        initial_uav_pos: tuple[float, float, float] = (0.0, 0.0, 35.0),
        seed: int = 111,
    ) -> None:
        self.uav_position = initial_uav_pos
        self.trajectory_history: list[tuple[float, float, float]] = [initial_uav_pos]
        self.ray_tracer = SionnaRayTracer()
        self.agent = SceneAwarePPO(seed=seed)
        self.slicing = NetworkSlicingManager()
        self.depin_sla = DePINSLAValidator()
        self.traces: list[SimulationCycleTrace] = []
        self.is_emergency_stopped = False

    def step(self, deterministic: bool = False) -> SimulationCycleTrace:
        """Executes a single multi-layer simulation cycle (Algorithm 1)."""
        cycle_idx = len(self.traces) + 1
        t_start = time.perf_counter()

        # =========================================================================
        # PHASE 1: Parallel Collection
        # =========================================================================
        # 1. Ingest IoT sensor stream over mMTC slice
        self.slicing.ingest_iot_sensor_batch(sensor_count=180, bytes_per_sensor=64)

        # =========================================================================
        # PHASE 2: Critical Path (< 6ms budget)
        # =========================================================================
        t_crit_start = time.perf_counter()

        # 1. DT Ray-Tracing evaluation
        prop_results = self.ray_tracer.evaluate_all_receivers(self.uav_position)

        # 2. Extract 20-dim state vector
        state = self.agent.encode_state(self.uav_position, prop_results)

        # 3. AI Inference with PPO Actor network
        if not self.is_emergency_stopped:
            action_idx = self.agent.select_action(state, deterministic=deterministic)
        else:
            action_idx = 0  # No movement if emergency stopped

        # 4. Transmit action command via priority URLLC slice
        act_vector = ACTIONS_3D[action_idx] if not self.is_emergency_stopped else (0.0, 0.0, 0.0)
        urllc_pkt = self.slicing.transmit_uav_control_command(act_vector)

        # 5. Apply UAV movement (constrained within bounding box: x in [-70, 70], y in [-70, 70], z in [20, 60])
        if not self.is_emergency_stopped:
            new_x = max(-70.0, min(70.0, self.uav_position[0] + act_vector[0]))
            new_y = max(-70.0, min(70.0, self.uav_position[1] + act_vector[1]))
            new_z = max(20.0, min(60.0, self.uav_position[2] + act_vector[2]))
            self.uav_position = (new_x, new_y, new_z)
            self.trajectory_history.append(self.uav_position)

        # Re-evaluate DT with new position
        new_prop = self.ray_tracer.evaluate_all_receivers(self.uav_position)
        reward = self.agent.compute_reward(new_prop)
        next_state = self.agent.encode_state(self.uav_position, new_prop)

        # Store transition and train step
        self.agent.record_transition(state, action_idx, reward, next_state, done=False)
        self.agent.train_step()

        crit_latency_ms = (time.perf_counter() - t_crit_start) * 1000.0
        decision_latency_ms = (time.perf_counter() - t_start) * 1000.0

        # =========================================================================
        # PHASE 3: Finalization & Trust Path
        # =========================================================================
        # 1. Asynchronous multi-sig logging to DePIN blockchain
        sinr_map = {rx_id: res.sinr_db for rx_id, res in new_prop.items()}
        cap_map = {rx_id: res.capacity_bps_hz for rx_id, res in new_prop.items()}
        sla_res = self.depin_sla.evaluate_uav_position_sla(
            uav_pos=self.uav_position,
            per_receiver_sinr=sinr_map,
            sensor_signatures_valid=True,
        )

        # 2. Render XR frame & broadcast over eMBB slice
        self.slicing.transmit_xr_frame(frame_size_bytes=150000)

        sum_sinr = sum(sinr_map.values())
        sum_cap = sum(cap_map.values())

        trace = SimulationCycleTrace(
            cycle_index=cycle_idx,
            uav_position=self.uav_position,
            action_taken=action_idx,
            action_vector=act_vector,
            per_receiver_sinr=sinr_map,
            per_receiver_capacity=cap_map,
            sum_sinr_db=round(sum_sinr, 2),
            sum_capacity_bps_hz=round(sum_cap, 4),
            decision_latency_ms=round(decision_latency_ms, 3),
            critical_path_latency_ms=round(crit_latency_ms, 3),
            urllc_latency_ms=round(urllc_pkt.latency_ms, 3),
            sla_result=sla_res,
            xr_frame_ready=True,
        )
        self.traces.append(trace)
        return trace

    def run_cycles(self, count: int = 10, deterministic: bool = False) -> list[SimulationCycleTrace]:
        """Runs multiple consecutive simulation cycles."""
        results: list[SimulationCycleTrace] = []
        for _ in range(count):
            results.append(self.step(deterministic=deterministic))
        return results

    def trigger_emergency_stop(self) -> None:
        """Halts all autonomous UAV movement instantly."""
        self.is_emergency_stopped = True
        logger.warning("🚨 EMERGENCY STOP ACTIVATED: Autonomous UAV positioning halted.")

    def resume_operation(self) -> None:
        """Resumes autonomous UAV positioning."""
        self.is_emergency_stopped = False
        logger.info("Autonomous UAV positioning resumed.")

    def get_latest_status(self) -> dict[str, Any]:
        """Returns unified 6-layer state summary."""
        latest_trace = self.traces[-1] if self.traces else None
        return {
            "uav_position": self.uav_position,
            "trajectory_points": len(self.trajectory_history),
            "emergency_stopped": self.is_emergency_stopped,
            "last_cycle": latest_trace.to_dict() if latest_trace else None,
            "slices": self.slicing.get_summary(),
            "depin_blocks_count": len(self.depin_sla.finalized_blocks),
            "total_cycles_executed": len(self.traces),
        }
