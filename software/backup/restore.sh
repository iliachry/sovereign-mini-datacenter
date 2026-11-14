#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Disaster Recovery & Restore Engine
#
# A snapshot holds database DUMPS (smdc-dumps/, with a MANIFEST), the
# deployment configuration (software/, .env included) and the static files of
# the volumes - never raw copies of live database directories. Restoring a
# service therefore means: put the static files back, then LOAD its dump with
# the database's own tool. `plan` prints those steps for a restored snapshot.
#
# This script never talks to Docker and never writes into a live volume: it
# extracts into a directory you name (or a temporary one for --verify).
# ====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

log()  { echo -e "\n\033[1;32m[RESTORE] $*\033[0m"; }
info() { echo "          $*"; }
warn() { echo -e "\033[1;33m[WARN]    $*\033[0m"; }
err()  { echo -e "\033[1;31m[ERROR]   $*\033[0m" >&2; exit 1; }

# .env is a Compose env file, not a shell script: read the keys we need as data.
env_file_value() {
    [[ -f "$ENV_FILE" ]] || return 0
    efv="$(sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r')"
    case "$efv" in
        \"*\"*) efv="${efv#\"}"; efv="${efv%\"*}" ;;
        \'*\'*) efv="${efv#\'}"; efv="${efv%\'*}" ;;
        *) efv="$(printf '%s' "$efv" | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//')" ;;
    esac
    printf '%s' "$efv"
}
for key in RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_S3_KEY RESTIC_S3_SECRET; do
    if [[ -z "${!key+set}" ]]; then
        value="$(env_file_value "$key")"
        if [[ -n "$value" ]]; then export "${key}=${value}"; fi
    fi
done

usage() {
    cat <<EOF
Sovereign Disaster Recovery & Restore CLI

Usage:
  ./restore.sh list                               List available snapshots
  ./restore.sh restore-latest <target>            Extract the latest snapshot into <target>
  ./restore.sh restore-id <snapshot_id> <target>  Extract a specific snapshot into <target>
  ./restore.sh plan <target>                      Print the per-service restore steps for an extracted snapshot
  ./restore.sh --verify [snapshot_id]             Restore the dumps of the latest (or given) snapshot into a
                                                  temporary directory and check that each one loads:
                                                  SQLite integrity_check, pg_restore --list, GitLab archive
                                                  listing. Live services are not touched. Non-zero on any failure.
  ./restore.sh check                              restic repository check (archive integrity only - it says
                                                  nothing about whether a database can be restored)

<target> must be an empty or new directory, never a live volume.

Examples:
  ./restore.sh --verify
  ./restore.sh restore-latest /mnt/restore_target && ./restore.sh plan /mnt/restore_target
EOF
    exit 1
}

need_restic() {
    export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-/var/backups/sovereign}"
    # No default: the password must be the one the repository was created with.
    [[ -n "${RESTIC_PASSWORD:-}" ]] || err "RESTIC_PASSWORD is not set. Set it in software/.env or the environment."
    export RESTIC_PASSWORD
    if [[ -z "${AWS_ACCESS_KEY_ID:-}" && -n "${RESTIC_S3_KEY:-}" ]]; then export AWS_ACCESS_KEY_ID="$RESTIC_S3_KEY"; fi
    if [[ -z "${AWS_SECRET_ACCESS_KEY:-}" && -n "${RESTIC_S3_SECRET:-}" ]]; then export AWS_SECRET_ACCESS_KEY="$RESTIC_S3_SECRET"; fi
    command -v restic >/dev/null 2>&1 || err "restic CLI not found. Install it with: apt-get install restic"
}

# restic_restore <snapshot|latest> <target> [restic restore options...]
restic_restore() {
    local snapshot="$1" target="$2"
    shift 2
    if [[ "$snapshot" == "latest" ]]; then
        restic restore latest --tag "sovereign-snapshot" --target "$target" "$@"
    else
        restic restore "$snapshot" --target "$target" "$@"
    fi
}

prepare_target() {
    local target="$1"
    [[ -n "$target" ]] || { echo "Error: Missing target directory."; usage; }
    mkdir -p "$target"
    if [[ -n "$(ls -A "$target")" ]]; then
        err "Target ${target} is not empty. Restore into a new directory, never over live data."
    fi
}

# Path of the one smdc-dumps directory inside an extracted snapshot.
find_dumps() {
    local target="$1" found count
    found="$(find "$target" -type f -name MANIFEST -path '*/smdc-dumps/MANIFEST' 2>/dev/null || true)"
    count="$(printf '%s' "$found" | grep -c . || true)"
    if [[ "$count" -ne 1 ]]; then
        err "Expected exactly one smdc-dumps/MANIFEST under ${target}, found ${count}. This snapshot holds no database dumps (taken by an older backup.sh?) and cannot restore any database."
    fi
    dirname "$found"
}

# ── Verification of one dump; returns non-zero when it cannot be loaded ──
verify_sqlite() {
    local file="$1" result tables
    [[ -s "$file" ]] || { info "    file missing or empty"; return 1; }
    result="$(sqlite3 "$file" "PRAGMA integrity_check;" 2>&1)" || { info "    sqlite3: ${result}"; return 1; }
    [[ "$result" == "ok" ]] || { info "    integrity_check: ${result}"; return 1; }
    tables="$(sqlite3 "$file" "SELECT count(*) FROM sqlite_master WHERE type='table';" 2>&1)" || { info "    sqlite3: ${tables}"; return 1; }
    info "    integrity_check ok, ${tables} table(s)"
}

verify_pgdump() {
    local file="$1" toc entries
    [[ -s "$file" ]] || { info "    file missing or empty"; return 1; }
    command -v pg_restore >/dev/null 2>&1 || {
        info "    pg_restore not found: install the PostgreSQL client (apt-get install postgresql-client) to verify this dump"
        return 1
    }
    toc="$(pg_restore --list "$file" 2>&1)" || { info "    pg_restore --list: ${toc}"; return 1; }
    entries="$(printf '%s\n' "$toc" | grep -c 'TABLE DATA' || true)"
    [[ "$entries" -gt 0 ]] || { info "    archive lists no TABLE DATA entries"; return 1; }
    info "    pg_restore --list ok, ${entries} table data entries"
}

verify_gitlab() {
    local file="$1" listing
    [[ -s "$file" ]] || { info "    archive missing or empty"; return 1; }
    listing="$(tar -tf "$file" 2>&1)" || { info "    tar: cannot list the archive"; return 1; }
    printf '%s\n' "$listing" | grep -Eq '(^|/)backup_information\.yml$' || { info "    no backup_information.yml in the archive"; return 1; }
    printf '%s\n' "$listing" | grep -Eq '(^|/)db/database\.sql\.gz$' || { info "    no db/database.sql.gz in the archive"; return 1; }
    local member
    member="$(printf '%s\n' "$listing" | grep -E '(^|/)db/database\.sql\.gz$' | head -n 1)"
    tar -xOf "$file" "$member" | gzip -t || { info "    db/database.sql.gz is not a valid gzip stream"; return 1; }
    info "    archive lists, database dump is a valid gzip stream"
}

verify_dumps() {
    local snapshot="${1:-latest}" tmp dumps manifest failed=0 checked=0
    need_restic
    command -v sqlite3 >/dev/null 2>&1 || err "sqlite3 CLI not found. Install it with: apt-get install sqlite3"
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/smdc-restore-verify.XXXXXX")"
    # shellcheck disable=SC2064  # expand now: tmp is a local
    trap "rm -rf '${tmp}'" EXIT

    log "Restoring the dumps of snapshot '${snapshot}' into ${tmp} (live services are not touched)..."
    restic_restore "$snapshot" "$tmp" --include "smdc-dumps"
    dumps="$(find_dumps "$tmp")"
    manifest="${dumps}/MANIFEST"
    grep -qx 'status|complete' "$manifest" || err "MANIFEST is not marked complete: this snapshot does not hold a complete set of dumps."
    info "dumps created: $(sed -n 's/^created|//p' "$manifest" | head -n 1)"

    local gitlab_tar
    gitlab_tar="$(awk -F'|' '$1 == "dump" && $3 == "gitlab" && $5 == "ok" { print $4; exit }' "$manifest")"
    if [[ -n "$gitlab_tar" ]]; then
        log "Restoring GitLab archive ${gitlab_tar}..."
        restic_restore "$snapshot" "$tmp" --include "$gitlab_tar"
    fi

    log "Checking every dump listed in the MANIFEST:"
    local _tag name kind path status detail file
    while IFS='|' read -r _tag name kind path status detail; do
        [[ "$_tag" == "dump" ]] || continue
        if [[ "$status" != "ok" ]]; then
            info "SKIPPED  ${name}: ${detail}"
            continue
        fi
        checked=$((checked + 1))
        case "$kind" in
            sqlite)
                file="${dumps}/${path}"
                if verify_sqlite "$file"; then info "OK       ${name} (sqlite)"; else info "FAILED   ${name} (sqlite)"; failed=$((failed + 1)); fi
                ;;
            pgdump)
                file="${dumps}/${path}"
                if verify_pgdump "$file"; then info "OK       ${name} (pg_dump custom format)"; else info "FAILED   ${name} (pg_dump)"; failed=$((failed + 1)); fi
                ;;
            gitlab)
                file="$(find "$tmp" -type f -name "$path" | head -n 1)"
                if [[ -n "$file" ]] && verify_gitlab "$file"; then info "OK       ${name} (gitlab-backup archive)"; else info "FAILED   ${name} (gitlab-backup archive ${path})"; failed=$((failed + 1)); fi
                ;;
            files)
                if [[ -d "${dumps}/${path}" ]]; then info "OK       ${name} (plain files present)"; else info "FAILED   ${name} (directory ${path} missing)"; failed=$((failed + 1)); fi
                ;;
            *)
                info "FAILED   ${name}: unknown dump kind '${kind}'"
                failed=$((failed + 1))
                ;;
        esac
    done <"$manifest"

    if [[ "$failed" -gt 0 ]]; then
        err "${failed} of ${checked} dump(s) cannot be loaded. This snapshot cannot restore those databases."
    fi
    [[ "$checked" -gt 0 ]] || err "The MANIFEST lists no successful dump: nothing to restore from."
    log "✅ All ${checked} dump(s) of snapshot '${snapshot}' load. (Skipped entries above were not deployed when the backup ran.)"
}

# ── Per-service restore steps for an extracted snapshot ──────────────
plan() {
    local target="$1" dumps manifest
    [[ -n "$target" && -d "$target" ]] || { echo "Error: Missing or unknown target directory."; usage; }
    dumps="$(find_dumps "$target")"
    manifest="${dumps}/MANIFEST"
    local config
    config="$(find "$target" -type f -name docker-compose.attach.yml 2>/dev/null | head -n 1)"
    config="${config%/docker-compose.attach.yml}"

    cat <<EOF

Restore plan for ${target}
  dumps          ${dumps}  (created $(sed -n 's/^created|//p' "$manifest" | head -n 1))
  configuration  ${config:-<not found in this snapshot>}  (compose files, overlays, provisioning, .env)
  volume files   under ${target}: .../<project>_<volume>/_data (host-mode snapshot) or /data/<volume> (container-mode snapshot)

Order on a fresh node:
  1. Copy the configuration directory back as software/ (it contains .env: keep it mode 0600).
  2. Create the stack WITHOUT starting it:   docker compose --env-file software/.env -f software/docker-compose.yml [-f overlays...] create
  3. Copy each volume's static files back into the volume it came from (see backup/volumes.conf), e.g.
       docker run --rm -v <project>_vaultwarden_data:/to -v <extracted>/vaultwarden_data:/from:ro alpine cp -a /from/. /to/
  4. Load the dumps below. 5. Start the stack with setup.sh.

Dumps in this snapshot:
EOF
    local _tag name kind path status detail
    while IFS='|' read -r _tag name kind path status detail; do
        [[ "$_tag" == "dump" ]] || continue
        if [[ "$status" != "ok" ]]; then
            echo "  - ${name}: not in this snapshot (${detail})"
            continue
        fi
        case "$name" in
            vaultwarden)
                echo "  - vaultwarden (stopped): copy ${dumps}/${path} to <vaultwarden_data>/db.sqlite3 and delete any db.sqlite3-wal / db.sqlite3-shm there." ;;
            headscale)
                echo "  - headscale (stopped): copy ${dumps}/${path} to <headscale_data>/db.sqlite; delete db.sqlite-wal / -shm." ;;
            grafana)
                echo "  - grafana (stopped): copy ${dumps}/${path} to <grafana_data>/grafana.db; chown it to 472:0." ;;
            open-webui)
                echo "  - open-webui (stopped): copy ${dumps}/${path} to <webui_data>/webui.db. Then re-index knowledge in Admin > Settings > Documents (Qdrant is not backed up)." ;;
            crowdsec)
                echo "  - crowdsec (stopped): copy ${dumps}/${path} to <crowdsec_data>/crowdsec.db." ;;
            dtn-spool)
                echo "  - dtn-spool (space-node stopped): copy ${dumps}/${path} to <space_spool_data>/bundles.db; delete bundles.db-wal / -shm." ;;
            nextcloud)
                echo "  - nextcloud (stopped): copy ${dumps}/${path} to <nextcloud_data>/data/owncloud.db (chown 33:33), start it, then:"
                echo "      docker exec -u www-data sovereign_nextcloud php occ maintenance:data-fingerprint"
                echo "      docker exec -u www-data sovereign_nextcloud php occ files:scan --all" ;;
            openproject)
                echo "  - openproject (dumped by: ${detail}). Official all-in-one procedure, adapted to the named volume and the custom-format dump"
                echo "    (https://www.openproject.org/docs/installation-and-operations/operation/restoring/); rehearse it on a spare node:"
                echo "      a. start sovereign_openproject once on its EMPTY openproject_pgdata volume, wait for 'Database setup finished', then: docker stop sovereign_openproject"
                echo "      b. docker run --rm -d --name op-restore-pg -v <project>_openproject_pgdata:/var/lib/postgresql/data postgres:<same major version as the dump>"
                echo "      c. docker cp ${dumps}/${path} op-restore-pg:/tmp/openproject.pgdump"
                echo "      d. docker exec op-restore-pg dropdb -U postgres openproject"
                echo "         docker exec op-restore-pg createdb -U postgres -O openproject openproject"
                echo "         docker exec op-restore-pg pg_restore -U postgres -d openproject --no-owner --role=openproject /tmp/openproject.pgdump"
                echo "      e. docker stop op-restore-pg"
                echo "         docker run --rm -v <project>_openproject_pgdata:/d alpine chown -R 102:102 /d   # the postgres image re-owned it"
                echo "      f. docker start sovereign_openproject" ;;
            gitlab)
                echo "  - gitlab: restore gitlab.rb and gitlab-secrets.json FIRST (volume gitlab_config), start GitLab (same version as the backup), then:"
                echo "      copy the extracted ${path} into <gitlab_data>/backups/ (owner git)"
                echo "      docker exec sovereign_gitlab gitlab-ctl stop puma && docker exec sovereign_gitlab gitlab-ctl stop sidekiq"
                echo "      docker exec -it sovereign_gitlab gitlab-backup restore BACKUP=${path%_gitlab_backup.tar}"
                echo "      docker restart sovereign_gitlab && docker exec sovereign_gitlab gitlab-rake gitlab:check SANITIZE=true"
                echo "    Official procedure: https://docs.gitlab.com/administration/backup_restore/restore_gitlab/" ;;
            smdc-state)
                echo "  - smdc-state: copy ${dumps}/${path}/ back to the node's SMDC_STATE_DIR (mode 0700; wallets and keys 0600). SQLite ledgers in it are consistent dumps." ;;
            smdc-state:*) ;;
            *)
                echo "  - ${name} (${kind}): ${dumps}/${path}" ;;
        esac
    done <"$manifest"
    cat <<EOF

Not in any snapshot (see backup/README.md): Qdrant vectors (re-index), Ollama models (ollama pull),
logs, and the most recent hours of Prometheus samples (WAL/head are excluded; completed blocks only).
EOF
}

cmd="${1:-}"
shift || true

case "$cmd" in
    list)
        need_restic
        log "Listing all snapshots in ${RESTIC_REPOSITORY}:"
        restic snapshots
        ;;
    restore-latest)
        need_restic
        target="${1:-}"
        prepare_target "$target"
        log "Extracting the latest snapshot into $target..."
        restic_restore latest "$target"
        log "✅ Extracted to $target. Databases are dumps, not live directories: run './restore.sh plan $target' for the per-service steps."
        ;;
    restore-id)
        need_restic
        snapshot_id="${1:-}"
        target="${2:-}"
        [[ -n "$snapshot_id" ]] || { echo "Error: Missing snapshot ID."; usage; }
        prepare_target "$target"
        log "Extracting snapshot $snapshot_id into $target..."
        restic_restore "$snapshot_id" "$target"
        log "✅ Extracted to $target. Databases are dumps, not live directories: run './restore.sh plan $target' for the per-service steps."
        ;;
    plan)
        plan "${1:-}"
        ;;
    --verify | verify-dumps)
        verify_dumps "${1:-latest}"
        ;;
    check | verify)
        need_restic
        if [[ "$cmd" == "verify" ]]; then
            warn "'verify' only checks the restic archive. Use './restore.sh --verify' to prove the database dumps load."
        fi
        log "Checking Restic repository consistency (archive integrity only)..."
        restic check --read-data-subset=10%
        log "✅ Repository check passed."
        ;;
    *)
        usage
        ;;
esac
