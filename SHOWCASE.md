# Sovereign Mini Datacenter — Community & Launch Kit

> Ready-to-publish launch copy, titles, and diagrams for **Hacker News (Show HN)**, **Reddit (r/selfhosted, r/homelab)**, and **Product Hunt**.

---

## 1. 🚀 Hacker News (Show HN) Post

**Title:**  
`Show HN: Sovereign Mini Datacenter – Self-Powered AI, Solar Micro-Grid and Space Comms`

**URL:**  
`https://github.com/iliachry/sovereign-mini-datacenter`

**Post Body:**
```markdown
Hi HN!

Over the past months, we designed the Sovereign Mini Datacenter — an open-source hardware design and software stack for computational and data sovereignty.

Status, up front: this is a design (CAD, BOM, wiring) plus a software stack validated in simulation and in CI (tests, Compose validation, host-side firmware parser tests, ESP32 cross-compile). No physical build or reference-rig run is documented yet, so every hardware figure below is a design value, not a measurement.

The design is a 9U 19" aluminum rack system powered by 10.24 kWh LiFePO4 batteries and 3.28 kW solar PV, with:

• 🤖 Private Local AI: 2x NVIDIA DGX Spark nodes (128GB unified memory each) running Ollama + Qdrant for semantic RAG, with an automated GitLab Code Reviewer and Nextcloud Knowledge Indexer.
• ⚡ Solar Micro-Grid & Load Shedding: Victron MultiPlus-II inverter, SmartSolar MPPT, and a sentinel that switches the node to a power-saving mode when battery SoC drops below 30% (the document indexer then defers its batch work).
• 🛰️ Space Communications (DTN / BPv7): RFC 9171 Delay-Tolerant Networking store-and-forward spool with a simplified orbital pass predictor and a simulated link (no radio driver yet).
• 🔌 ESP32 Microcontroller Bridge: firmware with a VE.Direct serial stream parser and DS18B20 1-Wire coolant telemetry (parser tested on the host, sketch cross-compiled for ESP32 in CI; not yet run on a rig).
• 🛡️ Security: Headscale WireGuard mesh VPN + CrowdSec intrusion detection (detection only, no blocking yet) with the `smdc audit` Linux hardening audit. No certification; planned items (mutual TLS, TPM-sealed keys) are listed in COMPLIANCE.md.
• 🌐 3D WebGL Digital Twin: Interactive 3D model with 0-100% exploded assembly, an illustrative thermal heatmap, an animated satellite-tracking antenna, and an interactive sizing calculator. It shows simulated data unless pointed at a running node.

3D Digital Twin & Sizing Calculator: https://iliachry.gr/sovereign-mini-datacenter/
GitHub Repo: https://github.com/iliachry/sovereign-mini-datacenter
Python CLI: `pip install sovereign-dc` / `smdc`

We'd love to hear your feedback on the CAD blueprints, electrical diagrams, and DTN space communications architecture!
```

---

## 2. 🤖 Reddit Launch Post (r/selfhosted & r/homelab)

**Title:**  
`I designed an open-source 9U Sovereign Mini Datacenter: Solar-powered AI cluster, battery storage, and Space DTN communications`

**Post Body:**
```markdown
Hey r/selfhosted and r/homelab!

I wanted to share the Sovereign Mini Datacenter — an end-to-end open-source blueprint for off-grid self-hosting. It is a design plus a software stack validated in simulation and CI; I have not documented a physical build yet, so the hardware list below is the design specification.

### 🛠️ Hardware Specs (design):
* 9U 19" EIA-310-D custom laser-cut aluminum enclosure
* 2x NVIDIA DGX Spark compute nodes (GB10 Superchip, 128GB unified memory each)
* 2× 4 TB NVMe PCIe 4.0 storage
* MikroTik CRS309 10GbE SFP+ switch
* 10.24 kWh LiFePO4 48V server-rack battery bank
* 3,280W Monocrystalline PERC solar array (8x 410W)
* Victron MultiPlus-II 48/3000 Inverter + SmartSolar MPPT 250/70
* Single 360mm copper radiator loop + Alphacool D5 pump for the system host CPU; the DGX Spark units are air-cooled, with three rear exhaust fans

### 💻 Software & Space Framework:
* Core 12-service stack: Traefik, Ollama, Open WebUI, Qdrant, GitLab, OpenProject, Nextcloud, Vaultwarden, Prometheus, Grafana, cAdvisor, node-exporter (Mailcow is installed separately)
* Autonomous Local AI Agents for code review and document vectorization
* Delay-Tolerant Space Comms (RFC 9171 BPv7 spool) + simplified satellite pass predictor (simulated link)
* ESP32 telemetry bridge firmware for VE.Direct and DS18B20 probes (host-tested parser)
* CrowdSec intrusion detection (bouncer not yet wired)

👉 Interactive 3D WebGL Model & Off-Grid Sizing Calculator: https://iliachry.gr/sovereign-mini-datacenter/
👉 Full Blueprints, BOM & Schematics: https://github.com/iliachry/sovereign-mini-datacenter
```
