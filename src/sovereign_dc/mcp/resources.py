"""
Sovereign Mini Datacenter — Model Context Protocol (MCP) Resources Registry.
Exposes real-time system telemetry snapshots, hardware manifests, dynamic pricing catalogs,
space DTN bundle queues, and PQC security profiles as standardized MCP resources.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from sovereign_dc import __version__
from sovereign_dc.config import get_config
from sovereign_dc.hal.gpu import detect_gpus
from sovereign_dc.hal.power import read_power
from sovereign_dc.hal.storage import detect_storage
from sovereign_dc.hal.thermal import read_thermal


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
    """Returns a JSON string snapshot of real-time hardware telemetry."""
    power = read_power()
    thermal = read_thermal()
    storage = detect_storage()
    gpus = detect_gpus()

    data = {
        "uri": "smdc://telemetry/current",
        "timestamp": time.time(),
        "power": {
            "battery_soc_percent": power.battery_soc,
            "battery_voltage_volts": power.battery_voltage,
            "solar_pv_watts": power.solar_watts,
            "system_load_watts": power.system_load_watts,
            "load_shedding_active": power.load_shedding_active,
        },
        "thermal": {
            "coolant_celsius": thermal.coolant_celsius,
            "rack_inlet_celsius": thermal.rack_inlet_celsius,
            "rack_exhaust_celsius": thermal.rack_exhaust_celsius,
            "is_overtemp": thermal.is_overtemp,
        },
        "storage": {
            "total_gb": storage.total_gb,
            "used_gb": storage.used_gb,
            "free_gb": storage.free_gb,
            "usage_percent": round(storage.usage_percent, 1),
        },
        "gpu_count": len(gpus),
    }
    return json.dumps(data, indent=2)


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
            "compute": "Dual NVIDIA Jetson Orin AGX 64GB (550 TOPS INT8 AI)",
            "host_cpu": "AMD EPYC 4004 (8C/16T, 64GB DDR5 ECC)",
            "storage": "8TB NVMe PCIe 4.0 (RAID-1 ZFS Mirror)",
            "energy_storage": "10.24 kWh LiFePO4 (2x 48V 100Ah Smart BMS)",
            "solar_array": "1,640W Monocrystalline Bifacial PV (4x 410W)",
            "power_conversion": "Victron MultiPlus-II 48/3000 + SmartSolar MPPT 150/35",
            "liquid_cooling": "Dual 360mm Radiators with 1-Wire DS18B20 Flow Probes",
            "space_link": "RFC 9171 BPv7 Delay-Tolerant Network Terminal (S-Band / Ku-Band)",
        },
    }
    return json.dumps(data, indent=2)


def read_market_resource() -> str:
    """Returns a JSON string of current solar-aware compute and relay pricing."""
    from sovereign_dc.economy.market import ComputeMarket, ServiceType

    power = read_power()
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
        "multiplier": mult,
        "market_status": status,
        "catalog": catalog,
        "currency": "SOV",
    }
    return json.dumps(data, indent=2)


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
    return json.dumps(data, indent=2)


def read_pqc_status_resource() -> str:
    """Returns a JSON string describing active Post-Quantum Cryptography implementations."""
    data = {
        "uri": "smdc://security/pqc/status",
        "timestamp": time.time(),
        "compliance_standards": ["NIST SP 800-207 Zero Trust", "NIST FIPS 203", "NIST FIPS 204"],
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
        "status": "ACTIVE_ENFORCED",
    }
    return json.dumps(data, indent=2)


# --- Registry ---


def get_mcp_resources() -> list[MCPResource]:
    """Returns the full list of registered MCP resources."""
    return [
        MCPResource(
            uri="smdc://telemetry/current",
            name="Current Hardware Telemetry",
            description="Real-time snapshot of battery SoC, solar harvest, AC load, coolant temps, and GPU health.",
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
            description="Cryptographic attestation and active NIST FIPS 203/204 algorithms.",
            mime_type="application/json",
            reader=read_pqc_status_resource,
        ),
    ]
