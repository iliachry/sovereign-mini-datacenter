#!/bin/sh
# ====================================================================
# Sovereign Mini Datacenter — Restic Backup Engine
# POSIX sh: runs unmodified in the restic/restic (busybox) image.
#
# A file-level copy of a RUNNING database is not a backup: pages and WAL can
# come from different moments and the copy may not start. So this script works
# in two phases and never hands restic a live database directory:
#
#   1. DUMP  (host only)  every database is exported with its own tool into
#            <BACKUP_DUMP_DIR>/smdc-dumps and checked; a MANIFEST lists the
#            result with the size and SHA-256 of every dump file. Nextcloud is
#            put into maintenance mode while its database is dumped and a
#            point-in-time copy of its volume is taken
#            (<BACKUP_DUMP_DIR>/smdc-nextcloud-files), then switched back on.
#            Any failing dump aborts the run before restic is called.
#   2. FILES restic backs up the dump directory, the Nextcloud snapshot, the
#            deployment configuration (software/, including .env) and the static
#            files of the volumes listed as `files` in volumes.conf. Live
#            database files are excluded.
#
# The run stops with a non-zero exit code, before anything is uploaded or pruned,
# when: the Docker daemon cannot be reached; a volume the daemon lists cannot be
# inspected; a dump fails; a dump named in the MANIFEST is missing, has another
# size or (container mode) another SHA-256; or a service that the previous
# complete MANIFEST backed up would now be skipped (BACKUP_ALLOW_COVERAGE_DROP=1
# accepts that once; such a run uploads but does not prune).
#
# Modes:
#   host (default)        sh backup.sh              dump + files, one consistent run
#                         sh backup.sh --dump-only  dump phase only (feeds the container)
#   container             BACKUP_MODE=container (set by docker-compose.backup.yml):
#                         FILES phase only. The container has no Docker socket on
#                         purpose, so it cannot dump; it refuses to run (non-zero)
#                         unless the host's dump MANIFEST is complete and fresh.
#
# Host requirements: root on the Docker host (volume mountpoints are read
# directly), docker, sqlite3, restic.
#
# Dump procedures follow the vendors' documentation:
#   OpenProject  https://www.openproject.org/docs/installation-and-operations/operation/backing-up/
#                (all-in-one container: su - postgres -c 'pg_dump -d openproject -x -O')
#   GitLab       https://docs.gitlab.com/administration/backup_restore/backup_gitlab/
#                (docker exec <container> gitlab-backup create; gitlab.rb and
#                gitlab-secrets.json are NOT in that archive: they come from the
#                gitlab_config volume)
#   Vaultwarden  https://github.com/dani-garcia/vaultwarden/wiki/Backing-up-your-vault
#                (sqlite3 online backup: .backup)
#   Nextcloud    https://docs.nextcloud.com/server/latest/admin_manual/maintenance/backup.html
#                (maintenance mode locks user sessions and blocks new logins so
#                that the data cannot change during the backup; then copy the
#                installation directory and dump the database). Default here: occ maintenance:mode --on, SQLite
#                .backup, copy of the volume, occ maintenance:mode --off.
#                BACKUP_NEXTCLOUD_MODE=online skips maintenance mode: the
#                database is still a consistent SQLite backup, the files are
#                walked live later and may not match it.
#   SQLite       https://www.sqlite.org/backup.html  (Headscale, Grafana, Open WebUI,
#                CrowdSec, DTN spool, SMDC ledgers)
#
# Environment (also read from software/.env, without executing it):
#   RESTIC_REPOSITORY, RESTIC_PASSWORD, RESTIC_S3_KEY, RESTIC_S3_SECRET
#   BACKUP_DUMP_DIR               host directory for dumps (default /var/backups/sovereign-dumps)
#   BACKUP_DUMP_MAX_AGE_SECONDS   container mode: oldest acceptable MANIFEST (default 93600 = 26 h)
#   BACKUP_SMDC_STATE_DIR         SMDC state directory to include (default: $SMDC_STATE_DIR; unset = skipped)
#   COMPOSE_PROJECT_NAME          Compose project of the stack (default: read from sovereign_traefik, else "software")
#   GITLAB_BACKUP_ARGS            extra arguments for gitlab-backup create (e.g. STRATEGY=copy)
#   BACKUP_NEXTCLOUD_MODE         maintenance (default) | online
#   BACKUP_NEXTCLOUD_SNAPSHOT     auto (default) | reflink | rsync | copy: how the volume copy is made
#   BACKUP_NEXTCLOUD_SETTLE_SECONDS  pause after maintenance mode is switched on (default 5)
# Environment only (deliberately not read from .env, so that it cannot stay set):
#   BACKUP_ALLOW_COVERAGE_DROP=1  accept, for this run, that a previously backed-up service is skipped
# ====================================================================

set -eu
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
VOLUMES_CONF="${SCRIPT_DIR}/volumes.conf"

log()  { printf '\n\033[1;32m[BACKUP] %s\033[0m\n' "$*"; }
info() { printf '         %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]   %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]  %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: sh backup.sh [--dump-only]

  (no option)   host: dump every database, then restic backup of dumps + config + static volume files
  --dump-only   host: dump phase only (schedule it before the sovereign_backup container runs)

Container mode (BACKUP_MODE=container) runs the restic phase only and needs fresh host dumps.
EOF
}

DUMP_ONLY=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dump-only) DUMP_ONLY=true ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

# -- Settings from .env, read as data --------------------------------
# .env is a Compose env file, not a shell script: sourcing it would run
# whatever it contains as root and mangles values holding `$` or spaces.
env_file_value() { # env_file_value KEY -> value of the last KEY= line
    [ -f "$ENV_FILE" ] || return 0
    efv="$(sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r')"
    case "$efv" in
        \"*\"*) efv="${efv#\"}"; efv="${efv%\"*}" ;;
        \'*\'*) efv="${efv#\'}"; efv="${efv%\'*}" ;;
        *) efv="$(printf '%s' "$efv" | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//')" ;;
    esac
    printf '%s' "$efv"
}
for key in RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_S3_KEY RESTIC_S3_SECRET \
    BACKUP_DUMP_DIR BACKUP_DUMP_MAX_AGE_SECONDS BACKUP_SMDC_STATE_DIR SMDC_STATE_DIR \
    COMPOSE_PROJECT_NAME GITLAB_BACKUP_ARGS BACKUP_NEXTCLOUD_MODE BACKUP_NEXTCLOUD_SNAPSHOT \
    BACKUP_NEXTCLOUD_SETTLE_SECONDS; do
    eval "is_set=\${${key}+set}"
    if [ -z "$is_set" ]; then
        value="$(env_file_value "$key")"
        if [ -n "$value" ]; then
            export "${key}=${value}"
        fi
    fi
done

MODE="${BACKUP_MODE:-host}"
case "$MODE" in
    host | container) ;;
    *) err "BACKUP_MODE must be 'host' or 'container' (got '${MODE}')." ;;
esac
if [ "$MODE" = "container" ] && [ "$DUMP_ONLY" = "true" ]; then
    err "--dump-only needs the Docker CLI and the volume mountpoints: run it on the host, not in the backup container."
fi

MAX_AGE="${BACKUP_DUMP_MAX_AGE_SECONDS:-93600}"
case "$MAX_AGE" in
    '' | *[!0-9]*) err "BACKUP_DUMP_MAX_AGE_SECONDS must be a whole number of seconds (got '${MAX_AGE}')." ;;
esac

NC_MODE="${BACKUP_NEXTCLOUD_MODE:-maintenance}"
case "$NC_MODE" in
    maintenance | online) ;;
    *) err "BACKUP_NEXTCLOUD_MODE must be 'maintenance' or 'online' (got '${NC_MODE}')." ;;
esac
NC_SNAPSHOT="${BACKUP_NEXTCLOUD_SNAPSHOT:-auto}"
case "$NC_SNAPSHOT" in
    auto | reflink | rsync | copy) ;;
    *) err "BACKUP_NEXTCLOUD_SNAPSHOT must be 'auto', 'reflink', 'rsync' or 'copy' (got '${NC_SNAPSHOT}')." ;;
esac
NC_SETTLE="${BACKUP_NEXTCLOUD_SETTLE_SECONDS:-5}"
case "$NC_SETTLE" in
    '' | *[!0-9]*) err "BACKUP_NEXTCLOUD_SETTLE_SECONDS must be a whole number of seconds (got '${NC_SETTLE}')." ;;
esac

# Container layout (docker-compose.backup.yml): /dumps, /config, /data/<volume key>.
# BACKUP_CONTAINER_ROOT relocates it; it exists for tests only.
CROOT="${BACKUP_CONTAINER_ROOT:-}"
if [ "$MODE" = "container" ]; then
    DUMP_PARENT="${CROOT}/dumps"
    CONFIG_DIR="${CROOT}/config"
else
    DUMP_PARENT="${BACKUP_DUMP_DIR:-/var/backups/sovereign-dumps}"
    CONFIG_DIR="$ROOT_DIR"
fi
case "$DUMP_PARENT" in
    /*) ;;
    *) err "BACKUP_DUMP_DIR must be an absolute path (got '${DUMP_PARENT}')." ;;
esac
case "$DUMP_PARENT" in
    *\'* | *\"*) err "BACKUP_DUMP_DIR must not contain quote characters." ;;
esac
# The directory name is fixed: restore.sh finds the dumps by it in any snapshot.
DUMPS="${DUMP_PARENT}/smdc-dumps"
NEW="${DUMP_PARENT}/smdc-dumps.new"
# Point-in-time copy of the nextcloud_data volume, taken in the maintenance window.
# It is a sibling of smdc-dumps (not inside it) so that `restore.sh --verify`, which
# restores smdc-dumps only, does not have to restore every user file.
NCSNAP_NAME="smdc-nextcloud-files"
NCSNAP="${DUMP_PARENT}/${NCSNAP_NAME}"
NCSNAP_NEW="${NCSNAP}.new"
LOCK_DIR="${DUMP_PARENT}/.smdc-backup.lock"
NC_MARKER="${DUMP_PARENT}/.smdc-nextcloud-maintenance"

[ -f "$VOLUMES_CONF" ] || err "Missing ${VOLUMES_CONF}."

OPENPROJECT_CONTAINER="sovereign_openproject"
OPENPROJECT_DB="openproject"
GITLAB_CONTAINER="sovereign_gitlab"
NEXTCLOUD_CONTAINER="sovereign_nextcloud"
NEXTCLOUD_ROOT_IN_CONTAINER="/var/www/html"

# -- Prerequisites, checked before anything is touched ----------------
if [ "$DUMP_ONLY" != "true" ]; then
    export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-/var/backups/sovereign}"
    # No default: a publicly known password would make the encryption worthless.
    [ -n "${RESTIC_PASSWORD:-}" ] || err "RESTIC_PASSWORD is not set. Set it in software/.env or the environment."
    export RESTIC_PASSWORD
    if [ -z "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${RESTIC_S3_KEY:-}" ]; then export AWS_ACCESS_KEY_ID="$RESTIC_S3_KEY"; fi
    if [ -z "${AWS_SECRET_ACCESS_KEY:-}" ] && [ -n "${RESTIC_S3_SECRET:-}" ]; then export AWS_SECRET_ACCESS_KEY="$RESTIC_S3_SECRET"; fi
    command -v restic >/dev/null 2>&1 || err "restic CLI not found. Install it with: apt-get install restic"
fi
if [ "$MODE" = "host" ]; then
    command -v docker >/dev/null 2>&1 || err "docker CLI not found: the dump phase runs the databases' own dump tools through 'docker exec'."
    command -v sqlite3 >/dev/null 2>&1 || err "sqlite3 CLI not found. Install it with: apt-get install sqlite3"
fi

HASH_TOOL=""
for hash_candidate in sha256sum shasum openssl; do
    if command -v "$hash_candidate" >/dev/null 2>&1; then
        HASH_TOOL="$hash_candidate"
        break
    fi
done
[ -n "$HASH_TOOL" ] || err "No SHA-256 tool found (sha256sum, shasum or openssl): the dump files cannot be fingerprinted."

sha256_of() { # sha256_of <file> -> 64 hex characters; non-zero when the file cannot be hashed
    case "$HASH_TOOL" in
        sha256sum) so_sum="$(sha256sum "$1")" || return 1 ;;
        shasum) so_sum="$(shasum -a 256 "$1")" || return 1 ;;
        *) so_sum="$(openssl dgst -sha256 -r "$1")" || return 1 ;;
    esac
    so_sum="${so_sum%%[!0-9a-fA-F]*}"
    [ "${#so_sum}" -eq 64 ] || return 1
    printf '%s' "$so_sum"
}

file_size() { # file_size <file> -> bytes
    fs_bytes="$(wc -c <"$1")" || return 1
    printf '%s' "$fs_bytes" | tr -d '[:space:]'
}

# ====================================================================
# Clean exit: Nextcloud never stays in maintenance mode, the lock is released
# ====================================================================
NC_MAINT_OWNED=false
LOCK_HELD=false

nc_occ() {
    docker exec -u www-data "$NEXTCLOUD_CONTAINER" php "${NEXTCLOUD_ROOT_IN_CONTAINER}/occ" "$@"
}

# Switch maintenance mode off if (and only if) this script switched it on.
nc_maintenance_off() {
    [ "$NC_MAINT_OWNED" = "true" ] || return 0
    for nmo_try in 1 2 3; do
        if nc_occ maintenance:mode --off >/dev/null 2>&1; then
            NC_MAINT_OWNED=false
            rm -f "$NC_MARKER"
            info "Nextcloud maintenance mode switched off"
            return 0
        fi
        if [ "$nmo_try" != "3" ]; then sleep 2; fi
    done
    printf '\033[1;31m[ERROR]  Nextcloud is STILL IN MAINTENANCE MODE: switching it off failed three times. Run:\n         docker exec -u www-data %s php %s/occ maintenance:mode --off\033[0m\n' \
        "$NEXTCLOUD_CONTAINER" "$NEXTCLOUD_ROOT_IN_CONTAINER" >&2
    return 1
}

cleanup() {
    cl_rc=$?
    trap - EXIT INT TERM HUP
    if ! nc_maintenance_off; then
        if [ "$cl_rc" -eq 0 ]; then cl_rc=1; fi
    fi
    if [ "$LOCK_HELD" = "true" ]; then
        if [ "$cl_rc" -ne 0 ]; then
            # A failed run leaves no half-written plaintext dumps or copies behind.
            rm -rf "$NEW" "$NCSNAP_NEW"
        fi
        rm -rf "$LOCK_DIR"
    fi
    exit "$cl_rc"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# One dump run at a time: two would overwrite each other's files and fight over
# Nextcloud's maintenance mode. A lock whose process is gone is stale and is taken over.
acquire_lock() {
    mkdir -p "$DUMP_PARENT"
    chmod 700 "$DUMP_PARENT"
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        al_pid="$(cat "${LOCK_DIR}/pid" 2>/dev/null || true)"
        case "$al_pid" in
            '' | *[!0-9]*) al_pid="" ;;
        esac
        if [ -n "$al_pid" ] && kill -0 "$al_pid" 2>/dev/null; then
            err "Another backup.sh dump run (pid ${al_pid}) holds ${LOCK_DIR}. If that process is not a backup, remove the directory and retry. Nothing was backed up."
        fi
        warn "Stale lock ${LOCK_DIR} (pid ${al_pid:-unknown} is not running): an earlier run was killed. Taking it over."
        rm -rf "$LOCK_DIR"
        mkdir "$LOCK_DIR" || err "Cannot create ${LOCK_DIR}. Nothing was backed up."
    fi
    LOCK_HELD=true
    printf '%s\n' "$$" >"${LOCK_DIR}/pid"
}

# ====================================================================
# Docker daemon: an unreachable daemon is a failure, not "nothing deployed"
# ====================================================================
VOLUME_LIST=""
docker_preflight() {
    dp_out="$(docker version --format '{{ .Server.Version }}' 2>&1)" \
        || err "The Docker daemon is not reachable (docker version: ${dp_out}). Without it no database can be dumped and no volume can be found, so this is NOT treated as 'nothing is deployed'. Run as root on the Docker host. Nothing was dumped, uploaded or pruned."
    [ -n "$dp_out" ] || err "The Docker daemon returned no server version. Nothing was dumped, uploaded or pruned."
    VOLUME_LIST="$(docker volume ls --quiet 2>&1)" \
        || err "The Docker daemon does not list its volumes (docker volume ls: ${VOLUME_LIST}). Nothing was dumped, uploaded or pruned."
}

# ====================================================================
# Volume table
# ====================================================================
conf_rows() { grep -v '^[[:space:]]*#' "$VOLUMES_CONF" | grep -v '^[[:space:]]*$' || true; }

conf_name() { # conf_name <key> -> the `name` column ('-' or a fixed name); empty if unknown
    conf_rows | awk -F'|' -v k="$1" '$1 == k { print $2; exit }'
}

PROJECT=""
project_name() {
    if [ -z "$PROJECT" ]; then
        PROJECT="${COMPOSE_PROJECT_NAME:-}"
        if [ -z "$PROJECT" ]; then
            PROJECT="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' sovereign_traefik 2>/dev/null || true)"
        fi
        if [ -z "$PROJECT" ]; then
            PROJECT="$(basename "$ROOT_DIR" | tr '[:upper:]' '[:lower:]')"
        fi
    fi
    printf '%s' "$PROJECT"
}

# volume_root <key>: directory holding the volume's content, or empty when the
# volume does not exist (its overlay is not deployed).
#
# "Does not exist" is decided from the volume list the daemon returned in
# docker_preflight, never from a failing command: a volume that is listed but
# cannot be inspected (daemon gone, socket permission, any other error) aborts
# the run. Call it as  x="$(volume_root key)" || exit 1.
volume_root() {
    if [ "$MODE" = "container" ]; then
        if [ -d "${CROOT}/data/$1" ]; then printf '%s' "${CROOT}/data/$1"; fi
        return 0
    fi
    vr_name="$(conf_name "$1")"
    [ -n "$vr_name" ] || err "Volume key '$1' is not listed in ${VOLUMES_CONF}."
    if [ "$vr_name" = "-" ]; then vr_name="$(project_name)_$1"; fi
    if ! printf '%s\n' "$VOLUME_LIST" | grep -Fqx -e "$vr_name"; then
        return 0
    fi
    vr_mp="$(docker volume inspect --format '{{ .Mountpoint }}' "$vr_name" 2>&1)" \
        || err "docker volume inspect ${vr_name} failed although the daemon lists that volume (${vr_mp}). This is an error, not a missing service. Nothing was backed up."
    [ -n "$vr_mp" ] || err "docker volume inspect ${vr_name} returned no mountpoint. Nothing was backed up."
    [ -d "$vr_mp" ] || err "Volume ${vr_name} exists but its mountpoint ${vr_mp} is not readable here. Run this script as root on the Docker host (not against Docker Desktop, rootless Docker or a remote daemon)."
    printf '%s' "$vr_mp"
}

# True when the container is running. A daemon that cannot answer aborts the run:
# "docker ps failed" must never be read as "the service is stopped".
container_running() {
    cr_list="$(docker ps --format '{{ .Names }}' 2>&1)" \
        || err "docker ps failed (${cr_list}): cannot tell which services are running. Nothing was backed up."
    printf '%s\n' "$cr_list" | grep -Fqx -e "$1"
}

# SQLite databases that live inside a `files` volume:
#   label|volume key|database path relative to the volume root|container
sqlite_services() {
    cat <<'EOF'
vaultwarden|vaultwarden_data|db.sqlite3|sovereign_vaultwarden
headscale|headscale_data|db.sqlite|sovereign_headscale
grafana|grafana_data|grafana.db|sovereign_grafana
open-webui|webui_data|webui.db|sovereign_webui
crowdsec|crowdsec_data|crowdsec.db|sovereign_crowdsec
dtn-spool|space_spool_data|bundles.db|sovereign_space_node
EOF
}

# Live paths restic must never walk (relative to the volume root; globs allowed):
#   volume key|relative path
live_excludes() {
    sqlite_services | while IFS='|' read -r _label key rel _container; do
        for suffix in "" "-wal" "-shm" "-journal"; do
            printf '%s|%s%s\n' "$key" "$rel" "$suffix"
        done
    done
    cat <<'EOF'
nextcloud_data|data/*.db
nextcloud_data|data/*.db-wal
nextcloud_data|data/*.db-shm
nextcloud_data|data/*.db-journal
prometheus_data|wal
prometheus_data|wbl
prometheus_data|chunks_head
prometheus_data|lock
prometheus_data|queries.active
EOF
}

# ====================================================================
# Phase 1: dumps (host)
# ====================================================================
# MANIFEST format 2 (one record per line, pipe-separated):
#   smdc-dump-manifest|2
#   created_epoch|<s>   created|<UTC>   project|<name>   nextcloud_mode|<maintenance|online>
#   dump|<name>|<kind>|<path>|<ok|skipped>|<detail>     kind: sqlite pgdump gitlab files tree
#   file|<path relative to smdc-dumps>|<bytes>|<sha256>  every dump file of an ok row
#   extfile|<volume key>|<path in the volume>|<bytes>|<sha256>   the GitLab archive
#   tree|<directory next to smdc-dumps>|<number of files>         the Nextcloud snapshot
#   volume|<key>|<present|absent>                       every `files` volume of volumes.conf
#   coverage|full   or   coverage|reduced|<names>       see coverage_guard
#   status|complete                                     last line, only when every step succeeded
# Format 1 (no file/extfile/tree/volume/coverage records) is still read.
manifest_add() { # manifest_add <name> <kind> <path relative to smdc-dumps> <ok|skipped> <detail>
    case "$1$3" in
        *'|'*) err "Dump of $1 failed: a '|' in a name or path cannot be recorded in the MANIFEST. Nothing was backed up." ;;
    esac
    printf 'dump|%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" >>"${NEW}/MANIFEST.partial"
    if [ "$4" = "ok" ]; then
        info "ok       $1  ($2)  $5"
    else
        info "skipped  $1  ($2)  $5"
    fi
}

# Size and SHA-256 of a finished dump file, so that the restic phase (possibly
# hours later, in the container) can prove it still has the very same bytes.
manifest_file() { # manifest_file <path relative to smdc-dumps>
    case "$1" in
        *'|'*) err "Cannot record '$1' in the MANIFEST: the path contains '|'. Nothing was backed up." ;;
    esac
    mf_size="$(file_size "${NEW}/$1")" || err "Cannot read the size of ${NEW}/$1. Nothing was backed up."
    [ "$mf_size" -gt 0 ] || err "Dump file ${NEW}/$1 is empty. Nothing was backed up."
    mf_sum="$(sha256_of "${NEW}/$1")" || err "Cannot compute the SHA-256 of ${NEW}/$1. Nothing was backed up."
    printf 'file|%s|%s|%s\n' "$1" "$mf_size" "$mf_sum" >>"${NEW}/MANIFEST.partial"
}

is_sqlite_file() {
    [ "$(dd if="$1" bs=15 count=1 2>/dev/null)" = "SQLite format 3" ]
}

# Consistent copy through SQLite's online backup API, then prove the copy opens.
sqlite_backup() { # sqlite_backup <label> <source db> <destination>
    mkdir -p "$(dirname "$3")"
    rm -f "$3"
    sqlite3 -bail "$2" ".timeout 60000" ".backup '$3'" \
        || err "Dump of $1 failed: sqlite3 .backup of $2 returned an error. Nothing was backed up."
    [ -s "$3" ] || err "Dump of $1 failed: sqlite3 .backup of $2 produced no file. Nothing was backed up."
    sb_check="$(sqlite3 "$3" "PRAGMA integrity_check;" 2>&1)" \
        || err "Dump of $1 failed: the copy $3 cannot be opened (${sb_check}). Nothing was backed up."
    [ "$sb_check" = "ok" ] || err "Dump of $1 failed: integrity_check of $3 says '${sb_check}'. Nothing was backed up."
}

dump_sqlite_service() { # dump_sqlite_service <label> <volume key> <relative db path> <container>
    ds_root="$(volume_root "$2")" || exit 1
    if [ -z "$ds_root" ]; then
        manifest_add "$1" sqlite "" skipped "volume $2 does not exist (service not deployed)"
        return 0
    fi
    if [ ! -f "${ds_root}/$3" ]; then
        if container_running "$4"; then
            err "Dump of $1 failed: $4 is running but its database ${ds_root}/$3 does not exist. Nothing was backed up."
        fi
        manifest_add "$1" sqlite "" skipped "no database file in volume $2 and $4 is not running (service never started)"
        return 0
    fi
    sqlite_backup "$1" "${ds_root}/$3" "${NEW}/sqlite/$1.sqlite"
    manifest_add "$1" sqlite "sqlite/$1.sqlite" ok "from volume $2:$3"
    manifest_file "sqlite/$1.sqlite"
}

nc_setting() { # nc_setting <config.php key> -> value; aborts the run when it cannot be read
    ns_value="$(nc_occ config:system:get "$1")" \
        || err "Dump of nextcloud failed: cannot read '$1' through occ. Nothing was backed up."
    ns_value="$(printf '%s' "$ns_value" | tr -d '\r' | head -n 1)"
    [ -n "$ns_value" ] || err "Dump of nextcloud failed: occ returned an empty '$1'. Nothing was backed up."
    printf '%s' "$ns_value"
}

nc_config_php_value() { # nc_config_php_value <config.php> <key> -> value of a 'key' => 'value' line, or empty
    sed -n "s/^[[:space:]]*'$2'[[:space:]]*=>[[:space:]]*'\\([^']*\\)'.*/\\1/p" "$1" | head -n 1
}

# -- Nextcloud maintenance window --------------------------------------
# Nextcloud's backup procedure starts with `occ maintenance:mode --on`, which
# locks the sessions of logged-in users and blocks new logins so that the data
# stays consistent. Without it a file renamed or deleted
# between the database dump and the file copy leaves the restored database
# pointing at files that are not in the snapshot.
#
# Guards:
#   - NC_MAINT_OWNED is set before `--on` is sent, and the EXIT/INT/TERM/HUP traps
#     switch maintenance mode off again whatever happens in between;
#   - a marker file records that this script switched it on. If Nextcloud is found
#     in maintenance mode WITH the marker, an earlier run was killed before it could
#     switch it off (SIGKILL, power loss): this run takes it over and switches it
#     off at the end. WITHOUT the marker somebody else switched it on (an upgrade,
#     an administrator): this script does not touch it and does not back up a
#     Nextcloud that may be in the middle of a migration.
nc_maintenance_state() { # -> enabled | disabled
    nms_out="$(nc_occ maintenance:mode 2>&1)" \
        || err "Dump of nextcloud failed: 'occ maintenance:mode' returned an error (${nms_out}). Nothing was backed up."
    case "$nms_out" in
        *"currently enabled"*) printf 'enabled' ;;
        *"currently disabled"*) printf 'disabled' ;;
        *) err "Dump of nextcloud failed: cannot tell the maintenance state from occ ('${nms_out}'). Nothing was backed up." ;;
    esac
}

nc_maintenance_on() {
    nmn_state="$(nc_maintenance_state)" || exit 1
    if [ "$nmn_state" = "enabled" ]; then
        if [ -f "$NC_MARKER" ]; then
            warn "Nextcloud is in maintenance mode and ${NC_MARKER} says an earlier run of this script switched it on ($(head -n 1 "$NC_MARKER" 2>/dev/null || true)) and was killed before switching it off. Taking it over: it is switched off at the end of this dump."
            NC_MAINT_OWNED=true
            return 0
        fi
        err "Dump of nextcloud failed: Nextcloud is already in maintenance mode and this script did not switch it on (upgrade or administrator action in progress?). It is left as it is. Finish that work, run 'occ maintenance:mode --off', then retry. Nothing was backed up."
    fi
    if [ -f "$NC_MARKER" ]; then
        warn "Removing stale ${NC_MARKER}: Nextcloud is not in maintenance mode any more."
        rm -f "$NC_MARKER"
    fi
    printf 'switched on %s by backup.sh pid %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" >"$NC_MARKER"
    NC_MAINT_OWNED=true
    nc_occ maintenance:mode --on >/dev/null \
        || err "Dump of nextcloud failed: 'occ maintenance:mode --on' returned an error. Nothing was backed up."
    info "Nextcloud maintenance mode switched on (users are locked out until the database dump and the file copy are done)"
    # Requests that were already running finish on their own; give them a moment.
    if [ "$NC_SETTLE" -gt 0 ]; then sleep "$NC_SETTLE"; fi
}

# How the point-in-time copy of the volume is made (decided before the window opens):
#   reflink  cp -a --reflink=always: copy-on-write clone, seconds, no extra space.
#            Needs Btrfs or XFS with reflinks, dump directory on the same filesystem.
#   rsync    rsync -a --link-dest=<previous snapshot>: unchanged files become hard
#            links to the PREVIOUS SNAPSHOT (never to the live files), only new and
#            changed files are copied. One extra copy of the data on disk.
#   copy     cp -a: a full copy every run; the slowest, same disk usage as rsync.
# Hard links to the LIVE files (cp -al) are not used: Nextcloud rewrites some files
# in place, which would change the "snapshot" after the window has closed.
nc_snapshot_method() { # nc_snapshot_method <volume root> -> reflink | rsync | copy
    nsm_method="$NC_SNAPSHOT"
    nsm_probe="${NCSNAP_NEW}.probe"
    if [ "$nsm_method" = "auto" ] || [ "$nsm_method" = "reflink" ]; then
        rm -f "$nsm_probe"
        if cp -a --reflink=always "$1/config/config.php" "$nsm_probe" 2>/dev/null; then
            nsm_method="reflink"
        elif [ "$nsm_method" = "reflink" ]; then
            rm -f "$nsm_probe"
            err "Dump of nextcloud failed: BACKUP_NEXTCLOUD_SNAPSHOT=reflink, but a copy-on-write copy from the volume to ${DUMP_PARENT} is not possible (needs GNU cp and both on one Btrfs or reflink-enabled XFS filesystem). Nothing was backed up."
        elif command -v rsync >/dev/null 2>&1; then
            nsm_method="rsync"
        else
            nsm_method="copy"
        fi
        rm -f "$nsm_probe"
    fi
    if [ "$nsm_method" = "rsync" ]; then
        command -v rsync >/dev/null 2>&1 \
            || err "Dump of nextcloud failed: BACKUP_NEXTCLOUD_SNAPSHOT=rsync, but rsync is not installed (apt-get install rsync). Nothing was backed up."
    fi
    # A full copy needs as much free space as the volume holds. Check before the
    # window opens rather than fill the disk while users are locked out.
    if [ "$nsm_method" = "copy" ] || { [ "$nsm_method" = "rsync" ] && [ ! -d "${NCSNAP}/volume" ]; }; then
        nsm_need="$(du -sk "$1" 2>/dev/null | awk '{ print $1; exit }')"
        nsm_free="$(df -Pk "$DUMP_PARENT" 2>/dev/null | awk 'NR == 2 { print $4 }')"
        case "${nsm_need}:${nsm_free}" in
            *[!0-9:]* | :* | *:) warn "Cannot compare the size of the Nextcloud volume with the free space in ${DUMP_PARENT}; continuing." >&2 ;;
            *)
                if [ "$nsm_free" -lt "$nsm_need" ]; then
                    err "Dump of nextcloud failed: the copy of the volume needs about ${nsm_need} KiB, ${DUMP_PARENT} has ${nsm_free} KiB free. Free space, move BACKUP_DUMP_DIR, or set BACKUP_NEXTCLOUD_MODE=online (see backup/README.md for what that gives up). Nothing was backed up."
                fi
                ;;
        esac
    fi
    printf '%s' "$nsm_method"
}

# nc_snapshot <volume root> <database path relative to the volume root> <method>
# Copies the whole volume except the live database files (the dump replaces them).
nc_snapshot() {
    rm -rf "$NCSNAP_NEW"
    mkdir -p "${NCSNAP_NEW}/volume"
    case "$3" in
        reflink)
            cp -a --reflink=always "$1/." "${NCSNAP_NEW}/volume/" \
                || err "Dump of nextcloud failed: the copy-on-write copy of the volume returned an error. Nothing was backed up."
            ;;
        rsync)
            ns_root="$1"
            set -- -a --numeric-ids \
                --exclude "/$2" --exclude "/$2-wal" --exclude "/$2-shm" --exclude "/$2-journal"
            if [ -d "${NCSNAP}/volume" ]; then
                set -- "$@" --link-dest "${NCSNAP}/volume"
            fi
            rsync "$@" "${ns_root}/" "${NCSNAP_NEW}/volume/" \
                || err "Dump of nextcloud failed: rsync of the volume returned an error. Nothing was backed up."
            ;;
        *)
            cp -a "$1/." "${NCSNAP_NEW}/volume/" \
                || err "Dump of nextcloud failed: the copy of the volume returned an error. Nothing was backed up."
            ;;
    esac
}

dump_nextcloud() {
    dn_root="$(volume_root nextcloud_data)" || exit 1
    if [ -z "$dn_root" ]; then
        manifest_add nextcloud sqlite "" skipped "volume nextcloud_data does not exist (service not deployed)"
        return 0
    fi
    if [ ! -f "${dn_root}/config/config.php" ]; then
        manifest_add nextcloud sqlite "" skipped "Nextcloud is not installed yet (no config/config.php)"
        return 0
    fi
    dn_running=false
    if container_running "$NEXTCLOUD_CONTAINER"; then
        dn_running=true
        if nc_occ status 2>/dev/null | grep -q 'installed: false'; then
            manifest_add nextcloud sqlite "" skipped "Nextcloud is not installed yet (occ status)"
            return 0
        fi
        dn_type="$(nc_setting dbtype)" || exit 1
        dn_name="$(nc_setting dbname)" || exit 1
        dn_dir="$(nc_setting datadirectory)" || exit 1
    else
        # Stopped: occ is unavailable, so read config.php; a stopped SQLite database
        # is consistent on disk and nothing writes to the files.
        dn_type="$(nc_config_php_value "${dn_root}/config/config.php" dbtype)"
        dn_name="$(nc_config_php_value "${dn_root}/config/config.php" dbname)"
        dn_dir="$(nc_config_php_value "${dn_root}/config/config.php" datadirectory)"
        dn_type="${dn_type:-sqlite3}"
        dn_name="${dn_name:-owncloud}"
        dn_dir="${dn_dir:-${NEXTCLOUD_ROOT_IN_CONTAINER}/data}"
    fi
    case "$dn_type" in
        sqlite | sqlite3) ;;
        *) err "Dump of nextcloud failed: it uses the '${dn_type}' database, which this stack does not deploy and this script cannot dump. Nothing was backed up." ;;
    esac
    case "$dn_dir" in
        "${NEXTCLOUD_ROOT_IN_CONTAINER}"/*) dn_rel="${dn_dir#"${NEXTCLOUD_ROOT_IN_CONTAINER}"/}/${dn_name}.db" ;;
        *) err "Dump of nextcloud failed: its data directory ${dn_dir} is outside the nextcloud_data volume. Nothing was backed up." ;;
    esac
    dn_db="${dn_root}/${dn_rel}"
    [ -f "$dn_db" ] || err "Dump of nextcloud failed: database file ${dn_db} not found. Nothing was backed up."

    if [ "$NC_MODE" = "online" ]; then
        sqlite_backup nextcloud "$dn_db" "${NEW}/sqlite/nextcloud.sqlite"
        manifest_add nextcloud sqlite "sqlite/nextcloud.sqlite" ok "from volume nextcloud_data:${dn_rel}; BACKUP_NEXTCLOUD_MODE=online: the files are walked live later and may not match this database"
        manifest_file "sqlite/nextcloud.sqlite"
        return 0
    fi

    dn_method="$(nc_snapshot_method "$dn_root")" || exit 1
    dn_start="$(date +%s)"
    if [ "$dn_running" = "true" ]; then
        nc_maintenance_on
    fi
    sqlite_backup nextcloud "$dn_db" "${NEW}/sqlite/nextcloud.sqlite"
    nc_snapshot "$dn_root" "$dn_rel" "$dn_method"
    if [ "$dn_running" = "true" ]; then
        nc_maintenance_off || err "Dump of nextcloud failed: maintenance mode could not be switched off (see above). Nothing was backed up."
    fi
    dn_window=$(($(date +%s) - dn_start))

    # The dump replaces the live database files; they must not be in the copy.
    rm -f "${NCSNAP_NEW}/volume/${dn_rel}" "${NCSNAP_NEW}/volume/${dn_rel}-wal" \
        "${NCSNAP_NEW}/volume/${dn_rel}-shm" "${NCSNAP_NEW}/volume/${dn_rel}-journal"
    [ -f "${NCSNAP_NEW}/volume/config/config.php" ] \
        || err "Dump of nextcloud failed: the copy of the volume has no config/config.php. Nothing was backed up."
    dn_count="$(find "${NCSNAP_NEW}/volume" -type f | wc -l | tr -d '[:space:]')"
    printf '%s\n' "$DUMP_EPOCH" >"${NCSNAP_NEW}/SNAPSHOT"

    if [ "$dn_running" = "true" ]; then
        dn_how="maintenance mode for ${dn_window} s"
    else
        dn_how="container stopped"
    fi
    manifest_add nextcloud sqlite "sqlite/nextcloud.sqlite" ok "from volume nextcloud_data:${dn_rel}; ${dn_how}"
    manifest_file "sqlite/nextcloud.sqlite"
    manifest_add nextcloud-files tree "$NCSNAP_NAME" ok "${dn_count} file(s), copy of volume nextcloud_data taken in the same window (${dn_how}; method ${dn_method}); database file ${dn_rel} left out"
    printf 'tree|%s|%s\n' "$NCSNAP_NAME" "$dn_count" >>"${NEW}/MANIFEST.partial"
}

dump_openproject() {
    do_root="$(volume_root openproject_pgdata)" || exit 1
    if [ -z "$do_root" ]; then
        manifest_add openproject pgdump "" skipped "volume openproject_pgdata does not exist (service not deployed)"
        return 0
    fi
    if ! container_running "$OPENPROJECT_CONTAINER"; then
        if [ -f "${do_root}/PG_VERSION" ]; then
            err "Dump of openproject failed: the volume holds a PostgreSQL database but ${OPENPROJECT_CONTAINER} is not running, so pg_dump cannot run. Start it and retry. Nothing was backed up."
        fi
        manifest_add openproject pgdump "" skipped "PostgreSQL was never initialised and ${OPENPROJECT_CONTAINER} is not running"
        return 0
    fi
    mkdir -p "${NEW}/postgres"
    do_out="${NEW}/postgres/openproject.pgdump"
    # Official all-in-one procedure, in custom format (-Fc) so that pg_restore
    # can list and verify it. No -t: a TTY would corrupt the binary stream.
    docker exec "$OPENPROJECT_CONTAINER" su - postgres -c "pg_dump -d ${OPENPROJECT_DB} -x -O -Fc" >"$do_out" \
        || err "Dump of openproject failed: pg_dump returned an error. Nothing was backed up."
    [ "$(dd if="$do_out" bs=5 count=1 2>/dev/null)" = "PGDMP" ] \
        || err "Dump of openproject failed: ${do_out} is not a PostgreSQL custom-format archive. Nothing was backed up."
    do_ver="$(docker exec "$OPENPROJECT_CONTAINER" su - postgres -c 'pg_dump --version' 2>/dev/null | tr -d '\r' | head -n 1 || true)"
    manifest_add openproject pgdump "postgres/openproject.pgdump" ok "${do_ver:-pg_dump version unknown}"
    manifest_file "postgres/openproject.pgdump"
}

dump_gitlab() {
    dg_root="$(volume_root gitlab_data)" || exit 1
    if [ -z "$dg_root" ]; then
        manifest_add gitlab gitlab "" skipped "volume gitlab_data does not exist (service not deployed)"
        return 0
    fi
    if ! container_running "$GITLAB_CONTAINER"; then
        if [ -d "${dg_root}/postgresql" ]; then
            err "Dump of gitlab failed: the volume holds GitLab data but ${GITLAB_CONTAINER} is not running, so gitlab-backup cannot run. Start it and retry. Nothing was backed up."
        fi
        manifest_add gitlab gitlab "" skipped "GitLab was never initialised and ${GITLAB_CONTAINER} is not running"
        return 0
    fi
    dg_before="$(ls -1 "${dg_root}/backups" 2>/dev/null || true)"
    # shellcheck disable=SC2086  # GITLAB_BACKUP_ARGS is a list of KEY=value arguments
    if ! docker exec "$GITLAB_CONTAINER" gitlab-backup create ${GITLAB_BACKUP_ARGS:-} >"${NEW}/gitlab-backup.log" 2>&1; then
        tail -n 20 "${NEW}/gitlab-backup.log" >&2 || true
        err "Dump of gitlab failed: gitlab-backup create returned an error (log above). Nothing was backed up."
    fi
    dg_new=""
    for dg_file in "${dg_root}/backups"/*_gitlab_backup.tar; do
        [ -f "$dg_file" ] || continue
        dg_base="$(basename "$dg_file")"
        if ! printf '%s\n' "$dg_before" | grep -Fqx "$dg_base"; then
            dg_new="$dg_base"
        fi
    done
    [ -n "$dg_new" ] || err "Dump of gitlab failed: gitlab-backup create left no new *_gitlab_backup.tar in ${dg_root}/backups. Nothing was backed up."
    dg_size="$(file_size "${dg_root}/backups/${dg_new}")" || err "Dump of gitlab failed: cannot read the size of ${dg_new}. Nothing was backed up."
    [ "$dg_size" -gt 0 ] || err "Dump of gitlab failed: ${dg_new} is empty. Nothing was backed up."
    dg_sum="$(sha256_of "${dg_root}/backups/${dg_new}")" || err "Dump of gitlab failed: cannot compute the SHA-256 of ${dg_new}. Nothing was backed up."
    manifest_add gitlab gitlab "$dg_new" ok "archive in volume gitlab_data:backups; gitlab.rb and gitlab-secrets.json come from volume gitlab_config"
    printf 'extfile|gitlab_data|backups/%s|%s|%s\n' "$dg_new" "$dg_size" "$dg_sum" >>"${NEW}/MANIFEST.partial"
}

# The SMDC CLI/dashboard state directory lives on the host, outside Compose:
# wallets and node keys (copied) and SQLite ledgers / DTN spool (dumped).
dump_state_dir() {
    st_dir="${BACKUP_SMDC_STATE_DIR:-${SMDC_STATE_DIR:-}}"
    if [ -z "$st_dir" ]; then
        manifest_add smdc-state files "" skipped "BACKUP_SMDC_STATE_DIR / SMDC_STATE_DIR not set"
        return 0
    fi
    [ -d "$st_dir" ] || err "Dump of smdc-state failed: ${st_dir} is not a directory. Nothing was backed up."
    (cd "$st_dir" && find . -type f) >"${NEW}/.state-files"
    st_copied=0
    while IFS= read -r st_file; do
        st_file="${st_file#./}"
        case "$st_file" in
            *-wal | *-shm | *-journal) continue ;;
        esac
        if is_sqlite_file "${st_dir}/${st_file}"; then
            sqlite_backup "smdc-state:${st_file}" "${st_dir}/${st_file}" "${NEW}/smdc-state/${st_file}"
            manifest_add "smdc-state:${st_file}" sqlite "smdc-state/${st_file}" ok "from ${st_dir}"
            manifest_file "smdc-state/${st_file}"
        else
            mkdir -p "$(dirname "${NEW}/smdc-state/${st_file}")"
            cp -p "${st_dir}/${st_file}" "${NEW}/smdc-state/${st_file}" \
                || err "Dump of smdc-state failed: cannot copy ${st_dir}/${st_file}. Nothing was backed up."
            if [ -s "${NEW}/smdc-state/${st_file}" ]; then
                manifest_file "smdc-state/${st_file}"
            fi
            st_copied=$((st_copied + 1))
        fi
    done <"${NEW}/.state-files"
    rm -f "${NEW}/.state-files"
    mkdir -p "${NEW}/smdc-state"
    manifest_add smdc-state files "smdc-state" ok "${st_copied} plain file(s) copied from ${st_dir} (wallets, keys); SQLite files are listed separately"
}

# Which `files` volumes exist, so that the next run can tell when one has gone.
record_volumes() {
    while IFS='|' read -r rv_key _name rv_class _paths _reason; do
        [ "$rv_class" = "files" ] || continue
        rv_root="$(volume_root "$rv_key")" || exit 1
        if [ -n "$rv_root" ]; then
            printf 'volume|%s|present\n' "$rv_key" >>"${NEW}/MANIFEST.partial"
        else
            printf 'volume|%s|absent\n' "$rv_key" >>"${NEW}/MANIFEST.partial"
        fi
    done <<EOF
$(conf_rows)
EOF
}

# A backup that silently covers less than the one before is worse than a failed
# one: monitoring stays green while a service is no longer protected, and the
# retention policy then prunes the last snapshots that still held it. So compare
# this run with the previous COMPLETE manifest (the one still in smdc-dumps): a
# dump that was `ok` and is now `skipped`, or a `files` volume that was present and
# is now absent, stops the run before the old dumps are replaced and before restic
# is called. BACKUP_ALLOW_COVERAGE_DROP=1 accepts it for one run (a service that was
# removed on purpose); the manifest then says `coverage|reduced|...` and the restic
# phase uploads but does not prune.
coverage_guard() {
    # A run that dumped nothing at all is a wrong project name or a wrong host far
    # more often than an empty node, and there is no earlier manifest to catch it on
    # a first run.
    if ! grep -q '^dump|[^|]*|[^|]*|[^|]*|ok|' "${NEW}/MANIFEST.partial"; then
        if [ "${BACKUP_ALLOW_COVERAGE_DROP:-}" != "1" ]; then
            err "No database was dumped: every service was skipped. The stack's volumes were looked up under the Compose project '$(project_name)'; if that is not the project the stack runs under, set COMPOSE_PROJECT_NAME. Nothing was uploaded or pruned. (BACKUP_ALLOW_COVERAGE_DROP=1 accepts a node without any database.)"
        fi
        warn "No database was dumped (every service skipped); accepted by BACKUP_ALLOW_COVERAGE_DROP=1."
    fi
    cg_prev="${DUMPS}/MANIFEST"
    if [ ! -f "$cg_prev" ] || ! grep -qx 'status|complete' "$cg_prev"; then
        info "coverage: no previous complete MANIFEST to compare with; this run is the baseline"
        printf 'coverage|full\n' >>"${NEW}/MANIFEST.partial"
        return 0
    fi
    cg_dropped="$(awk -F'|' '
        NR == FNR {
            if ($1 == "dump" && $5 == "ok") was_ok[$2] = 1
            if ($1 == "volume" && $3 == "present") was_present[$2] = 1
            next
        }
        $1 == "dump" && $5 == "skipped" && ($2 in was_ok) { printf "%s%s (%s)", sep, $2, $6; sep = ", " }
        $1 == "volume" && $3 == "absent" && ($2 in was_present) { printf "%svolume %s (no longer exists)", sep, $2; sep = ", " }
    ' "$cg_prev" "${NEW}/MANIFEST.partial")"
    if [ -z "$cg_dropped" ]; then
        printf 'coverage|full\n' >>"${NEW}/MANIFEST.partial"
        return 0
    fi
    cg_when="$(sed -n 's/^created|//p' "$cg_prev" | head -n 1)"
    if [ "${BACKUP_ALLOW_COVERAGE_DROP:-}" = "1" ]; then
        warn "Coverage is reduced against the previous dumps (${cg_when}), accepted by BACKUP_ALLOW_COVERAGE_DROP=1: ${cg_dropped}. This run uploads a snapshot but does not prune older ones."
        printf 'coverage|reduced|%s\n' "$(printf '%s' "$cg_dropped" | tr '|' '/')" >>"${NEW}/MANIFEST.partial"
        return 0
    fi
    err "Coverage drop: the previous dumps (${cg_when}) backed up what this run would skip: ${cg_dropped}. A stopped or broken service looks exactly like this, so the run stops here: the previous dumps are kept, nothing was uploaded and nothing was pruned. If the service was removed on purpose, run once with BACKUP_ALLOW_COVERAGE_DROP=1."
}

dump_phase() {
    log "Phase 1/2: application-consistent dumps into ${DUMPS}"
    # Before anything is written: is the daemon there at all?
    docker_preflight
    # The project-scoped volumes are looked up as <project>_<key>. A project name
    # under which the daemon has no volume at all is a wrong COMPOSE_PROJECT_NAME
    # (or the wrong host), not a node on which nothing is deployed.
    dp_project="$(project_name)"
    if ! printf '%s\n' "$VOLUME_LIST" | awk -v p="${dp_project}_" 'index($0, p) == 1 { found = 1 } END { exit !found }'; then
        if [ "${BACKUP_ALLOW_COVERAGE_DROP:-}" != "1" ]; then
            err "The Docker daemon has no volume of the Compose project '${dp_project}' (none is named ${dp_project}_*). Every project-scoped service would be skipped. Set COMPOSE_PROJECT_NAME to the project the stack runs under ('docker compose ls'). Nothing was dumped, uploaded or pruned. (BACKUP_ALLOW_COVERAGE_DROP=1 accepts a node without any such volume.)"
        fi
        warn "No volume of the Compose project '${dp_project}' exists; accepted by BACKUP_ALLOW_COVERAGE_DROP=1."
    fi
    acquire_lock
    rm -rf "$NEW" "$NCSNAP_NEW"
    mkdir -p "$NEW"
    DUMP_EPOCH="$(date +%s)"
    {
        printf 'smdc-dump-manifest|2\n'
        printf 'created_epoch|%s\n' "$DUMP_EPOCH"
        printf 'created|%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'project|%s\n' "$(project_name)"
        printf 'nextcloud_mode|%s\n' "$NC_MODE"
    } >"${NEW}/MANIFEST.partial"

    # Nextcloud first: its users are locked out for the length of its window, so
    # that window holds nothing but its own database dump and file copy and never
    # waits behind another service (the GitLab archive can take a long time).
    dump_nextcloud

    while IFS='|' read -r label key rel container; do
        dump_sqlite_service "$label" "$key" "$rel" "$container"
    done <<EOF
$(sqlite_services)
EOF
    dump_openproject
    dump_gitlab
    dump_state_dir
    record_volumes
    coverage_guard

    # Only a run in which every step succeeded gets a MANIFEST marked complete.
    printf 'status|complete\n' >>"${NEW}/MANIFEST.partial"
    mv "${NEW}/MANIFEST.partial" "${NEW}/MANIFEST"
    # Swap in the Nextcloud snapshot first, then the dumps. The snapshot carries the
    # creation time of its MANIFEST (file SNAPSHOT) and check_manifest compares the
    # two, so a crash between the two swaps cannot pair an old MANIFEST with a new
    # copy, or the reverse.
    rm -rf "${NCSNAP}.old" "${DUMPS}.old"
    if [ -d "$NCSNAP" ]; then mv "$NCSNAP" "${NCSNAP}.old"; fi
    if [ -d "$NCSNAP_NEW" ]; then mv "$NCSNAP_NEW" "$NCSNAP"; fi
    if [ -d "$DUMPS" ]; then mv "$DUMPS" "${DUMPS}.old"; fi
    mv "$NEW" "$DUMPS"
    rm -rf "${DUMPS}.old" "${NCSNAP}.old"
    log "Dumps complete: ${DUMPS}/MANIFEST"
}

# ====================================================================
# Phase 2: restic
# ====================================================================
# The restic phase is only allowed on top of a complete, fresh set of dumps whose
# files are the ones the dump phase wrote.
CM_NC_TREE=false
CM_COVERAGE_REDUCED=""

# verify_file <file> <bytes in the MANIFEST> <sha256 in the MANIFEST> <what it is>
# Existence and size always; the digest in container mode, where the dumps were
# written by another process (possibly hours ago) and arrive through a bind mount.
# The host run made them seconds ago and does not hash them a second time.
verify_file() {
    [ -f "$1" ] || err "The manifest lists $4, but $1 does not exist. The dump directory is incomplete or was changed after the dump. Nothing was backed up."
    vf_size="$(file_size "$1")" || err "Cannot read the size of $1. Nothing was backed up."
    [ "$vf_size" = "$2" ] || err "$4: $1 has ${vf_size} bytes, the manifest recorded $2. The file was truncated or changed after the dump. Nothing was backed up."
    if [ "$MODE" = "container" ]; then
        vf_sum="$(sha256_of "$1")" || err "Cannot compute the SHA-256 of $1. Nothing was backed up."
        [ "$vf_sum" = "$3" ] || err "$4: the SHA-256 of $1 is ${vf_sum}, the manifest recorded $3. The file was changed after the dump. Nothing was backed up."
    fi
}

check_manifest() {
    cm_file="${DUMPS}/MANIFEST"
    [ -f "$cm_file" ] || err "No dump manifest at ${cm_file}. The databases have not been dumped, and a file-level copy of running databases is not a backup. Run 'sh software/backup/backup.sh --dump-only' on the host (cron/systemd timer) before this job. Nothing was backed up."
    grep -qx 'status|complete' "$cm_file" || err "Dump manifest ${cm_file} is not marked complete. Nothing was backed up."
    cm_version="$(sed -n '1s/^smdc-dump-manifest|//p' "$cm_file")"
    case "$cm_version" in
        1) warn "Dump manifest ${cm_file} is in the previous format (no sizes or SHA-256 digests): only the existence and non-zero size of each dump can be checked. The next 'backup.sh --dump-only' on the host writes the current format." ;;
        2) ;;
        *) err "Dump manifest ${cm_file} has an unknown format ('${cm_version}'). Nothing was backed up." ;;
    esac
    cm_created="$(sed -n 's/^created_epoch|//p' "$cm_file" | head -n 1)"
    case "$cm_created" in
        '' | *[!0-9]*) err "Dump manifest ${cm_file} has no creation time. Nothing was backed up." ;;
    esac
    cm_age=$(($(date +%s) - cm_created))
    if [ "$cm_age" -gt "$MAX_AGE" ]; then
        err "The database dumps are ${cm_age} s old (limit BACKUP_DUMP_MAX_AGE_SECONDS=${MAX_AGE}). The host job 'backup.sh --dump-only' is not running. Nothing was backed up."
    fi

    CM_NC_TREE=false
    CM_COVERAGE_REDUCED=""
    cm_checked=0
    # shellcheck disable=SC2094  # the manifest is only read, here and by awk below
    while IFS='|' read -r cm_tag cm_a cm_b cm_c cm_d _cm_rest; do
        case "$cm_tag" in
            dump)
                [ "$cm_d" = "ok" ] || continue
                case "$cm_c" in
                    /* | *..*) err "Dump manifest ${cm_file}: unsafe path '${cm_c}' for ${cm_a}. Nothing was backed up." ;;
                esac
                case "$cm_b" in
                    sqlite | pgdump)
                        [ -s "${DUMPS}/${cm_c}" ] || err "The manifest lists the ${cm_a} dump as ok, but ${DUMPS}/${cm_c} is missing or empty. Nothing was backed up."
                        if [ "$cm_version" = "2" ]; then
                            awk -F'|' -v p="$cm_c" '$1 == "file" && $2 == p { found = 1 } END { exit !found }' "$cm_file" \
                                || err "Dump manifest ${cm_file} records no size and SHA-256 for ${cm_c}. Nothing was backed up."
                        fi
                        ;;
                    files)
                        [ -d "${DUMPS}/${cm_c}" ] || err "The manifest lists ${cm_a} as ok, but the directory ${DUMPS}/${cm_c} is missing. Nothing was backed up."
                        ;;
                    gitlab)
                        cm_root="$(volume_root gitlab_data)" || exit 1
                        [ -n "$cm_root" ] && [ -s "${cm_root}/backups/${cm_c}" ] \
                            || err "The manifest names GitLab archive ${cm_c}, but it is not in the gitlab_data volume (backups/) or is empty. Nothing was backed up."
                        ;;
                    tree)
                        [ "$cm_c" = "$NCSNAP_NAME" ] || err "Dump manifest ${cm_file}: unexpected snapshot directory '${cm_c}'. Nothing was backed up."
                        [ -d "${NCSNAP}/volume" ] || err "The manifest lists the Nextcloud file snapshot as ok, but ${NCSNAP}/volume is missing. Nothing was backed up."
                        cm_snap="$(head -n 1 "${NCSNAP}/SNAPSHOT" 2>/dev/null || true)"
                        [ "$cm_snap" = "$cm_created" ] || err "The Nextcloud file snapshot ${NCSNAP} (taken at '${cm_snap}') does not belong to this manifest (created_epoch ${cm_created}): database dump and files would come from different runs. Nothing was backed up."
                        CM_NC_TREE=true
                        ;;
                    *) err "Dump manifest ${cm_file}: unknown dump kind '${cm_b}' for ${cm_a}. Nothing was backed up." ;;
                esac
                cm_checked=$((cm_checked + 1))
                ;;
            file)
                case "$cm_a" in
                    /* | *..*) err "Dump manifest ${cm_file}: unsafe path '${cm_a}'. Nothing was backed up." ;;
                esac
                verify_file "${DUMPS}/${cm_a}" "$cm_b" "$cm_c" "dump file ${cm_a}"
                ;;
            extfile)
                case "$cm_b" in
                    /* | *..*) err "Dump manifest ${cm_file}: unsafe path '${cm_b}'. Nothing was backed up." ;;
                esac
                cm_root="$(volume_root "$cm_a")" || exit 1
                [ -n "$cm_root" ] || err "The manifest names ${cm_b} in volume ${cm_a}, but that volume is not available. Nothing was backed up."
                verify_file "${cm_root}/${cm_b}" "$cm_c" "$cm_d" "archive ${cm_b} in volume ${cm_a}"
                ;;
            tree)
                [ "$cm_a" = "$NCSNAP_NAME" ] || err "Dump manifest ${cm_file}: unexpected snapshot directory '${cm_a}'. Nothing was backed up."
                cm_count="$(find "${NCSNAP}/volume" -type f 2>/dev/null | wc -l | tr -d '[:space:]')"
                [ "$cm_count" = "$cm_b" ] || err "The Nextcloud file snapshot ${NCSNAP}/volume holds ${cm_count} file(s), the manifest recorded ${cm_b}. It is incomplete or was changed after the dump. Nothing was backed up."
                ;;
            coverage)
                if [ "$cm_a" = "reduced" ]; then CM_COVERAGE_REDUCED="${cm_b:-unnamed}"; fi
                ;;
        esac
    done <"$cm_file"

    info "dumps: $(sed -n 's/^created|//p' "$cm_file" | head -n 1), ${cm_age} s old, ${cm_checked} dump(s) present and matching the manifest"
    awk -F'|' '$1 == "dump" { printf "         %-8s %-28s %s\n", $5, $2, $6 }' "$cm_file"
}

files_phase() {
    log "Phase 2/2: restic backup (dumps + configuration + static volume files)"
    check_manifest

    TAG="sovereign-automated-$(date +%Y%m%d_%H%M%S)"
    set -- backup --tag "sovereign-snapshot" --tag "$TAG" --tag "mode-${MODE}" \
        --exclude "__pycache__"

    # Never walk a live database file.
    while IFS='|' read -r key rel; do
        ex_root="$(volume_root "$key")" || exit 1
        if [ -n "$ex_root" ]; then
            set -- "$@" --exclude "${ex_root}/${rel}"
        fi
    done <<EOF
$(live_excludes)
EOF

    # 1. dumps (and the Nextcloud snapshot)  2. deployment configuration (compose files, overlays, provisioning,
    # headscale config, acquis.d and .env - secrets included: recovery needs them and
    # the repository is encrypted)  3. static files of the `files` volumes.
    set -- "$@" "$DUMPS"
    if [ "$CM_NC_TREE" = "true" ]; then
        set -- "$@" "$NCSNAP"
    fi
    [ -d "$CONFIG_DIR" ] || err "Configuration directory ${CONFIG_DIR} not found. Nothing was backed up."
    set -- "$@" "$CONFIG_DIR"
    if [ ! -f "${CONFIG_DIR}/.env" ]; then
        warn "${CONFIG_DIR}/.env not found: the snapshot will not contain the deployment's secrets."
    fi

    while IFS='|' read -r key _name class paths _reason; do
        [ "$class" = "files" ] || continue
        if [ "$key" = "nextcloud_data" ] && [ "$CM_NC_TREE" = "true" ]; then
            # The copy taken in the maintenance window replaces the live volume:
            # walking the live files now would capture a later state than the database.
            info "volume nextcloud_data: not walked live; the snapshot ${NCSNAP} from the maintenance window is backed up instead"
            continue
        fi
        fp_root="$(volume_root "$key")" || exit 1
        if [ -z "$fp_root" ]; then
            if [ "$MODE" = "container" ]; then
                err "Volume ${key} is listed as 'files' in volumes.conf but is not mounted at /data/${key}. Fix docker-compose.backup.yml (scripts/validate_compose.sh checks this). Nothing was backed up."
            fi
            info "volume ${key}: does not exist (overlay not deployed); skipped"
            continue
        fi
        for fp_path in $paths; do
            if [ "$fp_path" = "." ]; then
                set -- "$@" "$fp_root"
            elif [ -e "${fp_root}/${fp_path}" ]; then
                set -- "$@" "${fp_root}/${fp_path}"
            fi
        done
    done <<EOF
$(conf_rows)
EOF

    log "Checking Restic repository: ${RESTIC_REPOSITORY}"
    if ! restic snapshots >/dev/null 2>&1; then
        log "Initializing new Restic encrypted repository..."
        mkdir -p "${RESTIC_REPOSITORY}" 2>/dev/null || true
        restic init
    fi

    # A non-zero exit (including 3: some files could not be read) fails the run,
    # and with it everything below: retention never runs after a failed backup.
    restic "$@"

    # -- Retention Policy (7 daily, 4 weekly, 12 monthly, 1 yearly) ----
    if [ -n "$CM_COVERAGE_REDUCED" ]; then
        # The new snapshot covers less than the ones before it. Pruning now could
        # delete the last snapshots that still hold the missing services.
        warn "Retention NOT applied: this snapshot covers less than the previous one (${CM_COVERAGE_REDUCED}). Older snapshots are kept; pruning resumes with the next run whose coverage is not reduced."
    else
        log "Applying snapshot retention policy..."
        restic forget \
            --tag "sovereign-snapshot" \
            --keep-daily 7 \
            --keep-weekly 4 \
            --keep-monthly 12 \
            --keep-yearly 1 \
            --prune
    fi

    log "✅ Backup completed successfully. Current snapshot summary:"
    restic snapshots --tag "sovereign-snapshot" --compact | tail -n 10
}

if [ "$MODE" = "host" ]; then
    dump_phase
fi
if [ "$DUMP_ONLY" = "true" ]; then
    exit 0
fi
files_phase
