#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Automated Setup & Deployment Script
# Supports Core, Mailcow, Headscale VPN, Restic, Telemetry, Space & Agents
# ====================================================================
set -euo pipefail

RED="\033[1;31m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
CYAN="\033[1;36m"
BOLD="\033[1m"
RESET="\033[0m"

log_info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/env.example"

DEPLOY_ALL=false
DEPLOY_MAILCOW=false
DEPLOY_VPN=false
DEPLOY_BACKUP=false
DEPLOY_TELEMETRY=false
DEPLOY_SPACE=false
DEPLOY_AGENTS=false
DEPLOY_SECURITY=false
DRY_RUN=false

usage() {
    cat <<EOF
${BOLD}Sovereign Mini Datacenter Setup${RESET}

Usage: $(basename "$0") [OPTIONS]

Options:
    --all              Deploy full infrastructure stack
    --with-mailcow     Include Mailcow container stack
    --with-vpn         Include Headscale Zero-Trust VPN
    --with-backup      Include Restic backup daemon
    --with-telemetry   Include Prometheus power & thermal exporter
    --with-space       Include Space Communications DTN & Orbit Tracking node
    --with-agents      Include Autonomous Local AI Agents (GitLab Reviewer, Knowledge Indexer)
    --with-security    Include CrowdSec Intrusion Prevention & Security Engine
    --dry-run          Validate configuration and exit without launching
    -h, --help         Show this help message
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)            DEPLOY_ALL=true; shift ;;
        --with-mailcow)   DEPLOY_MAILCOW=true; shift ;;
        --with-vpn)       DEPLOY_VPN=true; shift ;;
        --with-backup)    DEPLOY_BACKUP=true; shift ;;
        --with-telemetry) DEPLOY_TELEMETRY=true; shift ;;
        --with-space)     DEPLOY_SPACE=true; shift ;;
        --with-agents)    DEPLOY_AGENTS=true; shift ;;
        --with-security)  DEPLOY_SECURITY=true; shift ;;
        --dry-run)        DRY_RUN=true; shift ;;
        -h|--help)        usage ;;
        *)                log_error "Unknown option: $1"; usage ;;
    esac
done

echo -e "\n${BOLD}${CYAN}=== Sovereign Mini Datacenter Setup ===${RESET}\n"

# Ensure .env exists
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$ENV_EXAMPLE" ]]; then
        log_warn ".env not found. Generating default .env from env.example..."
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        log_ok "Created .env with restricted permissions (0600)."
    fi
fi

# Build Compose Command
COMPOSE_FILES=("-f" "${PROJECT_DIR}/docker-compose.yml")

if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_VPN" == "true" ]]; then
    COMPOSE_FILES+=("-f" "${PROJECT_DIR}/vpn/docker-compose.vpn.yml")
fi
if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_BACKUP" == "true" ]]; then
    COMPOSE_FILES+=("-f" "${PROJECT_DIR}/backup/docker-compose.backup.yml")
fi
if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_TELEMETRY" == "true" ]]; then
    COMPOSE_FILES+=("-f" "${PROJECT_DIR}/telemetry/docker-compose.telemetry.yml")
fi
if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_SPACE" == "true" ]]; then
    COMPOSE_FILES+=("-f" "${PROJECT_DIR}/space/docker-compose.space.yml")
fi
if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_AGENTS" == "true" ]]; then
    COMPOSE_FILES+=("-f" "${PROJECT_DIR}/agents/docker-compose.agents.yml")
fi
if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_SECURITY" == "true" ]]; then
    COMPOSE_FILES+=("-f" "${PROJECT_DIR}/security/docker-compose.crowdsec.yml")
fi

if [[ "$DRY_RUN" == "true" ]]; then
    log_info "DRY-RUN: Validating Docker Compose configuration..."
    docker compose "${COMPOSE_FILES[@]}" config --quiet
    log_ok "Docker Compose configuration is valid."
    exit 0
fi

log_info "Starting sovereign datacenter container stacks..."
docker compose "${COMPOSE_FILES[@]}" up -d --remove-orphans
log_ok "Deployment complete! All services running."
