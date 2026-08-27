"""Unit tests for the Metaverse Framework for Wireless Systems Management.

Validates:
1. SionnaRayTracer physics-based 3D multipath propagation & SINR grid.
2. SceneAwarePPO (SA-PPO) & ModelDrivenPPO (MD-PPO) neural policies.
3. 5G Network Slicing traffic isolation & packet scheduling.
4. DePIN SLA validation, PoS/dBFT consensus & smart contracts.
5. MetaverseOrchestrator multi-layer 3-phase lifecycle (Algorithm 1).
6. MetaverseBenchmark parametric evaluation & comparative metrics.
7. CLI commands, REST APIs, and MCP server tool/resource integration.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from sovereign_dc.metaverse.agent import (
    ACTIONS_3D,
    MLPNetwork,
    ModelDrivenPPO,
    SceneAwarePPO,
)
from sovereign_dc.metaverse.benchmark import MetaverseBenchmark
from sovereign_dc.metaverse.depin_sla import DePINSLAValidator
from sovereign_dc.metaverse.engine import MetaverseOrchestrator
from sovereign_dc.metaverse.ray_tracer import SionnaRayTracer
from sovereign_dc.metaverse.slicing import NetworkSlicingManager, SliceType

# =============================================================================
# 1. Physics-Based Ray-Tracing & Channel Modeling Tests
# =============================================================================


def test_ray_tracer_initialization():
    rt = SionnaRayTracer(carrier_freq_ghz=3.5, tx_power_dbm=23.0)
    assert rt.carrier_freq_ghz == 3.5
    assert rt.tx_power_dbm == 23.0
    assert len(rt.buildings) == 5
    assert len(rt.receivers) == 3
    assert "Rx1" in rt.receivers and rt.receivers["Rx1"].is_disadvantaged


def test_fspl_and_obstruction_check():
    rt = SionnaRayTracer()
    fspl = rt.compute_free_space_path_loss(100.0)
    assert fspl > 70.0

    # Test direct LoS vs obstructed
    is_obstructed = rt._check_los_obstruction((0.0, 0.0, 35.0), (-45.0, -45.0, 1.5))
    assert isinstance(is_obstructed, bool)


def test_propagation_evaluation_and_sinr():
    rt = SionnaRayTracer()
    results = rt.evaluate_all_receivers((0.0, 0.0, 35.0))
    assert len(results) == 3
    for rx_id, res in results.items():
        assert res.rx_id == rx_id
        assert len(res.multipath_components) >= 1
        assert res.capacity_bps_hz > 0.0
        assert res.total_received_power_dbm > -120.0


def test_sinr_grid_generation():
    rt = SionnaRayTracer()
    grid = rt.generate_sinr_grid((0.0, 0.0, 35.0), grid_size=4)
    assert len(grid) == 4
    assert len(grid[0]) == 4
    assert all(isinstance(val, float) for row in grid for val in row)


# =============================================================================
# 2. AI Agents & Neural Policy Tests (SA-PPO vs MD-PPO)
# =============================================================================


def test_mlp_network_forward_pass():
    policy_net = MLPNetwork(input_dim=20, output_dim=6, is_value_net=False, seed=42)
    val_net = MLPNetwork(input_dim=20, output_dim=1, is_value_net=True, seed=42)

    sample_state = [0.1] * 20
    probs = policy_net.forward(sample_state)
    assert len(probs) == 6
    assert abs(sum(probs) - 1.0) < 1e-4

    value = val_net.forward(sample_state)
    assert len(value) == 1


def test_sa_ppo_agent_lifecycle():
    rt = SionnaRayTracer()
    agent = SceneAwarePPO(seed=111)

    prop = rt.evaluate_all_receivers((0.0, 0.0, 35.0))
    state = agent.encode_state((0.0, 0.0, 35.0), prop)
    assert len(state) == 20

    action_idx = agent.select_action(state, deterministic=True)
    assert action_idx in ACTIONS_3D

    reward = agent.compute_reward(prop)
    assert isinstance(reward, float)

    # Record and train step
    next_prop = rt.evaluate_all_receivers((5.0, 0.0, 35.0))
    next_state = agent.encode_state((5.0, 0.0, 35.0), next_prop)
    agent.record_transition(state, action_idx, reward, next_state, done=False)
    assert len(agent.buffer) == 1

    losses = agent.train_step()
    assert "loss_policy" in losses and "loss_value" in losses
    assert len(agent.buffer) == 0


def test_model_driven_ppo_baseline():
    md_agent = ModelDrivenPPO(rician_k_db=10.0, seed=222)
    sinr_est = md_agent.evaluate_rician_sinr((0.0, 0.0, 35.0), (-45.0, -45.0, 1.5))
    assert isinstance(sinr_est, float)


# =============================================================================
# 3. 5G Network Slicing & QoS Isolation Tests
# =============================================================================


def test_network_slicing_bandwidth_isolation():
    mgr = NetworkSlicingManager()
    summary = mgr.get_summary()

    assert SliceType.URLLC.value in summary
    assert SliceType.EMBB.value in summary
    assert SliceType.MMTC.value in summary

    # URLLC packet transmission
    pkt_urllc = mgr.transmit_uav_control_command((5.0, 0.0, 0.0))
    assert pkt_urllc.slice_type == SliceType.URLLC
    assert pkt_urllc.latency_ms < 1.5

    # eMBB XR frame transmission
    pkt_xr = mgr.transmit_xr_frame(200000)
    assert pkt_xr.slice_type == SliceType.EMBB
    assert pkt_xr.latency_ms > 10.0

    # mMTC IoT batch transmission
    pkt_iot = mgr.ingest_iot_sensor_batch(180, 64)
    assert pkt_iot.slice_type == SliceType.MMTC
    assert pkt_iot.transmitted


# =============================================================================
# 4. DePIN Blockchain SLA & Byzantine Consensus Tests
# =============================================================================


def test_depin_sla_consensus_and_validation():
    val = DePINSLAValidator(num_validators=7)
    assert len(val.validators) == 7
    # ceil(14/3) + 1 = 5 + 1 = 6 signatures
    assert val.consensus_threshold == 6

    # 1. Nominal position approval
    res_nom = val.evaluate_uav_position_sla(
        (0.0, 0.0, 35.0),
        {"Rx1": -9.5, "Rx2": -8.1, "Rx3": -5.0},
        sensor_signatures_valid=True,
    )
    assert res_nom.valid
    assert res_nom.position_approved
    assert res_nom.validator_signatures_count >= 6
    assert len(val.finalized_blocks) == 1

    # 2. Rejection due to low SINR (< -15 dB)
    res_bad = val.evaluate_uav_position_sla(
        (-90.0, -90.0, 10.0),
        {"Rx1": -17.5, "Rx2": -10.0, "Rx3": -8.0},
        sensor_signatures_valid=True,
    )
    assert not res_bad.valid
    assert not res_bad.position_approved
    assert res_bad.optimization_event_triggered

    # 3. Rejection due to invalid sensor signatures
    res_sig_fail = val.evaluate_uav_position_sla(
        (0.0, 0.0, 35.0),
        {"Rx1": -9.0, "Rx2": -8.0, "Rx3": -5.0},
        sensor_signatures_valid=False,
    )
    assert not res_sig_fail.valid


# =============================================================================
# 5. Multi-Layer Orchestrator & Benchmark Tests
# =============================================================================


def test_metaverse_orchestrator_execution():
    orch = MetaverseOrchestrator(seed=111)
    trace = orch.step(deterministic=True)

    assert trace.cycle_index == 1
    assert trace.decision_latency_ms >= 0.0
    assert trace.critical_path_latency_ms >= 0.0
    assert trace.urllc_latency_ms < 1.5  # URLLC < 1.5ms mathematical latency
    assert len(orch.trajectory_history) == 2

    # Run multiple cycles
    traces = orch.run_cycles(count=3, deterministic=True)
    assert len(traces) == 3
    assert len(orch.traces) == 4

    # Test emergency stop
    orch.trigger_emergency_stop()
    assert orch.is_emergency_stopped
    stop_trace = orch.step()
    assert stop_trace.action_vector == (0.0, 0.0, 0.0)

    orch.resume_operation()
    assert not orch.is_emergency_stopped

    status = orch.get_latest_status()
    assert status["emergency_stopped"] is False
    assert status["total_cycles_executed"] == 5


def test_metaverse_benchmark_suite():
    bench = MetaverseBenchmark(seed=111)
    report = bench.run_comparison(episodes=3, steps_per_episode=5)

    assert report.episodes == 3
    assert report.total_timesteps == 15
    assert "Rx1" in report.receiver_metrics
    assert report.receiver_metrics["Rx1"].is_disadvantaged
    assert report.avg_urllc_latency_ms < 1.5


# =============================================================================
# 6. CLI Command Handlers Tests
# =============================================================================


def test_cli_sim_commands(capsys):
    from sovereign_dc.cli import (
        cmd_sim_benchmark,
        cmd_sim_run,
        cmd_sim_sla,
        cmd_sim_slices,
    )

    args_run = MagicMock(cycles=2, seed=111, deterministic=True)
    cmd_sim_run(args_run)
    out_run = capsys.readouterr().out
    assert "Metaverse 6-Layer Simulation" in out_run
    assert "Simulation Complete" in out_run

    args_slices = MagicMock()
    cmd_sim_slices(args_slices)
    out_slices = capsys.readouterr().out
    assert "5G Network Slicing" in out_slices

    args_sla = MagicMock()
    cmd_sim_sla(args_sla)
    out_sla = capsys.readouterr().out
    assert "DePIN Blockchain SLA" in out_sla

    args_bench = MagicMock(episodes=2, steps=4, seed=111)
    cmd_sim_benchmark(args_bench)
    out_bench = capsys.readouterr().out
    assert "Parametric RL Benchmark" in out_bench


# =============================================================================
# 7. MCP Server Tools & Resources Integration Tests
# =============================================================================


def test_mcp_metaverse_tools_and_resources():
    from sovereign_dc.mcp.prompts import get_mcp_prompts
    from sovereign_dc.mcp.resources import get_mcp_resources
    from sovereign_dc.mcp.tools import get_mcp_tools

    tools = {t.name: t for t in get_mcp_tools()}
    assert "run_metaverse_sim_cycle" in tools
    assert "get_5g_slices_status" in tools
    assert "validate_depin_sla" in tools

    # Test tool invocation
    sim_res = tools["run_metaverse_sim_cycle"].handler({"cycles": 1, "deterministic": True})
    assert sim_res["cycles_executed"] == 1
    assert "uav_final_position" in sim_res

    slices_res = tools["get_5g_slices_status"].handler({})
    assert "URLLC" in slices_res["slices"]

    sla_res = tools["validate_depin_sla"].handler({"uav_x": 10.0, "uav_y": 15.0, "uav_z": 40.0})
    assert sla_res["valid"] is True

    # Test resources
    resources = {r.uri: r for r in get_mcp_resources()}
    assert "smdc://metaverse/uav/status" in resources
    assert "smdc://metaverse/5g/slices" in resources

    raw_uav_res = resources["smdc://metaverse/uav/status"].reader()
    parsed_uav = json.loads(raw_uav_res)
    assert "uav_position" in parsed_uav

    # Test prompt
    prompts = {p.name: p for p in get_mcp_prompts()}
    assert "optimize_uav_coverage" in prompts
    prompt_msgs = prompts["optimize_uav_coverage"].builder({"target_receiver": "Rx1"})
    assert len(prompt_msgs) == 1
    assert "Scene-Aware PPO" in prompt_msgs[0]["content"]["text"]


# =============================================================================
# 8. Enterprise App Archetype Manifest Test
# =============================================================================


def test_oran_ric_enterprise_app_manifest():
    from pathlib import Path

    from sovereign_dc.enterprise.registry import EnterpriseRegistry

    app_dir = Path("examples/enterprise_apps/oran-ric-controller")
    manifest_path = app_dir / "smdc-app.yaml"
    assert manifest_path.exists()

    registry = EnterpriseRegistry()
    manifest = registry.load_manifest_file(manifest_path)
    assert manifest is not None
    assert manifest.app_id == "oran-ric-controller"
    assert manifest.power.tier.value == "L0_CRITICAL"
    errors = manifest.validate()
    assert len(errors) == 0
