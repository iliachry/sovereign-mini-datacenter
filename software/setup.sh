#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Unified Modular Setup & Deploy Engine
# Tested on Ubuntu 24.04 LTS (Noble Numbat) x86_64 / arm64
# ====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

log()  { echo -e "\033[1;32m[+] $*\033[0m"; }
warn() { echo -e "\033[1;33m[!] $*\033[0m"; }
err()  { echo -e "\033[1;31m[x] $*\033[0m" >&2; exit 1; }

WITH_MAILCOW=false
WITH_VPN=false
WITH_BACKUP=false
WITH_TELEMETRY=false
WITH_SPACE=false
DRY_RUN=false

show_help() {
    cat <<EOF
Usage: sudo bash setup.sh [OPTIONS]

Options:
  --all               Deploy core stack + Mailcow + VPN + Backup + Telemetry + Space Node
  --with-mailcow      Deploy Mailcow email stack alongside core
  --with-vpn          Deploy Headscale Zero-Trust Mesh VPN
  --with-backup       Deploy automated Restic snapshot backup daemon
  --with-telemetry    Deploy Solar/BMS Telemetry & Load-Shedder Sentinel
  --with-space        Deploy Space Communications (DTN / Satellite) Node
  --dry-run           Validate configurations and syntax without starting services
  -h, --help          Show this help message

Default (no flags): Deploys the Sovereign Core Stack (Traefik, Ollama, Open-WebUI,
                    Qdrant, GitLab, OpenProject, Nextcloud, Vaultwarden,
                    Prometheus, Grafana, cAdvisor, Node Exporter).
EOF
    exit 0
}

# Parse flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            WITH_MAILCOW=true
            WITH_VPN=true
            WITH_BACKUP=true
            WITH_TELEMETRY=true
            WITH_SPACE=true
            shift
            ;;
        --with-mailcow)
            WITH_MAILCOW=true
            shift
            ;;
        --with-vpn)
            WITH_VPN=true
            shift
            ;;
        --with-backup)
            WITH_BACKUP=true
            shift
            ;;
        --with-telemetry)
            WITH_TELEMETRY=true
            shift
            ;;
        --with-space)
            WITH_SPACE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            err "Unknown option: $1 (run with --help for usage)"
            ;;
    esac
done

# Dry-run check or require root
if [[ "$DRY_RUN" == "true" ]]; then
    log "DRY RUN MODE: Validating compose stacks and environment files..."
    cd "$SCRIPT_DIR"
    [[ -f .env ]] || cp env.example .env
    docker compose -f docker-compose.yml config --quiet
    docker compose -f vpn/docker-compose.vpn.yml config --quiet
    docker compose -f backup/docker-compose.backup.yml config --quiet
    docker compose -f telemetry/docker-compose.telemetry.yml config --quiet
    docker compose -f space/docker-compose.space.yml config --quiet
    echo "✅ All configurations valid."
    exit 0
fi

# Require root
[[ $EUID -eq 0 ]] || err "Please run as root or with sudo."

log "[1/6] Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl wget git build-essential ca-certificates gnupg lsb-release apache2-utils restic

# -- Docker --------------------------------------------------------
log "[2/6] Installing Docker Engine & Docker Compose..."
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      | tee /etc/apt/sources.list.d/docker.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    REAL_USER="${SUDO_USER:-}"
    if [[ -z "$REAL_USER" ]]; then
        REAL_USER=$(logname 2>/dev/null || true)
    fi
    if [[ -n "$REAL_USER" ]]; then
        usermod -aG docker "$REAL_USER"
        warn "Added $REAL_USER to docker group."
    fi
else
    log "Docker already installed — skipping."
fi

# -- NVIDIA Drivers + CUDA Toolkit --------------------------------
log "[3/6] Detecting GPU and installing NVIDIA stack..."
if lspci 2>/dev/null | grep -qi nvidia; then
    if ! command -v nvidia-smi &>/dev/null; then
        log "  Installing NVIDIA drivers..."
        apt-get install -y -qq ubuntu-drivers-common
        ubuntu-drivers autoinstall
    else
        DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
        log "  NVIDIA driver already installed (v${DRIVER_VER})."
    fi

    if ! dpkg -l 2>/dev/null | grep -q cuda-toolkit; then
        log "  Installing CUDA Toolkit 12.x..."
        CUDA_KEYRING_URL="https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/${ARCH}/cuda-keyring_1.1-1_all.deb"
        wget -qO /tmp/cuda-keyring.deb "$CUDA_KEYRING_URL"
        dpkg -i /tmp/cuda-keyring.deb
        apt-get update -qq
        apt-get install -y -qq cuda-toolkit-12-6
        rm -f /tmp/cuda-keyring.deb
    fi

    if ! dpkg -l 2>/dev/null | grep -q nvidia-container-toolkit; then
        log "  Installing NVIDIA Container Toolkit..."
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
            | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -sL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
        apt-get update -qq
        apt-get install -y -qq nvidia-container-toolkit
        nvidia-ctk runtime configure --runtime=docker
        systemctl restart docker
    fi
else
    warn "No NVIDIA GPU detected. Skipping GPU driver installation (CPU mode)."
fi

# -- Environment file ----------------------------------------------
log "[4/6] Preparing environment configuration..."
if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    cp "${SCRIPT_DIR}/env.example" "${SCRIPT_DIR}/.env"
    warn ".env created from template. Edit ${SCRIPT_DIR}/.env before production use!"
fi

# -- Model pre-pull info -------------------------------------------
log "[5/6] Checking default Ollama model..."
OLLAMA_MODEL=""
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    OLLAMA_MODEL=$(grep -E '^OLLAMA_DEFAULT_MODEL=' "${SCRIPT_DIR}/.env" | cut -d= -f2 || true)
fi

# -- Start the stack -----------------------------------------------
log "[6/6] Launching Selected Sovereign Stacks..."
cd "$SCRIPT_DIR"

# Launch Core Stack
docker compose -f docker-compose.yml up -d --remove-orphans

# Launch Optional Modules
if [[ "$WITH_VPN" == "true" ]]; then
    log "  Starting Headscale Mesh VPN..."
    docker compose -f vpn/docker-compose.vpn.yml up -d
fi

if [[ "$WITH_BACKUP" == "true" ]]; then
    log "  Starting Restic Backup Daemon..."
    docker compose -f backup/docker-compose.backup.yml up -d
fi

if [[ "$WITH_TELEMETRY" == "true" ]]; then
    log "  Starting Solar/BMS Telemetry & Exporter..."
    docker compose -f telemetry/docker-compose.telemetry.yml up -d
fi

if [[ "$WITH_SPACE" == "true" ]]; then
    log "  Starting Space Communications Node (DTN & Orbital Exporter)..."
    docker compose -f space/docker-compose.space.yml up -d
fi

if [[ "$WITH_MAILCOW" == "true" ]]; then
    log "  Mailcow integration enabled. See software/mailcow/README.md for initial mailbox setup."
fi

# Pull Ollama model
if [[ -n "$OLLAMA_MODEL" ]]; then
    log "  Pulling Ollama model: ${OLLAMA_MODEL}..."
    sleep 10
    docker exec sovereign_ollama ollama pull "${OLLAMA_MODEL}" || true
fi

get_env() { grep -E "^${1}=" .env 2>/dev/null | cut -d= -f2 || echo "${2}"; }

echo ""
echo "====================================================================="
echo "  ✅ Sovereign Mini Datacenter Deployed Successfully!"
echo "---------------------------------------------------------------------"
echo "  • Open-WebUI (AI + RAG):  https://$(get_env DOMAIN_WEBUI ai.sovereign.local)"
echo "  • GitLab CE:              https://$(get_env DOMAIN_GITLAB gitlab.sovereign.local)"
echo "  • OpenProject:            https://$(get_env DOMAIN_PROJECTS projects.sovereign.local)"
echo "  • Nextcloud:              https://$(get_env DOMAIN_NEXTCLOUD cloud.sovereign.local)"
echo "  • Vaultwarden:            https://$(get_env DOMAIN_VAULT vault.sovereign.local)"
echo "  • Grafana Dashboards:     https://$(get_env DOMAIN_GRAFANA grafana.sovereign.local)"
echo "  • Traefik Proxy:          https://$(get_env DOMAIN_TRAEFIK traefik.sovereign.local)"
if [[ "$WITH_VPN" == "true" ]]; then
echo "  • Headscale Mesh VPN:     https://$(get_env DOMAIN_VPN vpn.sovereign.local)"
fi
if [[ "$WITH_SPACE" == "true" ]]; then
echo "  • Space Comm Telemetry:   http://localhost:$(get_env SPACE_EXPORTER_PORT 9102)/metrics"
fi
echo "====================================================================="
