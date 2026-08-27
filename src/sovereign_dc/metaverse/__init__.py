"""Sovereign Mini Datacenter — Metaverse Framework for Wireless Systems Management.

Based on IEEE Internet of Things Magazine (2026):
Integrates 6 cyber-physical layers: Networking (5G Slicing), IoT, AI (SA-PPO),
Digital Twin (Sionna Ray-Tracing), XR Spatial Digital Shadow, and DePIN Blockchain SLA.
"""

from __future__ import annotations

from sovereign_dc.metaverse.agent import ModelDrivenPPO, SceneAwarePPO
from sovereign_dc.metaverse.benchmark import BenchmarkReport, MetaverseBenchmark
from sovereign_dc.metaverse.depin_sla import DePINSLAValidator, SLAVerificationResult, ValidatorNode
from sovereign_dc.metaverse.engine import MetaverseOrchestrator, SimulationCycleTrace
from sovereign_dc.metaverse.ray_tracer import PropagationResult, RayPath, Receiver, SionnaRayTracer
from sovereign_dc.metaverse.slicing import NetworkSlice, NetworkSlicingManager, Packet, SliceType

__all__ = [
    "BenchmarkReport",
    "DePINSLAValidator",
    "MetaverseBenchmark",
    "MetaverseOrchestrator",
    "ModelDrivenPPO",
    "NetworkSlice",
    "NetworkSlicingManager",
    "Packet",
    "PropagationResult",
    "RayPath",
    "Receiver",
    "SLAVerificationResult",
    "SceneAwarePPO",
    "SimulationCycleTrace",
    "SionnaRayTracer",
    "SliceType",
    "ValidatorNode",
]
