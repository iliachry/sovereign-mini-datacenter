"""5G Network Slicing and Software-Defined Networking (SDN) traffic isolation engine.

Simulates 3 parallel 5G slices:
1. URLLC: Sub-1ms UAV flight command execution (10 Mbps, 99.999% reliability).
2. eMBB: 100-200 Mbps XR 3D viewport streaming (127 Mbps, 15ms latency).
3. mMTC: 10,000+ concurrent IoT sensor connections (5 Mbps, NOMA scheduling).

Enforces mathematical bandwidth isolation: T_tx = (D * 8) / B_slice * 1000 ms.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SliceType(StrEnum):
    URLLC = "URLLC"  # Ultra-Reliable Low-Latency Communication
    EMBB = "eMBB"  # Enhanced Mobile Broadband
    MMTC = "mMTC"  # Massive Machine-Type Communication


@dataclass
class NetworkSlice:
    """Represents a dedicated 5G NR network slice profile."""

    slice_type: SliceType
    allocated_bandwidth_mbps: float
    target_latency_ms: float
    target_reliability_pct: float
    current_active_connections: int
    total_bytes_transmitted: int = 0
    total_packets_transmitted: int = 0
    packet_loss_rate: float = 0.00001
    avg_latency_ms: float = 0.8

    def calculate_transmission_time_ms(self, data_size_mb: float) -> float:
        """Calculates transmission delay with strict bandwidth isolation.

        Formula: T_tx = (D * 8) / B_slice * 1000 ms
        """
        if self.allocated_bandwidth_mbps <= 0:
            return float("inf")
        return (data_size_mb * 8.0) / self.allocated_bandwidth_mbps * 1000.0


@dataclass
class Packet:
    """Network packet routed through the 5G slicing infrastructure."""

    packet_id: str
    slice_type: SliceType
    payload_size_bytes: int
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    priority: int = 1
    transmitted: bool = False
    latency_ms: float = 0.0


class NetworkSlicingManager:
    """Manages 5G network slicing, QoS isolation, and SDN packet routing."""

    def __init__(self) -> None:
        self.slices: dict[SliceType, NetworkSlice] = {
            SliceType.URLLC: NetworkSlice(
                slice_type=SliceType.URLLC,
                allocated_bandwidth_mbps=10.0,
                target_latency_ms=1.0,
                target_reliability_pct=99.999,
                current_active_connections=4,
                avg_latency_ms=0.8,
            ),
            SliceType.EMBB: NetworkSlice(
                slice_type=SliceType.EMBB,
                allocated_bandwidth_mbps=127.0,
                target_latency_ms=15.0,
                target_reliability_pct=99.9,
                current_active_connections=18,
                avg_latency_ms=15.2,
            ),
            SliceType.MMTC: NetworkSlice(
                slice_type=SliceType.MMTC,
                allocated_bandwidth_mbps=5.0,
                target_latency_ms=100.0,
                target_reliability_pct=99.0,
                current_active_connections=12000,
                avg_latency_ms=85.0,
            ),
        }
        self.transmission_log: list[Packet] = []

    def transmit_packet(
        self,
        slice_type: SliceType,
        payload_size_bytes: int,
        priority: int = 1,
    ) -> Packet:
        """Inspects, queues, and transmits a packet across its designated slice."""
        s = self.slices[slice_type]
        data_size_mb = payload_size_bytes / (1024.0 * 1024.0)

        # Calculate isolated transmission delay
        tx_delay_ms = s.calculate_transmission_time_ms(data_size_mb)
        total_latency_ms = s.avg_latency_ms + tx_delay_ms

        packet = Packet(
            packet_id=f"pkt_{int(time.time() * 1000)}_{len(self.transmission_log) + 1}",
            slice_type=slice_type,
            payload_size_bytes=payload_size_bytes,
            priority=priority,
            transmitted=True,
            latency_ms=round(total_latency_ms, 3),
        )

        s.total_bytes_transmitted += payload_size_bytes
        s.total_packets_transmitted += 1
        self.transmission_log.append(packet)
        return packet

    def transmit_uav_control_command(self, action_vector: tuple[float, float, float]) -> Packet:
        """Transmits urgent UAV positioning movement command via priority URLLC slice."""
        # 128 bytes command payload
        return self.transmit_packet(SliceType.URLLC, payload_size_bytes=128, priority=0)

    def transmit_xr_frame(self, frame_size_bytes: int = 250000) -> Packet:
        """Transmits 3D WebGL / XR viewport frame over high-throughput eMBB slice."""
        return self.transmit_packet(SliceType.EMBB, payload_size_bytes=frame_size_bytes, priority=2)

    def ingest_iot_sensor_batch(self, sensor_count: int = 180, bytes_per_sensor: int = 64) -> Packet:
        """Ingests mass IoT telemetry stream over mMTC slice."""
        return self.transmit_packet(SliceType.MMTC, payload_size_bytes=sensor_count * bytes_per_sensor, priority=3)

    def get_summary(self) -> dict[str, Any]:
        """Returns JSON-serializable status of all 5G network slices."""
        return {
            s_type.value: {
                "allocated_bandwidth_mbps": s.allocated_bandwidth_mbps,
                "target_latency_ms": s.target_latency_ms,
                "avg_latency_ms": s.avg_latency_ms,
                "target_reliability_pct": s.target_reliability_pct,
                "active_connections": s.current_active_connections,
                "total_mb_transmitted": round(s.total_bytes_transmitted / (1024.0 * 1024.0), 3),
                "total_packets": s.total_packets_transmitted,
            }
            for s_type, s in self.slices.items()
        }
