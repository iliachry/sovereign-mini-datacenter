# Sovereign Mini Datacenter

![Sovereign Mini Datacenter 3D Render](cad/render.jpg)

**Sovereign Mini Datacenter** is a self-powered, solar-backed, liquid-cooled micro-datacenter stack designed for **complete data and computational sovereignty**. Run your own private AI with semantic RAG, Git hosting, project management, encrypted file cloud, email server, password vault, and zero-trust mesh VPN — fully off-grid capable.

Developed by **[Metatopia Studio](https://metatopia.gr)** · License: MIT · © 2026

[![CI](https://github.com/iliachry/sovereign-mini-datacenter/actions/workflows/ci.yml/badge.svg)](https://github.com/iliachry/sovereign-mini-datacenter/actions/workflows/ci.yml)
[![3D WebGL Viewer](https://img.shields.io/badge/3D%20CAD%20Viewer-Live%20Demo-10b981?style=flat&logo=three.js)](https://iliachry.gr/sovereign-mini-datacenter/)

---

## 🌐 Live Interactive 3D Viewer

Inspect the 9U 19" chassis, rails, liquid-cooling radiator cutouts, and physical dimensions directly in your browser:
👉 **[Open 3D WebGL CAD Viewer](https://iliachry.gr/sovereign-mini-datacenter/)**

---

## 🏛️ System Architecture

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
├── software/
│   ├── docker-compose.yml       # Sovereign Core 11-service production stack
│   ├── setup.sh                 # Modular deployment CLI (--all, --with-vpn, etc.)
│   ├── env.example              # Environment configuration template
│   ├── prometheus.yml           # Prometheus scrape configuration
│   ├── vpn/                     # Zero-Trust Mesh VPN (Headscale)
│   │   ├── docker-compose.vpn.yml
│   │   ├── config/headscale.yaml
│   │   ├── register-node.sh     # Client onboarding & preauth key manager
│   │   └── README.md
│   ├── backup/                  # Encrypted Backup & Recovery (Restic)
│   │   ├── docker-compose.backup.yml
│   │   ├── backup.sh            # Automated volume & database snapshot script
│   │   ├── restore.sh           # Interactive disaster recovery script
│   │   └── README.md
│   ├── telemetry/               # Solar/BMS Telemetry & Load-Shedding Sentinel
│   │   ├── docker-compose.telemetry.yml
│   │   ├── power_exporter.py    # Victron VE.Direct & LiFePO4 BMS Prometheus exporter
│   │   └── load_shedder.sh      # Autonomous power & thermal load-shedder
│   ├── mailcow/                 # Sovereign Email Stack
│   │   ├── docker-compose.mailcow-traefik.yml
│   │   ├── docker-compose.override.yml
│   │   └── README.md
│   └── grafana/
│       └── provisioning/        # Auto-provisioned Prometheus datasources & dashboards
├── hardware/
│   ├── COMPONENTS.md            # Full Bill of Materials with pricing & electrical specs
│   └── WIRING_DIAGRAM.md        # DC/AC electrical, liquid cooling & network schematics
├── cad/
│   ├── rack_enclosure.scad      # Parametric OpenSCAD 9U 19" chassis model
│   ├── MANUFACTURING_GUIDE.md   # Laser cut, CNC bend, and assembly instructions
│   └── render.jpg               # Photorealistic 3D product render
├── docs/                        # Interactive Three.js WebGL CAD Viewer for GitHub Pages
│   └── index.html
└── .github/
    └── workflows/
        └── ci.yml               # Complete CI pipeline + GitHub Pages automated deploy
```

---

## 🐍 Python CLI Package (`smdc`)

Manage the datacenter and view live telemetry from your terminal:

```bash
pip install sovereign-dc
# or with uv:
uv tool install sovereign-dc
```

```bash
# Check container health and live telemetry dashboard
smdc status

# Deploy container stacks
smdc deploy --all

# Run standalone solar/BMS telemetry exporter
smdc telemetry --port 9101
```

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