"""Decentralized Multi-Node Raft Consensus Engine for Sovereign Datacenter Swarms.

Implements leader election, log replication, and cluster state synchronization
over WireGuard mesh networks (100.64.0.0/10) with automatic fallback to
RFC 9171 Space Delay-Tolerant Networking (BPv7) on network partition.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger("sovereign_dc.mesh.consensus")


class NotLeaderError(RuntimeError):
    """Raised when a node that is not the leader is asked to produce leader-only RPCs."""


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

        # A cluster whose majority is the node itself (single-node) wins immediately:
        # there is no peer whose vote reply could otherwise trigger the transition.
        if len(self.votes_received) >= ((len(self.peers) + 1) // 2) + 1:
            self.become_leader()

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
                # The leader of the newer term is not known yet; in particular a demoted
                # leader must stop naming itself.
                self.leader_id = None
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
        """Generate AppendEntries RPC payload for a specific peer.

        Only a leader may replicate. Raises NotLeaderError otherwise: a node that was demoted
        by a higher-term reply still holds its old log, and sending that log under the newly
        learned term would let it overwrite entries the real leader has already committed.
        """
        if self.role != NodeRole.LEADER:
            raise NotLeaderError(
                f"{self.node_id} is {self.role.value} in term {self.current_term} and cannot send AppendEntries"
            )
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

        # At most one leader exists per term and a node never replicates to itself, so an
        # AppendEntries naming this node, or reaching a node that is itself the leader of
        # that term, cannot come from a legitimate leader. Refuse it without changing state.
        if args.leader_id == self.node_id or (self.role == NodeRole.LEADER and args.term == self.current_term):
            logger.error(
                "%s refused AppendEntries from %s in term %d: sender is not the leader of that term",
                self.node_id,
                args.leader_id,
                args.term,
            )
            return AppendEntriesReply(
                term=self.current_term,
                success=False,
                responder_id=self.node_id,
                match_index=self.commit_index,
            )

        # Valid leader recognized
        self.reset_election_timeout()
        self.leader_id = args.leader_id

        # Only a genuine term increase clears the vote: a candidate stepping down for a
        # same-term leader must keep voted_for, or it could vote twice in one term.
        if args.term > self.current_term:
            self.current_term = args.term
            self.voted_for = None
        self.role = NodeRole.FOLLOWER

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

        # Raft safety: a committed entry is never removed or replaced. A legitimate leader's
        # log contains every committed entry, so a conflict at or below commit_index proves
        # the sender is not one. Checked before anything is modified.
        for i, entry in enumerate(args.entries):
            idx = args.prev_log_index + i
            if idx >= len(self.log) or idx >= self.commit_index:
                break
            if self.log[idx].term != entry.term:
                logger.error(
                    "%s refused AppendEntries from %s in term %d: it conflicts with committed entry %d",
                    self.node_id,
                    args.leader_id,
                    args.term,
                    idx + 1,
                )
                return AppendEntriesReply(
                    term=self.current_term,
                    success=False,
                    responder_id=self.node_id,
                    match_index=self.commit_index,
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

        # Only the prefix verified by this RPC is known to match the leader's log; any
        # entries beyond it may be stale leftovers from an earlier term.
        last_new_index = args.prev_log_index + len(args.entries)

        # 4. If leader_commit > commit_index, set commit_index = min(leader_commit, last new entry)
        if args.leader_commit > self.commit_index:
            self.commit_index = max(self.commit_index, min(args.leader_commit, last_new_index))

        return AppendEntriesReply(
            term=self.current_term,
            success=True,
            responder_id=self.node_id,
            match_index=last_new_index,
        )

    def handle_append_reply(self, peer_id: str, reply: AppendEntriesReply, total_nodes: int) -> None:
        """Process peer response to AppendEntries."""
        if self.role != NodeRole.LEADER or reply.term != self.current_term:
            if reply.term > self.current_term:
                self.current_term = reply.term
                self.role = NodeRole.FOLLOWER
                self.voted_for = None
                # The leader of the newer term is not known yet; in particular a demoted
                # leader must stop naming itself.
                self.leader_id = None
            return

        if reply.success:
            # match_index is monotonic: a delayed/reordered reply must not move it backwards.
            self.match_index[peer_id] = max(self.match_index.get(peer_id, 0), reply.match_index)
            self.next_index[peer_id] = self.match_index[peer_id] + 1
            self._advance_commit_index(total_nodes)
        else:
            # Step back next_index on conflict
            self.next_index[peer_id] = max(1, self.next_index.get(peer_id, 1) - 1)

    def _advance_commit_index(self, total_nodes: int) -> None:
        """Commit the highest index stored on a strict majority of ``total_nodes``.

        Only entries from the leader's current term are committed by counting
        replicas (Raft section 5.4.2); earlier-term entries commit indirectly.
        """
        cluster_size = max(total_nodes, len(self.peers) + 1)
        majority = (cluster_size // 2) + 1
        replicated = sorted([*self.match_index.values(), len(self.log)], reverse=True)
        if len(replicated) < majority:
            return
        quorum_index = replicated[majority - 1]
        if quorum_index > self.commit_index and self.log[quorum_index - 1].term == self.current_term:
            self.commit_index = quorum_index

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

    def step_election(self, candidate_id: str, reachable: set[str] | None = None) -> str | None:
        """Simulate an election cycle triggered by a candidate node.

        ``reachable`` models a network partition: only peers in that set receive the
        RequestVote RPC. Cluster membership (and therefore quorum size) is unchanged.
        """
        candidate = self.nodes[candidate_id]
        args = candidate.start_election()

        for peer_id in candidate.peers:
            if peer_id not in self.nodes or (reachable is not None and peer_id not in reachable):
                continue
            reply = self.nodes[peer_id].handle_request_vote(args)
            if candidate.handle_vote_reply(reply, len(self.nodes)):
                return candidate_id

        return candidate.leader_id

    def replicate_heartbeats(self, leader_id: str, reachable: set[str] | None = None) -> None:
        """Simulate a round of leader heartbeats and log replication."""
        leader = self.nodes[leader_id]
        if leader.role != NodeRole.LEADER:
            return

        for peer_id in leader.peers:
            if leader.role != NodeRole.LEADER:
                # Demoted by a higher-term reply earlier in this round: stop at once. Its log
                # is no longer authoritative and must not reach the remaining peers.
                return
            if peer_id not in self.nodes or (reachable is not None and peer_id not in reachable):
                continue
            args = leader.create_append_entries(peer_id)
            reply = self.nodes[peer_id].handle_append_entries(args)
            leader.handle_append_reply(peer_id, reply, len(self.nodes))
        # A leader with no reachable peers (single-node cluster) still commits on its own majority.
        if leader.role == NodeRole.LEADER:
            leader._advance_commit_index(len(self.nodes))
