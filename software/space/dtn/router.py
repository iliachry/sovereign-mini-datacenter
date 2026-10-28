"""
Sovereign Mini Datacenter - DTN Store-and-Forward Router
Manages persistent bundle queues, priorities, and opportunistic sync.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from typing import Any

from .bundle import Bundle, BundlePriority

logger = logging.getLogger("dtn.router")


class DTNRouter:
    def __init__(
        self,
        db_path: str = "/tmp/dtn_spool.db",
        local_node_eid: str = "dtn://smdc-node-01.sovereign.space",
    ) -> None:
        self.db_path = db_path
        self.local_node_eid = local_node_eid
        self._lock = threading.RLock()
        self._memory_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._init_db()

    def close(self) -> None:
        """Closes any persistent in-memory database connection."""
        with self._lock:
            if self._memory_conn is not None:
                self._memory_conn.close()
                self._memory_conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @contextlib.contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            try:
                yield self._memory_conn
                self._memory_conn.commit()
            except Exception:
                self._memory_conn.rollback()
                raise
            return

        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_spool (
                    bundle_id TEXT PRIMARY KEY,
                    source_eid TEXT NOT NULL,
                    destination_eid TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    creation_timestamp REAL NOT NULL,
                    lifetime_seconds INTEGER NOT NULL,
                    fragment_offset INTEGER NOT NULL,
                    total_length INTEGER NOT NULL,
                    serialized_data BLOB NOT NULL,
                    retries INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'QUEUED'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_fragments (
                    bundle_id TEXT,
                    fragment_offset INTEGER,
                    total_length INTEGER,
                    payload BLOB,
                    received_at REAL,
                    PRIMARY KEY (bundle_id, fragment_offset)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delivered_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    delivered_at REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spool_prio ON outbound_spool (priority DESC, creation_timestamp ASC)"
            )

    def queue_bundle(self, bundle: Bundle) -> bool:
        """Enqueues a bundle into the persistent store-and-forward spool."""
        if bundle.is_expired():
            logger.warning("Rejecting expired bundle: %s", bundle.bundle_id)
            return False

        serialized = bundle.serialize()
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO outbound_spool
                (bundle_id, source_eid, destination_eid, priority, creation_timestamp, lifetime_seconds, fragment_offset, total_length, serialized_data, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED')
                ON CONFLICT(bundle_id) DO UPDATE SET
                    serialized_data = excluded.serialized_data,
                    priority = excluded.priority,
                    status = 'QUEUED'
                """,
                (
                    bundle.bundle_id,
                    bundle.source_eid,
                    bundle.destination_eid,
                    bundle.priority,
                    bundle.creation_timestamp,
                    bundle.lifetime_seconds,
                    bundle.fragment_offset,
                    bundle.total_application_data_length,
                    serialized,
                ),
            )
        logger.info("Queued bundle %s [Prio %s] -> %s", bundle.bundle_id, bundle.priority, bundle.destination_eid)
        return True

    def get_outbound_queue(self, max_bytes: int = 10 * 1024 * 1024) -> list[Bundle]:
        """Fetches prioritized bundles to transmit during an upcoming satellite pass."""
        if max_bytes <= 0:
            return []

        bundles: list[Bundle] = []
        accumulated_bytes = 0
        now = time.time()

        with self._lock, self._get_connection() as conn:
            conn.execute(
                "DELETE FROM outbound_spool WHERE (creation_timestamp + lifetime_seconds) < ?",
                (now,),
            )
            cursor = conn.execute(
                """
                SELECT serialized_data FROM outbound_spool
                WHERE status = 'QUEUED'
                ORDER BY priority DESC, creation_timestamp ASC
                """
            )
            for row in cursor.fetchall():
                raw = row[0]
                if accumulated_bytes + len(raw) > max_bytes and bundles:
                    break
                try:
                    b = Bundle.deserialize(raw)
                    bundles.append(b)
                    accumulated_bytes += len(raw)
                except Exception as e:
                    logger.error("Failed to deserialize spool bundle: %s", e)

        return bundles

    def mark_delivered(self, bundle_id: str) -> None:
        """Removes bundle from spool after successful transmission confirmation."""
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM outbound_spool WHERE bundle_id = ?", (bundle_id,))
            conn.execute(
                "INSERT OR REPLACE INTO delivered_bundles (bundle_id, delivered_at) VALUES (?, ?)",
                (bundle_id, time.time()),
            )

    def purge_expired(self) -> None:
        """Purges expired bundles from the spool database."""
        now = time.time()
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                DELETE FROM outbound_spool
                WHERE (creation_timestamp + lifetime_seconds) < ?
                """,
                (now,),
            )

    def get_queue_stats(self) -> dict[str, Any]:
        """Returns statistics on active queues and priorities."""
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*), coalesce(sum(length(serialized_data)), 0) FROM outbound_spool WHERE status = 'QUEUED'"
            )
            count, total_bytes = cur.fetchone()

            cur.execute("SELECT priority, count(*) FROM outbound_spool WHERE status = 'QUEUED' GROUP BY priority")
            prio_counts = {row[0]: row[1] for row in cur.fetchall()}

        return {
            "queued_bundle_count": count,
            "total_spool_bytes": total_bytes,
            "priority_counts": {
                "critical": prio_counts.get(BundlePriority.CRITICAL, 0),
                "expedited": prio_counts.get(BundlePriority.EXPEDITED, 0),
                "normal": prio_counts.get(BundlePriority.NORMAL, 0),
                "bulk": prio_counts.get(BundlePriority.BULK, 0),
            },
        }
