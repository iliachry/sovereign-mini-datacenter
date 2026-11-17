"""
Sovereign Mini Datacenter — Model Context Protocol (MCP) Resources Registry.
Exposes real-time system telemetry snapshots, hardware manifests, dynamic pricing catalogs,
space DTN bundle queues, and PQC security profiles as standardized MCP resources.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sovereign_dc import __version__
from sovereign_dc.config import get_config
from sovereign_dc.hal.gpu import detect_gpus
from sovereign_dc.hal.power import read_power
from sovereign_dc.hal.storage import detect_storage
from sovereign_dc.hal.thermal import read_thermal
from sovereign_dc.jsonutil import dumps_strict


@dataclass
class MCPResource:
    """Represents a Model Context Protocol resource definition."""

    uri: str
    name: str
    description: str
    mime_type: str
    reader: Callable[[], str]


# --- Resource Readers ---


def read_telemetry_resource() -> str:
    """Returns a JSON string snapshot of hardware telemetry, with its measured/simulated provenance."""
    power = read_power(exporter_url=get_config().power_exporter_url, simulation=get_config().is_simulation())
    thermal = read_thermal(exporter_url=get_config().power_exporter_url, simulation=get_config().is_simulation())
    storage = detect_storage()
    gpus = detect_gpus()

    data = {
        "uri": "smdc://telemetry/current",
        "timestamp": time.time(),
        "telemetry_simulated": bool(power.is_simulated or thermal.is_simulated),
        "power": {
            "battery_soc_percent": power.battery_soc,
            "battery_voltage_volts": power.battery_voltage,
            "solar_pv_watts": power.solar_watts,
            "system_load_watts": power.system_load_watts,
            "net_power_watts": power.net_power,
            "load_shedding_active": power.load_shedding_active,
            "is_simulated": bool(power.is_simulated),
        },
        "thermal": {
            "coolant_celsius": thermal.coolant_celsius,
            "rack_inlet_celsius": thermal.rack_inlet_celsius,
            "rack_exhaust_celsius": thermal.rack_exhaust_celsius,
            "is_overtemp": thermal.is_overtemp,
            "is_simulated": bool(thermal.is_simulated),
        },
        "storage": {
            "total_gb": storage.total_gb,
            "used_gb": storage.used_gb,
            "free_gb": storage.free_gb,
            "usage_percent": round(storage.usage_percent, 1),
        },
        "gpu_count": len(gpus),
    }
    return dumps_strict(data, indent=2)


def read_manifest_resource() -> str:
    """Returns a JSON string detailing hardware BOM, specifications, and cluster roles."""
    cfg = get_config()
    data = {
        "uri": "smdc://system/manifest",
        "name": "Sovereign Mini Datacenter 9U",
        "version": __version__,
        "node_id": cfg.node_id,
        "role": cfg.node_role,
        "specifications": {
            "chassis": '9U 19" EIA-310-D Anodized Aluminum Enclosure',
            "compute": "Dual NVIDIA DGX Spark (GB10 Superchip, 128GB unified memory each)",
            "host_cpu": "AMD EPYC 4004 (8C/16T, 64GB DDR5 ECC)",
            "storage": "4TB usable / 8TB raw NVMe PCIe 4.0 (2×4TB RAID-1 ZFS Mirror)",
            "energy_storage": "10.24 kWh LiFePO4 (2x 48V 100Ah Smart BMS)",
            "solar_array": "3,280W Monocrystalline PERC PV (8x 410W, 4S2P)",
            "power_conversion": "Victron MultiPlus-II 48/3000 + SmartSolar MPPT 250/70",
            "liquid_cooling": "System-host loop: single 360mm radiator with 1-Wire DS18B20 probes (DGX Spark units are air-cooled, three rear exhaust fans)",
            "space_link": "RFC 9171 BPv7 Delay-Tolerant Network Terminal (S-Band / Ku-Band)",
        },
    }
    return dumps_strict(data, indent=2)


def read_market_resource() -> str:
    """Returns a JSON string of current solar-aware compute and relay pricing."""
    from sovereign_dc.economy.market import ComputeMarket, ServiceType

    power = read_power(exporter_url=get_config().power_exporter_url, simulation=get_config().is_simulation())
    market = ComputeMarket()
    mult, status = market.get_dynamic_multiplier(power.battery_soc, power.solar_watts)
    catalog = {
        st.value: market.calculate_quote(
            st, 1.0, battery_soc=power.battery_soc, solar_power_w=power.solar_watts
        ).to_dict()
        for st in ServiceType
    }

    data = {
        "uri": "smdc://economy/market",
        "timestamp": time.time(),
        "battery_soc": power.battery_soc,
        "solar_watts": power.solar_watts,
        "telemetry_simulated": bool(power.is_simulated),
        "multiplier": mult,
        "market_status": status,
        "catalog": catalog,
        "currency": "SOV",
    }
    return dumps_strict(data, indent=2)


def read_dtn_spool_resource() -> str:
    """Returns a JSON string of the DTN store-and-forward bundle queue status."""
    from sovereign_dc.space.dtn.router import DTNRouter

    cfg = get_config()
    router = DTNRouter(db_path=cfg.dtn_db_path)
    stats = router.get_queue_stats()

    data = {
        "uri": "smdc://space/dtn/spool",
        "timestamp": time.time(),
        "dtn_protocol": "RFC 9171 BPv7",
        "queue_stats": stats,
    }
    return dumps_strict(data, indent=2)


_PQC_SELF_TEST_MESSAGE = b"SMDC_MCP_PQC_STATUS_SELF_TEST"
# Round-trip results per algorithm for this process. Only passes are cached; a failing
# engine is re-tested on the next read.
_PQC_SELF_TEST_PASSED: set[str] = set()


def _pqc_self_tests() -> dict[str, str]:
    """Runs (once per process) a round-trip self-test of each implemented PQC algorithm.

    Returns ``{algorithm name: "PASSED" | "FAILED"}`` for the four engines the package
    implements: ML-DSA-65/87 (keygen, sign, verify) and ML-KEM-768/1024 (keygen,
    encapsulate, decapsulate).
    """
    from sovereign_dc.security.pqc import PQCKEM, PQCAlgorithm, PQCSigner

    results: dict[str, str] = {}
    for algorithm in (
        PQCAlgorithm.ML_DSA_65,
        PQCAlgorithm.ML_DSA_87,
        PQCAlgorithm.ML_KEM_768,
        PQCAlgorithm.ML_KEM_1024,
    ):
        name = algorithm.value
        passed = name in _PQC_SELF_TEST_PASSED
        if not passed:
            try:
                if algorithm in (PQCAlgorithm.ML_DSA_65, PQCAlgorithm.ML_DSA_87):
                    signer = PQCSigner(algorithm)
                    keypair = signer.generate_keypair()
                    signature = signer.sign(_PQC_SELF_TEST_MESSAGE, keypair.private_key)
                    passed = signer.verify(_PQC_SELF_TEST_MESSAGE, signature, keypair.public_key)
                else:
                    kem = PQCKEM(algorithm)
                    keypair = kem.generate_keypair()
                    ciphertext, shared_client = kem.encapsulate(keypair.public_key)
                    passed = kem.decapsulate(ciphertext, keypair.private_key) == shared_client
            except Exception:
                passed = False
            if passed:
                _PQC_SELF_TEST_PASSED.add(name)
        results[name] = "PASSED" if passed else "FAILED"
    return results


def read_pqc_status_resource() -> str:
    """Returns a JSON string with what is verified about the PQC engines on this node.

    Only two things are measured: which algorithms the package implements and whether each
    one passes a functional round-trip self-test in this process. No compliance assessment
    is performed, no key or transport policy is inspected, and nothing here shows that PQC
    is enforced on any link, so unassessed standards are reported as ``not assessed``.
    """
    self_tests = _pqc_self_tests()
    all_passed = all(result == "PASSED" for result in self_tests.values())
    data = {
        "uri": "smdc://security/pqc/status",
        "timestamp": time.time(),
        "status": "SELF_TEST_PASSED" if all_passed else "SELF_TEST_FAILED",
        "status_basis": "functional round-trip self-test of each implemented algorithm; not a compliance audit",
        "self_tests": self_tests,
        "algorithms": {
            "digital_signatures": {
                "primary": "NIST FIPS 204 ML-DSA-87 (Category 5 lattice signature)",
                "secondary": "NIST FIPS 204 ML-DSA-65 (Category 3 lattice signature)",
                "legacy": "Ed25519 (RFC 8032)",
            },
            "key_encapsulation": {
                "primary": "NIST FIPS 203 ML-KEM-1024 (Category 5 lattice KEM)",
                "secondary": "NIST FIPS 203 ML-KEM-768 (Category 3 lattice KEM)",
            },
        },
        # Algorithms are implemented to these specifications; conformance (e.g. ACVP/CAVP
        # validation) and Zero Trust posture are not measured by this software.
        "implemented_specifications": ["NIST FIPS 203 (ML-KEM)", "NIST FIPS 204 (ML-DSA)"],
        "compliance_standards": {
            "NIST FIPS 203": "not assessed (implemented; self-test only, no CAVP validation)",
            "NIST FIPS 204": "not assessed (implemented; self-test only, no CAVP validation)",
            "NIST SP 800-207 Zero Trust": "not assessed",
        },
        "enforcement": "not assessed",
    }
    return dumps_strict(data, indent=2)


# --- Registry ---


def get_mcp_resources() -> list[MCPResource]:
    """Returns the full list of registered MCP resources."""
    return [
        MCPResource(
            uri="smdc://telemetry/current",
            name="Current Hardware Telemetry",
            description="Snapshot of battery SoC, solar harvest, AC load, coolant temps, and GPU count; is_simulated marks simulator-generated readings.",
            mime_type="application/json",
            reader=read_telemetry_resource,
        ),
        MCPResource(
            uri="smdc://system/manifest",
            name="Micro-Datacenter Hardware Manifest",
            description="Detailed Bill of Materials (BOM), compute specifications, energy capacity, and cluster config.",
            mime_type="application/json",
            reader=read_manifest_resource,
        ),
        MCPResource(
            uri="smdc://economy/market",
            name="Solar-Aware Pricing Catalog",
            description="Live pricing oracle rates for LLM inference, embedding generation, and Space DTN relays.",
            mime_type="application/json",
            reader=read_market_resource,
        ),
        MCPResource(
            uri="smdc://space/dtn/spool",
            name="Space DTN Bundle Spool",
            description="Persistent store-and-forward queue status for satellite communications.",
            mime_type="application/json",
            reader=read_dtn_spool_resource,
        ),
        MCPResource(
            uri="smdc://security/pqc/status",
            name="Post-Quantum Cryptography Status",
            description="Implemented NIST FIPS 203/204 algorithms and their round-trip self-test results (no compliance assessment).",
            mime_type="application/json",
            reader=read_pqc_status_resource,
        ),
        MCPResource(
            uri="smdc://enterprise/apps",
            name="Enterprise Applications Manifest & States",
            description="Live catalog of onboarded enterprise applications, running states, and power shedding allocations.",
            mime_type="application/json",
            reader=read_enterprise_apps_resource,
        ),
        MCPResource(
            uri="smdc://enterprise/schema",
            name="Enterprise Manifest Specification Schema",
            description="JSON schema definition and field constraints for smdc-app.yaml manifests.",
            mime_type="application/json",
            reader=read_enterprise_schema_resource,
        ),
        MCPResource(
            uri="smdc://metaverse/uav/status",
            name="Metaverse UAV Positioning & DT Telemetry",
            description="Simulated 3D UAV coordinates, per-receiver SINR values and DePIN SLA status of this process's orchestrator (latest cycle; reading runs none).",
            mime_type="application/json",
            reader=read_metaverse_uav_status_resource,
        ),
        MCPResource(
            uri="smdc://metaverse/5g/slices",
            name="5G Network Slices QoS & Isolation",
            description="Simulated bandwidth allocations, target vs avg latencies, and reliability figures for URLLC/eMBB/mMTC.",
            mime_type="application/json",
            reader=read_metaverse_slices_resource,
        ),
    ]


def read_metaverse_uav_status_resource() -> str:
    """Returns the UAV position and the latest ray-tracing trace of this process's orchestrator.

    Reading never advances the simulation. Like ``smdc://metaverse/5g/slices`` it reports the
    process's own orchestrator singleton with a ``source``: ``"process_orchestrator"`` once
    a cycle has run here, else ``"fresh_simulation"`` (the trace fields are then null and
    ``uav_position`` is the initial position). The MCP stdio server is normally a separate
    process from the dashboard and does not see the dashboard's cycles.
    """
    from sovereign_dc.web.dashboard import get_metaverse_orchestrator

    orch = get_metaverse_orchestrator()
    cycles = int(orch.total_cycles_executed)
    trace = orch.traces[-1] if orch.traces else None
    status: dict[str, Any] = {
        "uri": "smdc://metaverse/uav/status",
        "timestamp": time.time(),
        "source": "process_orchestrator" if cycles > 0 else "fresh_simulation",
        "total_cycles_executed": cycles,
        "simulated": True,
        "uav_position": orch.uav_position,
        "per_receiver_sinr": trace.per_receiver_sinr if trace else None,
        "sum_capacity_bps_hz": trace.sum_capacity_bps_hz if trace else None,
        "decision_latency_ms": trace.decision_latency_ms if trace else None,
        "depin_sla": {
            "valid": trace.sla_result.valid,
            "block_hash": trace.sla_result.block_hash,
            "signatures_count": trace.sla_result.validator_signatures_count,
        }
        if trace
        else None,
    }
    if trace is None:
        status["note"] = (
            "No simulation cycle has run in this process's orchestrator, so there is no trace "
            "to report. A dashboard running in another process keeps its own state: read it "
            "from its /api/metaverse/status endpoint."
        )
    return dumps_strict(status, indent=2)


def read_metaverse_slices_resource() -> str:
    """Returns the simulated 5G slice figures of this process's orchestrator, with their ``source``."""
    from sovereign_dc.mcp.tools import get_5g_slices_snapshot

    return dumps_strict({"uri": "smdc://metaverse/5g/slices", **get_5g_slices_snapshot()}, indent=2)


def read_enterprise_apps_resource() -> str:
    """Returns a JSON string snapshot of all registered enterprise applications."""
    from sovereign_dc.mcp.tools import get_enterprise_manager

    manager = get_enterprise_manager()
    states = manager.list_runtime_states()
    return dumps_strict(
        {
            "uri": "smdc://enterprise/apps",
            "timestamp": time.time(),
            "count": len(states),
            "apps": [s.to_dict() for s in states],
        },
        indent=2,
    )


def read_enterprise_schema_resource() -> str:
    """Returns the JSON schema specification for enterprise manifests."""
    from sovereign_dc.enterprise.schema import AppCategory, PowerPriority, RuntimeType

    schema = {
        "uri": "smdc://enterprise/schema",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SMDC Enterprise Application Manifest",
        "type": "object",
        "required": ["name", "app_id", "entrypoint"],
        "properties": {
            "name": {"type": "string", "description": "Display name of application"},
            "app_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{2,63}$"},
            "version": {"type": "string", "default": "1.0.0"},
            "category": {"type": "string", "enum": [c.value for c in AppCategory]},
            "runtime": {"type": "string", "enum": [r.value for r in RuntimeType]},
            "entrypoint": {"type": "string"},
            "power": {
                "type": "object",
                "properties": {
                    "tier": {"type": "string", "enum": [p.value for p in PowerPriority]},
                    "min_battery_soc": {"type": "number", "minimum": 0, "maximum": 100},
                },
            },
        },
    }
    return dumps_strict(schema, indent=2)
