#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Load-Shedding Sentinel (one pass per run)
#
# Reads the power exporter's /metrics once and decides:
#   SoC < 20 %  or coolant > 60 °C      -> shed
#   SoC >= 40 % and coolant <= 60 °C    -> normal (resume)
#   anything in between                 -> hold the current state (hysteresis)
#   no usable SoC reading               -> no decision, no action
#
# Run it from cron or a systemd timer (e.g. every minute).
#
# LOAD_SHED_ACTION selects what "shed" does:
#   log    (default) only logs the decision; nothing is touched.
#   pause  `docker pause`s LOAD_SHED_CONTAINER (default: sovereign_ollama, the
#          GPU inference server of the core stack) and `docker unpause`s it on
#          recovery. A paused container keeps its memory and loaded models but
#          gets no CPU/GPU time; requests to it hang until it is resumed, and
#          its healthcheck reports unhealthy meanwhile. The sentinel resumes
#          only a container it paused itself (recorded in LOAD_SHED_STATE_FILE)
#          and never pauses twice.
#
# Environment:
#   TELEMETRY_METRICS_URL  exporter URL   (default http://localhost:9101/metrics)
#   LOAD_SHED_ACTION       log | pause    (default log)
#   LOAD_SHED_CONTAINER    container name (default sovereign_ollama)
#   LOAD_SHED_STATE_FILE   marker written while this script holds the pause
#                          (default ${XDG_STATE_HOME:-$HOME/.local/state}/sovereign-dc/load_shedder.paused)
# ====================================================================

set -euo pipefail

METRICS_URL="${TELEMETRY_METRICS_URL:-http://localhost:9101/metrics}"
ACTION="${LOAD_SHED_ACTION:-log}"
CONTAINER="${LOAD_SHED_CONTAINER:-sovereign_ollama}"
STATE_FILE="${LOAD_SHED_STATE_FILE:-${XDG_STATE_HOME:-${HOME:-/var/lib}/.local/state}/sovereign-dc/load_shedder.paused}"
SOC_THRESHOLD_LOW=20.0
SOC_THRESHOLD_NORMAL=40.0
TEMP_COOLANT_MAX=60.0

log()  { echo -e "\033[1;32m[SENTINEL] $*\033[0m"; }
warn() { echo -e "\033[1;33m[SENTINEL-WARN] $*\033[0m"; }

case "$ACTION" in
    log | pause) ;;
    *)
        warn "LOAD_SHED_ACTION must be 'log' or 'pause' (got '${ACTION}')."
        exit 2
        ;;
esac

is_number() { [[ "$1" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; }

# Docker state of the container: running, paused, exited, ... or empty when
# docker is unavailable or the container does not exist.
container_status() {
    command -v docker >/dev/null 2>&1 || return 0
    docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true
}

shed() {
    if [[ "$ACTION" != "pause" ]]; then
        log "LOAD_SHED_ACTION=log: decision logged only, no workload was touched (set LOAD_SHED_ACTION=pause to pause ${CONTAINER})."
        return 0
    fi
    local status
    status="$(container_status)"
    case "$status" in
        running)
            if docker pause "$CONTAINER" >/dev/null; then
                mkdir -p "$(dirname "$STATE_FILE")"
                printf '%s\n' "$CONTAINER" >"$STATE_FILE"
                log "Paused ${CONTAINER}; it is resumed once SoC >= ${SOC_THRESHOLD_NORMAL}% and coolant <= ${TEMP_COOLANT_MAX}°C."
            else
                warn "docker pause ${CONTAINER} failed; nothing was shed."
            fi
            ;;
        paused)
            if [[ -f "$STATE_FILE" ]]; then
                log "${CONTAINER} is still paused by this sentinel."
            else
                log "${CONTAINER} is already paused, but not by this sentinel; leaving it alone."
            fi
            ;;
        "")
            warn "Container ${CONTAINER} not found (or docker unavailable); nothing to shed."
            ;;
        *)
            log "Container ${CONTAINER} is '${status}', not running; nothing to shed."
            ;;
    esac
}

# Undo our own pause - and only our own.
resume() {
    [[ -f "$STATE_FILE" ]] || return 0
    local paused_name status
    paused_name="$(head -n 1 "$STATE_FILE")"
    [[ -n "$paused_name" ]] || paused_name="$CONTAINER"
    CONTAINER="$paused_name"
    status="$(container_status)"
    if [[ "$status" == "paused" ]]; then
        if docker unpause "$CONTAINER" >/dev/null; then
            log "Resumed ${CONTAINER}."
        else
            warn "docker unpause ${CONTAINER} failed; will retry on the next run."
            return 0
        fi
    else
        log "${CONTAINER} is no longer paused (state: ${status:-unknown}); clearing the sentinel's marker."
    fi
    rm -f "$STATE_FILE"
}

# Fetch metrics
if ! metrics=$(curl -s --connect-timeout 5 "$METRICS_URL"); then
    warn "Failed to reach telemetry exporter at ${METRICS_URL}; no load-shedding decision made."
    exit 0
fi

# `|| true`: a missing metric must fall through to the checks below, not
# abort the script (grep exits 1 on no match under `set -euo pipefail`).
soc=$(echo "$metrics" | grep "^sovereign_battery_soc_percent " | awk '{print $2}' || true)
coolant_temp=$(echo "$metrics" | grep "^sovereign_temp_coolant_celsius " | awk '{print $2}' || true)

# Never substitute a healthy value for a missing measurement: with no usable
# battery reading (exporter has no source, or reports NaN), make no shedding
# decision at all - neither shed nor resume.
if ! is_number "$soc"; then
    warn "Exporter reports no usable battery SoC ('${soc}'; telemetry source down); no load-shedding decision made."
    exit 0
fi
if ! is_number "$coolant_temp"; then
    coolant_temp=""
fi

log "Telemetry status: Battery SoC = ${soc}%, Coolant Temp = ${coolant_temp:-n/a}°C"

# Critical check: low battery or excessive thermal load. A missing coolant
# reading triggers nothing by itself.
is_low_soc=$(awk -v s="$soc" -v t="$SOC_THRESHOLD_LOW" 'BEGIN {print (s < t) ? 1 : 0}')
is_high_temp=0
if [[ -n "$coolant_temp" ]]; then
    is_high_temp=$(awk -v temp="$coolant_temp" -v max="$TEMP_COOLANT_MAX" 'BEGIN {print (temp > max) ? 1 : 0}')
fi

if [[ "$is_low_soc" -eq 1 || "$is_high_temp" -eq 1 ]]; then
    warn "⚠️ Load-shedding condition met (SoC: ${soc}%, Temp: ${coolant_temp:-n/a}°C)"
    shed
else
    is_normal=$(awk -v s="$soc" -v t="$SOC_THRESHOLD_NORMAL" 'BEGIN {print (s >= t) ? 1 : 0}')
    if [[ "$is_normal" -eq 1 ]]; then
        log "Power & thermal levels normal. Standard operating profile active."
        resume
    else
        log "SoC between ${SOC_THRESHOLD_LOW}% and ${SOC_THRESHOLD_NORMAL}%: holding the current state."
    fi
fi
