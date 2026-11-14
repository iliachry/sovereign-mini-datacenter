#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Load-Shedding Sentinel (one pass per run)
#
# Reads the power exporter's /metrics once. Two independent causes can shed:
#   soc      battery SoC < 20 %
#   thermal  coolant > 60 °C
# A cause is only ever raised by a present, numeric reading; a missing or
# non-numeric metric raises nothing by itself.
#
# Resuming is the opposite: it needs PROOF. The pause marker records why the
# workload was paused, and every recorded cause must be verifiably over:
#   soc      a numeric SoC >= 40 %                  (hysteresis 20 -> 40)
#   thermal  a numeric coolant reading <= 55 °C     (hysteresis 60 -> 55)
# A missing or non-numeric metric that a recorded cause depends on means HOLD:
# a workload paused for overheating stays paused while its temperature sensor
# is silent. A cause that appears while the workload is already paused is added
# to the marker, so it has to clear as well.
#
# Run it from cron or a systemd timer (e.g. every minute).
#
# LOAD_SHED_ACTION selects what the decision does:
#   log    (default) only logs the decision. It NEVER calls `docker pause` or
#          `docker unpause`. If a marker from an earlier pause-mode run exists,
#          it is left in place (and the container stays paused); the log says
#          how to clear it.
#   pause  `docker pause`s LOAD_SHED_CONTAINER (default: sovereign_ollama, the
#          GPU inference server of the core stack) and `docker unpause`s it on
#          verified recovery. A paused container keeps its memory and loaded
#          models but gets no CPU/GPU time; requests to it hang until it is
#          resumed, and its healthcheck reports unhealthy meanwhile. The
#          sentinel resumes only a container it paused itself (recorded in
#          LOAD_SHED_STATE_FILE) and never pauses twice.
#
# Environment:
#   TELEMETRY_METRICS_URL  exporter URL   (default http://localhost:9101/metrics)
#   LOAD_SHED_ACTION       log | pause    (default log)
#   LOAD_SHED_CONTAINER    container name (default sovereign_ollama)
#   LOAD_SHED_STATE_FILE   marker written while this script holds the pause
#                          (default ${XDG_STATE_HOME:-$HOME/.local/state}/sovereign-dc/load_shedder.paused)
#
# Marker format (text):
#   container=<name>
#   cause=soc          one line per recorded cause
#   cause=thermal
# A marker written by an older release (a bare container name) records no
# cause; it is treated as "both", so both recoveries must be proven.
# ====================================================================

set -euo pipefail

METRICS_URL="${TELEMETRY_METRICS_URL:-http://localhost:9101/metrics}"
ACTION="${LOAD_SHED_ACTION:-log}"
CONTAINER="${LOAD_SHED_CONTAINER:-sovereign_ollama}"
STATE_FILE="${LOAD_SHED_STATE_FILE:-${XDG_STATE_HOME:-${HOME:-/var/lib}/.local/state}/sovereign-dc/load_shedder.paused}"
SOC_THRESHOLD_LOW=20.0
SOC_THRESHOLD_NORMAL=40.0
TEMP_COOLANT_MAX=60.0
TEMP_COOLANT_RESUME=55.0

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

# num_test <a> <op> <b>: exit 0 when the comparison holds. Callers pass
# validated numbers only.
num_test() {
    awk -v a="$1" -v b="$3" "BEGIN { exit !(a $2 b) }"
}

# Docker state of the container: running, paused, exited, ... or empty when
# docker is unavailable or the container does not exist. Read-only.
container_status() {
    command -v docker >/dev/null 2>&1 || return 0
    docker inspect --format '{{.State.Status}}' "$1" 2>/dev/null || true
}

# -- Pause marker ------------------------------------------------------
marker_container=""
marker_soc=0
marker_thermal=0

read_marker() {
    marker_container=""
    marker_soc=0
    marker_thermal=0
    [[ -f "$STATE_FILE" ]] || return 0
    local line seen_key=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            container=*) marker_container="${line#container=}"; seen_key=1 ;;
            cause=soc) marker_soc=1; seen_key=1 ;;
            cause=thermal) marker_thermal=1; seen_key=1 ;;
            "") ;;
            *)
                # Legacy marker: the first line is the bare container name.
                if [[ "$seen_key" -eq 0 && -z "$marker_container" ]]; then
                    marker_container="$line"
                fi
                ;;
        esac
    done <"$STATE_FILE"
    [[ -n "$marker_container" ]] || marker_container="$CONTAINER"
    # No recorded cause (legacy or damaged marker): assume both.
    if [[ "$marker_soc" -eq 0 && "$marker_thermal" -eq 0 ]]; then
        marker_soc=1
        marker_thermal=1
    fi
}

# write_marker <container> <soc 0|1> <thermal 0|1>  (atomic replace)
write_marker() {
    local dir tmp
    dir="$(dirname "$STATE_FILE")"
    mkdir -p "$dir"
    tmp="${STATE_FILE}.tmp.$$"
    {
        printf 'container=%s\n' "$1"
        if [[ "$2" -eq 1 ]]; then printf 'cause=soc\n'; fi
        if [[ "$3" -eq 1 ]]; then printf 'cause=thermal\n'; fi
    } >"$tmp"
    mv -f "$tmp" "$STATE_FILE"
}

causes_text() { # causes_text <soc 0|1> <thermal 0|1>
    local out=""
    if [[ "$1" -eq 1 ]]; then out="soc"; fi
    if [[ "$2" -eq 1 ]]; then out="${out:+${out}+}thermal"; fi
    printf '%s' "${out:-none}"
}

# Log-only mode never touches docker. Say so when an earlier pause-mode run
# left a paused workload behind, because this mode will not resume it.
note_leftover_marker() {
    [[ -f "$STATE_FILE" ]] || return 0
    read_marker
    warn "A pause marker from an earlier LOAD_SHED_ACTION=pause run exists (${STATE_FILE}: ${marker_container}, paused for $(causes_text "$marker_soc" "$marker_thermal")). LOAD_SHED_ACTION=log leaves it and the container untouched. To clear it: run with LOAD_SHED_ACTION=pause (resumes after verified recovery), or by hand: docker unpause ${marker_container} && rm -f '${STATE_FILE}'."
}

# -- Shed: at least one cause is active --------------------------------
shed() { # shed <soc active 0|1> <thermal active 0|1>
    local active_soc="$1" active_thermal="$2" status
    if [[ "$ACTION" != "pause" ]]; then
        log "LOAD_SHED_ACTION=log: decision logged only, no workload was touched (set LOAD_SHED_ACTION=pause to pause ${CONTAINER})."
        note_leftover_marker
        return 0
    fi
    status="$(container_status "$CONTAINER")"
    case "$status" in
        running)
            if docker pause "$CONTAINER" >/dev/null; then
                write_marker "$CONTAINER" "$active_soc" "$active_thermal"
                log "Paused ${CONTAINER} (cause: $(causes_text "$active_soc" "$active_thermal")). It is resumed only after every recorded cause has verifiably cleared (SoC >= ${SOC_THRESHOLD_NORMAL}%; coolant reading <= ${TEMP_COOLANT_RESUME}°C)."
            else
                warn "docker pause ${CONTAINER} failed; nothing was shed."
            fi
            ;;
        paused)
            if [[ -f "$STATE_FILE" ]]; then
                read_marker
                if [[ "$marker_container" != "$CONTAINER" ]]; then
                    log "${CONTAINER} is already paused, but the sentinel's marker names ${marker_container}; leaving both alone."
                    return 0
                fi
                # Remember a cause that appeared while already paused.
                local new_soc=$((marker_soc | active_soc)) new_thermal=$((marker_thermal | active_thermal))
                if [[ "$new_soc" -ne "$marker_soc" || "$new_thermal" -ne "$marker_thermal" ]]; then
                    write_marker "$CONTAINER" "$new_soc" "$new_thermal"
                    log "${CONTAINER} is still paused by this sentinel; recorded an additional cause (now: $(causes_text "$new_soc" "$new_thermal"))."
                else
                    log "${CONTAINER} is still paused by this sentinel (cause: $(causes_text "$new_soc" "$new_thermal"))."
                fi
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

# -- Resume: no cause is active. Undo our own pause, and only our own, and
# only when every recorded cause is proven to be over. ------------------
maybe_resume() {
    [[ -f "$STATE_FILE" ]] || return 1
    if [[ "$ACTION" != "pause" ]]; then
        note_leftover_marker
        return 0
    fi
    read_marker
    local hold=""
    if [[ "$marker_soc" -eq 1 ]]; then
        if [[ -z "$soc" ]]; then
            hold="paused for low SoC and there is no usable SoC reading"
        elif ! num_test "$soc" ">=" "$SOC_THRESHOLD_NORMAL"; then
            hold="paused for low SoC and SoC ${soc}% is still below ${SOC_THRESHOLD_NORMAL}%"
        fi
    fi
    if [[ "$marker_thermal" -eq 1 ]]; then
        if [[ -z "$coolant_temp" ]]; then
            hold="${hold:+${hold}; }paused for overheating and there is no usable coolant reading"
        elif ! num_test "$coolant_temp" "<=" "$TEMP_COOLANT_RESUME"; then
            hold="${hold:+${hold}; }paused for overheating and coolant ${coolant_temp}°C is still above ${TEMP_COOLANT_RESUME}°C"
        fi
    fi
    if [[ -n "$hold" ]]; then
        log "Holding the pause of ${marker_container}: ${hold}."
        return 0
    fi

    local status
    status="$(container_status "$marker_container")"
    if [[ "$status" == "paused" ]]; then
        if docker unpause "$marker_container" >/dev/null; then
            log "Recovery verified (cause was: $(causes_text "$marker_soc" "$marker_thermal")). Resumed ${marker_container}."
        else
            warn "docker unpause ${marker_container} failed; will retry on the next run."
            return 0
        fi
    else
        log "${marker_container} is no longer paused (state: ${status:-unknown}); clearing the sentinel's marker."
    fi
    rm -f "$STATE_FILE"
}

# -- Fetch metrics ------------------------------------------------------
if ! metrics=$(curl -s --connect-timeout 5 "$METRICS_URL"); then
    warn "Failed to reach telemetry exporter at ${METRICS_URL}; no load-shedding decision made (any existing pause is held)."
    exit 0
fi

# `|| true`: a missing metric must fall through to the checks below, not
# abort the script (grep exits 1 on no match under `set -euo pipefail`).
soc=$(echo "$metrics" | grep "^sovereign_battery_soc_percent " | awk '{print $2}' | head -n 1 || true)
coolant_temp=$(echo "$metrics" | grep "^sovereign_temp_coolant_celsius " | awk '{print $2}' | head -n 1 || true)

# Never substitute a healthy value for a missing measurement (the exporter has
# no source, or reports NaN): an unusable reading is "unknown", which can
# neither raise a cause nor prove a recovery.
soc_raw="$soc"
if ! is_number "$soc"; then soc=""; fi
if ! is_number "$coolant_temp"; then coolant_temp=""; fi

if [[ -z "$soc" && -z "$coolant_temp" ]]; then
    warn "Exporter reports neither a usable battery SoC ('${soc_raw}') nor a coolant temperature (telemetry source down); no load-shedding decision made (any existing pause is held)."
    exit 0
fi
if [[ -z "$soc" ]]; then
    warn "Exporter reports no usable battery SoC ('${soc_raw}'); deciding on coolant temperature alone."
fi

log "Telemetry status: Battery SoC = ${soc:-n/a}%, Coolant Temp = ${coolant_temp:-n/a}°C"

is_low_soc=0
is_high_temp=0
if [[ -n "$soc" ]] && num_test "$soc" "<" "$SOC_THRESHOLD_LOW"; then is_low_soc=1; fi
if [[ -n "$coolant_temp" ]] && num_test "$coolant_temp" ">" "$TEMP_COOLANT_MAX"; then is_high_temp=1; fi

if [[ "$is_low_soc" -eq 1 || "$is_high_temp" -eq 1 ]]; then
    warn "⚠️ Load-shedding condition met: $(causes_text "$is_low_soc" "$is_high_temp") (SoC: ${soc:-n/a}%, Temp: ${coolant_temp:-n/a}°C)"
    shed "$is_low_soc" "$is_high_temp"
elif maybe_resume; then
    :
elif [[ -n "$soc" ]] && num_test "$soc" ">=" "$SOC_THRESHOLD_NORMAL"; then
    log "Power & thermal levels normal. Standard operating profile active."
else
    log "No load-shedding condition; SoC ${soc:-n/a}% is not at or above ${SOC_THRESHOLD_NORMAL}%: holding the current state."
fi
