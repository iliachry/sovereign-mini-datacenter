"""Sovereign Mini Datacenter — Real-Time Operations Web Dashboard & REST API.

Provides an ultra-portable, zero-external-dependency HTTP server hosting:
1. REST API endpoints for telemetry, space link, mesh cluster, and PQC status.
2. A high-performance, dark-mode glassmorphic single-page operations interface
   for field operators and remote NOC engineers.
"""

from __future__ import annotations

import json
import logging
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from sovereign_dc import __version__
from sovereign_dc.config import get_config
from sovereign_dc.events import get_event_bus
from sovereign_dc.hal.power import read_power
from sovereign_dc.hal.storage import detect_storage
from sovereign_dc.hal.thermal import read_thermal
from sovereign_dc.mesh.consensus import RaftCluster
from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner
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
        <div class="status-row"><span>smdc-node-01 (Athens Core)</span><span class="code" style="color: var(--accent);">100.64.0.1 • 550 TOPS • ONLINE</span></div>
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
      <div class="card-title"><span>🔐 Storage & Cryptography</span><span class="code">ZFS ENCRYPTED</span></div>
      <div class="status-row"><span>NVMe Storage</span><span class="code" id="storage-used">-- GB / -- GB Free</span></div>
      <div class="status-row"><span>PQC Digital Signatures</span><span class="code" style="color: var(--accent);">ML-DSA-87 (FIPS 204)</span></div>
      <div class="status-row"><span>PQC Key Encapsulation</span><span class="code" style="color: var(--accent-purple);">ML-KEM-1024 (FIPS 203)</span></div>
      <div class="status-row"><span>Intrusion Prevention</span><span class="code" style="color: var(--accent);">CrowdSec ACTIVE</span></div>
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
        document.getElementById('solar-bar').style.width = `${Math.min(100, (solar / 1640) * 100)}%`;

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
      } catch (err) {
        console.warn('Telemetry fetch notice:', err);
      }
    }
    updateTelemetry();
    setInterval(updateTelemetry, 2500);
  </script>
</body>
</html>
"""


# Global Digital Shadow Hardware State (Synchronized across all connected 3D Twins)
_HARDWARE_STATE: dict[str, Any] = {
    "door_open": False,
    "pdu_outlets": [True, True, True, True, True, True, True, True],
    "fan_rpm": 2400,
    "last_bundle_tx": 0,
}


def get_system_status_payload() -> dict[str, Any]:
    """Assembles unified live system status JSON for REST API and dashboard."""
    import os
    import tempfile

    from sovereign_dc.economy.ledger import Ledger
    from sovereign_dc.economy.market import ComputeMarket
    from sovereign_dc.economy.wallet import NodeWallet

    cfg = get_config()
    power = read_power()
    thermal = read_thermal()
    storage = detect_storage()

    # Dynamic Fan RPM calculation based on thermal state and system load
    base_rpm = 1800
    temp_factor = max(0.0, (thermal.coolant_celsius - 25.0) * 80.0)
    load_factor = (power.system_load_watts / 600.0) * 800.0
    _HARDWARE_STATE["fan_rpm"] = int(min(4800, base_rpm + temp_factor + load_factor))

    # Space satellite calculation
    satellites = get_active_satellites()
    gs = GroundStation(cfg.ground_station_name, cfg.ground_station_lat, cfg.ground_station_lon)
    passes = gs.predict_passes(satellites[0], duration_hours=6.0) if satellites else []
    next_sec = max(0, int(passes[0]["aos_time"] - time.time())) if passes else 0

    router = DTNRouter(db_path=cfg.dtn_db_path)
    dtn_stats = router.get_queue_stats()

    signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
    kp = signer.generate_keypair()

    cluster = RaftCluster(["smdc-node-01", "smdc-node-02", "smdc-node-03"])
    leader = cluster.step_election("smdc-node-01")

    # Economy and wallet status
    wallet_path = os.path.join(tempfile.gettempdir(), "smdc_dashboard_wallet.json")
    db_path = os.path.join(tempfile.gettempdir(), "smdc_dashboard_ledger.db")
    if not os.path.exists(wallet_path):
        wallet = NodeWallet.create(node_id=cfg.node_id)
        wallet.save_to_file(wallet_path)
        ledger = Ledger(db_path=db_path)
        ledger.mint(wallet.address, 100.0, memo="DASHBOARD_GENESIS")
    else:
        wallet = NodeWallet.load_from_file(wallet_path)
        ledger = Ledger(db_path=db_path)

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
        "hardware": {
            "door_open": _HARDWARE_STATE["door_open"],
            "pdu_outlets": _HARDWARE_STATE["pdu_outlets"],
            "fan_rpm": _HARDWARE_STATE["fan_rpm"],
            "last_bundle_tx": _HARDWARE_STATE["last_bundle_tx"],
        },
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
            "balance_credits": ledger.get_balance(wallet.address),
            "algorithm": wallet.keypair.algorithm.value,
            "energy_multiplier": mult,
            "energy_state": status_desc,
            "recent_transactions_count": len(ledger.get_history(limit=50)),
        },
        "pqc": {
            "algorithm": PQCAlgorithm.ML_DSA_65.value,
            "key_id": kp.key_id,
            "status": "OPERATIONAL",
        },
        "events_count": len(get_event_bus().get_history()),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Sovereign Operations Dashboard & REST APIs."""

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            payload = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/api/status":
            payload = json.dumps(get_system_status_payload()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/api/telemetry/stream":
            # Server-Sent Events (SSE) Live Telemetry Stream
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._send_cors_headers()
            self.end_headers()

            # Transmit snapshot event
            status_data = get_system_status_payload()
            event_payload = f"data: {json.dumps(status_data)}\n\n".encode()
            try:
                self.wfile.write(event_payload)
                self.wfile.flush()
            except Exception:
                pass
        elif self.path in ("/health", "/healthz"):
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
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            req_data = json.loads(body) if body else {}
        except Exception:
            req_data = {}

        if self.path == "/api/control/rack-door":
            # Toggle or explicitly set rack door state
            from sovereign_dc.events import Event

            target_state = req_data.get("open", not _HARDWARE_STATE["door_open"])
            _HARDWARE_STATE["door_open"] = bool(target_state)
            get_event_bus().publish(
                Event(
                    event_type="hardware.door.changed",
                    source="dashboard",
                    payload={"open": _HARDWARE_STATE["door_open"]},
                )
            )

            res = {"success": True, "door_open": _HARDWARE_STATE["door_open"]}
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif self.path == "/api/control/pdu-outlet":
            from sovereign_dc.events import Event

            outlet_idx = int(req_data.get("outlet", 0))
            if 0 <= outlet_idx < len(_HARDWARE_STATE["pdu_outlets"]):
                new_state = req_data.get("state", not _HARDWARE_STATE["pdu_outlets"][outlet_idx])
                _HARDWARE_STATE["pdu_outlets"][outlet_idx] = bool(new_state)
                get_event_bus().publish(
                    Event(
                        event_type="hardware.pdu.outlet_changed",
                        source="dashboard",
                        payload={"outlet": outlet_idx, "state": _HARDWARE_STATE["pdu_outlets"][outlet_idx]},
                    )
                )

            res = {"success": True, "pdu_outlets": _HARDWARE_STATE["pdu_outlets"]}
            payload = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif self.path == "/api/control/dtn-transmit":
            # Spool and dispatch an RFC 9171 Space Bundle
            from sovereign_dc.events import Event
            from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority

            cfg = get_config()
            router = DTNRouter(db_path=cfg.dtn_db_path)
            bundle_payload = req_data.get("payload", f"STATUS_TELEMETRY_{int(time.time())}").encode("utf-8")
            priority_val = int(req_data.get("priority", 2))
            bundle_prio = BundlePriority.EXPEDITED if priority_val >= 2 else BundlePriority.NORMAL

            bundle = Bundle(
                source_eid=cfg.node_id,
                destination_eid=req_data.get("destination", "dtn://ground-station-alpha.earth/telemetry"),
                payload=bundle_payload,
                priority=bundle_prio,
            )
            router.queue_bundle(bundle)
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

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stdout HTTP access logs in production CLI."""


def run_dashboard_server(port: int = 8080, open_browser: bool = False) -> None:
    """Launches the Sovereign Web Operations Dashboard HTTP server."""
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    url = f"http://localhost:{port}"
    logger.info("Sovereign Operations Web Dashboard listening at %s", url)
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
        server.server_close()
