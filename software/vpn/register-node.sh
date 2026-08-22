#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Headscale User & Device Manager
# ====================================================================

set -euo pipefail

CONTAINER_NAME="sovereign_headscale"

usage() {
    cat <<EOF
Sovereign Mesh VPN Management CLI

Usage:
  ./register-node.sh create-user <username>
  ./register-node.sh list-users
  ./register-node.sh create-authkey <username> [--reusable] [--ephemeral]
  ./register-node.sh list-nodes
  ./register-node.sh register-node <username> <nodekey>
  ./register-node.sh delete-node <node-id>

Examples:
  ./register-node.sh create-user alice
  ./register-node.sh create-authkey alice --reusable
  ./register-node.sh register-node alice nodekey:123456789abcdef
EOF
    exit 1
}

check_container() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: Headscale container '${CONTAINER_NAME}' is not running." >&2
        echo "Start it first with: docker compose -f software/vpn/docker-compose.vpn.yml up -d" >&2
        exit 1
    fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
    create-user)
        username="${1:-}"
        [[ -n "$username" ]] || { echo "Error: Missing username."; usage; }
        check_container
        docker exec -it "$CONTAINER_NAME" headscale users create "$username"
        ;;
    list-users)
        check_container
        docker exec -it "$CONTAINER_NAME" headscale users list
        ;;
    create-authkey)
        username="${1:-}"
        [[ -n "$username" ]] || { echo "Error: Missing username."; usage; }
        check_container
        shift || true
        # shellcheck disable=SC2068
        docker exec -it "$CONTAINER_NAME" headscale preauthkeys create --user "$username" "$@"
        ;;
    list-nodes)
        check_container
        docker exec -it "$CONTAINER_NAME" headscale nodes list
        ;;
    register-node)
        username="${1:-}"
        nodekey="${2:-}"
        [[ -n "$username" && -n "$nodekey" ]] || { echo "Error: Missing username or nodekey."; usage; }
        check_container
        docker exec -it "$CONTAINER_NAME" headscale nodes register --user "$username" --key "$nodekey"
        ;;
    delete-node)
        node_id="${1:-}"
        [[ -n "$node_id" ]] || { echo "Error: Missing node id."; usage; }
        check_container
        docker exec -it "$CONTAINER_NAME" headscale nodes delete -i "$node_id"
        ;;
    *)
        usage
        ;;
esac