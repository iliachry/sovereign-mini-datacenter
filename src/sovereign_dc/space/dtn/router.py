"""
Sovereign Mini Datacenter - DTN Store-and-Forward Router
Manages persistent bundle queues, priorities, and opportunistic sync.
"""

import os
import sqlite3
import time
import logging
from typing import List, Optional, Dict, Any
from .bundle import Bundle, BundlePriority

logger = logging.getLogger("dtn.router")

class DTNRouter:
    def __init__(self, db_path: str = "/tmp/dtn_spool.db", local_node_eid: str = "dtn://smdc-node-01.sovereign.space"):
        self.db_path = db_path
        self.local_node_eid = local_node_eid
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
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
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbound_fragments (
                    bundle_id TEXT,
                    fragment_offset INTEGER,
                    total_length INTEGER,
                    payload BLOB,
                    received_at REAL,
                    PRIMARY KEY (bundle_id, fragment_offset)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS delivered_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    delivered_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spool_prio ON outbound_spool (priority DESC, creation_timestamp ASC)")

    def queue_bundle(self, bundle: Bundle) -> bool:
        """Enqueues a bundle into the persistent store-and-forward spool."""
        if bundle.is_expired():
            logger.warning(f"Rejecting expired bundle: {bundle.bundle_id}")
            return False

        serialized = bundle.serialize()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO outbound_spool 
                (bundle_id, source_eid, destination_eid, priority, creation_timestamp, lifetime_seconds, fragment_offset, total_length, serialized_data, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED')
            """, (
                bundle.bundle_id,
                bundle.source_eid,
                bundle.destination_eid,
                bundle.priority,
                bundle.creation_timestamp,
                bundle.lifetime_seconds,
                bundle.fragment_offset,
                bundle.total_application_data_length,
                serialized
            ))
        logger.info(f"Queued bundle {bundle.bundle_id} [Prio {bundle.priority}] -> {bundle.destination_eid}")
        return True

    def get_outbound_queue(self, max_bytes: int = 10 * 1024 * 1024) -> List[Bundle]:
        """Fetches prioritized bundles to transmit during an upcoming satellite pass."""
        self.purge_expired()
        bundles = []
        accumulated_bytes = 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT serialized_data FROM outbound_spool
                WHERE status = 'QUEUED'
                ORDER BY priority DESC, creation_timestamp ASC
            """)
            for row in cursor.fetchall():
                raw = row[0]
                if accumulated_bytes + len(raw) > max_bytes and bundles:
                    break
                try:
                    b = Bundle.deserialize(raw)
                    bundles.append(b)
                    accumulated_bytes += len(raw)
                except Exception as e:
                    logger.error(f"Failed to deserialize spool bundle: {e}")

        return bundles

    def mark_delivered(self, bundle_id: str):
        """Removes bundle from spool after successful transmission confirmation."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM outbound_spool WHERE bundle_id = ?", (bundle_id,))
            conn.execute("INSERT OR REPLACE INTO delivered_bundles (bundle_id, delivered_at) VALUES (?, ?)", (bundle_id, time.time()))

    def purge_expired(self):
        """Purges expired bundles from the spool database."""
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM outbound_spool 
                WHERE (creation_timestamp + lifetime_seconds) < ?
            """, (now,))

    def get_queue_stats(self) -> Dict[str, Any]:
        """Returns statistics on active queues and priorities."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*), coalesce(sum(length(serialized_data)), 0) FROM outbound_spool WHERE status = 'QUEUED'")
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
            }
        }
