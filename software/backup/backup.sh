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
#            result. Any failing dump aborts the run before restic is called.
#   2. FILES restic backs up the dump directory, the deployment configuration
#            (software/, including .env) and the static files of the volumes
#            listed as `files` in volumes.conf. Live database files are excluded.
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
#                (SQLite database; here via the online backup API instead of
#                maintenance mode + .dump, so Nextcloud stays available)
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
    COMPOSE_PROJECT_NAME GITLAB_BACKUP_ARGS; do
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
volume_root() {
    if [ "$MODE" = "container" ]; then
        if [ -d "${CROOT}/data/$1" ]; then printf '%s' "${CROOT}/data/$1"; fi
        return 0
    fi
    vr_name="$(conf_name "$1")"
    [ -n "$vr_name" ] || err "Volume key '$1' is not listed in ${VOLUMES_CONF}."
    if [ "$vr_name" = "-" ]; then vr_name="$(project_name)_$1"; fi
    vr_mp="$(docker volume inspect --format '{{ .Mountpoint }}' "$vr_name" 2>/dev/null || true)"
    [ -n "$vr_mp" ] || return 0
    [ -d "$vr_mp" ] || err "Volume ${vr_name} exists but its mountpoint ${vr_mp} is not readable here. Run this script as root on the Docker host (not against Docker Desktop, rootless Docker or a remote daemon)."
    printf '%s' "$vr_mp"
}

container_running() {
    [ "$(docker inspect --format '{{ .State.Running }}' "$1" 2>/dev/null || true)" = "true" ]
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
manifest_add() { # manifest_add <name> <kind> <path relative to smdc-dumps> <ok|skipped> <detail>
    printf 'dump|%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" >>"${NEW}/MANIFEST.partial"
    if [ "$4" = "ok" ]; then
        info "ok       $1  ($2)  $5"
    else
        info "skipped  $1  ($2)  $5"
    fi
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
    ds_root="$(volume_root "$2")"
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
}

nc_occ() {
    docker exec -u www-data "$NEXTCLOUD_CONTAINER" php "${NEXTCLOUD_ROOT_IN_CONTAINER}/occ" "$@"
}

nc_setting() { # nc_setting <config.php key> -> value; aborts the run when it cannot be read
    ns_value="$(nc_occ config:system:get "$1")" \
        || err "Dump of nextcloud failed: cannot read '$1' through occ. Nothing was backed up."
    ns_value="$(printf '%s' "$ns_value" | tr -d '\r' | head -n 1)"
    [ -n "$ns_value" ] || err "Dump of nextcloud failed: occ returned an empty '$1'. Nothing was backed up."
    printf '%s' "$ns_value"
}

dump_nextcloud() {
    dn_root="$(volume_root nextcloud_data)"
    if [ -z "$dn_root" ]; then
        manifest_add nextcloud sqlite "" skipped "volume nextcloud_data does not exist (service not deployed)"
        return 0
    fi
    if [ ! -f "${dn_root}/config/config.php" ]; then
        manifest_add nextcloud sqlite "" skipped "Nextcloud is not installed yet (no config/config.php)"
        return 0
    fi
    dn_db=""
    if container_running "$NEXTCLOUD_CONTAINER"; then
        if nc_occ status 2>/dev/null | grep -q 'installed: false'; then
            manifest_add nextcloud sqlite "" skipped "Nextcloud is not installed yet (occ status)"
            return 0
        fi
        dn_type="$(nc_setting dbtype)"
        case "$dn_type" in
            sqlite | sqlite3) ;;
            *) err "Dump of nextcloud failed: it uses the '${dn_type}' database, which this stack does not deploy and this script cannot dump. Nothing was backed up." ;;
        esac
        dn_name="$(nc_setting dbname)"
        dn_dir="$(nc_setting datadirectory)"
        case "$dn_dir" in
            "${NEXTCLOUD_ROOT_IN_CONTAINER}"/*) dn_db="${dn_root}/${dn_dir#"${NEXTCLOUD_ROOT_IN_CONTAINER}"/}/${dn_name}.db" ;;
            *) err "Dump of nextcloud failed: its data directory ${dn_dir} is outside the nextcloud_data volume. Nothing was backed up." ;;
        esac
    else
        # Stopped: occ is unavailable; a stopped SQLite database is consistent on disk.
        dn_db="${dn_root}/data/owncloud.db"
    fi
    [ -f "$dn_db" ] || err "Dump of nextcloud failed: database file ${dn_db} not found. Nothing was backed up."
    sqlite_backup nextcloud "$dn_db" "${NEW}/sqlite/nextcloud.sqlite"
    manifest_add nextcloud sqlite "sqlite/nextcloud.sqlite" ok "from volume nextcloud_data:${dn_db#"${dn_root}"/}"
}

dump_openproject() {
    do_root="$(volume_root openproject_pgdata)"
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
}

dump_gitlab() {
    dg_root="$(volume_root gitlab_data)"
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
    manifest_add gitlab gitlab "$dg_new" ok "archive in volume gitlab_data:backups; gitlab.rb and gitlab-secrets.json come from volume gitlab_config"
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
        else
            mkdir -p "$(dirname "${NEW}/smdc-state/${st_file}")"
            cp -p "${st_dir}/${st_file}" "${NEW}/smdc-state/${st_file}" \
                || err "Dump of smdc-state failed: cannot copy ${st_dir}/${st_file}. Nothing was backed up."
            st_copied=$((st_copied + 1))
        fi
    done <"${NEW}/.state-files"
    rm -f "${NEW}/.state-files"
    manifest_add smdc-state files "smdc-state" ok "${st_copied} plain file(s) copied from ${st_dir} (wallets, keys); SQLite files are listed separately"
}

dump_phase() {
    log "Phase 1/2: application-consistent dumps into ${DUMPS}"
    mkdir -p "$DUMP_PARENT"
    chmod 700 "$DUMP_PARENT"
    rm -rf "$NEW"
    mkdir -p "$NEW"
    {
        printf 'smdc-dump-manifest|1\n'
        printf 'created_epoch|%s\n' "$(date +%s)"
        printf 'created|%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'project|%s\n' "$(project_name)"
    } >"${NEW}/MANIFEST.partial"

    while IFS='|' read -r label key rel container; do
        dump_sqlite_service "$label" "$key" "$rel" "$container"
    done <<EOF
$(sqlite_services)
EOF
    dump_nextcloud
    dump_openproject
    dump_gitlab
    dump_state_dir

    # Only a run in which every step succeeded gets a MANIFEST marked complete.
    printf 'status|complete\n' >>"${NEW}/MANIFEST.partial"
    mv "${NEW}/MANIFEST.partial" "${NEW}/MANIFEST"
    rm -rf "${DUMPS}.old"
    if [ -d "$DUMPS" ]; then mv "$DUMPS" "${DUMPS}.old"; fi
    mv "$NEW" "$DUMPS"
    rm -rf "${DUMPS}.old"
    log "Dumps complete: ${DUMPS}/MANIFEST"
}

# ====================================================================
# Phase 2: restic
# ====================================================================
# The restic phase is only allowed on top of a complete, fresh set of dumps.
check_manifest() {
    cm_file="${DUMPS}/MANIFEST"
    [ -f "$cm_file" ] || err "No dump manifest at ${cm_file}. The databases have not been dumped, and a file-level copy of running databases is not a backup. Run 'sh software/backup/backup.sh --dump-only' on the host (cron/systemd timer) before this job. Nothing was backed up."
    grep -qx 'status|complete' "$cm_file" || err "Dump manifest ${cm_file} is not marked complete. Nothing was backed up."
    cm_created="$(sed -n 's/^created_epoch|//p' "$cm_file" | head -n 1)"
    case "$cm_created" in
        '' | *[!0-9]*) err "Dump manifest ${cm_file} has no creation time. Nothing was backed up." ;;
    esac
    cm_age=$(($(date +%s) - cm_created))
    if [ "$cm_age" -gt "$MAX_AGE" ]; then
        err "The database dumps are ${cm_age} s old (limit BACKUP_DUMP_MAX_AGE_SECONDS=${MAX_AGE}). The host job 'backup.sh --dump-only' is not running. Nothing was backed up."
    fi
    cm_tar="$(awk -F'|' '$1 == "dump" && $3 == "gitlab" && $5 == "ok" { print $4; exit }' "$cm_file")"
    if [ -n "$cm_tar" ]; then
        cm_root="$(volume_root gitlab_data)"
        [ -n "$cm_root" ] && [ -f "${cm_root}/backups/${cm_tar}" ] \
            || err "The manifest names GitLab archive ${cm_tar}, but it is not in the gitlab_data volume (backups/). Nothing was backed up."
    fi
    info "dumps: $(sed -n 's/^created|//p' "$cm_file" | head -n 1), ${cm_age} s old"
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
        ex_root="$(volume_root "$key")"
        if [ -n "$ex_root" ]; then
            set -- "$@" --exclude "${ex_root}/${rel}"
        fi
    done <<EOF
$(live_excludes)
EOF

    # 1. dumps  2. deployment configuration (compose files, overlays, provisioning,
    # headscale config, acquis.d and .env - secrets included: recovery needs them and
    # the repository is encrypted)  3. static files of the `files` volumes.
    set -- "$@" "$DUMPS"
    [ -d "$CONFIG_DIR" ] || err "Configuration directory ${CONFIG_DIR} not found. Nothing was backed up."
    set -- "$@" "$CONFIG_DIR"
    if [ ! -f "${CONFIG_DIR}/.env" ]; then
        warn "${CONFIG_DIR}/.env not found: the snapshot will not contain the deployment's secrets."
    fi

    while IFS='|' read -r key _name class paths _reason; do
        [ "$class" = "files" ] || continue
        fp_root="$(volume_root "$key")"
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

    # A non-zero exit (including 3: some files could not be read) fails the run.
    restic "$@"

    # -- Retention Policy (7 daily, 4 weekly, 12 monthly, 1 yearly) ----
    log "Applying snapshot retention policy..."
    restic forget \
        --tag "sovereign-snapshot" \
        --keep-daily 7 \
        --keep-weekly 4 \
        --keep-monthly 12 \
        --keep-yearly 1 \
        --prune

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
