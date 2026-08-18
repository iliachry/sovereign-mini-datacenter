#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter � Automated Restic Backup Engine
# ====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load .env if present
if [[ -f "${ROOT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env"
fi

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-/var/backups/sovereign}"
export RESTIC_PASSWORD="${RESTIC_PASSWORD:-changeme_sovereign_backup_key}"

log()  { echo -e "\n\033[1;32m[BACKUP] $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN]   $*\033[0m"; }
err()  { echo -e "\033[1;31m[ERROR]  $*\033[0m" >&2; exit 1; }

# -- Ensure restic is installed ------------------------------------
if ! command -v restic &>/dev/null; then
    err "restic CLI not found. Install it with: apt-get install restic"
fi

# -- Initialize repository if not already initialized --------------
log "Checking Restic repository: ${RESTIC_REPOSITORY}"
if ! restic snapshots &>/dev/null; then
    log "Initializing new Restic encrypted repository..."
    mkdir -p "${RESTIC_REPOSITORY}" 2>/dev/null || true
    restic init
fi

# -- Identify active Docker volumes --------------------------------
log "Starting volume backup snapshot..."

TAG="sovereign-automated-$(date +%Y%m%d_%H%M%S)"

# List of paths or volumes to back up
BACKUP_PATHS=(
    "${ROOT_DIR}/docker-compose.yml"
    "${ROOT_DIR}/.env"
    "${ROOT_DIR}/prometheus.yml"
    "${ROOT_DIR}/grafana"
    "${ROOT_DIR}/mailcow"
    "${ROOT_DIR}/vpn"
    "/var/lib/docker/volumes"
)

EXISTING_PATHS=()
for path in "${BACKUP_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
        EXISTING_PATHS+=("$path")
    fi
done

if [[ ${#EXISTING_PATHS[@]} -eq 0 ]]; then
    err "No valid backup paths found!"
fi

# -- Run snapshot --------------------------------------------------
restic backup \
    --tag "sovereign-snapshot" \
    --tag "$TAG" \
    --exclude "*/cache/*" \
    --exclude "*/tmp/*" \
    "${EXISTING_PATHS[@]}"

# -- Retention Policy (7 daily, 4 weekly, 12 monthly, 1 yearly) ----
log "Applying snapshot retention policy..."
restic forget \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12 \
    --keep-yearly 1 \
    --prune

log "? Backup completed successfully. Current snapshot summary:"
restic snapshots --tag "sovereign-snapshot" --compact | tail -n 10