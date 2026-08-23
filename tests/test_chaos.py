"""Unit tests for Decentralized Chaos Engineering & Mesh Resilience Simulator."""

from __future__ import annotations

from sovereign_dc.mesh.chaos import (
    ChaosResult,
    ChaosScenario,
    MeshChaosSimulator,
)


class TestMeshChaosSimulator:
    """Test chaos engineering fault injection scenarios."""

    def test_split_brain_partition_majority_quorum(self):
        sim = MeshChaosSimulator()
        res = sim.simulate_split_brain_partition(group_a_size=3)

        assert isinstance(res, ChaosResult)
        assert res.scenario == ChaosScenario.SPLIT_BRAIN_PARTITION
        assert res.success is True
        assert res.details["majority_leader"] is not None
        assert res.details["minority_leader"] is None
        assert res.details["split_brain_prevented"] is True
        assert res.resilience_score == 100.0

    def test_leader_crash_and_failover(self):
        sim = MeshChaosSimulator()
        res = sim.simulate_leader_crash_and_failover()

        assert res.scenario == ChaosScenario.LEADER_CRASH_FAILOVER
        assert res.success is True
        assert res.details["new_elected_leader"] is not None
        assert res.details["new_elected_leader"] in res.details["surviving_nodes"]
        assert res.details["new_elected_leader"] != res.details["crashed_leader"]
        assert res.resilience_score == 100.0

    def test_terrestrial_severed_dtn_fallback(self):
        sim = MeshChaosSimulator()
        res = sim.simulate_terrestrial_sever_dtn_fallback()

        assert res.scenario == ChaosScenario.TERRESTRIAL_SEVERED_DTN_FALLBACK
        assert res.success is True
        assert res.details["bundles_enqueued"] == 10
        assert res.details["stats"]["queued_bundle_count"] == 10
        assert res.resilience_score == 100.0

    def test_packet_loss_replication(self):
        sim = MeshChaosSimulator()
        res = sim.simulate_packet_loss_replication(loss_rate=0.2)

        assert res.scenario == ChaosScenario.PACKET_LOSS_REPLICATION
        assert res.success is True
        assert res.details["leader_log_entries"] == 5
        assert res.resilience_score > 0

    def test_run_all_scenarios_matrix(self):
        sim = MeshChaosSimulator()
        results = sim.run_all_scenarios()

        assert len(results) == 4
        assert "split_brain" in results
        assert "leader_crash" in results
        assert "dtn_fallback" in results
        assert "packet_loss" in results

        for r in results.values():
            assert r.success is True
            assert r.elapsed_ms >= 0.0
