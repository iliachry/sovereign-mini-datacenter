"""Regression tests for reproduced defects in the mesh layer (Raft consensus, chaos, LoRa gateway)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_dc.mesh.chaos import MeshChaosSimulator
from sovereign_dc.mesh.consensus import (
    AppendEntriesArgs,
    AppendEntriesReply,
    LogEntry,
    NodeRole,
    RaftCluster,
    RaftNode,
    RequestVoteArgs,
)
from sovereign_dc.mesh.lora import meshtastic_gateway
from sovereign_dc.space.dtn.router import DTNRouter


def _entry(term: int, index: int) -> LogEntry:
    return LogEntry(term=term, index=index, command={"i": index})


# --- Raft: follower reply / commit clamp (finding 1) -------------------------------------


def test_follower_reply_match_index_excludes_stale_suffix() -> None:
    follower = RaftNode("f", peers=["l", "x"])
    follower.current_term = 2
    # index 1 matches the leader; indexes 2-3 are stale uncommitted leftovers of term 1
    follower.log = [_entry(1, 1), _entry(1, 2), _entry(1, 3)]

    heartbeat = AppendEntriesArgs(term=2, leader_id="l", prev_log_index=1, prev_log_term=1, entries=[], leader_commit=3)
    reply = follower.handle_append_entries(heartbeat)

    assert reply.success is True
    # Only the verified prefix counts as replicated, not len(log) == 3
    assert reply.match_index == 1
    # ...and the follower must not commit unverified stale entries
    assert follower.commit_index == 1


def test_follower_commit_index_clamped_to_last_new_entry() -> None:
    follower = RaftNode("f", peers=["l", "x"])
    args = AppendEntriesArgs(
        term=1, leader_id="l", prev_log_index=0, prev_log_term=0, entries=[_entry(1, 1)], leader_commit=5
    )
    reply = follower.handle_append_entries(args)
    assert reply.match_index == 1
    assert follower.commit_index == 1


# --- Raft: true-majority commit quorum (finding 2) ---------------------------------------


def test_even_cluster_does_not_commit_with_half_the_copies() -> None:
    cluster = RaftCluster(["n1", "n2", "n3", "n4"])
    assert cluster.step_election("n1") == "n1"
    leader = cluster.nodes["n1"]
    assert leader.submit_command({"job": 1}) == 1

    # Only one follower acknowledges: 2 of 4 copies is NOT a majority
    args = leader.create_append_entries("n2")
    leader.handle_append_reply("n2", cluster.nodes["n2"].handle_append_entries(args), total_nodes=4)
    assert leader.commit_index == 0

    # A second follower makes it 3 of 4
    args = leader.create_append_entries("n3")
    leader.handle_append_reply("n3", cluster.nodes["n3"].handle_append_entries(args), total_nodes=4)
    assert leader.commit_index == 1


def test_leader_only_commits_entries_from_its_current_term() -> None:
    leader = RaftNode("l", peers=["a", "b"])
    leader.log = [_entry(1, 1)]
    leader.current_term = 2
    leader.become_leader()

    reply = AppendEntriesReply(term=2, success=True, responder_id="a", match_index=1)
    leader.handle_append_reply("a", reply, total_nodes=3)
    # Entry 1 is from term 1: replicated on a majority but not committable by counting (Raft 5.4.2)
    assert leader.commit_index == 0

    leader.submit_command({"noop": True})
    reply = AppendEntriesReply(term=2, success=True, responder_id="a", match_index=2)
    leader.handle_append_reply("a", reply, total_nodes=3)
    assert leader.commit_index == 2


def test_stale_reply_does_not_move_match_index_backwards() -> None:
    leader = RaftNode("l", peers=["a", "b"])
    leader.current_term = 1
    leader.become_leader()
    leader.submit_command({"x": 1})
    leader.submit_command({"x": 2})
    leader.handle_append_reply("a", AppendEntriesReply(1, True, "a", 2), total_nodes=3)
    leader.handle_append_reply("a", AppendEntriesReply(1, True, "a", 0), total_nodes=3)
    assert leader.match_index["a"] == 2
    assert leader.commit_index == 2


def test_single_node_cluster_elects_itself() -> None:
    cluster = RaftCluster(["solo"])
    assert cluster.step_election("solo") == "solo"
    node = cluster.nodes["solo"]
    assert node.role == NodeRole.LEADER
    assert node.submit_command({"job": 1}) == 1


def test_candidate_cannot_vote_twice_in_one_term() -> None:
    node = RaftNode("c", peers=["a", "b"])
    node.start_election()  # term 1, voted for itself
    assert node.voted_for == "c"

    # A same-term leader appears: step down, but the vote for this term stays cast
    node.handle_append_entries(
        AppendEntriesArgs(term=1, leader_id="a", prev_log_index=0, prev_log_term=0, entries=[], leader_commit=0)
    )
    assert node.role == NodeRole.FOLLOWER
    assert node.voted_for == "c"

    reply = node.handle_request_vote(RequestVoteArgs(term=1, candidate_id="b", last_log_index=0, last_log_term=0))
    assert reply.vote_granted is False


# --- Chaos simulator ----------------------------------------------------------------------


def test_packet_loss_total_loss_is_not_reported_as_success() -> None:
    res = MeshChaosSimulator().simulate_packet_loss_replication(loss_rate=1.0)
    assert res.success is False
    assert res.details["successful_transmissions"] == 0
    assert res.details["committed_entries"] == 0
    assert res.resilience_score == 0.0


def test_packet_loss_zero_loss_commits_everything() -> None:
    res = MeshChaosSimulator().simulate_packet_loss_replication(loss_rate=0.0)
    assert res.success is True
    assert res.details["committed_entries"] == 5
    assert res.resilience_score == 100.0


def test_packet_loss_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError):
        MeshChaosSimulator().simulate_packet_loss_replication(loss_rate=1.5)


@pytest.mark.parametrize("group_a_size", [0, 5, 6, -1])
def test_split_brain_rejects_degenerate_partition(group_a_size: int) -> None:
    with pytest.raises(ValueError):
        MeshChaosSimulator().simulate_split_brain_partition(group_a_size)


@pytest.mark.parametrize("group_a_size", [1, 2, 3, 4])
def test_split_brain_any_partition_of_five(group_a_size: int) -> None:
    res = MeshChaosSimulator().simulate_split_brain_partition(group_a_size)
    assert res.success is True
    assert len(res.details["majority_partition"]) >= 3
    assert res.details["majority_leader"] in res.details["majority_partition"]
    assert res.details["minority_leader"] is None


def test_split_brain_even_split_elects_no_leader() -> None:
    sim = MeshChaosSimulator(node_ids=["a", "b", "c", "d"])
    res = sim.simulate_split_brain_partition(group_a_size=2)
    assert res.details["majority_has_quorum"] is False
    assert res.details["majority_leader"] is None
    assert res.details["minority_leader"] is None
    assert res.success is True


def test_leader_crash_failover_needs_majority_of_original_cluster() -> None:
    # 2-node cluster: after the leader crashes the survivor (1 of 2) must NOT win an election
    res = MeshChaosSimulator(node_ids=["a", "b"]).simulate_leader_crash_and_failover()
    assert res.success is False
    assert res.details["new_elected_leader"] is None


# --- LoRa gateway -------------------------------------------------------------------------


def test_lora_daemon_reports_no_fabricated_traffic(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with patch("time.sleep", return_value=None):
        bridged = meshtastic_gateway.run_lora_daemon(max_iterations=25)
    assert bridged == 0
    assert "No LoRa radio attached" in caplog.text
    assert "Active nodes: 4" not in caplog.text
    assert "Total packets bridged" not in caplog.text


def test_lora_forward_uses_configured_spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spool = tmp_path / "configured" / "lora_spool.db"
    monkeypatch.setenv("DTN_DB_PATH", str(spool))

    assert meshtastic_gateway.forward_to_space_dtn("sensor-node-4", "BATTERY_CRITICAL") is True
    assert spool.exists()
    assert DTNRouter(db_path=str(spool)).get_queue_stats()["queued_bundle_count"] == 1


def test_lora_forward_reports_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
    with patch("sovereign_dc.space.dtn.router.DTNRouter", side_effect=Exception("DB Locked")):
        assert meshtastic_gateway.forward_to_space_dtn("sensor-node-4", "X") is False


def test_software_lora_gateway_is_in_sync_with_package() -> None:
    root = Path(__file__).resolve().parent.parent
    src = root / "src" / "sovereign_dc" / "mesh" / "lora" / "meshtastic_gateway.py"
    mirror = root / "software" / "mesh" / "lora" / "meshtastic_gateway.py"
    assert src.read_bytes() == mirror.read_bytes()


def test_single_node_cluster_commits_its_own_entries():
    """A one-node cluster is its own majority: submitted entries must commit."""
    from sovereign_dc.mesh.consensus import RaftCluster

    cluster = RaftCluster(["solo"])
    assert cluster.step_election("solo") == "solo"
    idx = cluster.nodes["solo"].submit_command({"task": "x"})
    cluster.replicate_heartbeats("solo")
    assert cluster.nodes["solo"].commit_index == idx


def test_four_node_leader_alone_does_not_commit():
    from sovereign_dc.mesh.consensus import RaftCluster

    ids = ["a", "b", "c", "d"]
    cluster = RaftCluster(ids)
    assert cluster.step_election("a") == "a"
    idx = cluster.nodes["a"].submit_command({"task": "x"})
    cluster.replicate_heartbeats("a", reachable={"b"})
    assert cluster.nodes["a"].commit_index < idx
