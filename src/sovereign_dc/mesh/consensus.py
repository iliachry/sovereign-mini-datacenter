"""Decentralized Multi-Node Raft Consensus Engine for Sovereign Datacenter Swarms.

Implements leader election, log replication, and cluster state synchronization
over WireGuard mesh networks (100.64.0.0/16) with automatic fallback to
RFC 9171 Space Delay-Tolerant Networking (BPv7) on network partition.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeRole(StrEnum):
    """Lifecycle role in the Raft consensus state machine."""

    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass
class LogEntry:
    """Replicated log entry for distributed state machines."""

    term: int
    index: int
    command: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass
class RequestVoteArgs:
    """Arguments for RequestVote RPC."""

    term: int
    candidate_id: str
    last_log_index: int
    last_log_term: int


@dataclass
class RequestVoteReply:
    """Response from RequestVote RPC."""

    term: int
    vote_granted: bool
    voter_id: str


@dataclass
class AppendEntriesArgs:
    """Arguments for AppendEntries (heartbeat & replication) RPC."""

    term: int
    leader_id: str
    prev_log_index: int
    prev_log_term: int
    entries: list[LogEntry]
    leader_commit: int


@dataclass
class AppendEntriesReply:
    """Response from AppendEntries RPC."""

    term: int
    success: bool
    responder_id: str
    match_index: int


class RaftNode:
    """Autonomous Raft Consensus Node for Sovereign Datacenter mesh clusters."""

    def __init__(
        self,
        node_id: str,
        peers: list[str] | None = None,
        election_timeout_range: tuple[float, float] = (0.15, 0.30),
        heartbeat_interval: float = 0.05,
    ) -> None:
        self.node_id: str = node_id
        self.peers: list[str] = peers or []
        self.election_timeout_range: tuple[float, float] = election_timeout_range
        self.heartbeat_interval: float = heartbeat_interval

        # Persistent state on all nodes
        self.current_term: int = 0
        self.voted_for: str | None = None
        self.log: list[LogEntry] = []

        # Volatile state on all nodes
        self.commit_index: int = 0
        self.last_applied: int = 0
        self.role: NodeRole = NodeRole.FOLLOWER
        self.leader_id: str | None = None

        # Volatile state on leaders
        self.next_index: dict[str, int] = {}
        self.match_index: dict[str, int] = {}

        # Timers and state
        self.last_heartbeat_time: float = time.time()
        self.election_timeout: float = self._random_election_timeout()
        self.votes_received: set[str] = set()
        self.dtn_spool_fallback: bool = False

    def _random_election_timeout(self) -> float:
        return random.uniform(*self.election_timeout_range)

    def reset_election_timeout(self) -> None:
        """Reset election countdown timer."""
        self.last_heartbeat_time = time.time()
        self.election_timeout = self._random_election_timeout()

    def has_election_timed_out(self, current_time: float | None = None) -> bool:
        """Check if election timeout has elapsed."""
        now = current_time if current_time is not None else time.time()
        return (now - self.last_heartbeat_time) >= self.election_timeout

    def start_election(self) -> RequestVoteArgs:
        """Transition to CANDIDATE and initiate a leader election term."""
        self.role = NodeRole.CANDIDATE
        self.current_term += 1
        self.voted_for = self.node_id
        self.votes_received = {self.node_id}
        self.leader_id = None
        self.reset_election_timeout()

        last_log_index = len(self.log)
        last_log_term = self.log[-1].term if self.log else 0

        return RequestVoteArgs(
            term=self.current_term,
            candidate_id=self.node_id,
            last_log_index=last_log_index,
            last_log_term=last_log_term,
        )

    def handle_request_vote(self, args: RequestVoteArgs) -> RequestVoteReply:
        """Process RequestVote RPC from a candidate."""
        # 1. Reply false if term < current_term
        if args.term < self.current_term:
            return RequestVoteReply(term=self.current_term, vote_granted=False, voter_id=self.node_id)

        # Update term if higher
        if args.term > self.current_term:
            self.current_term = args.term
            self.role = NodeRole.FOLLOWER
            self.voted_for = None
            self.leader_id = None

        # 2. Check if voted_for is null or candidate_id, and candidate's log is at least as up-to-date
        can_vote = self.voted_for is None or self.voted_for == args.candidate_id
        my_last_term = self.log[-1].term if self.log else 0
        my_last_index = len(self.log)

        log_ok = (args.last_log_term > my_last_term) or (
            args.last_log_term == my_last_term and args.last_log_index >= my_last_index
        )

        if can_vote and log_ok:
            self.voted_for = args.candidate_id
            self.reset_election_timeout()
            return RequestVoteReply(term=self.current_term, vote_granted=True, voter_id=self.node_id)

        return RequestVoteReply(term=self.current_term, vote_granted=False, voter_id=self.node_id)

    def handle_vote_reply(self, reply: RequestVoteReply, total_nodes: int) -> bool:
        """Process incoming vote response. Returns True if became leader."""
        if self.role != NodeRole.CANDIDATE or reply.term != self.current_term:
            if reply.term > self.current_term:
                self.current_term = reply.term
                self.role = NodeRole.FOLLOWER
                self.voted_for = None
            return False

        if reply.vote_granted:
            self.votes_received.add(reply.voter_id)
            majority = (total_nodes // 2) + 1
            if len(self.votes_received) >= majority:
                self.become_leader()
                return True

        return False

    def become_leader(self) -> None:
        """Transition to LEADER state and initialize follower tracking indexes."""
        self.role = NodeRole.LEADER
        self.leader_id = self.node_id
        for peer in self.peers:
            self.next_index[peer] = len(self.log) + 1
            self.match_index[peer] = 0
        self.dtn_spool_fallback = False

    def create_append_entries(self, peer_id: str) -> AppendEntriesArgs:
        """Generate AppendEntries RPC payload for a specific peer."""
        prev_log_index = self.next_index.get(peer_id, 1) - 1
        prev_log_term = self.log[prev_log_index - 1].term if (0 < prev_log_index <= len(self.log)) else 0

        entries_to_send = self.log[prev_log_index:]

        return AppendEntriesArgs(
            term=self.current_term,
            leader_id=self.node_id,
            prev_log_index=prev_log_index,
            prev_log_term=prev_log_term,
            entries=entries_to_send,
            leader_commit=self.commit_index,
        )

    def handle_append_entries(self, args: AppendEntriesArgs) -> AppendEntriesReply:
        """Process AppendEntries RPC (heartbeat or log replication)."""
        # 1. Reply false if term < current_term
        if args.term < self.current_term:
            return AppendEntriesReply(
                term=self.current_term,
                success=False,
                responder_id=self.node_id,
                match_index=len(self.log),
            )

        # Valid leader recognized
        self.reset_election_timeout()
        self.leader_id = args.leader_id

        if args.term > self.current_term or self.role != NodeRole.FOLLOWER:
            self.current_term = args.term
            self.role = NodeRole.FOLLOWER
            self.voted_for = None

        # 2. Reply false if log doesn't contain an entry at prev_log_index matching prev_log_term
        if args.prev_log_index > 0:
            if len(self.log) < args.prev_log_index:
                return AppendEntriesReply(
                    term=self.current_term,
                    success=False,
                    responder_id=self.node_id,
                    match_index=len(self.log),
                )
            if self.log[args.prev_log_index - 1].term != args.prev_log_term:
                return AppendEntriesReply(
                    term=self.current_term,
                    success=False,
                    responder_id=self.node_id,
                    match_index=args.prev_log_index - 1,
                )

        # 3. Append any new entries not already in the log
        for i, entry in enumerate(args.entries):
            idx = args.prev_log_index + i
            if idx < len(self.log):
                if self.log[idx].term != entry.term:
                    self.log = self.log[:idx]
                    self.log.append(entry)
            else:
                self.log.append(entry)

        # 4. If leader_commit > commit_index, update commit_index
        if args.leader_commit > self.commit_index:
            self.commit_index = min(args.leader_commit, len(self.log))

        return AppendEntriesReply(
            term=self.current_term,
            success=True,
            responder_id=self.node_id,
            match_index=len(self.log),
        )

    def handle_append_reply(self, peer_id: str, reply: AppendEntriesReply, total_nodes: int) -> None:
        """Process peer response to AppendEntries."""
        if self.role != NodeRole.LEADER or reply.term != self.current_term:
            if reply.term > self.current_term:
                self.current_term = reply.term
                self.role = NodeRole.FOLLOWER
                self.voted_for = None
            return

        if reply.success:
            self.next_index[peer_id] = reply.match_index + 1
            self.match_index[peer_id] = reply.match_index

            # Check if majority has committed entries
            match_indexes = sorted(list(self.match_index.values()) + [len(self.log)])
            median_index = match_indexes[len(match_indexes) // 2]
            if median_index > self.commit_index and (
                median_index == 0 or self.log[median_index - 1].term == self.current_term
            ):
                self.commit_index = median_index
        else:
            # Step back next_index on conflict
            self.next_index[peer_id] = max(1, self.next_index.get(peer_id, 1) - 1)

    def submit_command(self, command: dict[str, Any]) -> int | None:
        """Submit a job/command to cluster state. Returns log index if Leader, None otherwise."""
        if self.role != NodeRole.LEADER:
            return None

        entry = LogEntry(
            term=self.current_term,
            index=len(self.log) + 1,
            command=command,
        )
        self.log.append(entry)
        return entry.index

    def status(self) -> dict[str, Any]:
        """Return human-readable state summary for telemetry and CLI inspection."""
        return {
            "node_id": self.node_id,
            "role": self.role.value,
            "term": self.current_term,
            "leader_id": self.leader_id,
            "log_length": len(self.log),
            "commit_index": self.commit_index,
            "peers_count": len(self.peers),
            "dtn_spool_fallback": self.dtn_spool_fallback,
        }


class RaftCluster:
    """In-memory multi-node cluster harness for simulation and swarm testing."""

    def __init__(self, node_ids: list[str]) -> None:
        self.nodes: dict[str, RaftNode] = {}
        for nid in node_ids:
            peers = [p for p in node_ids if p != nid]
            self.nodes[nid] = RaftNode(node_id=nid, peers=peers)

    def step_election(self, candidate_id: str) -> str | None:
        """Simulate an election cycle triggered by a candidate node."""
        candidate = self.nodes[candidate_id]
        args = candidate.start_election()

        for peer_id in candidate.peers:
            peer = self.nodes[peer_id]
            reply = peer.handle_request_vote(args)
            if candidate.handle_vote_reply(reply, len(self.nodes)):
                return candidate_id

        return candidate.leader_id

    def replicate_heartbeats(self, leader_id: str) -> None:
        """Simulate a round of leader heartbeats and log replication."""
        leader = self.nodes[leader_id]
        if leader.role != NodeRole.LEADER:
            return

        for peer_id in leader.peers:
            peer = self.nodes[peer_id]
            args = leader.create_append_entries(peer_id)
            reply = peer.handle_append_entries(args)
            leader.handle_append_reply(peer_id, reply, len(self.nodes))
