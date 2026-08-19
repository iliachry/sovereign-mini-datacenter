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

Over the past months, we designed and built the Sovereign Mini Datacenter — a complete open-source hardware & software stack for 100% computational and data sovereignty.

It is a 9U 19" aluminum rack system powered by 10.24 kWh LiFePO4 batteries and 1.64 kW solar PV, featuring:

• 🤖 Private Local AI: 2x NVIDIA DGX Spark / Jetson nodes (550 TOPS INT8) running Ollama + Qdrant for semantic RAG, with an automated GitLab Code Reviewer and Nextcloud Knowledge Indexer.
• ⚡ Solar Micro-Grid & Load Shedding: Victron MultiPlus-II inverter, SmartSolar MPPT, and a dynamic sentinel that throttles heavy GPU batch jobs if battery SoC drops below 25%.
• 🛰️ Space Communications (DTN / BPv7): RFC 9171 Delay-Tolerant Networking with an SGP4 orbital mechanics pass predictor, tracking LEO/MEO satellite relays when terrestrial internet fails.
• 🔌 ESP32 Microcontroller Bridge: Physical VE.Direct serial stream parser, RS485 Modbus battery BMS reader, and DS18B20 1-Wire liquid coolant telemetry.
• 🛡️ Zero-Trust Security: Headscale WireGuard mesh VPN + CrowdSec automated intrusion prevention with `smdc audit` CIS scanning.
• 🌐 Live 3D WebGL Digital Twin: Interactive 3D model with 0-100% exploded assembly, thermal heatmap, real-time motorized satellite tracking, and an interactive sizing calculator.

Live 3D Digital Twin & Sizing Calculator: https://iliachry.gr/sovereign-mini-datacenter/
GitHub Repo: https://github.com/iliachry/sovereign-mini-datacenter
Python CLI: `pip install sovereign-dc` / `smdc`

We'd love to hear your feedback on the CAD blueprints, electrical diagrams, and DTN space communications architecture!
```

---

## 2. 🤖 Reddit Launch Post (r/selfhosted & r/homelab)

**Title:**  
`I built an open-source 9U Sovereign Mini Datacenter: Solar-powered AI cluster, liquid cooling, and Space DTN communications`

**Post Body:**
```markdown
Hey r/selfhosted and r/homelab!

I wanted to share the Sovereign Mini Datacenter — an end-to-end open-source blueprint for off-grid self-hosting.

### 🛠️ Hardware Specs:
* 9U 19" EIA-310-D custom laser-cut aluminum enclosure
* 2x NVIDIA DGX Spark / Jetson compute nodes (550 TOPS aggregate AI compute)
* 8 TB NVMe RAID-1 PCIe 4.0 storage
* MikroTik CRS309 10GbE SFP+ switch
* 10.24 kWh LiFePO4 48V server-rack battery bank
* 1,640W Monocrystalline PERC solar array (4x 410W)
* Victron MultiPlus-II 48/3000 Inverter + SmartSolar MPPT 150/70
* Dual 360mm copper radiator loop + Alphacool D5 pump

### 💻 Software & Space Framework:
* Core 11-service stack: Traefik, Ollama, Qdrant, GitLab, OpenProject, Nextcloud, Vaultwarden, Mailcow, Prometheus, Grafana
* Autonomous Local AI Agents for code review and document vectorization
* Delay-Tolerant Space Comms (RFC 9171 BPv7) + SGP4 satellite pass predictor
* ESP32 physical telemetry bridge reading VE.Direct & RS485 Modbus
* CrowdSec zero-trust intrusion prevention

👉 Interactive 3D WebGL Model & Off-Grid Sizing Calculator: https://iliachry.gr/sovereign-mini-datacenter/
👉 Full Blueprints, BOM & Schematics: https://github.com/iliachry/sovereign-mini-datacenter
```
