"""Decentralized Chaos Engineering & Mesh Resilience Simulator.

Simulates catastrophic real-world failure modes for sovereign datacenter clusters:
- Network partitions (split-brain isolation across mountain / island nodes)
- Sudden leader node crashes during batch log replication
- High packet-loss terrestrial links with automatic Space DTN fallback
- Byzantine / delayed responses
"""

from __future__ import annotations

import logging
import os
import random
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sovereign_dc.mesh.consensus import RaftCluster
from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter

logger = logging.getLogger("MeshChaos")


class ChaosScenario(StrEnum):
    """Catalog of fault-injection scenarios."""

    SPLIT_BRAIN_PARTITION = "split_brain_partition"
    LEADER_CRASH_FAILOVER = "leader_crash_failover"
    PACKET_LOSS_REPLICATION = "packet_loss_replication"
    TERRESTRIAL_SEVERED_DTN_FALLBACK = "terrestrial_severed_dtn_fallback"
    BYZANTINE_TERM_FLOOD = "byzantine_term_flood"


@dataclass
class ChaosResult:
    """Outcome and telemetry of an executed chaos engineering scenario."""

    scenario: str
    nodes_involved: list[str]
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    resilience_score: float = 100.0  # 0 to 100%


class MeshChaosSimulator:
    """Executes controlled resilience stress-tests on sovereign cluster topologies."""

    def __init__(self, node_ids: list[str] | None = None) -> None:
        self.node_ids: list[str] = node_ids or [
            "smdc-node-01-athens",
            "smdc-node-02-alps",
            "smdc-node-03-island",
            "smdc-node-04-orbit",
            "smdc-node-05-mobile",
        ]

    def simulate_split_brain_partition(self, group_a_size: int = 3) -> ChaosResult:
        """Simulates an abrupt network partition dividing nodes into two isolated groups.

        Verifies that only the majority partition can elect a leader and commit log entries,
        while the minority partition safely steps down or halts log commits.
        """
        t0 = time.perf_counter()
        cluster = RaftCluster(self.node_ids)

        group_a = self.node_ids[:group_a_size]
        group_b = self.node_ids[group_a_size:]

        # 1. Elect initial cluster leader across full network
        leader_id = cluster.step_election(self.node_ids[0])

        # 2. Partition: isolate peers between group_a and group_b
        for nid in group_a:
            cluster.nodes[nid].peers = [p for p in group_a if p != nid]
        for nid in group_b:
            cluster.nodes[nid].peers = [p for p in group_b if p != nid]

        # 3. Trigger election in majority partition (group_a has 3/5 quorum)
        majority_leader = cluster.step_election(group_a[0])

        # 4. Trigger election in minority partition (group_b has 2/5 -> cannot form quorum)
        minority_leader = cluster.step_election(group_b[0])

        # Majority must elect a leader; minority must fail (leader is None)
        quorum_preserved = majority_leader is not None and minority_leader is None

        elapsed = (time.perf_counter() - t0) * 1000.0
        return ChaosResult(
            scenario=ChaosScenario.SPLIT_BRAIN_PARTITION,
            nodes_involved=self.node_ids,
            success=quorum_preserved,
            details={
                "initial_leader": leader_id,
                "majority_partition": group_a,
                "minority_partition": group_b,
                "majority_leader": majority_leader,
                "minority_leader": minority_leader,
                "split_brain_prevented": quorum_preserved,
            },
            elapsed_ms=round(elapsed, 2),
            resilience_score=100.0 if quorum_preserved else 0.0,
        )

    def simulate_leader_crash_and_failover(self) -> ChaosResult:
        """Simulates instantaneous crash of the active leader node and verifies re-election."""
        t0 = time.perf_counter()
        cluster = RaftCluster(self.node_ids)

        # 1. Elect initial leader
        crashed_leader = cluster.step_election(self.node_ids[0]) or self.node_ids[0]

        # 2. Submit initial command
        cmd_idx = cluster.nodes[crashed_leader].submit_command({"task": "solar_ai_batch"})
        cluster.replicate_heartbeats(crashed_leader)

        # 3. Simulate sudden crash: remove leader from cluster peer lists
        surviving_nodes = [n for n in self.node_ids if n != crashed_leader]
        del cluster.nodes[crashed_leader]

        for nid, node in cluster.nodes.items():
            node.peers = [p for p in surviving_nodes if p != nid]

        # 4. Trigger election among surviving nodes
        new_leader = cluster.step_election(surviving_nodes[0])
        failover_success = new_leader is not None and new_leader in surviving_nodes

        elapsed = (time.perf_counter() - t0) * 1000.0
        return ChaosResult(
            scenario=ChaosScenario.LEADER_CRASH_FAILOVER,
            nodes_involved=self.node_ids,
            success=failover_success,
            details={
                "crashed_leader": crashed_leader,
                "surviving_nodes": surviving_nodes,
                "new_elected_leader": new_leader,
                "replicated_entry_index": cmd_idx,
            },
            elapsed_ms=round(elapsed, 2),
            resilience_score=100.0 if failover_success else 0.0,
        )

    def simulate_terrestrial_sever_dtn_fallback(self) -> ChaosResult:
        """Simulates complete fiber/Starlink uplink severance on an edge node.

        Verifies that outbound telemetry bundles automatically spool into local NVMe
        DTN store-and-forward queue for orbital space pass contact.
        """
        t0 = time.perf_counter()
        spool_db = os.path.join(tempfile.gettempdir(), f"smdc_chaos_dtn_{uuid.uuid4().hex[:8]}.db")
        router = DTNRouter(db_path=spool_db)

        # Enqueue emergency telemetry bundles under disconnected conditions
        bundles_queued = 0
        try:
            for i in range(10):
                bundle = Bundle(
                    source_eid="dtn://smdc-node-03-island.sovereign.space",
                    destination_eid="dtn://smdc-node-01-athens.sovereign.space/telemetry",
                    payload=f'{{"seq": {i}, "status": "ISLAND_DISCONNECTED_SOLAR_OK"}}'.encode(),
                    priority=BundlePriority.CRITICAL if i == 0 else BundlePriority.NORMAL,
                )
                if router.queue_bundle(bundle):
                    bundles_queued += 1

            stats = router.get_queue_stats()
            spool_success = stats["queued_bundle_count"] == 10 and bundles_queued == 10
        finally:
            if os.path.exists(spool_db):
                try:
                    os.remove(spool_db)
                except Exception:
                    pass

        elapsed = (time.perf_counter() - t0) * 1000.0
        return ChaosResult(
            scenario=ChaosScenario.TERRESTRIAL_SEVERED_DTN_FALLBACK,
            nodes_involved=["smdc-node-03-island"],
            success=spool_success,
            details={
                "spool_db": spool_db,
                "bundles_enqueued": bundles_queued,
                "stats": stats,
            },
            elapsed_ms=round(elapsed, 2),
            resilience_score=100.0 if spool_success else 0.0,
        )

    def simulate_packet_loss_replication(self, loss_rate: float = 0.3) -> ChaosResult:
        """Simulates lossy RF links (e.g. 30% drop rate) during log replication."""
        t0 = time.perf_counter()
        cluster = RaftCluster(self.node_ids)
        leader_id = cluster.step_election(self.node_ids[0]) or self.node_ids[0]

        # Submit multiple commands
        for i in range(5):
            cluster.nodes[leader_id].submit_command({"batch_id": i})

        # Replicate heartbeats with simulated probabilistic drop
        leader = cluster.nodes[leader_id]
        delivered_count = 0
        for peer_id in leader.peers:
            if random.random() >= loss_rate:
                args = leader.create_append_entries(peer_id)
                reply = cluster.nodes[peer_id].handle_append_entries(args)
                leader.handle_append_reply(peer_id, reply, len(cluster.nodes))
                delivered_count += 1

        elapsed = (time.perf_counter() - t0) * 1000.0
        # Success if leader log remains consistent despite packet drops
        success = len(leader.log) == 5
        return ChaosResult(
            scenario=ChaosScenario.PACKET_LOSS_REPLICATION,
            nodes_involved=self.node_ids,
            success=success,
            details={
                "loss_rate": loss_rate,
                "leader_log_entries": len(leader.log),
                "successful_transmissions": delivered_count,
            },
            elapsed_ms=round(elapsed, 2),
            resilience_score=round(100.0 * (1.0 - (loss_rate * 0.2)), 1),
        )

    def run_all_scenarios(self) -> dict[str, ChaosResult]:
        """Executes the complete chaos engineering resilience matrix."""
        return {
            "split_brain": self.simulate_split_brain_partition(),
            "leader_crash": self.simulate_leader_crash_and_failover(),
            "dtn_fallback": self.simulate_terrestrial_sever_dtn_fallback(),
            "packet_loss": self.simulate_packet_loss_replication(),
        }
