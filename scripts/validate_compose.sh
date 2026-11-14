#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Docker Compose validation (CI + local)
#
# 1. Every stack on its own.
# 2. The merged set that `software/setup.sh --all` deploys, plus a check
#    that every bind-mount source inside the repository exists. Standalone
#    validation alone cannot catch a wrong relative path in an overlay.
#
# Uses software/env.example as the env file; never creates software/.env.
# ====================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SW="${REPO_ROOT}/software"
ENV_FILE="${SW}/env.example"

# Overlays merged by setup.sh; their relative paths resolve against software/.
OVERLAYS=(
    vpn/docker-compose.vpn.yml
    backup/docker-compose.backup.yml
    telemetry/docker-compose.telemetry.yml
    space/docker-compose.space.yml
    agents/docker-compose.agents.yml
    security/docker-compose.crowdsec.yml
)
# Stacks that are only ever run on their own, from their own directory.
STANDALONE=(
    mesh/lora/docker-compose.lora.yml
)

echo "--> core: docker-compose.yml"
docker compose --env-file "$ENV_FILE" -f "${SW}/docker-compose.yml" config --quiet

MERGED=(-f "${SW}/docker-compose.yml")
for f in "${OVERLAYS[@]}"; do
    echo "--> overlay: ${f}"
    docker compose --env-file "$ENV_FILE" --project-directory "$SW" -f "${SW}/${f}" config --quiet
    MERGED+=(-f "${SW}/${f}")
done
for f in "${STANDALONE[@]}"; do
    echo "--> standalone: ${f}"
    docker compose --env-file "$ENV_FILE" -f "${SW}/${f}" config --quiet
done

echo "--> merged (setup.sh --all): bind-mount sources"
docker compose --env-file "$ENV_FILE" "${MERGED[@]}" config --format json \
    | REPO_ROOT="$REPO_ROOT" python3 -c '
import json, os, sys

root = os.path.realpath(os.environ["REPO_ROOT"])
cfg = json.load(sys.stdin)
missing = 0
for name, svc in sorted(cfg["services"].items()):
    sources = [m["source"] for m in svc.get("volumes", []) if m.get("type") == "bind"]
    context = (svc.get("build") or {}).get("context")
    if context:
        sources.append(context)
    for src in sources:
        real = os.path.realpath(src)
        if real != root and not real.startswith(root + os.sep):
            continue  # host path (/sys, /var/run/docker.sock, ...): not checkable here
        ok = os.path.exists(real)
        missing += not ok
        print("    %-7s %-18s %s" % ("ok" if ok else "MISSING", name, os.path.relpath(real, root)))
if missing:
    sys.exit("%d bind-mount source(s) missing in the merged configuration" % missing)
'

echo "--> setup.sh --all --dry-run"
bash "${SW}/setup.sh" --all --dry-run >/dev/null

echo "✅ All Docker Compose stacks validated successfully."
