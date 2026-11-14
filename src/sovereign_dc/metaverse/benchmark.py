"""Metaverse Benchmarking & Parametric Analysis Engine.

Reproduces and benchmarks empirical performance metrics from IEEE IoT Magazine:
1. SA-PPO (Scene-Aware PPO) vs. MD-PPO (Model-Driven PPO baseline with Rician fading).
2. Per-receiver SINR & Shannon capacity comparison (the paper reports +79.6% on the
   disadvantaged Rx1; this lightweight pure-Python reproduction reports whatever it measures).
3. 6-layer decision-to-action latency profile. Decision and ray-tracing latencies are
   wall-clock measurements of this implementation; URLLC latency and blockchain finality
   come from the analytical slicing / DePIN models (see ``BenchmarkReport.latency_sources``).
   The paper's published figures are kept separately in ``PAPER_REFERENCE_LATENCIES``.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from sovereign_dc.metaverse.agent import ACTIONS_3D, ModelDrivenPPO, SceneAwarePPO
from sovereign_dc.metaverse.depin_sla import DePINSLAValidator
from sovereign_dc.metaverse.ray_tracer import SionnaRayTracer
from sovereign_dc.metaverse.slicing import NetworkSlicingManager

# Latency figures published in the reference paper. These are NOT measured by this
# code; they are exposed only for side-by-side comparison with the measured values.
PAPER_REFERENCE_LATENCIES: dict[str, float] = {
    "decision_latency_ms": 4.8,
    "urllc_latency_ms": 0.8,
    "ray_tracing_latency_ms": 12.1,
    "blockchain_finality_sec": 3.5,
}


@dataclass
class ReceiverBenchmarkMetric:
    """Statistical summary for a single ground user equipment receiver."""

    rx_id: str
    is_disadvantaged: bool
    sa_ppo_mean_sinr_db: float
    sa_ppo_std_sinr_db: float
    md_ppo_mean_sinr_db: float
    md_ppo_std_sinr_db: float
    sinr_improvement_pct: float
    sa_ppo_mean_cap_bps_hz: float
    sa_ppo_std_cap_bps_hz: float
    md_ppo_mean_cap_bps_hz: float
    md_ppo_std_cap_bps_hz: float
    capacity_gain_pct: float


@dataclass
class BenchmarkReport:
    """Comprehensive benchmark comparison report between SA-PPO and MD-PPO."""

    episodes: int
    steps_per_episode: int
    total_timesteps: int
    receiver_metrics: dict[str, ReceiverBenchmarkMetric]
    avg_decision_latency_ms: float  # measured: state encoding + policy inference (wall clock)
    avg_urllc_latency_ms: float  # modelled: analytical URLLC slice latency for a UAV command
    avg_ray_tracing_latency_ms: float  # measured: one evaluate_all_receivers() call (wall clock)
    avg_blockchain_finality_sec: float  # modelled constant from DePINSLAValidator (no real chain)
    sa_ppo_total_sum_capacity: float
    md_ppo_total_sum_capacity: float
    overall_capacity_gain_pct: float
    latency_sources: dict[str, str] = field(default_factory=dict)
    paper_reference_latencies: dict[str, float] = field(default_factory=dict)


class MetaverseBenchmark:
    """Executes parametric analysis and comparative reinforcement learning sweeps."""

    def __init__(self, seed: int = 111) -> None:
        self.seed = seed

    def run_comparison(self, episodes: int = 20, steps_per_episode: int = 25) -> BenchmarkReport:
        """Executes head-to-head training comparison between SA-PPO and MD-PPO."""
        if episodes < 1 or steps_per_episode < 1:
            raise ValueError(f"episodes and steps_per_episode must both be >= 1 (got {episodes}, {steps_per_episode})")

        ray_tracer = SionnaRayTracer()
        slicing = NetworkSlicingManager()
        ray_tracing_samples_ms: list[float] = []
        decision_samples_ms: list[float] = []
        urllc_samples_ms: list[float] = []
        sa_agent = SceneAwarePPO(seed=self.seed)
        md_agent = ModelDrivenPPO(seed=self.seed + 100)

        sa_sinrs: dict[str, list[float]] = {"Rx1": [], "Rx2": [], "Rx3": []}
        sa_caps: dict[str, list[float]] = {"Rx1": [], "Rx2": [], "Rx3": []}
        md_sinrs: dict[str, list[float]] = {"Rx1": [], "Rx2": [], "Rx3": []}
        md_caps: dict[str, list[float]] = {"Rx1": [], "Rx2": [], "Rx3": []}

        # 1. Run SA-PPO Episodes (Scene-Aware)
        for _ in range(episodes):
            uav_pos = (0.0, 0.0, 35.0)
            for _ in range(steps_per_episode):
                t_rt = time.perf_counter()
                prop = ray_tracer.evaluate_all_receivers(uav_pos)
                t_decision = time.perf_counter()
                state = sa_agent.encode_state(uav_pos, prop)
                act_idx = sa_agent.select_action(state)
                t_done = time.perf_counter()
                ray_tracing_samples_ms.append((t_decision - t_rt) * 1000.0)
                decision_samples_ms.append((t_done - t_decision) * 1000.0)
                vec = ACTIONS_3D[act_idx]
                urllc_samples_ms.append(slicing.transmit_uav_control_command(vec).latency_ms)
                uav_pos = (
                    max(-70.0, min(70.0, uav_pos[0] + vec[0])),
                    max(-70.0, min(70.0, uav_pos[1] + vec[1])),
                    max(20.0, min(60.0, uav_pos[2] + vec[2])),
                )
                new_prop = ray_tracer.evaluate_all_receivers(uav_pos)
                reward = sa_agent.compute_reward(new_prop)
                next_state = sa_agent.encode_state(uav_pos, new_prop)
                sa_agent.record_transition(state, act_idx, reward, next_state, done=False)

                for rx_id, r in new_prop.items():
                    sa_sinrs[rx_id].append(r.sinr_db)
                    sa_caps[rx_id].append(r.capacity_bps_hz)
            sa_agent.train_step()

        # 2. Run MD-PPO Episodes (Model-Driven baseline)
        for _ in range(episodes):
            uav_pos = (0.0, 0.0, 35.0)
            for _ in range(steps_per_episode):
                prop = ray_tracer.evaluate_all_receivers(uav_pos)
                state = md_agent.encode_state(uav_pos, prop)
                act_idx = md_agent.select_action(state)
                vec = ACTIONS_3D[act_idx]
                uav_pos = (
                    max(-70.0, min(70.0, uav_pos[0] + vec[0])),
                    max(-70.0, min(70.0, uav_pos[1] + vec[1])),
                    max(20.0, min(60.0, uav_pos[2] + vec[2])),
                )
                new_prop = ray_tracer.evaluate_all_receivers(uav_pos)
                # MD-PPO receives statistical Rician estimate for policy updates
                reward = sum(
                    md_agent.evaluate_rician_sinr(uav_pos, ray_tracer.receivers[rx_id].position)
                    for rx_id in ["Rx1", "Rx2", "Rx3"]
                )
                next_state = md_agent.encode_state(uav_pos, new_prop)
                md_agent.record_transition(state, act_idx, reward, next_state, done=False)

                for rx_id, r in new_prop.items():
                    md_sinrs[rx_id].append(r.sinr_db)
                    md_caps[rx_id].append(r.capacity_bps_hz)
            md_agent.train_step()

        # Aggregate metrics over tail (post-convergence)
        tail_len = max(len(sa_sinrs["Rx1"]) // 3, 10)
        rx_metrics: dict[str, ReceiverBenchmarkMetric] = {}

        for rx_id in ["Rx1", "Rx2", "Rx3"]:
            sa_s = sa_sinrs[rx_id][-tail_len:]
            sa_c = sa_caps[rx_id][-tail_len:]
            md_s = md_sinrs[rx_id][-tail_len:]
            md_c = md_caps[rx_id][-tail_len:]

            sa_mean_s = statistics.mean(sa_s)
            sa_std_s = statistics.stdev(sa_s) if len(sa_s) > 1 else 0.1
            md_mean_s = statistics.mean(md_s)
            md_std_s = statistics.stdev(md_s) if len(md_s) > 1 else 0.1

            sa_mean_c = statistics.mean(sa_c)
            sa_std_c = statistics.stdev(sa_c) if len(sa_c) > 1 else 0.01
            md_mean_c = statistics.mean(md_c)
            md_std_c = statistics.stdev(md_c) if len(md_c) > 1 else 0.01

            # Gains
            sinr_gain_pct = ((sa_mean_s - md_mean_s) / max(abs(md_mean_s), 1e-6)) * 100.0
            cap_gain_pct = ((sa_mean_c - md_mean_c) / max(md_mean_c, 1e-6)) * 100.0

            rx_metrics[rx_id] = ReceiverBenchmarkMetric(
                rx_id=rx_id,
                is_disadvantaged=(rx_id == "Rx1"),
                sa_ppo_mean_sinr_db=round(sa_mean_s, 2),
                sa_ppo_std_sinr_db=round(sa_std_s, 2),
                md_ppo_mean_sinr_db=round(md_mean_s, 2),
                md_ppo_std_sinr_db=round(md_std_s, 2),
                sinr_improvement_pct=round(sinr_gain_pct, 1),
                sa_ppo_mean_cap_bps_hz=round(sa_mean_c, 4),
                sa_ppo_std_cap_bps_hz=round(sa_std_c, 4),
                md_ppo_mean_cap_bps_hz=round(md_mean_c, 4),
                md_ppo_std_cap_bps_hz=round(md_std_c, 4),
                capacity_gain_pct=round(cap_gain_pct, 1),
            )

        sa_total_cap = sum(m.sa_ppo_mean_cap_bps_hz for m in rx_metrics.values())
        md_total_cap = sum(m.md_ppo_mean_cap_bps_hz for m in rx_metrics.values())
        overall_gain = ((sa_total_cap - md_total_cap) / max(md_total_cap, 1e-6)) * 100.0

        return BenchmarkReport(
            episodes=episodes,
            steps_per_episode=steps_per_episode,
            total_timesteps=episodes * steps_per_episode,
            receiver_metrics=rx_metrics,
            avg_decision_latency_ms=round(statistics.mean(decision_samples_ms), 4),
            avg_urllc_latency_ms=round(statistics.mean(urllc_samples_ms), 4),
            avg_ray_tracing_latency_ms=round(statistics.mean(ray_tracing_samples_ms), 4),
            avg_blockchain_finality_sec=DePINSLAValidator.SIMULATED_FINALITY_SEC,
            sa_ppo_total_sum_capacity=round(sa_total_cap, 4),
            md_ppo_total_sum_capacity=round(md_total_cap, 4),
            overall_capacity_gain_pct=round(overall_gain, 1),
            latency_sources={
                "avg_decision_latency_ms": "measured (wall clock, this pure-Python implementation)",
                "avg_ray_tracing_latency_ms": "measured (wall clock, this pure-Python implementation)",
                "avg_urllc_latency_ms": "modelled (analytical slice latency, not a radio measurement)",
                "avg_blockchain_finality_sec": "modelled constant (no real blockchain)",
            },
            paper_reference_latencies=dict(PAPER_REFERENCE_LATENCIES),
        )
