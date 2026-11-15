#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Disaster Recovery & Restore Engine
#
# A snapshot holds database DUMPS (smdc-dumps/, with a MANIFEST that records the
# size and SHA-256 of every dump file), the copy of the Nextcloud volume taken in
# its maintenance window (smdc-nextcloud-files/), the deployment configuration
# (software/, .env included) and the static files of the other volumes - never
# raw copies of live database directories. Restoring a
# service therefore means: put the static files back, then LOAD its dump with
# the database's own tool. `plan` prints those steps for a restored snapshot.
#
# Where the two directories sit inside a snapshot: backup.sh writes every dump
# run into its own generation, so they are found under
# .../generations/<created_epoch>/ (one generation per snapshot; a snapshot of a
# run that completed carries the tag dumps-<created_epoch>-<MANIFEST digest>). Snapshots taken by an
# earlier backup.sh hold them directly under the dump directory. Both are found
# by name.
#
# WHICH SNAPSHOT. `restic backup` writes a snapshot even when it could not read
# every file (exit 3), so "the latest snapshot" is not necessarily a usable one.
# backup.sh classifies every snapshot after the run (see its header):
#   complete      tagged sovereign-complete - or, for a snapshot of an earlier
#                 backup.sh, tagged dumps-<epoch>-<digest>, which was only ever
#                 added after a clean run
#   incomplete    tagged sovereign-incomplete (restic exited non-zero, or the dump
#                 MANIFEST changed while it was read)
#   unclassified  neither tag: an interrupted run, or a snapshot of a backup.sh that
#                 did not tag yet
# `restore-latest` and `--verify` without an id use the NEWEST COMPLETE snapshot,
# name it, and warn about newer snapshots that are not complete. An incomplete or
# unclassified snapshot is only restored when it is named: `restore-id <id>` and
# `--verify <id>` do that, with a warning. `list` shows the state of each.
#
# WHICH REPOSITORY. RESTIC_REPOSITORY from the environment of this command is used
# as it is. Otherwise the value in software/.env applies (default
# /var/backups/sovereign) - and that path means two different places: on the host
# it is a host directory (written by a host-mode `sh backup.sh`), inside the
# sovereign_backup container it is the named volume <project>_backup_storage. When
# the path is that default, this script therefore looks (read-only: `docker inspect`,
# `docker volume inspect`) for the volume and
#   - uses the volume's mountpoint when only the volume holds a repository,
#   - uses the host directory when only that holds one,
#   - stops when both do: set RESTIC_REPOSITORY in the environment to choose.
# See "Verifying and restoring" in backup/README.md, also for running this script
# inside a throwaway container when the volume is not on this filesystem.
#
# This script never changes anything through Docker and never writes into a live
# volume: it extracts into a directory you name (or a temporary one for --verify).
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
    # The last assignment wins, in any spelling Compose accepts for the key:
    # `KEY=`, `KEY =`, `export KEY=`, `KEY: `.
    efv="$(sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*[=:]//p" "$ENV_FILE" | tail -n 1 | tr -d '\r')"
    # Decoded as Docker Compose decodes its .env file, so that this script and the backup
    # container (which gets the value through Compose) use the same password:
    # blanks after `=` are dropped; a single-quoted value is literal; in unquoted and
    # double-quoted values `$$` is one `$`; a comment starts at a space followed by `#`
    # (a tab before `#` is part of the value); after a closing quote only such a comment
    # may follow. What Compose would expand or unescape (a `$NAME` / `${NAME}` reference, a
    # backslash inside quotes) is refused here instead of being guessed.
    efv="$(printf '%s' "$efv" | sed -e 's/^[[:space:]]*//')"
    efv_quote=""
    case "$efv" in
        \'*) efv_quote="'" ;;
        \"*) efv_quote='"' ;;
    esac
    if [ -n "$efv_quote" ]; then
        efv="${efv#?}"
        case "$efv" in
            *"$efv_quote"*) ;;
            *) echo "[ERROR]   $1 in ${ENV_FILE} has an opening quote without a closing one." >&2; return 2 ;;
        esac
        efv_after="${efv#*"$efv_quote"}"
        efv="${efv%%"$efv_quote"*}"
        case "$efv" in
            *\\*) echo "[ERROR]   $1 in ${ENV_FILE} is quoted and contains a backslash, which Docker Compose may treat as an escape; use a value without backslashes." >&2; return 2 ;;
        esac
        efv_after="$(printf '%s' "$efv_after" | sed -e 's/^  *#.*$//' -e 's/[[:space:]]*$//')"
        if [ -n "$efv_after" ]; then
            echo "[ERROR]   $1 in ${ENV_FILE} has text after its closing quote; only a comment (space, then #) may follow." >&2
            return 2
        fi
        if [ "$efv_quote" = "'" ]; then
            printf '%s' "$efv"
            return 0
        fi
    else
        efv="$(printf '%s' "$efv" | sed -e 's/ #.*$//' -e 's/[[:space:]]*$//')"
    fi
    efv_rest="$(printf '%s' "$efv" | sed 's/\$\$//g')"
    case "$efv_rest" in
        *\$[A-Za-z_\{]*)
            echo "[ERROR]   $1 in ${ENV_FILE} contains a \$NAME reference, which Docker Compose would expand; write a literal dollar sign as \$\$, or put the value in single quotes." >&2
            return 2 ;;
    esac
    printf '%s' "$efv" | sed 's/\$\$/$/g'
}
# A repository named in the environment of this command is used as it is (see the header).
REPOSITORY_FROM_ENVIRONMENT=false
if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then REPOSITORY_FROM_ENVIRONMENT=true; fi
for key in RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_S3_KEY RESTIC_S3_SECRET; do
    if [[ -z "${!key+set}" ]]; then
        value="$(env_file_value "$key")" || exit 2
        if [[ -n "$value" ]]; then export "${key}=${value}"; fi
    fi
done

usage() {
    cat <<EOF
Sovereign Disaster Recovery & Restore CLI

Usage:
  ./restore.sh list                               List the snapshots and whether each is complete
  ./restore.sh restore-latest <target>            Extract the newest COMPLETE snapshot into <target>
  ./restore.sh restore-id <snapshot_id> <target>  Extract a specific snapshot into <target> (also an incomplete
                                                  one, with a warning)
  ./restore.sh plan <target>                      Print the per-service restore steps for an extracted snapshot
  ./restore.sh --verify [snapshot_id]             Restore the dumps of the newest complete (or the given) snapshot
                                                  into a temporary directory and check that each one is the file the
                                                  MANIFEST recorded (size, SHA-256) and loads: SQLite
                                                  integrity_check, pg_restore --list, GitLab archive listing.
                                                  Live services are not touched. Non-zero on any failure.
  ./restore.sh check                              restic repository check (archive integrity only - it says
                                                  nothing about whether a database can be restored)

<target> must be an empty or new directory, never a live volume.

A snapshot is complete when backup.sh tagged it after a clean run (restic exit 0, dump MANIFEST unchanged).
Repository: RESTIC_REPOSITORY from the environment is used as it is; the default /var/backups/sovereign is
looked for on the host and in the backup container's volume (see the header of this script).

Examples:
  ./restore.sh --verify
  ./restore.sh restore-latest /mnt/restore_target && ./restore.sh plan /mnt/restore_target
EOF
    exit 1
}

DEFAULT_REPOSITORY="/var/backups/sovereign"
BACKUP_CONTAINER="sovereign_backup"
SNAPSHOT_TAG="sovereign-snapshot"
COMPLETE_TAG="sovereign-complete"
INCOMPLETE_TAG="sovereign-incomplete"

# repository_state <dir>: yes | no | missing | unreadable. A local restic repository
# has a `config` file and a `keys` directory at its root.
repository_state() {
    if [[ ! -d "$1" ]]; then echo missing; return 0; fi
    if [[ ! -r "$1" || ! -x "$1" ]]; then echo unreadable; return 0; fi
    if [[ -f "$1/config" && -d "$1/keys" ]]; then echo yes; else echo no; fi
}

# Name of the volume the backup container keeps its repository in: what the
# container has mounted there if it exists, else <project>_backup_storage.
backup_volume_name() {
    local name project
    name="$(docker inspect --format "{{ range .Mounts }}{{ if and (eq .Type \"volume\") (eq .Destination \"${DEFAULT_REPOSITORY}\") }}{{ .Name }}{{ end }}{{ end }}" "$BACKUP_CONTAINER" 2>/dev/null || true)"
    if [[ -z "$name" ]]; then
        project="${COMPOSE_PROJECT_NAME:-$(env_file_value COMPOSE_PROJECT_NAME)}"
        if [[ -z "$project" ]]; then
            project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' sovereign_traefik 2>/dev/null || true)"
        fi
        name="${project:-software}_backup_storage"
    fi
    printf '%s' "$name"
}

# Decides what ${DEFAULT_REPOSITORY} means on this host (see WHICH REPOSITORY in the
# header). Never picks silently between two repositories.
resolve_repository() {
    export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$DEFAULT_REPOSITORY}"
    if [[ "$REPOSITORY_FROM_ENVIRONMENT" == "true" || "${RESTIC_REPOSITORY%/}" != "$DEFAULT_REPOSITORY" ]]; then
        return 0
    fi
    local volume="" mountpoint="" host_state volume_state hint
    host_state="$(repository_state "$DEFAULT_REPOSITORY")"
    if command -v docker >/dev/null 2>&1; then
        volume="$(backup_volume_name)"
        mountpoint="$(docker volume inspect --format '{{ .Mountpoint }}' "$volume" 2>/dev/null || true)"
    fi
    # No Docker, no reachable daemon or no backup volume: only the host directory can be meant.
    if [[ -z "$mountpoint" ]]; then
        case "$host_state" in
            yes) return 0 ;;
            unreadable) err "Cannot read ${DEFAULT_REPOSITORY} as $(id -un): run this script as root (sudo)." ;;
        esac
        err "No restic repository at ${DEFAULT_REPOSITORY} on this host, and Docker reports no backup volume${volume:+ named ${volume}} (docker missing, daemon not reachable, or the volume belongs to another Compose project). If the backups are made by the ${BACKUP_CONTAINER} container, the repository is its volume <project>_backup_storage: find its mountpoint with 'docker volume ls' / 'docker volume inspect' and run  sudo RESTIC_REPOSITORY=<mountpoint> bash $0 ...  ; otherwise set RESTIC_REPOSITORY to where the repository is."
    fi
    volume_state="$(repository_state "$mountpoint")"
    hint="Name the repository in the environment of this command, e.g.  sudo RESTIC_REPOSITORY=${mountpoint} bash $0 ...   (the volume)  or  sudo RESTIC_REPOSITORY=${DEFAULT_REPOSITORY} bash $0 ...   (the host directory)."
    case "${host_state}/${volume_state}" in
        yes/yes)
            err "Two restic repositories answer to RESTIC_REPOSITORY=${DEFAULT_REPOSITORY}: the host directory ${DEFAULT_REPOSITORY} (host-mode 'sh backup.sh') and the volume ${volume} of the backup container (mountpoint ${mountpoint}). This script does not guess. ${hint}"
            ;;
        yes/no)
            info "repository: host directory ${DEFAULT_REPOSITORY} (the volume ${volume} holds no repository)"
            ;;
        no/yes | missing/yes)
            export RESTIC_REPOSITORY="$mountpoint"
            warn "RESTIC_REPOSITORY=${DEFAULT_REPOSITORY} holds no restic repository on this host; using the backup container's volume ${volume} at ${mountpoint} (inside the container that volume is mounted at ${DEFAULT_REPOSITORY})."
            ;;
        unreadable/* | */unreadable)
            err "Cannot read ${DEFAULT_REPOSITORY} and/or the volume ${volume} at ${mountpoint} as $(id -un): run this script as root (sudo), or name the repository. ${hint}"
            ;;
        yes/missing)
            err "The host directory ${DEFAULT_REPOSITORY} holds a restic repository, but the backup container keeps its own in the volume ${volume}, whose mountpoint ${mountpoint} cannot be seen from here (not root, or Docker runs in a VM or on another host), so this script cannot tell which one you mean. ${hint} To read the volume where it is not on this filesystem, run this script in a throwaway container: see 'Verifying and restoring' in backup/README.md."
            ;;
        */missing)
            err "${DEFAULT_REPOSITORY} holds no restic repository on this host, and the mountpoint ${mountpoint} of the backup container's volume ${volume} cannot be seen from here (not root, or Docker runs in a VM or on another host). Run this script as root, or in a throwaway container with the volume mounted: see 'Verifying and restoring' in backup/README.md."
            ;;
        *)
            err "Neither ${DEFAULT_REPOSITORY} on this host nor the backup container's volume ${volume} (${mountpoint}) holds a restic repository: no backup has completed yet, or the repository is elsewhere (set RESTIC_REPOSITORY)."
            ;;
    esac
}

need_restic() {
    resolve_repository
    # No default: the password must be the one the repository was created with.
    [[ -n "${RESTIC_PASSWORD:-}" ]] || err "RESTIC_PASSWORD is not set. Set it in software/.env or the environment."
    export RESTIC_PASSWORD
    if [[ -z "${AWS_ACCESS_KEY_ID:-}" && -n "${RESTIC_S3_KEY:-}" ]]; then export AWS_ACCESS_KEY_ID="$RESTIC_S3_KEY"; fi
    if [[ -z "${AWS_SECRET_ACCESS_KEY:-}" && -n "${RESTIC_S3_SECRET:-}" ]]; then export AWS_SECRET_ACCESS_KEY="$RESTIC_S3_SECRET"; fi
    command -v restic >/dev/null 2>&1 || err "restic CLI not found. Install it with: apt-get install restic"
}

# snapshot_rows: reads `restic snapshots --json` on stdin and prints one line per
# snapshot, in restic's order (oldest first):  <short id>|<time>|<tag>,<tag>,...
# The same function as in backup.sh (no JSON tool is assumed; text inside JSON
# strings cannot produce `"tags":[`, `"time":"` or `"short_id":"` because a quote
# inside a string is written `\"`).
snapshot_rows() {
    tr '}' '\n' | awk '
        {
            if (time == "" && match($0, /"time":[ ]*"[^"]*"/)) {
                time = substr($0, RSTART, RLENGTH); sub(/^"time":[ ]*"/, "", time); sub(/"$/, "", time)
            }
            if (match($0, /,"tags":[ ]*\[[^]]*\]/)) {
                tags = substr($0, RSTART, RLENGTH); sub(/^,"tags":[ ]*\[/, "", tags); sub(/\]$/, "", tags)
                gsub(/"/, "", tags); gsub(/[ ]*,[ ]*/, ",", tags)
            }
            if (match($0, /"short_id":[ ]*"[0-9a-f]+"/)) {
                id = substr($0, RSTART, RLENGTH); sub(/^"short_id":[ ]*"/, "", id); sub(/"$/, "", id)
                print id "|" time "|" tags
                time = ""; tags = ""
            }
        }'
}

# SNAPSHOT_ROWS: one line per snapshot tagged sovereign-snapshot, oldest first:
#   <short id>|<time>|<tags>|<complete|incomplete|unclassified>
SNAPSHOT_ROWS=""
SNAPSHOTS_LOADED=false
load_snapshots() {
    local json
    if [[ "$SNAPSHOTS_LOADED" == "true" ]]; then return 0; fi
    json="$(restic snapshots --tag "$SNAPSHOT_TAG" --json)" || err "Cannot list the snapshots of ${RESTIC_REPOSITORY} (see restic's message above)."
    SNAPSHOT_ROWS="$(snapshot_rows <<<"$json" | awk -F'|' -v c="$COMPLETE_TAG" -v i="$INCOMPLETE_TAG" '
        {
            tags = "," $3 ","
            state = "unclassified"
            if (index(tags, "," i ",") > 0) state = "incomplete"
            else if (index(tags, "," c ",") > 0 || index(tags, ",dumps-") > 0) state = "complete"
            print $1 "|" $2 "|" $3 "|" state
        }')"
    SNAPSHOTS_LOADED=true
}

print_snapshot_rows() { # stdin: rows of SNAPSHOT_ROWS
    awk -F'|' '{ printf "          %-10s %-36s %-13s %s\n", $1, $2, $4, $3 }'
}

# Sets SELECTED to the id of the newest complete snapshot; says which one it is and
# which newer snapshots were passed over. Stops when there is none.
SELECTED=""
select_latest_complete() {
    local newer total
    load_snapshots
    SELECTED="$(awk -F'|' '$4 == "complete" { id = $1 } END { print id }' <<<"$SNAPSHOT_ROWS")"
    if [[ -z "$SELECTED" ]]; then
        total="$(grep -c . <<<"$SNAPSHOT_ROWS" || true)"
        if [[ "$total" -eq 0 ]]; then
            err "${RESTIC_REPOSITORY} holds no snapshot tagged ${SNAPSHOT_TAG}."
        fi
        info "snapshots in ${RESTIC_REPOSITORY} (oldest first):"
        print_snapshot_rows <<<"$SNAPSHOT_ROWS"
        err "None of the ${total} snapshot(s) is marked complete, so there is no 'latest' this script will pick. Snapshots written by a backup.sh that did not tag yet carry no mark: choose one with './restore.sh list', check it with './restore.sh --verify <id>' and restore it with './restore.sh restore-id <id> <target>'."
    fi
    newer="$(awk -F'|' -v chosen="$SELECTED" 'seen { print } $1 == chosen { seen = 1 }' <<<"$SNAPSHOT_ROWS")"
    if [[ -n "$newer" ]]; then
        warn "$(grep -c . <<<"$newer") newer snapshot(s) are NOT complete (a backup run failed or was interrupted) and are not used:"
        print_snapshot_rows <<<"$newer"
        info "Fix the backup (docker logs ${BACKUP_CONTAINER} / the host job's output). To restore one of them anyway: ./restore.sh restore-id <id> <target>"
    fi
    info "selected: snapshot ${SELECTED} ($(awk -F'|' -v chosen="$SELECTED" '$1 == chosen { print $2 }' <<<"$SNAPSHOT_ROWS")), the newest one marked complete"
}

# warn_unless_complete <snapshot id>: a named snapshot is used as asked, but not quietly
# when backup.sh did not mark it complete.
warn_unless_complete() {
    local row state
    load_snapshots
    row="$(awk -F'|' -v id="$1" 'index(id, $1) == 1 || index($1, id) == 1 { print; exit }' <<<"$SNAPSHOT_ROWS")"
    if [[ -z "$row" ]]; then
        warn "Snapshot ${1} is not among the snapshots tagged ${SNAPSHOT_TAG}: this script cannot tell whether it is a complete backup."
        return 0
    fi
    state="${row##*|}"
    case "$state" in
        complete) ;;
        incomplete)
            warn "SNAPSHOT ${1} IS INCOMPLETE: the backup run that wrote it failed (restic could not read every file, or the dump MANIFEST changed while it was read). Files are missing from it or do not belong together. Use it only for what the complete snapshots do not have."
            ;;
        *)
            warn "SNAPSHOT ${1} IS NOT MARKED COMPLETE: it was written by an interrupted run or by a backup.sh that did not tag its snapshots yet. Check it with './restore.sh --verify ${1}' before relying on it."
            ;;
    esac
}

# restic_restore <snapshot id> <target> [restic restore options...]
restic_restore() {
    local snapshot="$1" target="$2"
    shift 2
    restic restore "$snapshot" --target "$target" "$@"
}

prepare_target() {
    local target="$1"
    [[ -n "$target" ]] || { echo "Error: Missing target directory."; usage; }
    mkdir -p "$target"
    if [[ -n "$(ls -A "$target")" ]]; then
        err "Target ${target} is not empty. Restore into a new directory, never over live data."
    fi
}

# This script runs under `set -o pipefail`, so no pipeline may end in a reader
# that stops early (`grep -q`, `head`): the writer is then killed by SIGPIPE and
# the pipeline fails although the reader succeeded. Readers here take a file or
# a here-string, or read their input to the end (`sed -n 1p`, `grep -c`, awk END).

# manifest_value <key> <manifest>: value of the first `<key>|<value>` record.
manifest_value() {
    sed -n "/^$1|/{s/^$1|//p;q;}" "$2"
}

# Generation (<created_epoch>) an smdc-dumps directory lies in, or empty for a
# snapshot of the earlier fixed-name layout.
dump_generation() {
    local parent grand
    parent="$(dirname "$1")"
    grand="$(dirname "$parent")"
    if [[ "$(basename "$grand")" == "generations" && "$(basename "$parent")" =~ ^[0-9]+$ ]]; then
        basename "$parent"
    fi
}

# check_generation <dumps dir> <manifest>: prints the layout; non-zero when the
# generation directory and the MANIFEST inside it are not from the same dump run.
check_generation() {
    local generation created
    generation="$(dump_generation "$1")"
    created="$(manifest_value created_epoch "$2")"
    if [[ -z "$generation" ]]; then
        info "dump layout: fixed-name directory (snapshot taken before dump generations were introduced)"
        return 0
    fi
    if [[ "$generation" != "$created" ]]; then
        info "dump generation ${generation}, but its MANIFEST says created_epoch ${created:-<none>}"
        return 1
    fi
    info "dump generation: ${generation} (matches the MANIFEST's created_epoch)"
}

# Path of the one smdc-dumps directory inside an extracted snapshot.
find_dumps() {
    local target="$1" found count
    found="$(find "$target" -type f -name MANIFEST -path '*/smdc-dumps/MANIFEST' 2>/dev/null || true)"
    count="$(grep -c . <<<"$found" || true)"
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
    entries="$(grep -c 'TABLE DATA' <<<"$toc" || true)"
    [[ "$entries" -gt 0 ]] || { info "    archive lists no TABLE DATA entries"; return 1; }
    info "    pg_restore --list ok, ${entries} table data entries"
}

verify_gitlab() {
    local file="$1" listing
    [[ -s "$file" ]] || { info "    archive missing or empty"; return 1; }
    listing="$(tar -tf "$file" 2>&1)" || { info "    tar: cannot list the archive"; return 1; }
    # Here-strings, not `printf ... | grep -q`: a GitLab archive lists thousands of
    # members, and grep -q leaving at its first match would SIGPIPE the writer and
    # (pipefail) turn a found member into "not in the archive".
    grep -Eq '(^|/)backup_information\.yml$' <<<"$listing" || { info "    no backup_information.yml in the archive"; return 1; }
    local member
    member="$(awk '/(^|\/)db\/database\.sql\.gz$/ { print; exit }' <<<"$listing")"
    [[ -n "$member" ]] || { info "    no db/database.sql.gz in the archive"; return 1; }
    tar -xOf "$file" "$member" | gzip -t || { info "    db/database.sql.gz is not a valid gzip stream"; return 1; }
    info "    archive lists, database dump is a valid gzip stream"
}

sha256_of() {
    local out
    if command -v sha256sum >/dev/null 2>&1; then
        out="$(sha256sum "$1")" || return 1
    elif command -v shasum >/dev/null 2>&1; then
        out="$(shasum -a 256 "$1")" || return 1
    else
        out="$(openssl dgst -sha256 -r "$1")" || return 1
    fi
    printf '%s' "${out%%[!0-9a-fA-F]*}"
}

# verify_recorded <file> <bytes> <sha256>: the restored file is the one the dump phase wrote.
verify_recorded() {
    local file="$1" want_size="$2" want_sum="$3" size sum
    [[ -f "$file" ]] || { info "    not in the snapshot"; return 1; }
    size="$(wc -c <"$file" | tr -d '[:space:]')"
    [[ "$size" == "$want_size" ]] || { info "    ${size} bytes, the MANIFEST recorded ${want_size}"; return 1; }
    sum="$(sha256_of "$file")" || { info "    cannot compute SHA-256"; return 1; }
    [[ "$sum" == "$want_sum" ]] || { info "    SHA-256 ${sum}, the MANIFEST recorded ${want_sum}"; return 1; }
}

# Number of files of the Nextcloud snapshot that the restic snapshot lists (nothing is restored).
count_tree_files() {
    local snapshot="$1" dir="$2"
    restic ls "$snapshot" --long | grep -E '^-' | grep -c "/${dir}/volume/" || true
}

verify_dumps() {
    local snapshot="${1:-latest}" tmp dumps manifest failed=0 checked=0 version
    need_restic
    command -v sqlite3 >/dev/null 2>&1 || err "sqlite3 CLI not found. Install it with: apt-get install sqlite3"
    if [[ "$snapshot" == "latest" ]]; then
        log "Choosing the snapshot to verify in ${RESTIC_REPOSITORY}:"
        select_latest_complete
        snapshot="$SELECTED"
    else
        warn_unless_complete "$snapshot"
    fi
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/smdc-restore-verify.XXXXXX")"
    # shellcheck disable=SC2064  # expand now: tmp is a local
    trap "rm -rf '${tmp}'" EXIT

    log "Restoring the dumps of snapshot '${snapshot}' into ${tmp} (live services are not touched)..."
    restic_restore "$snapshot" "$tmp" --include "smdc-dumps"
    dumps="$(find_dumps "$tmp")"
    manifest="${dumps}/MANIFEST"
    grep -qx 'status|complete' "$manifest" || err "MANIFEST is not marked complete: this snapshot does not hold a complete set of dumps."
    info "dumps created: $(manifest_value created "$manifest")"
    check_generation "$dumps" "$manifest" || err "The generation directory in this snapshot does not belong to its MANIFEST: the dumps were not taken from one published generation. This snapshot cannot be trusted to restore any database."
    version="$(sed -n '1s/^smdc-dump-manifest|//p' "$manifest")"
    case "$version" in
        1) warn "MANIFEST format 1 (written by an older backup.sh): it records no sizes or SHA-256 digests, so only loadability is checked." ;;
        2) ;;
        *) err "MANIFEST has an unknown format ('${version}'): use the restore.sh that belongs to the backup.sh that wrote it (it is in the snapshot's configuration directory)." ;;
    esac
    if grep -q '^coverage|reduced|' "$manifest"; then
        warn "This snapshot was taken with BACKUP_ALLOW_COVERAGE_DROP=1 and covers less than the one before it: $(manifest_value 'coverage|reduced' "$manifest")"
    fi

    local gitlab_tar
    gitlab_tar="$(awk -F'|' '$1 == "dump" && $3 == "gitlab" && $5 == "ok" { print $4; exit }' "$manifest")"
    if [[ -n "$gitlab_tar" ]]; then
        log "Restoring GitLab archive ${gitlab_tar}..."
        restic_restore "$snapshot" "$tmp" --include "$gitlab_tar"
    fi

    if [[ "$version" == "2" ]]; then
        log "Comparing the restored dump files with the sizes and SHA-256 digests in the MANIFEST:"
        local rtag ra rb rc rd _rrest rfile
        while IFS='|' read -r rtag ra rb rc rd _rrest; do
            case "$rtag" in
                file)
                    if verify_recorded "${dumps}/${ra}" "$rb" "$rc"; then info "OK       ${ra}"; else info "FAILED   ${ra}"; failed=$((failed + 1)); fi
                    ;;
                extfile)
                    rfile="$(find "$tmp" -type f -name "$(basename "$rb")" | sed -n '1p')"
                    if [[ -n "$rfile" ]] && verify_recorded "$rfile" "$rc" "$rd"; then info "OK       ${rb} (volume ${ra})"; else info "FAILED   ${rb} (volume ${ra})"; failed=$((failed + 1)); fi
                    ;;
            esac
        done <"$manifest"
    fi

    log "Checking every dump listed in the MANIFEST:"
    local _tag name kind path status detail file listed recorded
    # shellcheck disable=SC2094  # the manifest is only read, here and by awk below
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
                file="$(find "$tmp" -type f -name "$path" | sed -n '1p')"
                if [[ -n "$file" ]] && verify_gitlab "$file"; then info "OK       ${name} (gitlab-backup archive)"; else info "FAILED   ${name} (gitlab-backup archive ${path})"; failed=$((failed + 1)); fi
                ;;
            files)
                if [[ -d "${dumps}/${path}" ]]; then info "OK       ${name} (plain files present)"; else info "FAILED   ${name} (directory ${path} missing)"; failed=$((failed + 1)); fi
                ;;
            tree)
                # The Nextcloud file copy is not restored here (it can be large): compare the
                # number of files the snapshot lists with the number the dump phase recorded.
                recorded="$(awk -F'|' -v d="$path" '$1 == "tree" && $2 == d { print $3; exit }' "$manifest")"
                listed="$(count_tree_files "$snapshot" "$path")"
                if [[ -n "$recorded" && "$listed" == "$recorded" ]]; then
                    info "OK       ${name} (${listed} files listed in the snapshot, as recorded; contents not read)"
                else
                    info "FAILED   ${name} (snapshot lists ${listed:-0} files under ${path}/volume, the MANIFEST recorded ${recorded:-nothing})"
                    failed=$((failed + 1))
                fi
                ;;
            *)
                info "FAILED   ${name}: unknown dump kind '${kind}'"
                failed=$((failed + 1))
                ;;
        esac
    done <"$manifest"

    if [[ "$failed" -gt 0 ]]; then
        err "${failed} check(s) failed on the ${checked} dump(s) of this snapshot: a dump that fails cannot be loaded, and this snapshot cannot restore that database."
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
    config="$(find "$target" -type f -name docker-compose.attach.yml 2>/dev/null | sed -n '1p' || true)"
    config="${config%/docker-compose.attach.yml}"

    cat <<EOF

Restore plan for ${target}
  dumps          ${dumps}  (created $(manifest_value created "$manifest"))
  generation     $(check_generation "$dumps" "$manifest" | sed 's/^ *//' || true)
  configuration  ${config:-<not found in this snapshot>}  (compose files, overlays, provisioning, .env)
  volume files   under ${target}: .../<project>_<volume>/_data (host-mode snapshot) or /data/<volume> (container-mode snapshot)
  nextcloud      $(manifest_value nextcloud_mode "$manifest" | sed -e 's/^maintenance$/maintenance mode: database dump and file copy (smdc-nextcloud-files) are from the same window/' -e 's/^online$/online: the files were walked live after the database dump and may not match it/' -e 's/^$/older backup.sh: the files were walked live after the database dump and may not match it/')

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
                echo "  - nextcloud (stopped): copy ${dumps}/${path} to the database path named in this line's source (\"${detail%%;*}\"; data/owncloud.db unless dbname was changed), chown 33:33, start it, then:"
                echo "      docker exec -u www-data sovereign_nextcloud php occ maintenance:mode --off     # a maintenance-mode snapshot restores config.php with maintenance => true"
                echo "      docker exec -u www-data sovereign_nextcloud php occ maintenance:data-fingerprint"
                echo "      docker exec -u www-data sovereign_nextcloud php occ files:scan --all           # needed for an online-mode snapshot; harmless otherwise" ;;
            nextcloud-files)
                echo "  - nextcloud-files: BEFORE the database step above, copy the volume copy taken in the maintenance window back into the EMPTY nextcloud_data volume"
                echo "    (it replaces step 3 for this volume; the live volume is not in this snapshot):"
                echo "      docker run --rm -v <project>_nextcloud_data:/to -v $(dirname "$dumps")/${path}/volume:/from:ro alpine cp -a /from/. /to/"
                echo "    ${detail}" ;;
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
        load_snapshots
        log "Backup snapshots (tag ${SNAPSHOT_TAG}), oldest first:"
        if [[ -n "$SNAPSHOT_ROWS" ]]; then
            printf '          %-10s %-36s %-13s %s\n' "ID" "TIME" "STATE" "TAGS"
            print_snapshot_rows <<<"$SNAPSHOT_ROWS"
        else
            info "(none)"
        fi
        info "complete: backup.sh finished cleanly; used by restore-latest and --verify."
        info "incomplete: the run failed after restic wrote the snapshot. unclassified: no mark (interrupted run or older backup.sh)."
        info "incomplete and unclassified snapshots are only restored by id: ./restore.sh restore-id <id> <target>"
        ;;
    restore-latest)
        need_restic
        target="${1:-}"
        prepare_target "$target"
        log "Choosing the snapshot to restore from ${RESTIC_REPOSITORY}:"
        select_latest_complete
        log "Extracting snapshot ${SELECTED} into $target..."
        restic_restore "$SELECTED" "$target"
        log "✅ Extracted to $target. Databases are dumps, not live directories: run './restore.sh plan $target' for the per-service steps."
        ;;
    restore-id)
        need_restic
        snapshot_id="${1:-}"
        target="${2:-}"
        [[ -n "$snapshot_id" ]] || { echo "Error: Missing snapshot ID."; usage; }
        prepare_target "$target"
        warn_unless_complete "$snapshot_id"
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
