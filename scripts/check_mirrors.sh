#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — standalone-copy drift gate (CI + local)
#
# software/{agents,mesh,space} hold standalone copies of modules from
# src/sovereign_dc/ so the Compose overlays can bind-mount or COPY them
# without installing the package (software/space/Dockerfile relies on the
# copies being byte-identical). The Helm chart carries its own copy of the
# standalone power exporter (Helm can only read files inside the chart). This
# gate fails when a copy drifts.
#
#   scripts/check_mirrors.sh          # check the repository this script is in
#   scripts/check_mirrors.sh <root>   # check another checkout (used to test the gate)
#
# Fix a failure with:  cp src/sovereign_dc/<path> software/<path>
#                      cp <source> <chart copy>          (CHART_COPIES)
# ====================================================================
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRC="${ROOT}/src/sovereign_dc"
SW="${ROOT}/software"

# Must be byte-identical to src/sovereign_dc/<path>.
MIRRORS=(
    agents/gitlab_reviewer.py
    agents/knowledge_indexer.py
    agents/sentinel_copilot.py
    mesh/lora/meshtastic_gateway.py
    mesh/mesh_sync.py
    space/__init__.py
    space/dtn/__init__.py
    space/dtn/bundle.py
    space/dtn/router.py
    space/orbital/__init__.py
    space/orbital/propagator.py
    space/orbital/tle_updater.py
    space/space_exporter.py
    space/transceiver/__init__.py
    space/transceiver/simulated_link.py
)

# Same relative path under src/sovereign_dc, different content ON PURPOSE:
# the package __init__ re-exports its API, the standalone one is a docstring
# only (the overlay containers run the modules as scripts).
# software/telemetry/power_exporter.py is not listed in either list: it has no
# byte-identical counterpart under src/sovereign_dc (src/sovereign_dc/telemetry.py
# is the packaged variant; tests/ compares the blocks the two share).
INTENTIONALLY_DIFFERENT=(
    agents/__init__.py
    mesh/__init__.py
)

# ── Chart copies ─────────────────────────────────────────────────────
# "<source>|<copy>", both relative to the repository root; the copy must be
# byte-identical to the source. The Helm chart mounts the exporter script from a
# ConfigMap built with .Files.Get, which cannot reach outside the chart directory.
CHART_COPIES=(
    "software/telemetry/power_exporter.py|kubernetes/helm/sovereign-stack/files/power_exporter.py"
)

[[ -d "$SRC" && -d "$SW" ]] || { echo "error: ${ROOT} is not a repository root (src/sovereign_dc or software missing)" >&2; exit 2; }

contains() { # contains <needle> <haystack...>
    local needle="$1" item
    shift
    for item in "$@"; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

failed=0

for rel in "${MIRRORS[@]}"; do
    if [[ ! -f "${SRC}/${rel}" || ! -f "${SW}/${rel}" ]]; then
        echo "MISSING  ${rel} (expected in both src/sovereign_dc/ and software/)" >&2
        failed=$((failed + 1))
    elif ! cmp -s "${SRC}/${rel}" "${SW}/${rel}"; then
        echo "DRIFT    software/${rel} differs from src/sovereign_dc/${rel}" >&2
        diff -u "${SRC}/${rel}" "${SW}/${rel}" | sed -n '1,20p' >&2 || true
        failed=$((failed + 1))
    fi
done

for rel in "${INTENTIONALLY_DIFFERENT[@]}"; do
    if [[ -f "${SRC}/${rel}" && -f "${SW}/${rel}" ]] && cmp -s "${SRC}/${rel}" "${SW}/${rel}"; then
        echo "STALE    ${rel} is listed as intentionally different but is identical: move it to MIRRORS" >&2
        failed=$((failed + 1))
    fi
done

# A new copy must be classified in one of the two lists above.
while IFS= read -r path; do
    rel="${path#"${SW}/"}"
    [[ -f "${SRC}/${rel}" ]] || continue
    if ! contains "$rel" "${MIRRORS[@]}" && ! contains "$rel" "${INTENTIONALLY_DIFFERENT[@]}"; then
        echo "UNLISTED software/${rel} has a counterpart in src/sovereign_dc/ but is in neither list of scripts/check_mirrors.sh" >&2
        failed=$((failed + 1))
    fi
done < <(find "${SW}/agents" "${SW}/mesh" "${SW}/space" -name '*.py' -type f | sort)

for pair in "${CHART_COPIES[@]}"; do
    source_rel="${pair%%|*}"
    copy_rel="${pair#*|}"
    if [[ ! -f "${ROOT}/${source_rel}" || ! -f "${ROOT}/${copy_rel}" ]]; then
        echo "MISSING  ${copy_rel} (chart copy of ${source_rel}; both must exist)" >&2
        failed=$((failed + 1))
    elif ! cmp -s "${ROOT}/${source_rel}" "${ROOT}/${copy_rel}"; then
        echo "DRIFT    ${copy_rel} differs from ${source_rel}" >&2
        diff -u "${ROOT}/${source_rel}" "${ROOT}/${copy_rel}" | sed -n '1,20p' >&2 || true
        failed=$((failed + 1))
    fi
done

# Every file under a chart's files/ directory must be a listed copy.
while IFS= read -r path; do
    rel="${path#"${ROOT}/"}"
    listed=false
    for pair in "${CHART_COPIES[@]}"; do
        [[ "${pair#*|}" == "$rel" ]] && listed=true
    done
    if [[ "$listed" == "false" ]]; then
        echo "UNLISTED ${rel} is not in CHART_COPIES of scripts/check_mirrors.sh" >&2
        failed=$((failed + 1))
    fi
done < <(find "${ROOT}/kubernetes/helm" -path '*/files/*' -type f -not -path '*/__pycache__/*' 2>/dev/null | sort)

if [[ "$failed" -gt 0 ]]; then
    echo "❌ ${failed} copy problem(s). Sync with: cp src/sovereign_dc/<path> software/<path>   or   cp <source> <chart copy>" >&2
    exit 1
fi
echo "✅ ${#MIRRORS[@]} standalone copies under software/ are byte-identical to src/sovereign_dc/; ${#CHART_COPIES[@]} chart copy is byte-identical to its source."
