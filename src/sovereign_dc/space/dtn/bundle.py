"""
Sovereign Mini Datacenter - Bundle Protocol v7 (RFC 9171) & DTN Engine
Asynchronous store-and-forward bundle routing for space and orbital links.
"""

import base64
import hashlib
import json
import time
import uuid


class BundlePriority:
    BULK = 0  # AI models, dataset weights, bulk backups
    NORMAL = 1  # Git commits, Nextcloud documents, email
    EXPEDITED = 2  # Solar/BMS telemetry, GPS, health heartbeats
    CRITICAL = 3  # Emergency load-shedding alerts, sentinel triggers, safe-mode


class Bundle:
    def __init__(
        self,
        source_eid: str,
        destination_eid: str,
        payload: bytes,
        priority: int = BundlePriority.NORMAL,
        lifetime_seconds: int = 86400 * 7,  # 7 days default space TTL
        bundle_id: str | None = None,
        creation_timestamp: float | None = None,
        fragment_offset: int = 0,
        total_application_data_length: int | None = None,
    ):
        self.bundle_id = bundle_id or str(uuid.uuid4())
        self.source_eid = source_eid
        self.destination_eid = destination_eid
        self.payload = payload
        self.priority = max(0, min(3, int(priority)))
        self.creation_timestamp = creation_timestamp or time.time()
        self.lifetime_seconds = lifetime_seconds
        self.fragment_offset = fragment_offset
        self.total_application_data_length = total_application_data_length or len(payload)
        self.checksum = self._calculate_checksum()

    def _calculate_checksum(self) -> str:
        h = hashlib.sha256()
        h.update(self.bundle_id.encode("utf-8"))
        h.update(self.source_eid.encode("utf-8"))
        h.update(self.destination_eid.encode("utf-8"))
        h.update(str(self.priority).encode("utf-8"))
        h.update(str(self.creation_timestamp).encode("utf-8"))
        h.update(self.payload)
        return h.hexdigest()

    def is_expired(self) -> bool:
        return time.time() > (self.creation_timestamp + self.lifetime_seconds)

    def is_fragment(self) -> bool:
        return len(self.payload) < self.total_application_data_length

    def serialize(self) -> bytes:
        """Serializes the bundle into a compact transmission payload."""
        data = {
            "v": 7,  # BPv7
            "id": self.bundle_id,
            "src": self.source_eid,
            "dst": self.destination_eid,
            "pri": self.priority,
            "ts": self.creation_timestamp,
            "ttl": self.lifetime_seconds,
            "off": self.fragment_offset,
            "total_len": self.total_application_data_length,
            "payload_b64": base64.b64encode(self.payload).decode("ascii"),
            "chk": self.checksum,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def deserialize(cls, raw_bytes: bytes) -> "Bundle":
        data = json.loads(raw_bytes.decode("utf-8"))
        if data.get("v") != 7:
            raise ValueError(f"Unsupported Bundle Protocol version: {data.get('v')}")

        payload = base64.b64decode(data["payload_b64"])
        bundle = cls(
            source_eid=data["src"],
            destination_eid=data["dst"],
            payload=payload,
            priority=data["pri"],
            lifetime_seconds=data["ttl"],
            bundle_id=data["id"],
            creation_timestamp=data["ts"],
            fragment_offset=data["off"],
            total_application_data_length=data["total_len"],
        )

        expected_chk = data["chk"]
        actual_chk = bundle._calculate_checksum()
        if expected_chk != actual_chk:
            raise ValueError(f"Bundle integrity checksum mismatch: {expected_chk} vs {actual_chk}")

        return bundle

    def create_fragments(self, max_fragment_size: int) -> list["Bundle"]:
        """Splits a large bundle into MTU-sized fragments for short satellite passes."""
        if len(self.payload) <= max_fragment_size:
            return [self]

        fragments = []
        total_len = len(self.payload)
        offset = 0

        while offset < total_len:
            chunk = self.payload[offset : offset + max_fragment_size]
            frag = Bundle(
                source_eid=self.source_eid,
                destination_eid=self.destination_eid,
                payload=chunk,
                priority=self.priority,
                lifetime_seconds=self.lifetime_seconds,
                bundle_id=f"{self.bundle_id}-frag-{offset}",
                creation_timestamp=self.creation_timestamp,
                fragment_offset=offset,
                total_application_data_length=total_len,
            )
            fragments.append(frag)
            offset += len(chunk)

        return fragments
