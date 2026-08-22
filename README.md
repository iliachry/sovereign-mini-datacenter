# Sovereign Mini Datacenter

![Sovereign Mini Datacenter 3D Render](cad/render.jpg)

**Sovereign Mini Datacenter** is a self-powered, solar-backed, liquid-cooled micro-datacenter stack designed for **complete data and computational sovereignty**. Run your own private AI with semantic RAG, Git hosting, project management, encrypted file cloud, email server, password vault, and zero-trust mesh VPN — fully off-grid capable.

Developed by **[Metatopia Studio](https://metatopia.gr)** · License: MIT · © 2026

[![CI](https://github.com/iliachry/sovereign-mini-datacenter/actions/workflows/ci.yml/badge.svg)](https://github.com/iliachry/sovereign-mini-datacenter/actions/workflows/ci.yml)
[![3D WebGL Viewer](https://img.shields.io/badge/3D%20CAD%20Viewer-Live%20Demo-10b981?style=flat&logo=three.js)](https://iliachry.gr/sovereign-mini-datacenter/)
[![AI Agents Guide](https://img.shields.io/badge/AI%20Agents-Engineering%20Playbook-6366f1?style=flat)](AGENTS.md)
[![Architecture](https://img.shields.io/badge/Architecture-Autonomous%20Mesh-3b82f6?style=flat)](ARCHITECTURE.md)

---

## 🌐 Live Interactive 3D Viewer

Inspect the 9U 19" chassis, rails, liquid-cooling radiator cutouts, and physical dimensions directly in your browser:
👉 **[Open 3D WebGL CAD Viewer](https://iliachry.gr/sovereign-mini-datacenter/)**

---

## 🏛️ System Architecture

> 📖 **Looking for the full multi-node network architecture?** See the comprehensive [Autonomous Sovereign Mesh Architecture](ARCHITECTURE.md) blueprint covering 7-layer protocol stacks, energy-directed workload scheduling, space DTN fallbacks, and zero-trust PQC security.

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                   INTERNET / OFF-GRID                   │
                    └───────────┬─────────────────────────────────┬───────────┘
                                │ Public HTTPS (443)              │ Encrypted WireGuard (3478)
                                ▼                                 ▼
                    ┌───────────────────────┐         ┌───────────────────────┐
                    │       TRAEFIK v3      │         │   HEADSCALE MESH VPN  │
                    │  (Automatic TLS / LE) │◄───────►│  (Zero-Trust Overlay) │
                    └───────────┬───────────┘         └───────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┬───────────────────────┐
        ▼                       ▼                       ▼                       ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│  OPEN-WEBUI   │       │   GITLAB CE   │       │  OPENPROJECT  │       │   NEXTCLOUD   │
│  Private AI   │       │  Code & CI/CD │       │  Project Mgmt │       │  Cloud Files  │
└───────┬───────┘       └───────────────┘       └───────────────┘       └───────────────┘
        │
        ├───────────────────────┐
        ▼                       ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│  OLLAMA (GPU) │       │ QDRANT VECTOR │       │  VAULTWARDEN  │       │    MAILCOW    │
│  LLM Engine   │       │  Private RAG  │       │ Password Safe │       │ Sovereign Mail│
└───────┬───────┘       └───────────────┘       └───────────────┘       └───────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                      TELEMETRY, AUTOMATION & DISASTER RECOVERY                        │
├───────────────────────┬───────────────────────┬───────────────────────────────────────┤
│    PROMETHEUS &       │   POWER & BMS         │          RESTIC BACKUP                │
│       GRAFANA         │   TELEMETRY EXPORTER  │             ENGINE                    │
│ Metrics & Dashboards  │ Solar / Battery / Temp│ AES-256 Snapshots to NVMe / S3 / B2   │
└───────────────────────┴───────────┬───────────┴───────────────────────────────────────┘
                                    │
                                    ▼ (Dynamic Load-Shedding Trigger)
                        ┌───────────────────────┐
                        │ LOAD-SHEDDER SENTINEL │
                        │ Auto-Throttles GPU if │
                        │  Battery SoC < 20%    │
                        └───────────────────────┘
```

---

## 🏗️ Repository Structure

```
sovereign-mini-datacenter/
├── src/
│   └── sovereign_dc/            # Python CLI & Core Engine (`smdc`)
│       ├── cli.py               # Unified management CLI entry point
│       ├── __main__.py          # `python -m sovereign_dc` execution support
│       ├── agents/              # Sentinel, Indexer & Reviewer AI integrations
│       ├── mesh/                # Multi-node WireGuard & LoRa bridge logic
│       ├── space/               # Space DTN routing & orbital propagator
│       └── telemetry/           # Power & thermal telemetry collector
├── software/
│   ├── docker-compose.yml       # Sovereign Core 11-service production stack
│   ├── setup.sh                 # Modular deployment CLI (--all, --with-vpn, etc.)
│   ├── env.example              # Environment configuration template
│   ├── prometheus.yml           # Prometheus scrape configuration (Node, Space, ESP32, LoRa)
│   ├── agents/                  # AI Knowledge Indexer & Code Reviewer daemons
│   ├── mesh/                    # WireGuard mesh & LoRa Meshtastic packet gateway
│   ├── vpn/                     # Zero-Trust Mesh VPN (Headscale)
│   ├── backup/                  # Encrypted Backup & Recovery (Restic)
│   ├── telemetry/               # Solar/BMS Telemetry & Load-Shedding Sentinel
│   ├── space/                   # Space & Satellite Communications (DTN / BPv7)
│   ├── mailcow/                 # Sovereign Email Stack
│   └── grafana/
│       └── provisioning/        # Auto-provisioned dashboards (Power, Thermal, Space)
├── kubernetes/
│   └── helm/
│       └── sovereign-stack/     # Production Helm chart (K3s/Talos AI & Telemetry cluster)
├── firmware/
│   ├── esp32_telemetry_bridge.ino # Arduino C++ ESP32 firmware with I2C OLED display
│   └── esphome_smdc_bridge.yaml   # ESPHome firmware with MQTT discovery & metrics
├── hardware/
│   ├── COMPONENTS.md            # Full Bill of Materials with pricing & electrical specs
│   └── WIRING_DIAGRAM.md        # DC/AC electrical, liquid cooling & network schematics
├── cad/
│   ├── rack_enclosure.scad      # Parametric OpenSCAD 9U 19" chassis model
│   ├── accessories.scad         # 3D printable DIN rails, Jetson mounts, OLED bezels
│   ├── MANUFACTURING_GUIDE.md   # Laser cut, CNC bend, and assembly instructions
│   └── render.jpg               # Photorealistic 3D product render
├── tests/                       # 92 Automated unit & integration tests (94% coverage)
├── docs/                        # Interactive Three.js WebGL CAD & Space Viewer for GitHub Pages
└── .github/
    └── workflows/
        ├── ci.yml               # Complete CI pipeline + Pytest + GitHub Pages deploy
        └── publish.yml          # Automated PyPI package & GHCR multi-arch release pipeline
```

---

## 🐍 Python CLI Package (`smdc`)

Manage the datacenter, live telemetry, space communications, autonomous AI agents, and security audits directly from your terminal:

```bash
pip install sovereign-dc
# or with uv:
uv tool install sovereign-dc
```

```bash
# Check container health, solar power, and space link telemetry
smdc status

# Run automated security compliance & CIS benchmark audit
smdc audit

# Autonomous AI Agent operations
smdc agent status                                    # Inspect running agent daemons & Ollama status
smdc agent ask "How do I throttle background jobs?" # Ask Sentinel Copilot directly
smdc agent review --diff patch.diff                  # AI code review on local git diff
smdc agent index --path /data/docs                   # Trigger semantic RAG vector indexing

# Inspect multi-node global mesh cluster topology
smdc mesh

# Predict upcoming satellite contact passes (AOS / TCA / LOS)
smdc space passes --hours 12

# Inspect real-time space link budget, SNR, and Doppler shift
smdc space status

# Queue an encrypted bundle for space transmission on next orbital pass
smdc space send dtn://ground-station-alpha.earth/telemetry "STATUS_OK" --priority 2

# Inspect DTN store-and-forward spool queue
smdc space queue

# Deploy all container stacks (Core, VPN, Backup, Telemetry, Space, Agents, Security)
smdc deploy --all
```


---

## 🏛️ Advanced Capabilities & Pillars

### 1. 🤖 Autonomous Local AI Agents (`software/agents/`)
* **Nextcloud Knowledge Indexer:** Daemon that monitors folders, extracts text from documents, embeds them via Ollama (`nomic-embed-text`), and stores vectors in Qdrant for semantic RAG in Open-WebUI.
* **GitLab Automated Code Reviewer:** Webhook worker that analyzes Merge Request diffs with local LLMs (`qwen2.5-coder`) and posts inline security and quality reviews.
* **Datacenter Sentinel Copilot:** AI assistant that schedules heavy GPU training workloads during peak solar hours and throttles jobs during battery preservation mode.

### 2. 🔌 ESP32 Microcontroller Hardware Bridge (`firmware/`)
* C++ Arduino and ESPHome firmware reading physical **Victron VE.Direct** serial streams, **LiFePO4 RS485 Modbus** BMS registers, and **DS18B20 1-Wire** coolant temperature probes, broadcasting via HTTP `/metrics` and MQTT.

### 3. 🛡️ Security Hardening & Zero-Trust Sentinel (`software/security/`)
* **CrowdSec Intrusion Prevention:** Automated threat bouncer integration with Traefik to block brute-force scanners and malicious IP ranges.
* **`smdc audit` Scanner:** Evaluates host kernel sysctl parameters, Docker socket isolation, TLS configurations, and firewall rules.

### 4. 🧮 Interactive 3D Sizing & Cost Configurator
* Live WebGL sizing calculator on **[https://iliachry.gr/sovereign-mini-datacenter/](https://iliachry.gr/sovereign-mini-datacenter/)** simulating daily kWh draw, solar harvest, zero-sun battery autonomy, and dynamic BOM cost with one-click CSV export.

### 5. 🌐 Multi-Node Sovereign Mesh (`software/mesh/`)
* Inter-datacenter cluster synchronization over WireGuard mesh and BPv7 space bundle relays.

---

## 🚀 Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/iliachry/sovereign-mini-datacenter.git
cd sovereign-mini-datacenter/software

# Copy template and fill in your domain names and secrets
cp env.example .env && nano .env
```

### 2. Deploy via Modular CLI

```bash
# Option A: Deploy everything (Core + VPN + Backup + Telemetry + Mailcow)
sudo bash setup.sh --all

# Option B: Deploy Core Stack only
sudo bash setup.sh

# Option C: Deploy Core Stack with Zero-Trust VPN and Automated Backup
sudo bash setup.sh --with-vpn --with-backup

# Option D: Dry-run configuration test
bash setup.sh --dry-run
```

---

## 📦 Services Catalog

All web services are securely routed over **HTTPS** via Traefik with automatic Let's Encrypt TLS:

| Service | Default URL | Purpose |
|:--|:--|:--|
| 🤖 **Open-WebUI** | `https://ai.yourdomain.com` | Private ChatGPT-style interface with local RAG |
| 🦙 **Ollama API** | Internal (`:11434`) | GPU-accelerated local LLM inference engine |
| 🧠 **Qdrant** | Internal (`:6333`) | High-performance vector database for document embeddings |
| 🦊 **GitLab CE** | `https://gitlab.yourdomain.com` | Self-hosted Git repositories & CI/CD pipelines |
| 📋 **OpenProject** | `https://projects.yourdomain.com` | Project tracking, Gantt charts, and task management |
| ☁️ **Nextcloud** | `https://cloud.yourdomain.com` | End-to-end encrypted file sync, calendar, and office suite |
| 🔐 **Vaultwarden** | `https://vault.yourdomain.com` | Bitwarden-compatible password manager |
| 📬 **Mailcow** | `https://mail.yourdomain.com` | Sovereign email stack (SOGo Webmail + Postfix/Dovecot) |
| 🛡️ **Headscale VPN** | `https://vpn.yourdomain.com` | Self-hosted Zero-Trust WireGuard Mesh control plane |
| 📊 **Grafana** | `https://grafana.yourdomain.com` | Infrastructure, container, power & thermal dashboards |
| 📈 **Prometheus** | `https://metrics.yourdomain.com` | Time-series metrics aggregator (HTTP Basic Auth protected) |
| 🔀 **Traefik** | `https://traefik.yourdomain.com` | Reverse proxy edge router & dashboard |

---

## 🛡️ Sovereign Remote Mesh VPN (Headscale)

Connect phones, laptops, and remote workstations securely without public port-forwarding:

```bash
# 1. Create a user
./software/vpn/register-node.sh create-user admin

# 2. Generate a 90-day pre-authenticated key
./software/vpn/register-node.sh create-authkey admin --reusable --expiration 90d

# 3. Connect client devices
tailscale up --login-server https://vpn.yourdomain.com --authkey <AUTH_KEY>
```

See [`software/vpn/README.md`](software/vpn/README.md) for full configuration details.

---

## 💾 Encrypted Backup & Disaster Recovery (Restic)

- **Local & Remote Targets:** Snapshots saved locally to `/var/backups/sovereign` (cold NVMe) and optionally synced to off-site S3/MinIO.
- **Deduplicated & Encrypted:** AES-256 encryption with automated bit-rot verification.

```bash
# Run an immediate backup
sudo bash software/backup/backup.sh

# List available snapshots
sudo bash software/backup/restore.sh list

# Disaster recovery restore
sudo bash software/backup/restore.sh restore-latest /mnt/restore_target
```

See [`software/backup/README.md`](software/backup/README.md) for automated cron scheduling and restore procedures.

---

## ☀️ Power, Solar & BMS Telemetry

- **Real-time Metrics:** Collects solar PV generation, LiFePO4 battery pack SoC %, DC bus voltage, current, and coolant temperatures.
- **Autonomous Load-Shedder:** If battery SoC drops below 20% or coolant exceeds 60°C, `software/telemetry/load_shedder.sh` automatically pauses intensive background AI batch processing.

---

## ☸️ Kubernetes & GitOps Deployment (Helm / K3s / Talos)

Deploy the Sovereign stack onto edge Kubernetes clusters (K3s, Talos, MicroK8s):

```bash
# 1. Inspect the sovereign-stack Helm chart
helm lint kubernetes/helm/sovereign-stack

# 2. Deploy AI cluster & telemetry to your sovereign namespace
helm upgrade --install sovereign-stack ./kubernetes/helm/sovereign-stack \
  --namespace sovereign \
  --create-namespace \
  --values kubernetes/helm/sovereign-stack/values.yaml
```

---

## 🧪 Comprehensive Unit & Integration Tests

The project includes an extensive test suite covering DTN BPv7 routing, orbital mechanics, link budgets, AI agent prompts, mesh encoding, and CLI interfaces:

```bash
# Run the test suite with coverage
uv run --with pytest --with pyyaml pytest -v --cov=sovereign_dc
```

---

## 🔋 Hardware & Physical Enclosure

- **Compute:** 2× NVIDIA DGX Spark (550 TOPS aggregate AI compute, 128GB unified memory)
- **Storage:** 2× Samsung 990 PRO 4TB NVMe (8TB total NVMe storage)
- **Power:** 10.24 kWh LiFePO4 battery bank + 1,640W solar PV array
- **Cooling:** Dual 360mm radiator loop with Alphacool D5 pump
- **Chassis:** Custom 9U 19" aluminum rack enclosure (EIA-310-D compliant)

See [`hardware/COMPONENTS.md`](hardware/COMPONENTS.md) for the complete Bill of Materials and [`cad/MANUFACTURING_GUIDE.md`](cad/MANUFACTURING_GUIDE.md) for laser cutting and CNC bending instructions.

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.