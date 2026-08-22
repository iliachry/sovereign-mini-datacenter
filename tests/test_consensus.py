"""Unit tests for the Decentralized Multi-Node Raft Consensus Engine."""

import time

from sovereign_dc.mesh.consensus import (
    AppendEntriesArgs,
    LogEntry,
    NodeRole,
    RaftCluster,
    RaftNode,
    RequestVoteArgs,
    RequestVoteReply,
)


def test_raft_node_initialization() -> None:
    node = RaftNode(node_id="smdc-01", peers=["smdc-02", "smdc-03"])
    assert node.node_id == "smdc-01"
    assert node.role == NodeRole.FOLLOWER
    assert node.current_term == 0
    assert node.voted_for is None
    assert len(node.log) == 0
    assert not node.has_election_timed_out(current_time=time.time())


def test_start_election_and_voting() -> None:
    node1 = RaftNode(node_id="smdc-01", peers=["smdc-02", "smdc-03"])
    node2 = RaftNode(node_id="smdc-02", peers=["smdc-01", "smdc-03"])

    args = node1.start_election()
    assert node1.role == NodeRole.CANDIDATE
    assert node1.current_term == 1
    assert node1.voted_for == "smdc-01"
    assert args.candidate_id == "smdc-01"
    assert args.term == 1

    reply = node2.handle_request_vote(args)
    assert reply.vote_granted is True
    assert reply.term == 1
    assert node2.voted_for == "smdc-01"


def test_reject_vote_if_already_voted() -> None:
    node = RaftNode(node_id="smdc-02", peers=["smdc-01", "smdc-03"])
    node.current_term = 1
    node.voted_for = "smdc-01"

    # Incoming vote from a different node in same term
    args = RequestVoteArgs(term=1, candidate_id="smdc-03", last_log_index=0, last_log_term=0)
    reply = node.handle_request_vote(args)
    assert reply.vote_granted is False


def test_reject_vote_if_term_is_older() -> None:
    node = RaftNode(node_id="smdc-02", peers=["smdc-01", "smdc-03"])
    node.current_term = 2

    args = RequestVoteArgs(term=1, candidate_id="smdc-01", last_log_index=0, last_log_term=0)
    reply = node.handle_request_vote(args)
    assert reply.vote_granted is False


def test_election_win_and_leader_transition() -> None:
    node1 = RaftNode(node_id="smdc-01", peers=["smdc-02", "smdc-03"])
    node1.start_election()

    reply2 = RequestVoteReply(term=1, vote_granted=True, voter_id="smdc-02")
    became_leader = node1.handle_vote_reply(reply2, total_nodes=3)
    assert became_leader is True
    assert node1.role == NodeRole.LEADER
    assert node1.leader_id == "smdc-01"


def test_append_entries_heartbeat_and_commit() -> None:
    cluster = RaftCluster(["smdc-01", "smdc-02", "smdc-03"])
    leader_id = cluster.step_election("smdc-01")
    assert leader_id == "smdc-01"
    assert cluster.nodes["smdc-01"].role == NodeRole.LEADER

    # Submit command to leader
    cmd_idx = cluster.nodes["smdc-01"].submit_command({"action": "schedule_gpu_batch", "job_id": 42})
    assert cmd_idx == 1

    # Replicate heartbeats
    cluster.replicate_heartbeats("smdc-01")

    # Verify follower log updated
    follower = cluster.nodes["smdc-02"]
    assert len(follower.log) == 1
    assert follower.log[0].command["job_id"] == 42
    assert follower.leader_id == "smdc-01"


def test_non_leader_cannot_submit_command() -> None:
    node = RaftNode(node_id="smdc-02", peers=["smdc-01", "smdc-03"])
    assert node.role == NodeRole.FOLLOWER
    res = node.submit_command({"action": "test"})
    assert res is None


def test_node_status_summary() -> None:
    node = RaftNode(node_id="smdc-01", peers=["smdc-02"])
    status = node.status()
    assert status["node_id"] == "smdc-01"
    assert status["role"] == "follower"
    assert status["peers_count"] == 1
    assert status["dtn_spool_fallback"] is False


def test_append_entries_term_mismatch() -> None:
    node = RaftNode(node_id="smdc-02", peers=["smdc-01"])
    node.current_term = 3

    args = AppendEntriesArgs(
        term=1,
        leader_id="smdc-01",
        prev_log_index=0,
        prev_log_term=0,
        entries=[],
        leader_commit=0,
    )
    reply = node.handle_append_entries(args)
    assert reply.success is False


def test_append_entries_log_inconsistency() -> None:
    node = RaftNode(node_id="smdc-02", peers=["smdc-01"])
    node.current_term = 2
    node.log = [LogEntry(term=1, index=1, command={"step": 1})]

    # Leader sends entry with prev_log_index 2, which doesn't exist
    args = AppendEntriesArgs(
        term=2,
        leader_id="smdc-01",
        prev_log_index=2,
        prev_log_term=1,
        entries=[LogEntry(term=2, index=3, command={"step": 3})],
        leader_commit=1,
    )
    reply = node.handle_append_entries(args)
    assert reply.success is False
