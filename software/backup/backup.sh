#!/bin/sh
# ====================================================================
# Sovereign Mini Datacenter — Automated Restic Backup Engine
# POSIX sh: runs unmodified in the restic/restic (busybox) image.
# ====================================================================

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load .env if present
if [ -f "${ROOT_DIR}/.env" ]; then
    # shellcheck disable=SC1091
    . "${ROOT_DIR}/.env"
fi

log()  { printf '\n\033[1;32m[BACKUP] %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[WARN]   %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]  %s\033[0m\n' "$*" >&2; exit 1; }

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-/var/backups/sovereign}"
# No default: a publicly known password would make the encryption worthless.
[ -n "${RESTIC_PASSWORD:-}" ] || err "RESTIC_PASSWORD is not set. Set it in software/.env or the environment."
export RESTIC_PASSWORD

# -- Ensure restic is installed ------------------------------------
if ! command -v restic >/dev/null 2>&1; then
    err "restic CLI not found. Install it with: apt-get install restic"
fi

# -- Initialize repository if not already initialized --------------
log "Checking Restic repository: ${RESTIC_REPOSITORY}"
if ! restic snapshots >/dev/null 2>&1; then
    log "Initializing new Restic encrypted repository..."
    mkdir -p "${RESTIC_REPOSITORY}" 2>/dev/null || true
    restic init
fi

# -- Identify active Docker volumes --------------------------------
log "Starting volume backup snapshot..."

TAG="sovereign-automated-$(date +%Y%m%d_%H%M%S)"

# Paths or volumes to back up; missing ones are skipped.
# /data is where docker-compose.backup.yml mounts the read-only service volumes
# when this script runs inside the sovereign_backup container.
set --
for path in \
    "${ROOT_DIR}/docker-compose.yml" \
    "${ROOT_DIR}/.env" \
    "${ROOT_DIR}/prometheus.yml" \
    "${ROOT_DIR}/grafana" \
    "${ROOT_DIR}/mailcow" \
    "${ROOT_DIR}/vpn" \
    "/var/lib/docker/volumes" \
    "/data"; do
    if [ -e "$path" ]; then
        set -- "$@" "$path"
    fi
done

if [ "$#" -eq 0 ]; then
    err "No valid backup paths found!"
fi

# -- Run snapshot --------------------------------------------------
restic backup \
    --tag "sovereign-snapshot" \
    --tag "$TAG" \
    --exclude "*/cache/*" \
    --exclude "*/tmp/*" \
    "$@"

# -- Retention Policy (7 daily, 4 weekly, 12 monthly, 1 yearly) ----
log "Applying snapshot retention policy..."
restic forget \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12 \
    --keep-yearly 1 \
    --prune

log "✅ Backup completed successfully. Current snapshot summary:"
restic snapshots --tag "sovereign-snapshot" --compact | tail -n 10
