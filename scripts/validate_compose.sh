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
# 4. Backup coverage: every named volume of the merged stack is classified in
#    software/backup/volumes.conf and the backup overlay mounts exactly the
#    `files` ones (read-only), never a `dumped` live database directory.
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

# ── Backup coverage ──────────────────────────────────────────────────
# Every named volume of the merged stack must be accounted for in
# software/backup/volumes.conf: either backed up (`files`: mounted read-only at
# /data/<key> by the backup overlay), replaced by a native dump (`dumped`: never
# mounted, a live database directory) or explicitly not backed up (`skip`, with a
# reason). A new volume that nobody classified fails here instead of silently
# missing from every snapshot.
echo "--> backup coverage: every named volume is backed up, dumped or explicitly skipped"
python3 - "$INVARIANT_DIR" "${SW}/backup/volumes.conf" "$SW" <<'PY'
import json, os, sys

tmp, conf_path, sw = sys.argv[1], sys.argv[2], os.path.realpath(sys.argv[3])
errors = []
BACKUP_SERVICE = "backup-cron"
BACKUP_TAG = "backup_docker-compose.backup.yml"

conf = {}
with open(conf_path, encoding="utf-8") as fh:
    for lineno, raw in enumerate(fh, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != 5:
            errors.append("volumes.conf:%d: expected 5 pipe-separated fields, got %d" % (lineno, len(parts)))
            continue
        key, name, cls, paths, reason = parts
        if key in conf:
            errors.append("volumes.conf:%d: duplicate key %s" % (lineno, key))
        if cls not in ("files", "dumped", "skip"):
            errors.append("volumes.conf:%d: %s has unknown class %r" % (lineno, key, cls))
        if cls == "files" and not paths.split():
            errors.append("volumes.conf:%d: files volume %s lists no paths" % (lineno, key))
        if cls in ("dumped", "skip") and not reason.strip():
            errors.append("volumes.conf:%d: %s is %s without a reason" % (lineno, key, cls))
        conf[key] = {"name": name, "class": cls, "paths": paths, "reason": reason}


def load(name):
    with open(os.path.join(tmp, name), encoding="utf-8") as fh:
        return json.load(fh)


def check_backup_service(cfg, label, project):
    """Mounts of the backup container must match volumes.conf exactly."""
    svc = (cfg.get("services") or {}).get(BACKUP_SERVICE)
    if svc is None:
        errors.append("%s: service %s not found" % (label, BACKUP_SERVICE))
        return
    vols = cfg.get("volumes") or {}
    data_mounts, binds = {}, {}
    for m in svc.get("volumes", []):
        if m.get("type") == "bind":
            binds[m["target"]] = m
        elif m.get("type") == "volume" and m["target"].startswith("/data/"):
            data_mounts[m["source"]] = m
    for key, row in sorted(conf.items()):
        m = data_mounts.get(key)
        want = row["name"] if row["name"] != "-" else "%s_%s" % (project, key)
        if row["class"] == "files":
            if m is None:
                errors.append("%s: volume %s is `files` in volumes.conf but %s does not mount it under /data"
                              % (label, key, BACKUP_SERVICE))
                continue
            if m["target"] != "/data/" + key:
                errors.append("%s: volume %s is mounted at %s, expected /data/%s" % (label, key, m["target"], key))
            if not m.get("read_only"):
                errors.append("%s: volume %s must be mounted read-only in %s" % (label, key, BACKUP_SERVICE))
            got = (vols.get(key) or {}).get("name")
            if got != want:
                errors.append("%s: volume %s resolves to %s, volumes.conf (and the stack) expect %s"
                              % (label, key, got, want))
        elif m is not None:
            errors.append("%s: volume %s is `%s` in volumes.conf but %s mounts it at %s (a live database "
                          "directory must not be walked by restic)" % (label, key, row["class"], BACKUP_SERVICE, m["target"]))
    for key in sorted(set(data_mounts) - set(conf)):
        errors.append("%s: %s mounts volume %s, which volumes.conf does not list" % (label, BACKUP_SERVICE, key))
    # Deployment configuration and the host's dump directory.
    config = binds.get("/config")
    if config is None or os.path.realpath(config["source"]) != sw or not config.get("read_only"):
        errors.append("%s: %s must bind-mount software/ read-only at /config (compose files, overlays, .env)"
                      % (label, BACKUP_SERVICE))
    dumps = binds.get("/dumps")
    if dumps is None or not dumps.get("read_only"):
        errors.append("%s: %s must bind-mount the host dump directory read-only at /dumps" % (label, BACKUP_SERVICE))
    for target, m in binds.items():
        if "docker.sock" in m["source"]:
            errors.append("%s: %s must not mount the Docker socket (%s)" % (label, BACKUP_SERVICE, m["source"]))


for project in ("software", "edgeone"):
    before = len(errors)
    merged = load("%s.merged.ALL.json" % project)
    stack = {k: v["name"] for k, v in (merged.get("volumes") or {}).items()}
    for key in sorted(set(stack) - set(conf)):
        errors.append("-p %s: named volume %s (%s) of the merged stack is not listed in software/backup/volumes.conf: "
                      "add it as files, dumped or skip (with a reason)" % (project, key, stack[key]))
    for key in sorted(set(conf) - set(stack)):
        errors.append("-p %s: volumes.conf lists %s, which no Compose file of the merged stack declares" % (project, key))
    for key in sorted(set(conf) & set(stack)):
        want = conf[key]["name"] if conf[key]["name"] != "-" else "%s_%s" % (project, key)
        if stack[key] != want:
            errors.append("-p %s: volume %s resolves to %s, volumes.conf says %s" % (project, key, stack[key], want))
    check_backup_service(merged, "-p %s merged.ALL" % project, project)
    check_backup_service(load("%s.merged.%s.json" % (project, BACKUP_TAG)), "-p %s core+backup" % project, project)
    check_backup_service(load("%s.attach.%s.json" % (project, BACKUP_TAG)), "-p %s attach+backup (standalone)" % project, project)
    if len(errors) > before:
        continue
    counts = {c: sum(1 for k in stack if conf[k]["class"] == c) for c in ("files", "dumped", "skip")}
    print("    ok  -p %-8s %d named volumes: %d backed up (files), %d replaced by a native dump, %d explicitly "
          "not backed up; backup mounts identical merged / core+backup / standalone"
          % (project, len(stack), counts["files"], counts["dumped"], counts["skip"]))
    if project == "software":
        for key in sorted(stack):
            row = conf[key]
            what = {"files": "files   /data/%s:ro  paths=%s" % (key, row["paths"]),
                    "dumped": "dumped  not mounted",
                    "skip": "skip    not backed up"}[row["class"]]
            print("        %-20s %-44s %s" % (key, what, row["reason"]))

if errors:
    print("\n".join("    FAIL " + e for e in errors), file=sys.stderr)
    sys.exit("%d backup-coverage violation(s)" % len(errors))
PY

# ── Traefik basic-auth hash survives the documented .env transformation ──
# env.example tells the operator to double every `$` of the htpasswd hash. Compose
# interpolates unquoted env-file values, where `$$` is an escaped `$`, so Traefik must
# receive the hash with single dollars. `docker compose config` prints every literal `$`
# of its output as `$$` (so that the output is itself a valid Compose file): the label is
# un-escaped once before it is compared. A container label has the un-escaped value.
echo "--> Traefik dashboard auth: documented htpasswd transformation yields the original hash"
AUTH_TEST_HASH='admin:$apr1$Ab3dEf9h$pnmZV.qJrPK4ciUn2AqRt/' # openssl passwd -apr1 -salt Ab3dEf9h yourpassword
AUTH_ENV="${INVARIANT_DIR}/auth-test.env"
grep -q "sed -e 's/\\\\\$/\\\\\$\\\\\$/g'" "$ENV_FILE" \
    || { echo "env.example no longer documents the sed transformation this check applies" >&2; exit 1; }
grep -v '^TRAEFIK_DASHBOARD_AUTH=' "$ENV_FILE" >"$AUTH_ENV"
# shellcheck disable=SC2016  # the sed program is the one documented in env.example
printf 'TRAEFIK_DASHBOARD_AUTH=%s\n' "$(printf '%s' "$AUTH_TEST_HASH" | sed -e 's/\$/\$\$/g')" >>"$AUTH_ENV"
docker compose --env-file "$AUTH_ENV" -f "${SW}/docker-compose.yml" config --format json \
    | EXPECTED="$AUTH_TEST_HASH" python3 -c '
import json, os, sys

expected = os.environ["EXPECTED"]
cfg = json.load(sys.stdin)
found = []
for name, svc in sorted(cfg["services"].items()):
    labels = svc.get("labels") or {}
    if isinstance(labels, list):
        labels = dict(item.split("=", 1) for item in labels)
    for key, value in labels.items():
        if key.endswith(".basicauth.users"):
            found.append((name, key, value.replace("$$", "$")))
if not found:
    sys.exit("no Traefik basicauth.users label found in docker-compose.yml")
for name, key, value in found:
    if value != expected:
        sys.exit("%s: %s resolves to %r, expected %r" % (name, key, value, expected))
    print("    ok  %s: %s = %s" % (name, key, value))
'

echo "--> setup.sh --all --dry-run"
bash "${SW}/setup.sh" --all --dry-run >/dev/null

echo "✅ All Docker Compose stacks validated successfully."
