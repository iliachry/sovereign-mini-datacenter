# Sovereign Mini Datacenter

![Sovereign Mini Datacenter 3D Render](cad/render.jpg)

**Sovereign Mini Datacenter** is a self-powered, solar-backed, liquid-cooled micro-datacenter stack designed for **complete data autonomy**. Run your own AI, code hosting, project management, file cloud, and password vault — fully off-grid capable.

Developed by **[Metatopia Studio](https://metatopia.gr)** · License: MIT · © 2026

![CI](https://github.com/iliachry/sovereign-mini-datacenter/actions/workflows/ci.yml/badge.svg)

---

## 🏗️ Repository Structure

```
sovereign-mini-datacenter/
├── software/
│   ├── docker-compose.yml      # Full 10-service production stack
│   ├── setup.sh                # Idempotent Ubuntu 24.04 bootstrap script
│   ├── env.example             # Environment config template → copy to .env
│   ├── prometheus.yml          # Prometheus scrape config
│   └── grafana/
│       └── provisioning/       # Auto-provisioned datasources & dashboards
├── hardware/
│   ├── COMPONENTS.md           # Full Bill of Materials with pricing
│   └── WIRING_DIAGRAM.md       # DC/AC electrical + cooling + network diagrams
├── cad/
│   ├── rack_enclosure.scad     # 9U 19" enclosure OpenSCAD model
│   ├── MANUFACTURING_GUIDE.md  # Laser cut, CNC bend, assembly guide
│   └── render.jpg              # 3D render
└── .github/
    └── workflows/
        └── ci.yml              # ShellCheck, compose validate, OpenSCAD lint
```

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/iliachry/sovereign-mini-datacenter.git
cd sovereign-mini-datacenter/software

# 2. Configure (edit .env with your domains, secrets, ACME email)
cp env.example .env && nano .env

# 3. Bootstrap (Ubuntu Server 24.04 LTS)
sudo bash setup.sh
```

After deployment, all services are reachable over **HTTPS** via Traefik:

| Service | Default Domain | Purpose |
|:--|:--|:--|
| 🤖 Open-WebUI | `ai.yourdomain.com` | Private ChatGPT interface |
| 🦙 Ollama API | internal only | GPU-accelerated LLM inference |
| 🦊 GitLab CE | `gitlab.yourdomain.com` | Self-hosted Git + CI/CD |
| 📋 OpenProject | `projects.yourdomain.com` | Project & task management |
| ☁️ Nextcloud | `cloud.yourdomain.com` | Encrypted file sync & share |
| 🔐 Vaultwarden | `vault.yourdomain.com` | Bitwarden-compatible password manager |
| 📊 Grafana | `grafana.yourdomain.com` | Infrastructure dashboards |
| 📈 Prometheus | `metrics.yourdomain.com` | Metrics collection (auth-protected) |
| 🔀 Traefik | `traefik.yourdomain.com` | Reverse proxy dashboard |

---

## 🔋 Hardware at a Glance

- **Compute:** 2× NVIDIA DGX Spark (275 TOPS each, 64GB unified memory)
- **Storage:** 2× Samsung 990 PRO 4TB NVMe (RAID-1 or JBOD)
- **Power:** 10.24 kWh LiFePO4 battery bank + 1,640W solar PV array
- **Cooling:** Dual 360mm liquid loop with Alphacool D5 pump
- **Enclosure:** Custom 9U 19" aluminum rack (540mm deep)

See [`hardware/COMPONENTS.md`](hardware/COMPONENTS.md) for full BOM and [`hardware/WIRING_DIAGRAM.md`](hardware/WIRING_DIAGRAM.md) for schematics.

---

## 🛡️ Security Notes

- All services exposed exclusively via **Traefik** with automatic Let's Encrypt TLS.
- **Vaultwarden signups disabled** by default (`VAULTWARDEN_SIGNUPS_ALLOWED=false`).
- **Prometheus and Traefik dashboard** protected with HTTP Basic Auth.
- Replace all `change_me` placeholders in `.env` before any production use.
- Generate `VAULTWARDEN_ADMIN_TOKEN` with `openssl rand -base64 48`.
- Generate Traefik `TRAEFIK_DASHBOARD_AUTH` with `htpasswd -nb admin yourpassword`.

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
