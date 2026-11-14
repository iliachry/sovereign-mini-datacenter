"""Sovereign Mini Datacenter — Real-Time Operations Web Dashboard & REST API.

Provides an ultra-portable, zero-external-dependency HTTP server hosting:
1. REST API endpoints for telemetry, space link, mesh cluster, and PQC status.
2. A high-performance, dark-mode glassmorphic single-page operations interface
   for field operators and remote NOC engineers.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sovereign_dc import __version__
from sovereign_dc.config import get_config
from sovereign_dc.events import get_event_bus
from sovereign_dc.hal.power import read_power
from sovereign_dc.hal.storage import detect_storage
from sovereign_dc.hal.thermal import read_thermal
from sovereign_dc.mesh.consensus import RaftCluster
from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner, get_node_pqc_keypair
from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
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
      <div class="card-title"><span>🔋 Battery Storage</span><span id="shedding-mode" class="code">--</span></div>
      <div class="metric-value" id="battery-soc">-- %</div>
      <div class="metric-sub" id="battery-voltage">Voltage: -- V | Load: -- W</div>
      <div class="gauge-bar"><div class="gauge-fill" id="soc-bar"></div></div>
    </div>

    <!-- Card 2: Solar PV Array -->
    <div class="card">
      <div class="card-title"><span>☀️ Solar Generation</span><span id="mppt-status" class="code">--</span></div>
      <div class="metric-value" style="color: var(--accent-blue);" id="solar-watts">-- W</div>
      <div class="metric-sub" id="net-power">Net Flow: -- W</div>
      <div class="gauge-bar"><div class="gauge-fill" style="background: linear-gradient(90deg, #f59e0b, #38bdf8);" id="solar-bar"></div></div>
    </div>

    <!-- Card 3: Thermal Liquid Cooling -->
    <div class="card">
      <div class="card-title"><span>❄️ Liquid Coolant Loop</span><span id="thermal-status" class="code">--</span></div>
      <div class="metric-value" style="color: var(--accent);" id="coolant-temp">-- °C</div>
      <div class="metric-sub" id="rack-air">Inlet: -- °C | Exhaust: -- °C</div>
      <div class="gauge-bar"><div class="gauge-fill" style="background: var(--accent);" id="temp-bar"></div></div>
    </div>

    <!-- Card 4: Space DTN Communications -->
    <div class="card">
      <div class="card-title"><span>🛰️ Space DTN / BPv7</span><span id="space-link-status" class="code">--</span></div>
      <div class="metric-value" style="color: var(--accent-purple);" id="dtn-spool">-- Bundles</div>
      <div class="metric-sub" id="satellite-pass">Next Contact: Calculating...</div>
      <div class="gauge-bar"><div class="gauge-fill" style="background: var(--accent-purple);" id="dtn-bar"></div></div>
    </div>
  </div>

  <div class="grid">
    <!-- Card 5: Swarm Mesh Topology -->
    <div class="card">
      <div class="card-title"><span>🌐 Sovereign Mesh Cluster</span><span class="code" id="raft-role">--</span></div>
      <div class="metric-sub" id="mesh-provenance">Waiting for node status...</div>
      <div id="mesh-nodes-list"></div>
    </div>

    <!-- Card 6: Compute Economy & Wallet -->
    <div class="card">
      <div class="card-title"><span>💰 Compute Economy & Wallet</span><span class="code" id="econ-pricing-mode">--</span></div>
      <div class="metric-value" style="color: var(--accent-orange);" id="wallet-balance">-- Credits</div>
      <div class="metric-sub" id="wallet-addr">Wallet: --</div>
      <div class="status-row" style="margin-top: 10px;"><span>Dynamic Rate Multiplier</span><span class="code" id="econ-multiplier">1.00x</span></div>
      <div class="status-row"><span>Ledger Transactions</span><span class="code" id="econ-tx-count">0 Recorded</span></div>
    </div>

    <!-- Card 7: Storage & Security -->
    <div class="card">
      <div class="card-title"><span>🔐 Storage & Cryptography</span><span class="code" id="pqc-status">--</span></div>
      <div class="status-row"><span>NVMe Storage</span><span class="code" id="storage-used">-- GB / -- GB Free</span></div>
      <div class="status-row"><span>PQC Digital Signatures</span><span class="code" style="color: var(--accent);">ML-DSA-87 (FIPS 204)</span></div>
      <div class="status-row"><span>PQC Key Encapsulation</span><span class="code" style="color: var(--accent-purple);">ML-KEM-1024 (FIPS 203)</span></div>
      <div class="status-row"><span>Intrusion Detection</span><span class="code">CrowdSec overlay (not probed)</span></div>
    </div>

    <!-- Card 8: 5G RAN & UAV Metaverse (IEEE IoT Mag 2026) -->
    <div class="card">
      <div class="card-title"><span>📡 5G RAN & UAV Metaverse (simulation)</span><span class="code" id="metaverse-sla-status">--</span></div>
      <div class="status-row"><span>Autonomous UAV Pos</span><span class="code" style="color: var(--accent-blue);" id="uav-pos-display">(0.0m, 0.0m, 35.0m)</span></div>
      <div class="status-row"><span>URLLC Slice (simulated)</span><span class="code" style="color: var(--accent);" id="slice-urllc">--</span></div>
      <div class="status-row"><span>eMBB Slice (simulated)</span><span class="code" style="color: var(--accent-purple);" id="slice-embb">--</span></div>
      <div class="status-row"><span>mMTC Slice (simulated)</span><span class="code" id="slice-mmtc">--</span></div>
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
    // Operator token (SMDC_DASHBOARD_TOKEN): supply once via ?token=... ; it is moved into
    // sessionStorage (this page's origin is the node itself) and removed from the address bar.
    let uavStopped = false;
    function controlToken() {
      try {
        const url = new URL(window.location.href);
        const fromUrl = url.searchParams.get('token');
        if (fromUrl) {
          sessionStorage.setItem('smdc_token', fromUrl);
          url.searchParams.delete('token');
          window.history.replaceState(null, '', url.pathname + url.search + url.hash);
        }
        return sessionStorage.getItem('smdc_token') || '';
      } catch (err) { return ''; }
    }
    function authHeaders(extra) {
      const headers = Object.assign({}, extra || {});
      const token = controlToken();
      if (token) { headers['Authorization'] = `Bearer ${token}`; }
      return headers;
    }

    // Every status badge is derived from the /api/status payload; none is a static claim.
    const BADGE_IDS = ['shedding-mode', 'mppt-status', 'thermal-status', 'space-link-status', 'raft-role', 'pqc-status', 'econ-pricing-mode', 'metaverse-sla-status'];
    function setBadge(id, text, color) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.style.color = color || '';
    }
    function markUnavailable(reason) {
      const live = document.getElementById('live-indicator');
      if (live) { live.textContent = `● ${reason}`; live.style.color = 'var(--accent-red)'; live.style.borderColor = 'var(--accent-red)'; }
      BADGE_IDS.forEach((id) => setBadge(id, 'NO DATA', 'var(--text-muted)'));
      const list = document.getElementById('mesh-nodes-list');
      if (list) { list.textContent = ''; }
      const provenance = document.getElementById('mesh-provenance');
      if (provenance) { provenance.textContent = 'No node status available.'; }
    }
    function renderMesh(mesh, nodeId) {
      const list = document.getElementById('mesh-nodes-list');
      const provenance = document.getElementById('mesh-provenance');
      const nodes = (mesh && Array.isArray(mesh.nodes)) ? mesh.nodes : [];
      const simulated = !mesh || mesh.simulated !== false;
      const role = mesh ? (mesh.local_role || (mesh.raft_leader === nodeId ? 'leader' : 'follower')) : '';
      const prefix = simulated ? 'SIM RAFT · ' : 'RAFT ';
      if (role === 'leader') { setBadge('raft-role', `${prefix}LEADER`, 'var(--accent)'); }
      else if (role === 'follower') { setBadge('raft-role', `${prefix}FOLLOWER`, 'var(--accent-blue)'); }
      else { setBadge('raft-role', `${prefix}NOT A MEMBER`, 'var(--text-muted)'); }
      if (provenance) {
        provenance.textContent = simulated
          ? 'In-process simulated Raft cluster (no network peers are probed; these are not real hosts).'
          : 'Cluster members reported by this node.';
      }
      if (!list) return;
      list.textContent = '';
      nodes.forEach((n) => {
        const row = document.createElement('div');
        row.className = 'status-row';
        const name = document.createElement('span');
        name.textContent = `${n.node_id}${simulated ? ' (simulated)' : ''}`;
        const state = document.createElement('span');
        state.className = 'code';
        state.textContent = `${String(n.role || 'unknown').toUpperCase()} • term ${n.term}`;
        state.style.color = n.role === 'leader' ? 'var(--accent)' : 'var(--text-muted)';
        row.appendChild(name);
        row.appendChild(state);
        list.appendChild(row);
      });
      if (!nodes.length) {
        const row = document.createElement('div');
        row.className = 'status-row';
        row.textContent = 'No cluster members reported.';
        list.appendChild(row);
      }
    }

    async function updateTelemetry() {
      try {
        const res = await fetch('/api/status', { headers: authHeaders() });
        if (!res.ok) {
          markUnavailable(res.status === 401 ? 'OPERATOR TOKEN REQUIRED (?token=)' : 'TELEMETRY UNAVAILABLE');
          return;
        }
        const data = await res.json();

        document.getElementById('node-id').textContent = `Node ID: ${data.node_id} • ${data.role}`;
        document.getElementById('smdc-version').textContent = data.version;

        // Battery
        const soc = data.power.battery_soc || 0;
        document.getElementById('battery-soc').textContent = `${soc.toFixed(1)} %`;
        document.getElementById('battery-voltage').textContent = `Voltage: ${data.power.battery_voltage.toFixed(1)} V | Load: ${data.power.system_load_watts.toFixed(0)} W`;
        document.getElementById('soc-bar').style.width = `${Math.min(100, Math.max(0, soc))}%`;
        // load_shedding_active is the exporter's own flag; the L-level is this node's SoC policy tier.
        const shedding = !!data.power.load_shedding_active;
        const level = Number.isInteger(data.power.shedding_level) ? ` · SoC TIER L${data.power.shedding_level}` : '';
        setBadge('shedding-mode', `${shedding ? 'LOAD SHEDDING ACTIVE' : 'NOT SHEDDING'}${level}`, shedding ? 'var(--accent-red)' : 'var(--accent)');

        // Solar
        const solar = data.power.solar_watts || 0;
        document.getElementById('solar-watts').textContent = `${solar.toFixed(0)} W`;
        document.getElementById('net-power').textContent = `Net Flow: ${(solar - data.power.system_load_watts).toFixed(0)} W`;
        document.getElementById('solar-bar').style.width = `${Math.min(100, (solar / 3280) * 100)}%`;
        setBadge('mppt-status', solar > 0 ? 'MPPT ACTIVE' : 'MPPT IDLE', solar > 0 ? 'var(--accent)' : 'var(--text-muted)');

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
        const overtemp = !!data.thermal.is_overtemp;
        setBadge('thermal-status', overtemp ? 'OVERTEMP' : 'NORMAL', overtemp ? 'var(--accent-red)' : 'var(--accent)');
        document.getElementById('coolant-temp').style.color = overtemp ? 'var(--accent-red)' : 'var(--accent)';

        // Space DTN (pass times come from the node's simplified orbit model: predictions, not a link probe)
        const space = data.space;
        document.getElementById('dtn-spool').textContent = `${space.queued_bundles} Bundles`;
        document.getElementById('dtn-bar').style.width = `${Math.min(100, space.queued_bundles * 10)}%`;
        const passEl = document.getElementById('satellite-pass');
        if (space.contact_state === 'in_contact') {
          setBadge('space-link-status', 'IN CONTACT (PREDICTED)', 'var(--accent)');
          passEl.textContent = `Predicted pass in progress: ${space.next_satellite}`;
        } else if (space.contact_state === 'tracking') {
          setBadge('space-link-status', 'AWAITING PASS', 'var(--accent-purple)');
          passEl.textContent = `Next predicted pass: ${space.next_satellite} in ${Math.round(space.next_pass_seconds / 60)}m`;
        } else if (space.contact_state === 'no_pass') {
          setBadge('space-link-status', 'NO PASS PREDICTED', 'var(--text-muted)');
          passEl.textContent = `No pass predicted in the next ${space.prediction_window_hours} h`;
        } else {
          setBadge('space-link-status', 'NO SATELLITES', 'var(--text-muted)');
          passEl.textContent = 'Satellite catalog is empty';
        }

        // Mesh
        renderMesh(data.mesh, data.node_id);

        // Economy
        if (data.economy) {
          document.getElementById('wallet-balance').textContent = `${data.economy.balance_credits.toFixed(2)} Credits`;
          document.getElementById('wallet-addr').textContent = `Wallet: ${data.economy.wallet_address.substring(0, 16)}... (${data.economy.algorithm})`;
          document.getElementById('econ-multiplier').textContent = `${data.economy.energy_multiplier.toFixed(2)}x`;
          setBadge('econ-pricing-mode', data.economy.energy_state.replace(/_/g, ' '), data.economy.energy_multiplier <= 1.0 ? 'var(--accent)' : 'var(--accent-orange)');
          document.getElementById('econ-tx-count').textContent = `${data.economy.recent_transactions_count} Recorded`;
        }

        // Storage & PQC
        document.getElementById('storage-used').textContent = `${data.storage.free_gb.toFixed(0)}GB Free of ${data.storage.total_gb.toFixed(0)}GB`;
        if (data.pqc) {
          // SELF_TEST_PASSED: node key loaded and a sign/verify round-trip succeeded.
          // KEY_LOADED: key present but the round-trip did not pass. UNAVAILABLE: no usable key.
          const pqcOk = data.pqc.status === 'SELF_TEST_PASSED';
          setBadge('pqc-status', `PQC ${String(data.pqc.status).replace(/_/g, ' ')}`, pqcOk ? 'var(--accent)' : 'var(--accent-red)');
        }

        // Metaverse
        if (data.metaverse && data.metaverse.uav_position) {
          const pos = data.metaverse.uav_position;
          document.getElementById('uav-pos-display').textContent = `(${pos[0].toFixed(1)}m, ${pos[1].toFixed(1)}m, ${pos[2].toFixed(1)}m)`;
          const stopped = data.metaverse.emergency_stopped;
          uavStopped = !!stopped;
          const stopBtn = document.getElementById('btn-stop-uav');
          if (stopBtn) { stopBtn.textContent = stopped ? '▶ Resume UAV' : '🛑 E-Stop'; }
          setBadge('metaverse-sla-status', stopped ? 'E-STOPPED' : 'SIM RUNNING', stopped ? 'var(--accent-red)' : 'var(--accent)');
        }
        // 5G slices: figures of the in-process slicing simulator (no radio is measured).
        const slices = (data.metaverse && data.metaverse.slices) || {};
        const sliceText = (id, s, render) => { document.getElementById(id).textContent = s ? render(s) : '--'; };
        sliceText('slice-urllc', slices.URLLC, s => `${s.avg_latency_ms.toFixed(2)} ms avg (target ${s.target_latency_ms} ms, ${s.target_reliability_pct}%)`);
        sliceText('slice-embb', slices.eMBB, s => `${s.allocated_bandwidth_mbps} Mbps (${s.avg_latency_ms} ms avg)`);
        sliceText('slice-mmtc', slices.mMTC, s => `${s.active_connections.toLocaleString('en-US')} connections, ${s.total_packets} pkts`);
      } catch (err) {
        console.warn('Telemetry fetch notice:', err);
        markUnavailable('TELEMETRY UNAVAILABLE');
      }
    }

    async function postControl(path, body) {
      const res = await fetch(path, { method: 'POST', headers: authHeaders({'Content-Type': 'application/json'}), body: JSON.stringify(body) });
      if (!res.ok) {
        const denied = res.status === 401 || res.status === 403;
        setBadge('metaverse-sla-status', denied ? 'CONTROL DENIED (TOKEN)' : `CONTROL ERROR ${res.status}`, 'var(--accent-red)');
      }
      return res;
    }

    document.addEventListener('DOMContentLoaded', () => {
      const btnOpt = document.getElementById('btn-opt-uav');
      if (btnOpt) {
        btnOpt.addEventListener('click', async () => {
          const res = await postControl('/api/metaverse/optimize', {cycles: 3});
          if (res.ok) updateTelemetry();
        });
      }
      const btnStop = document.getElementById('btn-stop-uav');
      if (btnStop) {
        btnStop.addEventListener('click', async () => {
          const res = await postControl(uavStopped ? '/api/metaverse/resume' : '/api/metaverse/emergency-stop', {});
          if (res.ok) updateTelemetry();
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


# --- DTN submission contract -------------------------------------------------------------
#
# One contract for every path that spools a bundle on behalf of a client: the dashboard's
# POST /api/control/dtn-transmit, SMDCClient.send_dtn_bundle (HTTP and in-process fallback)
# and the MCP ``spool_dtn_bundle`` tool. Request fields:
#
#   destination   str   destination EID (default DTN_DEFAULT_DESTINATION)
#   payload_b64   str   payload bytes, standard base64 (lossless; wins over ``payload``)
#   payload       str   legacy text payload, stored as its UTF-8 encoding
#   priority      int   0 bulk, 1 normal (default), 2 expedited, 3 critical
#   ttl           int   lifetime in seconds, 1..DTN_MAX_TTL_SECONDS (default 7 days)
#   source        str   source EID; used only if syntactically valid, else the node's own EID
#
# ``describe_dtn_bundle`` reports what was actually stored.

DTN_DEFAULT_DESTINATION = "dtn://ground-station-alpha.earth/telemetry"
DTN_DEFAULT_PRIORITY = BundlePriority.NORMAL
DTN_DEFAULT_TTL_SECONDS = 86400 * 7
DTN_MAX_TTL_SECONDS = 86400 * 30
DTN_PRIORITY_NAMES: dict[int, str] = {
    BundlePriority.BULK: "BULK",
    BundlePriority.NORMAL: "NORMAL",
    BundlePriority.EXPEDITED: "EXPEDITED",
    BundlePriority.CRITICAL: "CRITICAL",
}
_EID_RE = re.compile(r"^(?:dtn://[A-Za-z0-9][A-Za-z0-9._~-]*(?:/[!-~]*)?|ipn:[0-9]+\.[0-9]+)$")


def is_valid_eid(value: Any) -> bool:
    """True for a syntactically valid RFC 9171 endpoint ID (``dtn://node/...`` or ``ipn:N.S``)."""
    return isinstance(value, str) and len(value) <= 1024 and _EID_RE.match(value) is not None


def node_eid(node_id: str, service: str = "api") -> str:
    """The node's own source EID for ``service``: ``dtn://<node-id>/<service>``."""
    name = re.sub(r"[^A-Za-z0-9._~-]", "-", node_id.strip()) or "node"
    if not name[0].isalnum():
        name = f"n{name}"
    return f"dtn://{name}/{service}"


@dataclass(frozen=True)
class DTNSubmission:
    """A validated bundle submission (see the contract above)."""

    destination: str
    payload: bytes
    priority: int
    ttl_seconds: int
    source: str
    ttl_capped: bool = False

    def to_bundle(self) -> Bundle:
        return Bundle(
            source_eid=self.source,
            destination_eid=self.destination,
            payload=self.payload,
            priority=self.priority,
            lifetime_seconds=self.ttl_seconds,
        )


def parse_dtn_submission(req: dict[str, Any], node_id: str, service: str = "api") -> DTNSubmission:
    """Validates a bundle submission; raises ValueError naming the offending field."""
    destination = req.get("destination", DTN_DEFAULT_DESTINATION)
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("'destination' must be a non-empty endpoint ID string")

    if req.get("payload_b64") is not None:
        raw_b64 = req["payload_b64"]
        if not isinstance(raw_b64, str):
            raise ValueError("'payload_b64' must be a base64 string")
        try:
            payload = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError("'payload_b64' is not valid base64") from e
    elif req.get("payload") is not None:
        text = req["payload"]
        if not isinstance(text, str):
            raise ValueError("'payload' must be a string (send binary data as 'payload_b64')")
        payload = text.encode("utf-8")
    else:
        payload = f"STATUS_TELEMETRY_{int(time.time())}".encode()

    priority = _as_int(req.get("priority", DTN_DEFAULT_PRIORITY), "priority")
    if priority not in DTN_PRIORITY_NAMES:
        raise ValueError("'priority' must be 0 (bulk), 1 (normal), 2 (expedited) or 3 (critical)")

    ttl = _as_int(req.get("ttl", DTN_DEFAULT_TTL_SECONDS), "ttl")
    if ttl <= 0:
        raise ValueError("'ttl' must be a positive number of seconds")
    ttl_capped = ttl > DTN_MAX_TTL_SECONDS

    requested_source = req.get("source")
    source = requested_source if isinstance(requested_source, str) and is_valid_eid(requested_source) else None
    return DTNSubmission(
        destination=destination.strip(),
        payload=payload,
        priority=priority,
        ttl_seconds=min(ttl, DTN_MAX_TTL_SECONDS),
        source=source or node_eid(node_id, service),
        ttl_capped=ttl_capped,
    )


def describe_dtn_bundle(bundle: Bundle, ttl_capped: bool = False) -> dict[str, Any]:
    """What was actually stored for ``bundle`` (returned by every submission path)."""
    return {
        "bundle_id": bundle.bundle_id,
        "source": bundle.source_eid,
        "destination": bundle.destination_eid,
        "priority": bundle.priority,
        "priority_name": DTN_PRIORITY_NAMES.get(bundle.priority, "NORMAL"),
        "ttl_seconds": bundle.lifetime_seconds,
        "ttl_capped": ttl_capped,
        "payload_bytes": len(bundle.payload),
        "payload_sha256": hashlib.sha256(bundle.payload).hexdigest(),
    }


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
_PASS_WINDOW_HOURS = 6.0
# Data endpoints that require the operator token from non-local clients when one is configured.
_PROTECTED_GET_PATHS = frozenset(
    {"/api/status", "/api/metaverse/status", "/api/metaverse/slices", "/api/telemetry/stream"}
)


def _get_state_dir() -> str:
    """Returns a user-private directory (0o700) for dashboard wallet & ledger state.

    Shares the MCP server's resolution so a pre-existing world-accessible temp directory
    is never trusted as the home of wallet key material.
    """
    from sovereign_dc.mcp.tools import _get_mcp_state_dir

    return _get_mcp_state_dir()


def _get_raft_state() -> tuple[RaftCluster, str]:
    """Returns the cached in-process *simulated* Raft cluster and its leader.

    This is an in-memory harness (``RaftCluster``): no network peers exist or are probed,
    and the status payload labels it ``simulated`` accordingly.
    """
    global _RAFT_CLUSTER, _RAFT_LEADER
    with _HARDWARE_LOCK:
        if _RAFT_CLUSTER is None or _RAFT_LEADER is None:
            _RAFT_CLUSTER = RaftCluster(["smdc-node-01", "smdc-node-02", "smdc-node-03"])
            _RAFT_LEADER = _RAFT_CLUSTER.step_election("smdc-node-01") or "smdc-node-01"
        return _RAFT_CLUSTER, _RAFT_LEADER


_PQC_SELF_TEST_MESSAGE = b"SMDC_DASHBOARD_PQC_SELF_TEST"
# key_id values whose sign/verify round-trip has passed in this process. Only successes are
# remembered: a failing key is re-tested on the next status build.
_PQC_SELF_TEST_PASSED: set[str] = set()


def get_pqc_status() -> dict[str, Any]:
    """Reports what is actually verified about the node's ML-DSA-65 signing identity.

    ``status`` is one of:

    * ``SELF_TEST_PASSED``: the node key loaded and signing then verifying a fixed message
      with it succeeded (the round-trip runs once per key and process, then is cached);
    * ``KEY_LOADED``: the key loaded but the round-trip did not pass (``self_test`` is
      ``"failed"``);
    * ``UNAVAILABLE``: no usable key (``key_id`` is ``None``).

    Nothing else is claimed: this is not a compliance or transport-security statement.
    """
    algorithm = PQCAlgorithm.ML_DSA_65
    result: dict[str, Any] = {
        "algorithm": algorithm.value,
        "key_id": None,
        "status": "UNAVAILABLE",
        "self_test": "not_run",
    }
    try:
        keypair = get_node_pqc_keypair(algorithm)
    except Exception as err:
        logger.warning("PQC node key unavailable: %s", err)
        return result
    result["key_id"] = keypair.key_id
    passed = keypair.key_id in _PQC_SELF_TEST_PASSED
    if not passed:
        try:
            signer = PQCSigner(algorithm)
            signature = signer.sign(_PQC_SELF_TEST_MESSAGE, keypair.private_key)
            passed = signer.verify(_PQC_SELF_TEST_MESSAGE, signature, keypair.public_key)
        except Exception as err:
            logger.warning("PQC self-test raised for key %s: %s", keypair.key_id, err)
            passed = False
        if passed:
            _PQC_SELF_TEST_PASSED.add(keypair.key_id)
    result["status"] = "SELF_TEST_PASSED" if passed else "KEY_LOADED"
    result["self_test"] = "passed" if passed else "failed"
    return result


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
    passes = gs.predict_passes(satellites[0], duration_hours=_PASS_WINDOW_HOURS) if satellites else []
    now = time.time()
    next_sec = max(0, int(passes[0]["aos_time"] - now)) if passes else 0
    if not satellites:
        contact_state = "no_satellites"
    elif not passes:
        contact_state = "no_pass"
    elif passes[0]["aos_time"] <= now < passes[0].get("los_time", passes[0]["aos_time"]):
        contact_state = "in_contact"
    else:
        contact_state = "tracking"

    router = DTNRouter(db_path=cfg.dtn_db_path)
    dtn_stats = router.get_queue_stats()

    pqc_status = get_pqc_status()
    cluster, leader = _get_raft_state()
    mesh_nodes = []
    for raft_node in cluster.nodes.values():
        node_status = raft_node.status()
        mesh_nodes.append({key: node_status[key] for key in ("node_id", "role", "term", "commit_index")})
    if leader == cfg.node_id:
        local_role = "leader"
    elif cfg.node_id in cluster.nodes:
        local_role = "follower"
    else:
        local_role = "not_a_member"

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
            # The exporter's own flag (the bundled exporter raises it below 20 % SoC).
            "load_shedding_active": power.load_shedding_active,
            # This node's configured SoC policy tier (0 nominal .. 4 emergency) for that SoC.
            "shedding_level": cfg.get_shedding_level(power.battery_soc),
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
            # no_satellites | no_pass | tracking | in_contact; predicted by the orbit model,
            # not measured on a radio link.
            "contact_state": contact_state,
            "prediction_window_hours": _PASS_WINDOW_HOURS,
            "queued_bundles": dtn_stats.get("queued_bundle_count", 0),
        },
        "mesh": {
            # In-process Raft harness: these members are simulated, not hosts on a network.
            "simulated": True,
            "raft_leader": leader,
            "local_role": local_role,
            "nodes_count": len(cluster.nodes),
            "nodes": mesh_nodes,
        },
        "economy": {
            "wallet_address": wallet.address,
            "balance_credits": balance_credits,
            "algorithm": wallet.keypair.algorithm.value,
            "energy_multiplier": mult,
            "energy_state": status_desc,
            "recent_transactions_count": recent_tx_count,
        },
        # Derived from a real check (see get_pqc_status), not a constant.
        "pqc": pqc_status,
        "metaverse": get_metaverse_orchestrator().get_latest_status(),
        "events_count": len(get_event_bus().get_history()),
    }


_STATUS_CACHE_TTL_SECONDS = 1.0
_STATUS_CACHE_LOCK = threading.Lock()
_STATUS_CACHE: tuple[float, Any, dict[str, Any]] | None = None


def invalidate_status_cache() -> None:
    """Drops the cached status payload (after a control action changes what it reports)."""
    global _STATUS_CACHE
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE = None


def get_cached_system_status_payload(max_age_seconds: float = _STATUS_CACHE_TTL_SECONDS) -> dict[str, Any]:
    """Status payload shared by ``/api/status`` and every SSE client.

    Assembling the payload opens SQLite connections and runs a pass prediction, so it is
    built at most once per ``max_age_seconds`` however many clients are streaming.
    Failures are not cached, and a configuration reload discards the cached payload.
    """
    global _STATUS_CACHE
    cfg = get_config()
    with _STATUS_CACHE_LOCK:
        cached = _STATUS_CACHE
        if cached is not None and cached[1] is cfg and 0.0 <= time.monotonic() - cached[0] < max_age_seconds:
            return cached[2]
        payload = get_system_status_payload()
        _STATUS_CACHE = (time.monotonic(), cfg, payload)
        return payload


def _is_loopback_origin(origin: str) -> bool:
    """True for an http(s) browser origin whose host is this machine."""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and _is_loopback_host(parts.hostname or "")


def _configured_cors_origins() -> list[str]:
    """Origins listed in SMDC_CORS_ORIGIN (comma or whitespace separated; ``*`` allows any)."""
    raw = os.environ.get("SMDC_CORS_ORIGIN", "")
    return [item.rstrip("/") for item in re.split(r"[\s,]+", raw) if item]


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Sovereign Operations Dashboard & REST APIs."""

    def _cors_allow_origin(self) -> str | None:
        """The ``Access-Control-Allow-Origin`` value for this request, or None for no CORS access.

        Cross-origin reads are denied by default. A loopback origin (the locally served 3D
        twin) is reflected, as is an origin listed in SMDC_CORS_ORIGIN; ``*`` there opts in
        to any origin.
        """
        configured = _configured_cors_origins()
        if "*" in configured:
            return "*"
        origin = str(self.headers.get("Origin", "") or "").strip()
        if not origin:
            return None
        if origin.rstrip("/") in configured or _is_loopback_origin(origin):
            return origin
        return None

    def _send_cors_headers(self) -> None:
        self.send_header("Vary", "Origin")
        allow_origin = self._cors_allow_origin()
        if allow_origin is None:
            return
        self.send_header("Access-Control-Allow-Origin", allow_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _bearer_token_matches(self, expected_token: str, allow_query_token: bool = False) -> bool:
        """Constant-time check of the ``Authorization: Bearer`` header (or ``?token=``)."""
        provided = ""
        auth_header = str(self.headers.get("Authorization", "") or "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[len("Bearer ") :].strip()
        elif allow_query_token:
            # EventSource cannot set headers, so the SSE stream also accepts ?token=.
            values = parse_qs(urlsplit(self.path).query).get("token", [])
            provided = values[0].strip() if values else ""
        if not provided:
            return False
        return hmac.compare_digest(provided.encode("utf-8"), expected_token.encode("utf-8"))

    def _is_local_request(self) -> bool:
        """True when the request was made on this machine and was not relayed by a proxy."""
        client = getattr(self, "client_address", None)
        peer_host = str(client[0]) if client else ""
        if not _is_loopback_host(peer_host):
            return False
        # A reverse proxy connects from loopback on behalf of a remote client.
        return not any(self.headers.get(name) for name in ("X-Forwarded-For", "Forwarded", "X-Real-IP"))

    def _check_get_auth(self, allow_query_token: bool = False) -> bool:
        """Authorizes a data GET (JSON endpoints and the SSE stream).

        Without SMDC_DASHBOARD_TOKEN the endpoints stay open, as before (the default bind is
        loopback and cross-origin browser reads are denied by the CORS policy). With the
        token set, any request that is not made locally must present it.
        """
        expected_token = os.environ.get("SMDC_DASHBOARD_TOKEN", "").strip()
        if not expected_token or self._is_local_request():
            return True
        return self._bearer_token_matches(expected_token, allow_query_token=allow_query_token)

    def _send_unauthorized(self) -> None:
        payload = json.dumps({"success": False, "error": "Unauthorized"}).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", "Bearer")
        self._send_cors_headers()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
            return self._bearer_token_matches(expected_token)

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
        elif path in _PROTECTED_GET_PATHS and not self._check_get_auth(
            allow_query_token=path == "/api/telemetry/stream"
        ):
            self._send_unauthorized()
        elif path == "/api/status":
            try:
                status_payload = get_cached_system_status_payload()
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
            # The orchestrator's own slicing simulator: the same figures as "metaverse.slices"
            # in /api/status (a fresh manager would always report zero traffic).
            payload = json.dumps(get_metaverse_orchestrator().slicing.get_summary()).encode("utf-8")
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
                        frame = f"data: {json.dumps(get_cached_system_status_payload())}\n\n"
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
        finally:
            # A control action may have changed what /api/status reports.
            invalidate_status_cache()

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
            # Spool an RFC 9171 bundle (see the DTN submission contract above)
            from sovereign_dc.events import Event

            cfg = get_config()
            submission = parse_dtn_submission(req_data, cfg.node_id)
            router = DTNRouter(db_path=cfg.dtn_db_path)
            bundle = submission.to_bundle()
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
                **describe_dtn_bundle(bundle, ttl_capped=submission.ttl_capped),
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
    beyond loopback, control POSTs are refused unless SMDC_DASHBOARD_TOKEN is configured;
    with the token set, remote clients need it for the JSON and SSE data endpoints too.
    Cross-origin browser access is limited to loopback origins and SMDC_CORS_ORIGIN.
    """
    bind_host = host or os.environ.get("SMDC_DASHBOARD_HOST", "").strip() or "127.0.0.1"
    server = HTTPServer((bind_host, port), DashboardHandler)
    server.daemon_threads = True  # SSE streams must not block shutdown
    url = f"http://localhost:{port}"
    logger.info("Sovereign Operations Web Dashboard listening on %s:%d", bind_host, port)
    if not _is_loopback_host(bind_host) and not os.environ.get("SMDC_DASHBOARD_TOKEN", "").strip():
        logger.warning(
            "Dashboard exposed on %s without SMDC_DASHBOARD_TOKEN: control endpoints are disabled "
            "and the status/telemetry endpoints are readable by anyone who can reach this address",
            bind_host,
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
