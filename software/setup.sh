#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Node Initialization & Setup Script
# Target OS: Ubuntu Server 24.04 LTS (x86_64 / arm64)
# Idempotent: safe to run multiple times.
# ====================================================================

set -euo pipefail

ARCH=$(dpkg --print-architecture)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { echo -e "\n\033[1;32m>>> $*\033[0m"; }
warn() { echo -e "\033[1;33m⚠  $*\033[0m"; }
err()  { echo -e "\033[1;31m✗  $*\033[0m" >&2; exit 1; }

# ── Require root ────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || err "Please run as root or with sudo."

log "[1/6] Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl wget git build-essential ca-certificates gnupg lsb-release htpasswd

# ── Docker ──────────────────────────────────────────────────────────
log "[2/6] Installing Docker Engine & Docker Compose..."
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      | tee /etc/apt/sources.list.d/docker.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    SUDO_USER=${SUDO_USER:-$(logname 2>/dev/null || echo "")}
    [[ -n "$SUDO_USER" ]] && usermod -aG docker "$SUDO_USER" \
        && warn "Added $SUDO_USER to docker group. Log out/in for this to take effect."
else
    log "Docker already installed — skipping."
fi

# ── NVIDIA Drivers + CUDA Toolkit ───────────────────────────────────
log "[3/6] Detecting GPU and installing NVIDIA stack..."
if lspci 2>/dev/null | grep -qi nvidia; then
    if ! command -v nvidia-smi &>/dev/null; then
        log "  Installing NVIDIA drivers (ubuntu-drivers autoinstall)..."
        apt-get install -y -qq ubuntu-drivers-common
        ubuntu-drivers autoinstall
    else
        DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
        log "  NVIDIA driver already installed (v${DRIVER_VER})."
    fi

    if ! dpkg -l | grep -q cuda-toolkit; then
        log "  Installing CUDA Toolkit 12.x..."
        CUDA_KEYRING_URL="https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/${ARCH}/cuda-keyring_1.1-1_all.deb"
        wget -qO /tmp/cuda-keyring.deb "$CUDA_KEYRING_URL"
        dpkg -i /tmp/cuda-keyring.deb
        apt-get update -qq
        apt-get install -y -qq cuda-toolkit-12-6
        rm /tmp/cuda-keyring.deb
    else
        log "  CUDA Toolkit already installed — skipping."
    fi

    log "  Installing NVIDIA Container Toolkit..."
    if ! dpkg -l | grep -q nvidia-container-toolkit; then
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
            | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -sL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
        apt-get update -qq
        apt-get install -y -qq nvidia-container-toolkit
        nvidia-ctk runtime configure --runtime=docker
        systemctl restart docker
    else
        log "  NVIDIA Container Toolkit already installed — skipping."
    fi
else
    warn "No NVIDIA GPU detected. Skipping GPU driver/CUDA installation."
    warn "Ollama will run in CPU-only mode."
fi

# ── Environment file ─────────────────────────────────────────────────
log "[4/6] Preparing environment configuration..."
if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    cp "${SCRIPT_DIR}/env.example" "${SCRIPT_DIR}/.env"
    warn ".env created from template. Edit ${SCRIPT_DIR}/.env before continuing!"
    warn "At minimum, set: ACME_EMAIL, all DOMAIN_* vars, all *_PASSWORD/*_SECRET vars."
    read -rp "Press Enter to continue after editing .env, or Ctrl+C to abort..."
else
    log "  .env already exists — skipping copy."
fi

# ── Pull default LLM model ───────────────────────────────────────────
log "[5/6] Pre-pulling default Ollama LLM model..."
OLLAMA_MODEL=$(grep -E '^OLLAMA_DEFAULT_MODEL=' "${SCRIPT_DIR}/.env" | cut -d= -f2 || echo "")
if [[ -n "$OLLAMA_MODEL" ]]; then
    log "  Will pull: ${OLLAMA_MODEL} (this happens after stack start in step 6)"
fi

# ── Start the stack ──────────────────────────────────────────────────
log "[6/6] Starting Sovereign Stack..."
cd "$SCRIPT_DIR"
docker compose up -d --remove-orphans

# Pull Ollama model after containers start
if [[ -n "$OLLAMA_MODEL" ]]; then
    log "  Pulling Ollama model: ${OLLAMA_MODEL}..."
    sleep 10
    docker exec sovereign_ollama ollama pull "${OLLAMA_MODEL}" || \
        warn "Model pull failed. Run manually: docker exec sovereign_ollama ollama pull ${OLLAMA_MODEL}"
fi

echo ""
echo "====================================================================="
echo "  ✅ Sovereign Mini Datacenter Stack Deployed!"
echo "---------------------------------------------------------------------"
DOMAIN_WEBUI=$(grep -E '^DOMAIN_WEBUI=' .env | cut -d= -f2 || echo 'ai.sovereign.local')
DOMAIN_GITLAB=$(grep -E '^DOMAIN_GITLAB=' .env | cut -d= -f2 || echo 'gitlab.sovereign.local')
DOMAIN_PROJECTS=$(grep -E '^DOMAIN_PROJECTS=' .env | cut -d= -f2 || echo 'projects.sovereign.local')
DOMAIN_NEXTCLOUD=$(grep -E '^DOMAIN_NEXTCLOUD=' .env | cut -d= -f2 || echo 'cloud.sovereign.local')
DOMAIN_VAULT=$(grep -E '^DOMAIN_VAULT=' .env | cut -d= -f2 || echo 'vault.sovereign.local')
DOMAIN_GRAFANA=$(grep -E '^DOMAIN_GRAFANA=' .env | cut -d= -f2 || echo 'grafana.sovereign.local')
DOMAIN_TRAEFIK=$(grep -E '^DOMAIN_TRAEFIK=' .env | cut -d= -f2 || echo 'traefik.sovereign.local')
echo "  • Open-WebUI (AI):    https://${DOMAIN_WEBUI}"
echo "  • GitLab CE:          https://${DOMAIN_GITLAB}"
echo "  • OpenProject:        https://${DOMAIN_PROJECTS}"
echo "  • Nextcloud:          https://${DOMAIN_NEXTCLOUD}"
echo "  • Vaultwarden:        https://${DOMAIN_VAULT}"
echo "  • Grafana:            https://${DOMAIN_GRAFANA}"
echo "  • Traefik Dashboard:  https://${DOMAIN_TRAEFIK}"
echo "====================================================================="
