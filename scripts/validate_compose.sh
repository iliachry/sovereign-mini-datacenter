#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Docker Compose validation (CI + local)
#
# 1. Every stack on its own.
# 2. The merged set that `software/setup.sh --all` deploys, plus a check
#    that every bind-mount source inside the repository exists. Standalone
#    validation alone cannot catch a wrong relative path in an overlay.
# 3. Storage-identity invariant: for two project names, merging any overlay
#    (or all of them) must not change the resolved name of any volume or network
#    a core service uses, and an overlay run standalone through
#    docker-compose.attach.yml must attach to those same objects as external.
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

# ── Storage-identity invariant ───────────────────────────────────────
# An overlay that sets `name:` on a volume or network key of the core file moves
# every core service using that key onto a different (empty) volume as soon as the
# overlay is enabled under another project name. Compare resolved names instead of
# trusting the YAML.
ATTACH="${SW}/docker-compose.attach.yml"
INVARIANT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/smdc-compose-invariant.XXXXXX")"
trap 'rm -rf "$INVARIANT_DIR"' EXIT

resolve() { # resolve <out.json> <project> <compose args...>
    local out="$1" project="$2"
    shift 2
    docker compose --env-file "$ENV_FILE" -p "$project" --project-directory "$SW" "$@" \
        config --format json >"$out"
}

echo "--> invariant: overlays never change the core stack's volume/network names"
for project in software edgeone; do
    resolve "${INVARIANT_DIR}/${project}.core.json" "$project" -f "${SW}/docker-compose.yml"
    for f in "${OVERLAYS[@]}"; do
        tag="${f//\//_}"
        resolve "${INVARIANT_DIR}/${project}.merged.${tag}.json" "$project" \
            -f "${SW}/docker-compose.yml" -f "${SW}/${f}"
        resolve "${INVARIANT_DIR}/${project}.attach.${tag}.json" "$project" \
            -f "$ATTACH" -f "${SW}/${f}"
    done
    resolve "${INVARIANT_DIR}/${project}.merged.ALL.json" "$project" "${MERGED[@]}"
    # Never deployed like this: `config` drops volumes no service uses, so the
    # attach file is resolved together with the core services to see every key.
    resolve "${INVARIANT_DIR}/${project}.attachfile.json" "$project" \
        -f "${SW}/docker-compose.yml" -f "$ATTACH"
done

python3 - "$INVARIANT_DIR" <<'PY'
import glob, json, os, sys

tmp = sys.argv[1]
errors = []


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def service_storage(cfg):
    """{service: {"volumes": {target: physical name}, "networks": {key: physical name}}}"""
    vols, nets = cfg.get("volumes") or {}, cfg.get("networks") or {}
    out = {}
    for name, svc in (cfg.get("services") or {}).items():
        out[name] = {
            "volumes": {
                m["target"]: vols[m["source"]]["name"]
                for m in svc.get("volumes", [])
                if m.get("type") == "volume"
            },
            "networks": {key: nets[key]["name"] for key in (svc.get("networks") or {})},
        }
    return out


for project in ("software", "edgeone"):
    before = len(errors)
    core = load(os.path.join(tmp, project + ".core.json"))
    core_storage = service_storage(core)
    core_vols = {k: v["name"] for k, v in (core.get("volumes") or {}).items()}
    core_nets = {k: v["name"] for k, v in (core.get("networks") or {}).items()}
    fixed = sorted(n for n in core_vols.values() if not n.startswith(project + "_"))

    merged = sorted(glob.glob(os.path.join(tmp, project + ".merged.*.json")))
    for path in merged:
        label = os.path.basename(path)[len(project) + 1 : -len(".json")]
        cfg = load(path)
        storage = service_storage(cfg)
        for svc, want in core_storage.items():
            got = storage.get(svc)
            if got != want:
                errors.append("-p %s %s: service %s storage changed\n      core : %s\n      now  : %s"
                              % (project, label, svc, want, got))
        for kind, core_map in (("volumes", core_vols), ("networks", core_nets)):
            for key, name in core_map.items():
                got = ((cfg.get(kind) or {}).get(key) or {}).get("name")
                if got != name:
                    errors.append("-p %s %s: %s key %s resolves to %s, core stack uses %s"
                                  % (project, label, kind, key, got, name))

    # The attach file must cover every volume and network of the core file, as
    # external objects under the names the core stack gives them.
    attach = load(os.path.join(tmp, project + ".attachfile.json"))
    for kind, core_map in (("volumes", core_vols), ("networks", core_nets)):
        declared = attach.get(kind) or {}
        for key, name in core_map.items():
            obj = declared.get(key) or {}
            if obj.get("name") != name or not obj.get("external"):
                errors.append("-p %s docker-compose.attach.yml: %s.%s = %s, expected external %s"
                              % (project, kind, key, obj, name))

    attached = sorted(glob.glob(os.path.join(tmp, project + ".attach.*.json")))
    for path in attached:
        label = os.path.basename(path)[len(project) + 1 : -len(".json")]
        cfg = load(path)
        for kind, core_map in (("volumes", core_vols), ("networks", core_nets)):
            for key, obj in (cfg.get(kind) or {}).items():
                if key in core_map and (obj.get("name") != core_map[key] or not obj.get("external")):
                    errors.append("-p %s %s: %s.%s = %s, expected external %s"
                                  % (project, label, kind, key, obj, core_map[key]))

    if len(errors) > before:
        continue
    nvol = sum(len(s["volumes"]) for s in core_storage.values())
    print("    ok  -p %-8s %d core services / %d volume mounts / network %s unchanged in %d merged "
          "configs; %d standalone (attach) configs bind the same objects as external"
          % (project, len(core_storage), nvol, ",".join(sorted(core_nets.values())), len(merged), len(attached)))
    print("        fixed-name (not project-scoped) core volumes: %s" % (", ".join(fixed) or "none"))

if errors:
    print("\n".join("    FAIL " + e for e in errors), file=sys.stderr)
    sys.exit("%d storage-identity violation(s): an overlay must not rename core volumes/networks" % len(errors))
PY

echo "--> setup.sh --all --dry-run"
bash "${SW}/setup.sh" --all --dry-run >/dev/null

echo "✅ All Docker Compose stacks validated successfully."
