"""Regression tests for reproduced defects in the metaverse wireless-management simulation."""

from __future__ import annotations

import math

import pytest

from sovereign_dc.metaverse.agent import ModelDrivenPPO, SceneAwarePPO
from sovereign_dc.metaverse.benchmark import PAPER_REFERENCE_LATENCIES, MetaverseBenchmark
from sovereign_dc.metaverse.depin_sla import DePINSLAValidator
from sovereign_dc.metaverse.engine import MetaverseOrchestrator
from sovereign_dc.metaverse.ray_tracer import SionnaRayTracer
from sovereign_dc.metaverse.slicing import NetworkSlicingManager

# --- Path-loss units (agent.py) -----------------------------------------------------------------


def test_rician_baseline_uses_metre_ghz_fspl_like_the_ray_tracer() -> None:
    md = ModelDrivenPPO(seed=222)
    rt = SionnaRayTracer()
    samples = [md.evaluate_rician_sinr((0.0, 0.0, 100.0), (0.0, 0.0, 0.0)) for _ in range(400)]
    mean_sinr = sum(samples) / len(samples)
    # 100 m at 3.5 GHz: FSPL = 83.3 dB -> SINR = 23 - 83.3 + 94 = 33.7 dB (old code: ~-26 dB, 60 dB off)
    expected = 23.0 - rt.compute_free_space_path_loss(100.0) + 94.0
    assert expected == pytest.approx(33.68, abs=0.05)
    assert mean_sinr == pytest.approx(expected, abs=0.6)


# --- Advantage normalisation / PPO clip -----------------------------------------------------------


def _fill(agent: SceneAwarePPO, rewards: list[float]) -> None:
    for i, reward in enumerate(rewards):
        state = [0.05 * ((i + j) % 7) for j in range(20)]
        next_state = [0.05 * ((i + j + 1) % 7) for j in range(20)]
        agent.record_transition(state, i % 6, reward, next_state, done=False)


def test_advantages_are_standardised_not_saturated() -> None:
    agent = SceneAwarePPO(seed=3)
    _fill(agent, [100.0 + 5.0 * i for i in range(12)])  # dB-scale rewards: raw advantages >> 0.2
    raw = [t.advantage for t in agent.buffer]
    assert all(abs(a) > agent.clip_ratio for a in raw)  # the old +/-0.2 clip saturated 100 % of these

    normalised = agent.normalize_advantages(raw)
    assert sum(normalised) / len(normalised) == pytest.approx(0.0, abs=1e-9)
    assert len({round(a, 6) for a in normalised}) == len(normalised)  # ordering information preserved
    assert any(a > 0 for a in normalised) and any(a < 0 for a in normalised)


def test_policy_update_scales_with_relative_advantage() -> None:
    agent = SceneAwarePPO(seed=3)
    state = [0.1] * 20
    before = agent.policy_net.forward(state)
    # action 0 consistently better than action 1, both with huge raw rewards
    for _ in range(40):
        for _ in range(4):
            agent.record_transition(state, 0, 150.0, state, done=False)
            agent.record_transition(state, 1, 50.0, state, done=False)
        agent.train_step()
    after = agent.policy_net.forward(state)
    assert after[0] > before[0]
    assert after[1] < before[1]
    assert 0.0 <= agent.last_update_stats["ratio_clipped_fraction"] <= 1.0


def test_single_transition_update_does_not_blow_up() -> None:
    agent = SceneAwarePPO(seed=3)
    _fill(agent, [80.0])
    losses = agent.train_step()
    assert math.isfinite(losses["loss_policy"])
    assert agent.last_update_stats["max_abs_normalized_advantage"] == 0.0  # no scale information yet


# --- Receiver placement (ray_tracer.py) -----------------------------------------------------------


def test_receivers_are_outside_building_footprints_and_reachable() -> None:
    rt = SionnaRayTracer()
    for rx in rt.receivers.values():
        assert rt.is_inside_building(rx.position) is False, rx.rx_id
        los = total = 0
        for x in range(-70, 71, 10):
            for y in range(-70, 71, 10):
                for z in (20, 40, 60):
                    total += 1
                    los += not rt._check_los_obstruction((float(x), float(y), float(z)), rx.position)
        assert los > 0, f"{rx.rx_id} has no line of sight from any UAV position"
        assert los < total, f"{rx.rx_id} is never obstructed: buildings have no effect"

    # Rx1 stays the disadvantaged canyon user: blocked from the default UAV start position
    assert rt._check_los_obstruction((0.0, 0.0, 35.0), rt.receivers["Rx1"].position) is True


# --- Benchmark ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("episodes", "steps"), [(0, 5), (3, 0), (-1, 5)])
def test_benchmark_rejects_empty_runs(episodes: int, steps: int) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        MetaverseBenchmark().run_comparison(episodes=episodes, steps_per_episode=steps)


def test_benchmark_latencies_are_measured_not_paper_constants() -> None:
    report = MetaverseBenchmark(seed=111).run_comparison(episodes=2, steps_per_episode=4)
    assert report.avg_decision_latency_ms > 0.0
    assert report.avg_ray_tracing_latency_ms > 0.0
    # literal paper figures must not be passed off as measurements
    assert report.avg_decision_latency_ms != PAPER_REFERENCE_LATENCIES["decision_latency_ms"]
    assert report.avg_ray_tracing_latency_ms != PAPER_REFERENCE_LATENCIES["ray_tracing_latency_ms"]
    # URLLC latency comes from the slicing model for a real 128-byte command
    expected_urllc = NetworkSlicingManager().transmit_uav_control_command((5.0, 0.0, 0.0)).latency_ms
    assert report.avg_urllc_latency_ms == pytest.approx(expected_urllc)
    assert report.latency_sources["avg_ray_tracing_latency_ms"].startswith("measured")
    assert report.latency_sources["avg_blockchain_finality_sec"].startswith("modelled")
    assert report.paper_reference_latencies == PAPER_REFERENCE_LATENCIES
    for metric in report.receiver_metrics.values():
        assert math.isfinite(metric.capacity_gain_pct)


# --- DePIN quorum and attestation -----------------------------------------------------------------


@pytest.mark.parametrize(("n", "quorum"), [(1, 1), (2, 2), (3, 3), (4, 3), (7, 5), (10, 7)])
def test_bft_quorum_is_floor_two_thirds_plus_one(n: int, quorum: int) -> None:
    val = DePINSLAValidator(num_validators=n)
    assert val.consensus_threshold == quorum
    assert val.consensus_threshold <= n


def test_seven_validators_tolerate_two_faults() -> None:
    val = DePINSLAValidator(num_validators=7)
    sinr = {"Rx1": -9.0, "Rx2": -8.0, "Rx3": -5.0}
    val.validators[0].is_active = False
    val.validators[1].is_active = False
    assert val.evaluate_uav_position_sla((0.0, 0.0, 35.0), sinr).valid is True  # 5 of 7
    val.validators[2].is_active = False
    assert val.evaluate_uav_position_sla((0.0, 0.0, 35.0), sinr).valid is False  # 4 of 7


def test_small_validator_sets_can_reach_consensus() -> None:
    for n in (1, 2):
        res = DePINSLAValidator(num_validators=n).evaluate_uav_position_sla((0.0, 0.0, 35.0), {"Rx1": -5.0})
        assert res.valid is True


def test_sensor_signature_requires_the_sensor_key() -> None:
    import hashlib

    val = DePINSLAValidator()
    sensor_id, payload = "sensor-Rx1", b"Rx1:-9.500000"
    key = val.register_sensor(sensor_id)
    good = val.sign_sensor_payload(key, sensor_id, payload)

    assert val.verify_sensor_signature(sensor_id, payload, good) is True
    # the old scheme accepted this public, unkeyed digest (and any string sharing its first 16 hex chars)
    unkeyed = hashlib.sha256(sensor_id.encode() + payload).hexdigest()
    assert val.verify_sensor_signature(sensor_id, payload, unkeyed) is False
    assert val.verify_sensor_signature(sensor_id, payload, good[:16] + "0" * 48) is False
    assert val.verify_sensor_signature(sensor_id, b"Rx1:+30.000000", good) is False
    assert val.verify_sensor_signature("sensor-unknown", payload, good) is False
    wrong_key = val.sign_sensor_payload(b"k" * 32, sensor_id, payload)
    assert val.verify_sensor_signature(sensor_id, payload, wrong_key) is False


def test_engine_computes_sensor_signature_validity() -> None:
    orch = MetaverseOrchestrator(seed=111)
    assert orch.step(deterministic=True).sla_result.valid is True

    # Rotate one sensor key at the validator: the sensor now signs with a stale key
    orch.depin_sla.register_sensor("sensor-Rx2")
    trace = orch.step(deterministic=True)
    assert trace.sla_result.valid is False
    assert "signature verification failed" in (trace.sla_result.rejection_reason or "")


# --- Bounded histories ----------------------------------------------------------------------------


def test_orchestrator_histories_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MetaverseOrchestrator, "MAX_TRACES", 5)
    monkeypatch.setattr(MetaverseOrchestrator, "MAX_TRAJECTORY_POINTS", 6)
    monkeypatch.setattr(NetworkSlicingManager, "MAX_TRANSMISSION_LOG", 7)
    monkeypatch.setattr(DePINSLAValidator, "MAX_FINALIZED_BLOCKS_RETAINED", 4)

    orch = MetaverseOrchestrator(seed=111)
    traces = orch.run_cycles(count=12)

    assert len(orch.traces) == 5
    assert len(orch.trajectory_history) <= 6
    assert len(orch.slicing.transmission_log) == 7
    assert len(orch.depin_sla.finalized_blocks) <= 4
    # counters and cycle numbering keep counting past the caps
    assert traces[-1].cycle_index == 12
    status = orch.get_latest_status()
    assert status["total_cycles_executed"] == 12
    assert status["depin_blocks_count"] == orch.depin_sla.total_finalized_blocks
    assert orch.slicing.total_packets_logged == 36
