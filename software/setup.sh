#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Automated Setup & Deployment Script
# Supports Core, Headscale VPN, Restic, Telemetry, Space, Agents & CrowdSec.
# Mailcow is a separate stack; --with-mailcow prints its manual steps.
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
HEADSCALE_CONFIG="${PROJECT_DIR}/vpn/config/headscale.yaml"

# Values shipped in env.example that must never reach a deployment.
PLACEHOLDER_RE='^[A-Za-z0-9_]+=.*(change_?me|replace_this|your_|your@|yourdomain|yourmailprovider)'

# OpenProject all-in-one container (container_name in docker-compose.yml) and the
# named volume that now holds its embedded PostgreSQL data directory.
OPENPROJECT_CONTAINER="sovereign_openproject"
OPENPROJECT_PGDATA_PATH="/var/openproject/pgdata"
OPENPROJECT_PGDATA_VOLUME_KEY="openproject_pgdata"

DEPLOY_ALL=false
SHOW_MAILCOW=false
DEPLOY_VPN=false
DEPLOY_BACKUP=false
DEPLOY_TELEMETRY=false
DEPLOY_SPACE=false
DEPLOY_AGENTS=false
DEPLOY_SECURITY=false
DRY_RUN=false

usage() {
    cat <<EOF
Sovereign Mini Datacenter Setup

Usage: $(basename "$0") [OPTIONS]

Options:
    --all              Deploy the core stack plus every overlay below (not Mailcow)
    --with-vpn         Include Headscale Zero-Trust VPN
    --with-backup      Include Restic backup daemon
    --with-telemetry   Include Prometheus power & thermal exporter
    --with-space       Include Space Communications DTN & Orbit Tracking node
    --with-agents      Include Autonomous Local AI Agents (GitLab Reviewer, Knowledge Indexer)
    --with-security    Include CrowdSec security engine (detection only, no bouncer)
    --with-mailcow     Print the manual Mailcow install steps (separate stack, not deployed here)
    --dry-run          Validate configuration and exit without launching
    -h, --help         Show this help message
EOF
}

print_mailcow_steps() {
    cat <<EOF

Mailcow is not deployed by this script. It is a separate Compose project:

  1. git clone https://github.com/mailcow/mailcow-dockerized.git ${PROJECT_DIR}/mailcow/core
  2. cd ${PROJECT_DIR}/mailcow/core
     cp ../mailcow.conf.example mailcow.conf    # then edit MAILCOW_HOSTNAME, TZ
  3. cp ../docker-compose.override.yml docker-compose.override.yml
  4. docker compose up -d                       # core stack must already be running

Details and DNS records: ${PROJECT_DIR}/mailcow/README.md
EOF
}

# Pre-flight guard for the OpenProject database volume.
#
# docker-compose.yml mounts the named volume <project>_openproject_pgdata at
# /var/openproject/pgdata. The openproject/community image declares that path as a
# VOLUME, so a deployment created before the named volume existed keeps its PostgreSQL
# data in an ANONYMOUS volume. Recreating the container would start OpenProject on the
# empty named volume: the data would not be deleted, but orphaned. Refuse to deploy
# until the data has been copied across.
#
# No-op when the container does not exist or already uses the named volume.
check_openproject_pgdata_volume() {
    if ! docker container inspect "$OPENPROJECT_CONTAINER" >/dev/null 2>&1; then
        return 0
    fi

    local project expected mount mount_type mount_name
    # Project name exactly as Compose resolves it (COMPOSE_PROJECT_NAME, `name:`, directory).
    project=$(docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" config 2>/dev/null \
        | sed -n 's/^name:[[:space:]]*//p' | head -n 1 | tr -d "\"'" || true)
    if [[ -z "$project" ]]; then
        project=$(docker container inspect \
            --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
            "$OPENPROJECT_CONTAINER" 2>/dev/null || true)
    fi
    if [[ -z "$project" ]]; then
        log_error "Cannot determine the Compose project name; refusing to touch the existing ${OPENPROJECT_CONTAINER} container."
        exit 1
    fi
    expected="${project}_${OPENPROJECT_PGDATA_VOLUME_KEY}"

    mount=$(docker container inspect \
        --format "{{ range .Mounts }}{{ if eq .Destination \"${OPENPROJECT_PGDATA_PATH}\" }}{{ .Type }} {{ .Name }}{{ end }}{{ end }}" \
        "$OPENPROJECT_CONTAINER" 2>/dev/null || true)
    mount_type="${mount%% *}"
    mount_name=""
    if [[ "$mount" == *" "* ]]; then mount_name="${mount#* }"; fi

    # Only a Docker volume other than the named one is at risk (no mount / bind mount: nothing to orphan here).
    if [[ "$mount_type" != "volume" || -z "$mount_name" || "$mount_name" == "$expected" ]]; then
        return 0
    fi

    log_error "The existing ${OPENPROJECT_CONTAINER} container keeps its PostgreSQL data in the volume"
    log_error "  ${mount_name}"
    log_error "but docker-compose.yml now mounts the named volume ${expected} at ${OPENPROJECT_PGDATA_PATH}."
    log_error "Deploying now would start OpenProject with an EMPTY database (the old data would be orphaned, not deleted)."
    log_error "Nothing was deployed. Migrate the data once, then re-run this script:"
    cat >&2 <<EOF

  # 1. Stop OpenProject so PostgreSQL shuts down cleanly
  docker stop ${OPENPROJECT_CONTAINER}

  # 2. Create the named volume (labelled so Compose adopts it) and copy the data into it.
  #    If ${expected} already exists, make sure it is empty first.
  docker volume create \\
      --label com.docker.compose.project=${project} \\
      --label com.docker.compose.volume=${OPENPROJECT_PGDATA_VOLUME_KEY} \\
      ${expected}
  docker run --rm -v ${mount_name}:/from -v ${expected}:/to alpine cp -a /from/. /to/

  # 3. Remove the old container. Do NOT pass -v: that would delete the old volume,
  #    which is your only copy until OpenProject is confirmed working again.
  docker rm ${OPENPROJECT_CONTAINER}

  # 4. Re-run
  $0${ORIGINAL_ARGS:+ ${ORIGINAL_ARGS}}

  Once OpenProject is verified, the old volume can be removed with:
  docker volume rm ${mount_name}

EOF
    exit 1
}

ORIGINAL_ARGS="$*"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)            DEPLOY_ALL=true; shift ;;
        --with-mailcow)   SHOW_MAILCOW=true; shift ;;
        --with-vpn)       DEPLOY_VPN=true; shift ;;
        --with-backup)    DEPLOY_BACKUP=true; shift ;;
        --with-telemetry) DEPLOY_TELEMETRY=true; shift ;;
        --with-space)     DEPLOY_SPACE=true; shift ;;
        --with-agents)    DEPLOY_AGENTS=true; shift ;;
        --with-security)  DEPLOY_SECURITY=true; shift ;;
        --dry-run)        DRY_RUN=true; shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                log_error "Unknown option: $1"; usage >&2; exit 2 ;;
    esac
done

echo -e "\n${BOLD}${CYAN}=== Sovereign Mini Datacenter Setup ===${RESET}\n"

# Build Compose Command
COMPOSE_FILES=("-f" "${PROJECT_DIR}/docker-compose.yml")

if [[ "$DEPLOY_ALL" == "true" || "$DEPLOY_VPN" == "true" ]]; then
    DEPLOY_VPN=true
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
    # Validation only: fall back to the template so no .env is created.
    compose_env="$ENV_FILE"
    if [[ ! -f "$compose_env" ]]; then
        compose_env="$ENV_EXAMPLE"
        log_warn ".env not found; validating against env.example."
    fi
    log_info "DRY-RUN: Validating Docker Compose configuration..."
    docker compose --env-file "$compose_env" "${COMPOSE_FILES[@]}" config --quiet
    log_ok "Docker Compose configuration is valid."
    if [[ "$SHOW_MAILCOW" == "true" ]]; then print_mailcow_steps; fi
    exit 0
fi

# Never deploy with the public placeholder credentials from env.example.
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_EXAMPLE" ]]; then
        log_error "Neither .env nor env.example found in ${PROJECT_DIR}."
        exit 1
    fi
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log_warn "Created ${ENV_FILE} from env.example (mode 0600)."
    log_error "Nothing was deployed. Edit ${ENV_FILE}, replace every placeholder value, then re-run."
    exit 1
fi

placeholder_keys=$(grep -Ei "$PLACEHOLDER_RE" "$ENV_FILE" | cut -d= -f1 | tr '\n' ' ' || true)
if [[ -n "$placeholder_keys" ]]; then
    log_error "${ENV_FILE} still contains placeholder values for: ${placeholder_keys}"
    log_error "Replace them (or clear the ones you do not use), then re-run. Nothing was deployed."
    exit 1
fi

if [[ "$DEPLOY_VPN" == "true" ]] && grep -Eq '^server_url:.*yourdomain' "$HEADSCALE_CONFIG"; then
    log_error "Set server_url in ${HEADSCALE_CONFIG} before deploying the VPN. Nothing was deployed."
    exit 1
fi

check_openproject_pgdata_volume

log_info "Starting sovereign datacenter container stacks..."
docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" up -d --remove-orphans
log_ok "Containers started. Check their health with: docker compose --project-directory ${PROJECT_DIR} ps"

if [[ "$SHOW_MAILCOW" == "true" ]]; then print_mailcow_steps; fi
