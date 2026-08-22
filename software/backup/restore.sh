#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter � Disaster Recovery & Restore Engine
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

log()  { echo -e "\n\033[1;32m[RESTORE] $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN]    $*\033[0m"; }
err()  { echo -e "\033[1;31m[ERROR]   $*\033[0m" >&2; exit 1; }

if ! command -v restic >/dev/null 2>&1; then
    err "restic CLI not found. Install it with: apt-get install restic"
fi

usage() {
    cat <<EOF
Sovereign Disaster Recovery & Restore CLI

Usage:
  ./restore.sh list                       # List available snapshots
  ./restore.sh restore-latest <target>    # Restore latest snapshot to target folder
  ./restore.sh restore-id <snapshot_id> <target> # Restore specific snapshot
  ./restore.sh verify                     # Check integrity of backup archive

Examples:
  ./restore.sh list
  ./restore.sh restore-latest /mnt/restore_target
EOF
    exit 1
}

cmd="${1:-}"
shift || true

case "$cmd" in
    list)
        log "Listing all snapshots in ${RESTIC_REPOSITORY}:"
        restic snapshots
        ;;
    restore-latest)
        target="${1:-}"
        [[ -n "$target" ]] || { echo "Error: Missing target directory."; usage; }
        mkdir -p "$target"
        log "Restoring latest snapshot to $target..."
        restic restore latest --target "$target"
        log "? Restore complete to $target"
        ;;
    restore-id)
        snapshot_id="${1:-}"
        target="${2:-}"
        [[ -n "$snapshot_id" && -n "$target" ]] || { echo "Error: Missing snapshot ID or target directory."; usage; }
        mkdir -p "$target"
        log "Restoring snapshot $snapshot_id to $target..."
        restic restore "$snapshot_id" --target "$target"
        log "? Restore complete to $target"
        ;;
    verify)
        log "Verifying Restic repository consistency..."
        restic check --read-data-subset=10%
        log "? Repository verification passed."
        ;;
    *)
        usage
        ;;
esac