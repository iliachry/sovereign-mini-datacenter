"""Sovereign Mini Datacenter — Real-Time Operations Web Dashboard & REST API.

Provides an ultra-portable, zero-external-dependency HTTP server hosting:
1. REST API endpoints for telemetry, space link, mesh cluster, and PQC status.
2. A high-performance, dark-mode glassmorphic single-page operations interface
   for field operators and remote NOC engineers.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as HTTPServer
from typing import Any
from urllib.parse import urlsplit

from sovereign_dc import __version__
from sovereign_dc.config import get_config
from sovereign_dc.events import get_event_bus
from sovereign_dc.hal.power import read_power
from sovereign_dc.hal.storage import detect_storage
from sovereign_dc.hal.thermal import read_thermal
from sovereign_dc.mesh.consensus import RaftCluster
from sovereign_dc.security.pqc import PQCAlgorithm, get_node_pqc_keypair
from sovereign_dc.space.dtn.router import DTNRouter
from sovereign_dc.space.orbital.propagator import GroundStation
from sovereign_dc.space.orbital.tle_updater import get_active_satellites

logger = logging.getLogger("Dashboard")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sovereign Mini Datacenter — Operations Control Center</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #030712;
      --card-bg: rgba(15, 23, 42, 0.75);
      --border: rgba(51, 65, 85, 0.6);
      --accent: #10b981;
      --accent-blue: #38bdf8;
      --accent-purple: #c084fc;
      --accent-orange: #f59e0b;
      --accent-red: #ef4444;
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Space Grotesk', sans-serif;
      padding: 24px;
      min-height: 100vh;
      background-image: radial-gradient(circle at 10% 20%, rgba(16, 185, 129, 0.08) 0%, transparent 40%),
                        radial-gradient(circle at 90% 80%, rgba(56, 189, 248, 0.08) 0%, transparent 40%);
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }
    .logo-title {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .logo-icon {
      font-size: 28px;
    }
    h1 { font-size: 24px; font-weight: 700; }
    .badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      padding: 4px 10px;
      border-radius: 9999px;
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid var(--accent);
      color: var(--accent);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 20px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      backdrop-filter: blur(12px);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
      transition: border-color 0.2s;
    }
    .card:hover { border-color: rgba(56, 189, 248, 0.5); }
    .card-title {
      font-size: 14px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .metric-value {
      font-family: 'JetBrains Mono', monospace;
      font-size: 32px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 8px;
    }
    .metric-sub {
      font-size: 13px;
      color: var(--text-muted);
    }
    .gauge-bar {
      height: 8px;
      background: rgba(51, 65, 85, 0.6);
      border-radius: 4px;
      margin-top: 12px;
      overflow: hidden;
    }
    .gauge-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent), var(--accent-blue));
      width: 0%;
      transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .status-row {
      display: flex;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid rgba(51, 65, 85, 0.3);
      font-size: 14px;
    }
    .status-row:last-child { border-bottom: none; }
    .code { font-family: 'JetBrains Mono', monospace; }
    .footer {
      text-align: center;
      color: var(--text-muted);
      font-size: 13px;
      margin-top: 32px;
    }
    .footer a { color: var(--accent-blue); text-decoration: none; }
  </style>
</head>
<body>
  <div class="header">
    <div class="logo-title">
      <div class="logo-icon">⚡</div>
      <div>
        <h1>Sovereign Mini Datacenter</h1>
        <div style="font-size: 13px; color: var(--text-muted);" id="node-id">Connecting to node telemetry...</div>
      </div>
    </div>
    <div style="display: flex; gap: 10px; align-items: center;">
      <span class="badge" id="live-indicator">● LIVE TELEMETRY</span>
      <span class="badge" style="background: rgba(192, 132, 252, 0.15); border-color: var(--accent-purple); color: var(--accent-purple);" id="pqc-badge">PQC ML-DSA-65</span>
    </div>
  </div>

  <div class="grid">
    <!-- Card 1: Energy & Battery -->
    <div class="card">
      <div class="card-title"><span>🔋 Battery Storage</span><span id="shedding-mode" class="code">L0 NOMINAL</span></div>
      <div class="metric-value" id="battery-soc">-- %</div>
      <div class="metric-sub" id="battery-voltage">Voltage: -- V | Load: -- W</div>
      <div class="gauge-bar"><div class="gauge-fill" id="soc-bar"></div></div>
    </div>

    <!-- Card 2: Solar PV Array -->
    <div class="card">
      <div class="card-title"><span>☀️ Solar Generation</span><span id="mppt-status" class="code">MPPT ACTIVE</span></div>
      <div class="metric-value" style="color: var(--accent-blue);" id="solar-watts">-- W</div>
      <div class="metric-sub" id="net-power">Net Flow: -- W</div>
      <div class="gauge-bar"><div class="gauge-fill" style="background: linear-gradient(90deg, #f59e0b, #38bdf8);" id="solar-bar"></div></div>
    </div>

    <!-- Card 3: Thermal Liquid Cooling -->
    <div class="card">
      <div class="card-title"><span>❄️ Liquid Coolant Loop</span><span id="thermal-status" class="code">NORMAL</span></div>
      <div class="metric-value" style="color: var(--accent);" id="coolant-temp">-- °C</div>
      <div class="metric-sub" id="rack-air">Inlet: -- °C | Exhaust: -- °C</div>
      <div class="gauge-bar"><div class="gauge-fill" style="background: var(--accent);" id="temp-bar"></div></div>
    </div>

    <!-- Card 4: Space DTN Communications -->
    <div class="card">
      <div class="card-title"><span>🛰️ Space DTN / BPv7</span><span id="space-link-status" class="code">TRACKING</span></div>
      <div class="metric-value" style="color: var(--accent-purple);" id="dtn-spool">-- Bundles</div>
      <div class="metric-sub" id="satellite-pass">Next Contact: Calculating...</div>
      <div class="gauge-bar"><div class="gauge-fill" style="background: var(--accent-purple);" id="dtn-bar"></div></div>
    </div>
  </div>

  <div class="grid">
    <!-- Card 5: Swarm Mesh Topology -->
    <div class="card">
      <div class="card-title"><span>🌐 Sovereign Mesh Cluster</span><span class="code" id="raft-role">RAFT LEADER</span></div>
      <div id="mesh-nodes-list">
        <div class="status-row"><span>smdc-node-01 (Athens Core)</span><span class="code" style="color: var(--accent);">100.64.0.1 • 2x DGX Spark • ONLINE</span></div>
        <div class="status-row"><span>smdc-node-02 (Alpine Edge)</span><span class="code" style="color: var(--accent);">100.64.0.2 • 275 TOPS • ONLINE</span></div>
        <div class="status-row"><span>smdc-node-03 (Aegean Island)</span><span class="code" style="color: var(--accent-blue);">100.64.0.3 • 275 TOPS • DTN ARMED</span></div>
      </div>
    </div>

    <!-- Card 6: Compute Economy & Wallet -->
    <div class="card">
      <div class="card-title"><span>💰 Compute Economy & Wallet</span><span class="code" id="econ-pricing-mode" style="color: var(--accent);">50% SOLAR DISCOUNT</span></div>
      <div class="metric-value" style="color: var(--accent-orange);" id="wallet-balance">-- Credits</div>
      <div class="metric-sub" id="wallet-addr">Wallet: --</div>
      <div class="status-row" style="margin-top: 10px;"><span>Dynamic Rate Multiplier</span><span class="code" id="econ-multiplier">1.00x</span></div>
      <div class="status-row"><span>Ledger Transactions</span><span class="code" id="econ-tx-count">0 Recorded</span></div>
    </div>

    <!-- Card 7: Storage & Security -->
    <div class="card">
      <div class="card-title"><span>🔐 Storage & Cryptography</span><span class="code">PQC OPERATIONAL</span></div>
      <div class="status-row"><span>NVMe Storage</span><span class="code" id="storage-used">-- GB / -- GB Free</span></div>
      <div class="status-row"><span>PQC Digital Signatures</span><span class="code" style="color: var(--accent);">ML-DSA-87 (FIPS 204)</span></div>
      <div class="status-row"><span>PQC Key Encapsulation</span><span class="code" style="color: var(--accent-purple);">ML-KEM-1024 (FIPS 203)</span></div>
      <div class="status-row"><span>Intrusion Detection</span><span class="code">CrowdSec overlay (not probed)</span></div>
    </div>

    <!-- Card 8: 5G RAN & UAV Metaverse (IEEE IoT Mag 2026) -->
    <div class="card">
      <div class="card-title"><span>📡 5G RAN & UAV Metaverse</span><span class="code" style="color: var(--accent);" id="metaverse-sla-status">SLA NOMINAL</span></div>
      <div class="status-row"><span>Autonomous UAV Pos</span><span class="code" style="color: var(--accent-blue);" id="uav-pos-display">(0.0m, 0.0m, 35.0m)</span></div>
      <div class="status-row"><span>URLLC Slicing Latency</span><span class="code" style="color: var(--accent);">0.80 ms (99.999%)</span></div>
      <div class="status-row"><span>eMBB XR Streaming</span><span class="code" style="color: var(--accent-purple);">127 Mbps (15 ms)</span></div>
      <div class="status-row"><span>mMTC IoT Density</span><span class="code">12,000+ nodes/km²</span></div>
      <div class="status-row" style="margin-top: 10px;">
        <button id="btn-opt-uav" style="background: rgba(16,185,129,0.2); border: 1px solid var(--accent); color: var(--text); padding: 4px 10px; border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 11px;">🎯 Optimize UAV (SA-PPO)</button>
        <button id="btn-stop-uav" style="background: rgba(239,68,68,0.2); border: 1px solid var(--accent-red); color: var(--text); padding: 4px 10px; border-radius: 8px; cursor: pointer; font-family: inherit; font-size: 11px;">🛑 E-Stop</button>
      </div>
    </div>
  </div>

  <div class="footer">
    Sovereign Mini Datacenter v<span id="smdc-version"></span> •
    <a href="https://github.com/iliachry/sovereign-mini-datacenter" target="_blank">GitHub Repository</a> •
    <a href="https://iliachry.gr/sovereign-mini-datacenter/" target="_blank">3D WebGL Digital Twin</a>
  </div>

  <script>
    async function updateTelemetry() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) {
          const live = document.getElementById('live-indicator');
          if (live) { live.textContent = '● TELEMETRY UNAVAILABLE'; live.style.color = 'var(--accent-red)'; }
          return;
        }
        if (!res.ok) return;
        const data = await res.json();

        document.getElementById('node-id').textContent = `Node ID: ${data.node_id} • ${data.role}`;
        document.getElementById('smdc-version').textContent = data.version;

        // Battery
        const soc = data.power.battery_soc || 0;
        document.getElementById('battery-soc').textContent = `${soc.toFixed(1)} %`;
        document.getElementById('battery-voltage').textContent = `Voltage: ${data.power.battery_voltage.toFixed(1)} V | Load: ${data.power.system_load_watts.toFixed(0)} W`;
        document.getElementById('soc-bar').style.width = `${Math.min(100, Math.max(0, soc))}%`;
        document.getElementById('shedding-mode').textContent = data.power.load_shedding_active ? 'L2 SHEDDING' : 'L0 NOMINAL';
        document.getElementById('shedding-mode').style.color = data.power.load_shedding_active ? 'var(--accent-red)' : 'var(--accent)';

        // Solar
        const solar = data.power.solar_watts || 0;
        document.getElementById('solar-watts').textContent = `${solar.toFixed(0)} W`;
        document.getElementById('net-power').textContent = `Net Flow: ${(solar - data.power.system_load_watts).toFixed(0)} W`;
        document.getElementById('solar-bar').style.width = `${Math.min(100, (solar / 3280) * 100)}%`;

        // Telemetry provenance: never present simulated values as live
        const simulated = data.power.is_simulated || data.thermal.is_simulated;
        const live = document.getElementById('live-indicator');
        if (live) {
          live.textContent = simulated ? '● SIMULATED TELEMETRY' : '● LIVE TELEMETRY';
          live.style.color = simulated ? 'var(--accent-orange)' : '';
          live.style.borderColor = simulated ? 'var(--accent-orange)' : '';
        }

        // Thermal
        const coolant = data.thermal.coolant_celsius || 0;
        document.getElementById('coolant-temp').textContent = `${coolant.toFixed(1)} °C`;
        document.getElementById('rack-air').textContent = `Inlet: ${data.thermal.rack_inlet_celsius.toFixed(1)}°C | Exhaust: ${data.thermal.rack_exhaust_celsius.toFixed(1)}°C`;
        document.getElementById('temp-bar').style.width = `${Math.min(100, (coolant / 60) * 100)}%`;

        // Space DTN
        document.getElementById('dtn-spool').textContent = `${data.space.queued_bundles} Bundles`;
        document.getElementById('satellite-pass').textContent = `Next Pass: ${data.space.next_satellite} in ${Math.round(data.space.next_pass_seconds / 60)}m`;
        document.getElementById('dtn-bar').style.width = `${Math.min(100, data.space.queued_bundles * 10)}%`;

        // Economy
        if (data.economy) {
          document.getElementById('wallet-balance').textContent = `${data.economy.balance_credits.toFixed(2)} Credits`;
          document.getElementById('wallet-addr').textContent = `Wallet: ${data.economy.wallet_address.substring(0, 16)}... (${data.economy.algorithm})`;
          document.getElementById('econ-multiplier').textContent = `${data.economy.energy_multiplier.toFixed(2)}x`;
          document.getElementById('econ-pricing-mode').textContent = data.economy.energy_state.replace(/_/g, ' ');
          document.getElementById('econ-pricing-mode').style.color = data.economy.energy_multiplier <= 1.0 ? 'var(--accent)' : 'var(--accent-orange)';
          document.getElementById('econ-tx-count').textContent = `${data.economy.recent_transactions_count} Recorded`;
        }

        // Storage
        document.getElementById('storage-used').textContent = `${data.storage.free_gb.toFixed(0)}GB Free of ${data.storage.total_gb.toFixed(0)}GB`;

        // Metaverse
        if (data.metaverse && data.metaverse.uav_position) {
          const pos = data.metaverse.uav_position;
          document.getElementById('uav-pos-display').textContent = `(${pos[0].toFixed(1)}m, ${pos[1].toFixed(1)}m, ${pos[2].toFixed(1)}m)`;
          const stopped = data.metaverse.emergency_stopped;
          uavStopped = !!stopped;
          const stopBtn = document.getElementById('btn-stop-uav');
          if (stopBtn) { stopBtn.textContent = stopped ? '▶ Resume UAV' : '🛑 E-Stop'; }
          document.getElementById('metaverse-sla-status').textContent = stopped ? 'E-STOPPED' : 'SLA NOMINAL';
          document.getElementById('metaverse-sla-status').style.color = stopped ? 'var(--accent-red)' : 'var(--accent)';
        }
      } catch (err) {
        console.warn('Telemetry fetch notice:', err);
      }
    }

    // Operator token (SMDC_DASHBOARD_TOKEN): supply once via ?token=... ; kept in sessionStorage only.
    let uavStopped = false;
    function controlToken() {
      try {
        const fromUrl = new URLSearchParams(window.location.search).get('token');
        if (fromUrl) { sessionStorage.setItem('smdc_token', fromUrl); }
        return sessionStorage.getItem('smdc_token') || '';
      } catch (err) { return ''; }
    }
    async function postControl(path, body) {
      const headers = {'Content-Type': 'application/json'};
      const token = controlToken();
      if (token) { headers['Authorization'] = `Bearer ${token}`; }
      const res = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) });
      if (!res.ok) {
        const el = document.getElementById('metaverse-sla-status');
        if (el) {
          el.textContent = res.status === 401 || res.status === 403 ? 'CONTROL DENIED (TOKEN)' : `CONTROL ERROR ${res.status}`;
          el.style.color = 'var(--accent-red)';
        }
      }
      return res;
    }

    document.addEventListener('DOMContentLoaded', () => {
      const btnOpt = document.getElementById('btn-opt-uav');
      if (btnOpt) {
        btnOpt.addEventListener('click', async () => {
          await postControl('/api/metaverse/optimize', {cycles: 3});
          updateTelemetry();
        });
      }
      const btnStop = document.getElementById('btn-stop-uav');
      if (btnStop) {
        btnStop.addEventListener('click', async () => {
          await postControl(uavStopped ? '/api/metaverse/resume' : '/api/metaverse/emergency-stop', {});
          updateTelemetry();
        });
      }
    });

    updateTelemetry();
    setInterval(updateTelemetry, 2500);
  </script>
</body>
</html>
"""


def _is_loopback_host(host: str) -> bool:
    """True for localhost names and loopback IP literals (IPv4, IPv6, IPv4-mapped)."""
    host = host.strip().strip("[]").lower()
    if host in ("localhost", ""):
        return host == "localhost"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(addr.is_loopback or (mapped is not None and mapped.is_loopback))


def _as_bool(value: Any, field: str) -> bool:
    """Accepts only real JSON booleans (``bool("false")`` would otherwise be True)."""
    if not isinstance(value, bool):
        raise ValueError(f"'{field}' must be a JSON boolean")
    return value


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{field}' must be an integer")
    return value


# Global Digital Shadow Hardware State (Synchronized across all connected 3D Twins)
_HARDWARE_LOCK = threading.RLock()
_HARDWARE_STATE: dict[str, Any] = {
    "door_open": False,
    "pdu_outlets": [True, True, True, True, True, True, True, True],
    "fan_rpm": 2400,
    "last_bundle_tx": 0,
}

_METAVERSE_ORCH: Any = None
_RAFT_CLUSTER: RaftCluster | None = None
_RAFT_LEADER: str | None = None
_MAX_POST_BODY_BYTES = 1_048_576  # 1 MiB cap for control POST payloads


def _get_state_dir() -> str:
    """Returns a user-private directory (0o700) for dashboard wallet & ledger state.

    Shares the MCP server's resolution so a pre-existing world-accessible temp directory
    is never trusted as the home of wallet key material.
    """
    from sovereign_dc.mcp.tools import _get_mcp_state_dir

    return _get_mcp_state_dir()


def _get_raft_state() -> tuple[RaftCluster, str]:
    """Returns cached Raft cluster instance and current leader."""
    global _RAFT_CLUSTER, _RAFT_LEADER
    with _HARDWARE_LOCK:
        if _RAFT_CLUSTER is None or _RAFT_LEADER is None:
            _RAFT_CLUSTER = RaftCluster(["smdc-node-01", "smdc-node-02", "smdc-node-03"])
            _RAFT_LEADER = _RAFT_CLUSTER.step_election("smdc-node-01") or "smdc-node-01"
        return _RAFT_CLUSTER, _RAFT_LEADER


def get_metaverse_orchestrator():
    """Returns singleton instance of MetaverseOrchestrator."""
    global _METAVERSE_ORCH
    with _HARDWARE_LOCK:
        if _METAVERSE_ORCH is None:
            from sovereign_dc.metaverse.engine import MetaverseOrchestrator

            _METAVERSE_ORCH = MetaverseOrchestrator()
        return _METAVERSE_ORCH


def get_system_status_payload() -> dict[str, Any]:
    """Assembles unified live system status JSON for REST API and dashboard."""
    from sovereign_dc.economy.ledger import Ledger
    from sovereign_dc.economy.market import ComputeMarket
    from sovereign_dc.economy.wallet import NodeWallet

    cfg = get_config()
    # Outside simulation mode a failed read raises instead of returning invented values.
    power = read_power(exporter_url=cfg.power_exporter_url, simulation=cfg.is_simulation())
    thermal = read_thermal(exporter_url=cfg.power_exporter_url, simulation=cfg.is_simulation())
    storage = detect_storage()

    # Dynamic Fan RPM calculation based on thermal state and system load
    base_rpm = 1800
    temp_factor = max(0.0, (thermal.coolant_celsius - 25.0) * 80.0)
    load_factor = (power.system_load_watts / 600.0) * 800.0
    with _HARDWARE_LOCK:
        _HARDWARE_STATE["fan_rpm"] = int(min(4800, base_rpm + temp_factor + load_factor))
        hw_snapshot = {
            "door_open": _HARDWARE_STATE["door_open"],
            "pdu_outlets": list(_HARDWARE_STATE["pdu_outlets"]),
            "fan_rpm": _HARDWARE_STATE["fan_rpm"],
            "last_bundle_tx": _HARDWARE_STATE["last_bundle_tx"],
        }

    # Space satellite calculation
    satellites = get_active_satellites()
    gs = GroundStation(cfg.ground_station_name, cfg.ground_station_lat, cfg.ground_station_lon)
    passes = gs.predict_passes(satellites[0], duration_hours=6.0) if satellites else []
    next_sec = max(0, int(passes[0]["aos_time"] - time.time())) if passes else 0

    router = DTNRouter(db_path=cfg.dtn_db_path)
    dtn_stats = router.get_queue_stats()

    kp = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_65)
    cluster, leader = _get_raft_state()

    # Economy and wallet status stored in private state directory
    state_dir = _get_state_dir()
    wallet_path = os.path.join(state_dir, "smdc_dashboard_wallet.json")
    db_path = os.path.join(state_dir, "smdc_dashboard_ledger.db")
    with _HARDWARE_LOCK:
        if not os.path.exists(wallet_path):
            wallet = NodeWallet.create(node_id=cfg.node_id)
            wallet.save_to_file(wallet_path)
            ledger = Ledger(db_path=db_path)
            ledger.mint(wallet.address, 100.0, memo="DASHBOARD_GENESIS")
        else:
            wallet = NodeWallet.load_from_file(wallet_path)
            ledger = Ledger(db_path=db_path)

        balance_credits = ledger.get_balance(wallet.address)
        recent_tx_count = len(ledger.get_history(limit=50))

    market = ComputeMarket()
    mult, status_desc = market.get_dynamic_multiplier(power.battery_soc, power.solar_watts)

    return {
        "node_id": cfg.node_id,
        "role": cfg.node_role,
        "version": __version__,
        "timestamp": time.time(),
        "power": {
            "battery_soc": power.battery_soc,
            "solar_watts": power.solar_watts,
            "battery_voltage": power.battery_voltage,
            "system_load_watts": power.system_load_watts,
            "load_shedding_active": power.load_shedding_active,
            "is_simulated": power.is_simulated,
        },
        "thermal": {
            "coolant_celsius": thermal.coolant_celsius,
            "rack_inlet_celsius": thermal.rack_inlet_celsius,
            "rack_exhaust_celsius": thermal.rack_exhaust_celsius,
            "is_overtemp": thermal.is_overtemp,
            "is_simulated": thermal.is_simulated,
        },
        "storage": {
            "total_gb": storage.total_gb,
            "used_gb": storage.used_gb,
            "free_gb": storage.free_gb,
            "usage_percent": round(storage.usage_percent, 1),
        },
        "hardware": hw_snapshot,
        "space": {
            "next_satellite": satellites[0].name if satellites else "N/A",
            "next_pass_seconds": next_sec,
            "queued_bundles": dtn_stats.get("queued_bundle_count", 0),
        },
        "mesh": {
            "raft_leader": leader,
            "nodes_count": len(cluster.nodes),
        },
        "economy": {
            "wallet_address": wallet.address,
            "balance_credits": balance_credits,
            "algorithm": wallet.keypair.algorithm.value,
            "energy_multiplier": mult,
            "energy_state": status_desc,
            "recent_transactions_count": recent_tx_count,
        },
        "pqc": {
            "algorithm": PQCAlgorithm.ML_DSA_65.value,
            "key_id": kp.key_id,
            "status": "OPERATIONAL",
        },
        "metaverse": get_metaverse_orchestrator().get_latest_status(),
        "events_count": len(get_event_bus().get_history()),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Sovereign Operations Dashboard & REST APIs."""

    def _send_cors_headers(self) -> None:
        cors_origin = os.environ.get("SMDC_CORS_ORIGIN", "*")
        self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    # SSE pacing; tests bound the stream with sse_max_events.
    sse_interval_seconds: float = 2.0
    sse_max_events: int | None = None

    def _check_post_auth(self) -> bool:
        """Authorizes a control POST.

        With SMDC_DASHBOARD_TOKEN set, a matching bearer token is required. Without a token,
        control is limited to requests that originate on this machine: the TCP peer must be
        loopback and any browser ``Origin`` must itself be a loopback origin, so neither a
        LAN host nor a web page from another site can actuate hardware.
        """
        expected_token = os.environ.get("SMDC_DASHBOARD_TOKEN", "").strip()
        if expected_token:
            auth_header = str(self.headers.get("Authorization", ""))
            if not auth_header.startswith("Bearer "):
                return False
            provided = auth_header[len("Bearer ") :].strip()
            return hmac.compare_digest(provided.encode("utf-8"), expected_token.encode("utf-8"))

        client = getattr(self, "client_address", None)
        peer_host = str(client[0]) if client else ""
        if not _is_loopback_host(peer_host):
            return False
        origin = str(self.headers.get("Origin", "") or "")
        if origin and not _is_loopback_host(urlsplit(origin).hostname or ""):
            return False
        return True

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_cors_headers()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_bad_request(self, message: str) -> None:
        self._send_json(400, {"success": False, "error": message})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        # Route on the path only: the page is opened as /?token=... and probes add query strings.
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            payload = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/status":
            try:
                status_payload = get_system_status_payload()
            except Exception as e:
                # Hardware mode with no telemetry source: report it, do not invent readings.
                logger.warning("System status unavailable: %s", e)
                self._send_json(503, {"success": False, "error": f"telemetry unavailable: {e}"})
                return
            self._send_json(200, status_payload)
        elif path == "/api/metaverse/status":
            orch = get_metaverse_orchestrator()
            payload = json.dumps(orch.get_latest_status()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/metaverse/slices":
            from sovereign_dc.metaverse.slicing import NetworkSlicingManager

            mgr = NetworkSlicingManager()
            payload = json.dumps(mgr.get_summary()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/telemetry/stream":
            # Server-Sent Events (SSE) Live Telemetry Stream
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_cors_headers()
            self.end_headers()
            # The response has no Content-Length, so the socket must close when the stream ends.
            self.close_connection = True

            sent = 0
            try:
                while self.sse_max_events is None or sent < self.sse_max_events:
                    try:
                        frame = f"data: {json.dumps(get_system_status_payload())}\n\n"
                    except (BrokenPipeError, ConnectionError):
                        raise
                    except Exception as e:
                        frame = f"event: unavailable\ndata: {json.dumps({'error': str(e)})}\n\n"
                    self.wfile.write(frame.encode())
                    self.wfile.flush()
                    sent += 1
                    if self.sse_max_events is not None and sent >= self.sse_max_events:
                        break
                    time.sleep(self.sse_interval_seconds)
            except (BrokenPipeError, ConnectionError, OSError):
                logger.debug("SSE client disconnected after %d events", sent)
        elif path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b"OK\n")
        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def do_POST(self) -> None:
        if not self._check_post_auth():
            token_set = bool(os.environ.get("SMDC_DASHBOARD_TOKEN", "").strip())
            if token_set:
                self._send_json(401, {"success": False, "error": "Unauthorized"})
            else:
                self._send_json(
                    403,
                    {"success": False, "error": "Remote control requires SMDC_DASHBOARD_TOKEN to be configured"},
                )
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self.send_response(400)
            self._send_cors_headers()
            self.end_headers()
            return

        if content_length < 0 or content_length > _MAX_POST_BODY_BYTES:
            self.send_response(413 if content_length > _MAX_POST_BODY_BYTES else 400)
            self._send_cors_headers()
            self.end_headers()
            return

        body = self.rfile.read(content_length).decode("utf-8", errors="replace") if content_length > 0 else "{}"
        try:
            req_data = json.loads(body) if body.strip() else {}
        except ValueError:
            self._send_bad_request("Request body is not valid JSON")
            return
        if not isinstance(req_data, dict):
            self._send_bad_request("Request body must be a JSON object")
            return

        try:
            self._route_post(req_data)
        except ValueError as e:
            self._send_bad_request(str(e))

    def _route_post(self, req_data: dict[str, Any]) -> None:
        """Dispatches an authorized control POST; raises ValueError on invalid fields."""
        path = urlsplit(self.path).path
        if path == "/api/control/rack-door":
            # Toggle or explicitly set rack door state
            from sovereign_dc.events import Event

            with _HARDWARE_LOCK:
                if "open" in req_data:
                    target_state = _as_bool(req_data["open"], "open")
                else:
                    target_state = not _HARDWARE_STATE["door_open"]
                _HARDWARE_STATE["door_open"] = target_state
                door_open_val = _HARDWARE_STATE["door_open"]

            get_event_bus().publish(
                Event(
                    event_type="hardware.door.changed",
                    source="dashboard",
                    payload={"open": door_open_val},
                )
            )

            res = {"success": True, "door_open": door_open_val}
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == "/api/control/pdu-outlet":
            from sovereign_dc.events import Event

            outlet_idx = _as_int(req_data.get("outlet", 0), "outlet")
            requested_state = _as_bool(req_data["state"], "state") if "state" in req_data else None
            outlet_changed = False
            outlet_state = False
            with _HARDWARE_LOCK:
                if 0 <= outlet_idx < len(_HARDWARE_STATE["pdu_outlets"]):
                    current = _HARDWARE_STATE["pdu_outlets"][outlet_idx]
                    _HARDWARE_STATE["pdu_outlets"][outlet_idx] = (
                        (not current) if requested_state is None else requested_state
                    )
                    outlet_state = _HARDWARE_STATE["pdu_outlets"][outlet_idx]
                    outlet_changed = True
                outlets_snapshot = list(_HARDWARE_STATE["pdu_outlets"])

            if outlet_changed:
                get_event_bus().publish(
                    Event(
                        event_type="hardware.pdu.outlet_changed",
                        source="dashboard",
                        payload={"outlet": outlet_idx, "state": outlet_state},
                    )
                )

            if not outlet_changed:
                raise ValueError(f"'outlet' must be between 0 and {len(outlets_snapshot) - 1}")
            res = {"success": True, "pdu_outlets": outlets_snapshot}
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == "/api/control/dtn-transmit":
            # Spool and dispatch an RFC 9171 Space Bundle
            from sovereign_dc.events import Event
            from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority

            cfg = get_config()
            router = DTNRouter(db_path=cfg.dtn_db_path)
            bundle_payload = str(req_data.get("payload", f"STATUS_TELEMETRY_{int(time.time())}")).encode("utf-8")
            priority_val = _as_int(req_data.get("priority", 2), "priority")
            bundle_prio = BundlePriority.EXPEDITED if priority_val >= 2 else BundlePriority.NORMAL

            bundle = Bundle(
                source_eid=cfg.node_id,
                destination_eid=str(req_data.get("destination", "dtn://ground-station-alpha.earth/telemetry")),
                payload=bundle_payload,
                priority=bundle_prio,
            )
            if not router.queue_bundle(bundle):
                self._send_json(507, {"success": False, "error": "DTN spool rejected the bundle"})
                return
            with _HARDWARE_LOCK:
                _HARDWARE_STATE["last_bundle_tx"] = time.time()
            get_event_bus().publish(
                Event(
                    event_type="space.dtn.bundle_queued",
                    source="dashboard",
                    payload={"bundle_id": bundle.bundle_id},
                )
            )

            res = {
                "success": True,
                "bundle_id": bundle.bundle_id,
                "queued_bundles": router.get_queue_stats().get("queued_bundle_count", 1),
            }
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == "/api/enterprise/telemetry":
            # Out-of-process enterprise workloads (SMDCClient.emit_telemetry) report metrics here.
            from sovereign_dc.enterprise.schema import is_valid_app_id
            from sovereign_dc.events import Event

            app_id = req_data.get("app_id")
            metrics = req_data.get("metrics")
            if not isinstance(app_id, str) or not is_valid_app_id(app_id):
                raise ValueError("'app_id' is missing or not a valid application id")
            if not isinstance(metrics, dict):
                raise ValueError("'metrics' must be a JSON object")
            delivered = get_event_bus().publish(
                Event(
                    event_type=f"enterprise.{app_id}.telemetry",
                    source=f"enterprise.{app_id}",
                    payload={"app_id": app_id, "timestamp": time.time(), "metrics": metrics},
                )
            )
            self._send_json(200, {"success": True, "subscribers_notified": delivered})

        elif path == "/api/metaverse/optimize":
            orch = get_metaverse_orchestrator()
            raw_cycles = _as_int(req_data.get("cycles", 1), "cycles")
            cycles = max(1, min(raw_cycles, 100))
            traces = orch.run_cycles(count=cycles, deterministic=True)
            res = {
                "success": True,
                "cycles_executed": len(traces),
                "uav_position": orch.uav_position,
                "latest_trace": traces[-1].to_dict() if traces else None,
            }
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == "/api/metaverse/emergency-stop":
            # Idempotent: a repeated or concurrent press must never restart the UAV.
            orch = get_metaverse_orchestrator()
            if not orch.is_emergency_stopped:
                orch.trigger_emergency_stop()
            self._send_json(200, {"success": True, "emergency_stopped": orch.is_emergency_stopped})

        elif path == "/api/metaverse/resume":
            orch = get_metaverse_orchestrator()
            if orch.is_emergency_stopped:
                orch.resume_operation()
            res = {
                "success": True,
                "emergency_stopped": orch.is_emergency_stopped,
            }
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stdout HTTP access logs in production CLI."""


def run_dashboard_server(port: int = 8080, open_browser: bool = False, host: str | None = None) -> None:
    """Launches the Sovereign Web Operations Dashboard HTTP server.

    Binds to loopback unless ``host`` (or SMDC_DASHBOARD_HOST) says otherwise. When exposed
    beyond loopback, control POSTs are refused unless SMDC_DASHBOARD_TOKEN is configured.
    """
    bind_host = host or os.environ.get("SMDC_DASHBOARD_HOST", "").strip() or "127.0.0.1"
    server = HTTPServer((bind_host, port), DashboardHandler)
    server.daemon_threads = True  # SSE streams must not block shutdown
    url = f"http://localhost:{port}"
    logger.info("Sovereign Operations Web Dashboard listening on %s:%d", bind_host, port)
    if not _is_loopback_host(bind_host) and not os.environ.get("SMDC_DASHBOARD_TOKEN", "").strip():
        logger.warning(
            "Dashboard exposed on %s without SMDC_DASHBOARD_TOKEN: control endpoints are disabled", bind_host
        )
    print(f"\n🌐 Sovereign Operations Dashboard available at: \033[1;36m{url}\033[0m\n")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard server shutting down.")
    finally:
        server.server_close()
