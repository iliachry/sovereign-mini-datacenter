#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Load-Shedding Sentinel (one pass per run)
#
# Reads the power exporter's /metrics once. Two independent causes can shed:
#   soc      battery SoC < SHEDDING_L3_SOC (default 20 %): shedding level 3 or 4
#            of the node's one SoC policy (levels 1-4 start below 50 / 30 / 20 /
#            10 %; the same SHEDDING_L*_SOC variables and defaults as
#            SovereignConfig and the sentinel copilot)
#   thermal  coolant > 60 °C
# A cause is only ever raised by a present, numeric reading; a missing or
# non-numeric metric raises nothing by itself.
#
# Resuming is the opposite: it needs PROOF. The pause marker records why the
# workload was paused, and every recorded cause must be verifiably over:
#   soc      a numeric SoC >= SHEDDING_L1_SOC (default 50 %), i.e. back at
#            shedding level 0                       (hysteresis 20 -> 50)
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
#   LOAD_SHED_LOCK_TIMEOUT seconds to wait for an overlapping LOAD_SHED_ACTION=pause
#                          run before skipping (default 30)
#   SHEDDING_L3_SOC        SoC % below which the soc cause is raised (default 20)
#   SHEDDING_L1_SOC        SoC % at or above which it is over        (default 50)
#   SHEDDING_L2_SOC, SHEDDING_L4_SOC  (defaults 30, 10) are not used for a
#                          decision here, but the four form ONE policy: all must
#                          be numbers within 0-100 with L1 > L2 > L3 > L4, else
#                          the script exits 2 without deciding. The Python
#                          components (smdc, sentinel, power exporter) apply
#                          the same test but, being long-running, log one
#                          warning and use all four defaults instead, so a
#                          broken policy is never half-applied anywhere. An
#                          unset or empty variable stands for its default in
#                          both. This script accepts plain decimals only
#                          (25, 27.5); the Python components also accept
#                          what float() does (1e1, +25, surrounding blanks).
#
# Docker state: a pause is only given up on a DEFINITE answer from a reachable
# daemon (the container is running / exited / does not exist). When docker is
# missing or `docker inspect` fails for any other reason the state is unknown,
# which means HOLD: the marker is kept, so the pause is still resumed later.
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
LOCK_TIMEOUT="${LOAD_SHED_LOCK_TIMEOUT:-30}"
SOC_THRESHOLD_LOW="${SHEDDING_L3_SOC:-20}"
SOC_THRESHOLD_NORMAL="${SHEDDING_L1_SOC:-50}"
SOC_THRESHOLD_L2="${SHEDDING_L2_SOC:-30}"
SOC_THRESHOLD_L4="${SHEDDING_L4_SOC:-10}"
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

if ! is_number "$SOC_THRESHOLD_NORMAL" || ! is_number "$SOC_THRESHOLD_L2" ||
    ! is_number "$SOC_THRESHOLD_LOW" || ! is_number "$SOC_THRESHOLD_L4" ||
    ! num_test "$SOC_THRESHOLD_NORMAL" "<=" 100 ||
    ! num_test "$SOC_THRESHOLD_NORMAL" ">" "$SOC_THRESHOLD_L2" ||
    ! num_test "$SOC_THRESHOLD_L2" ">" "$SOC_THRESHOLD_LOW" ||
    ! num_test "$SOC_THRESHOLD_LOW" ">" "$SOC_THRESHOLD_L4" ||
    ! num_test "$SOC_THRESHOLD_L4" ">=" 0; then
    warn "SHEDDING_L1_SOC ('${SOC_THRESHOLD_NORMAL}'), SHEDDING_L2_SOC ('${SOC_THRESHOLD_L2}'), SHEDDING_L3_SOC ('${SOC_THRESHOLD_LOW}') and SHEDDING_L4_SOC ('${SOC_THRESHOLD_L4}') must be numbers within 0-100 with SHEDDING_L1_SOC > SHEDDING_L2_SOC > SHEDDING_L3_SOC > SHEDDING_L4_SOC; no load-shedding decision made."
    exit 2
fi

if ! is_number "$LOCK_TIMEOUT" || ! num_test "$LOCK_TIMEOUT" ">" 0; then
    warn "LOAD_SHED_LOCK_TIMEOUT ('${LOCK_TIMEOUT}') must be a positive number; no load-shedding decision made."
    exit 2
fi

LOCK_FILE="${STATE_FILE}.lock"
LOCK_HELD=0

process_alive() {
    local pid="$1" err=""
    [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] || return 1
    if kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    err="$(kill -0 "$pid" 2>&1 || true)"
    case "$err" in
        *"not permitted"* | *"Not permitted"* | *"EPERM"*) return 0 ;;
        *) return 1 ;;
    esac
}

release_run_lock() {
    if [[ "$LOCK_HELD" -eq 1 ]]; then
        if [[ "$(readlink "$LOCK_FILE" 2>/dev/null || true)" == "$$" ]]; then
            rm -f "$LOCK_FILE"
        fi
        LOCK_HELD=0
    fi
}

acquire_run_lock() {
    local max_ticks waited_ticks=0 holder_pid="" reap_pid="" now_pid=""
    mkdir -p "$(dirname "$LOCK_FILE")"
    max_ticks="$(awk -v t="$LOCK_TIMEOUT" 'BEGIN { m = int(t / 0.05 + 0.999999); print (m < 1 ? 1 : m) }')"
    while true; do
        if ln -s "$$" "$LOCK_FILE" 2>/dev/null; then
            LOCK_HELD=1
            return 0
        fi
        holder_pid="$(readlink "$LOCK_FILE" 2>/dev/null || true)"
        if [[ -z "$holder_pid" ]] || ! process_alive "$holder_pid"; then
            if [[ -L "${LOCK_FILE}.reap" ]]; then
                reap_pid="$(readlink "${LOCK_FILE}.reap" 2>/dev/null || true)"
                if [[ -n "$reap_pid" ]] && ! process_alive "$reap_pid"; then
                    rm -f "${LOCK_FILE}.reap"
                fi
            fi
            if ln -s "$$" "${LOCK_FILE}.reap" 2>/dev/null; then
                now_pid="$(readlink "$LOCK_FILE" 2>/dev/null || true)"
                if [[ -n "$now_pid" && "$now_pid" == "$holder_pid" ]] && ! process_alive "$now_pid"; then
                    rm -rf "$LOCK_FILE"
                elif [[ -z "$now_pid" && -e "$LOCK_FILE" && ! -L "$LOCK_FILE" ]]; then
                    rm -rf "$LOCK_FILE"
                fi
                rm -f "${LOCK_FILE}.reap"
                continue
            fi
        fi
        if [[ "$waited_ticks" -ge "$max_ticks" ]]; then
            warn "Timed out after ${LOCK_TIMEOUT}s waiting for active load_shedder.sh lock (${LOCK_FILE}, PID ${holder_pid:-unknown}); no load-shedding decision made (any existing pause is held)."
            exit 0
        fi
        sleep 0.05
        waited_ticks=$((waited_ticks + 1))
    done
}

# Docker state of the container. Read-only. Prints exactly one word:
#   running | paused | exited | ...  the daemon's State.Status
#   absent    a reachable daemon answered that the container does not exist
#   unknown   docker is not installed, the daemon did not answer, or inspect
#             failed for any other reason: NOT evidence that a pause is over
container_status() {
    local out err rc=0
    command -v docker >/dev/null 2>&1 || { printf 'unknown'; return 0; }
    err="$(mktemp "${TMPDIR:-/tmp}/load_shedder.XXXXXX")" || { printf 'unknown'; return 0; }
    out="$(docker inspect --format '{{.State.Status}}' "$1" 2>"$err")" || rc=$?
    if [[ "$rc" -eq 0 && -n "$out" ]]; then
        printf '%s' "$out"
    elif [[ "$rc" -ne 0 ]] && grep -qiE 'no such (container|object)' "$err" &&
        ! grep -qiE 'cannot connect|failed to connect|dial unix' "$err"; then
        printf 'absent'
    else
        printf 'unknown'
    fi
    rm -f "$err"
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
        absent)
            warn "Container ${CONTAINER} not found; nothing to shed."
            ;;
        unknown)
            warn "Cannot determine the state of ${CONTAINER} (docker unavailable or 'docker inspect' failed); nothing was shed on this run."
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
    elif [[ "$status" == "unknown" ]]; then
        # Not a definite answer: giving the marker up here would orphan a
        # container that is in fact still paused.
        warn "Recovery verified, but the state of ${marker_container} cannot be determined (docker unavailable or 'docker inspect' failed); holding the pause marker and retrying on the next run."
        return 0
    else
        log "${marker_container} is no longer paused (state: ${status}); clearing the sentinel's marker."
    fi
    rm -f "$STATE_FILE"
}

# -- Serialize pause-mode runs across telemetry, marker and docker ------
if [[ "$ACTION" == "pause" ]]; then
    trap release_run_lock EXIT
    trap 'release_run_lock; exit 130' INT
    trap 'release_run_lock; exit 143' TERM
    acquire_run_lock
fi

# -- Fetch metrics ------------------------------------------------------
if ! metrics=$(curl -s --connect-timeout 5 "$METRICS_URL"); then
    warn "Failed to reach telemetry exporter at ${METRICS_URL}; no load-shedding decision made (any existing pause is held)."
    exit 0
fi

# A missing metric must fall through to the checks below, not abort the
# script (`set -euo pipefail`): awk prints nothing and exits 0 on no match.
# One awk reading a here-string: no `producer | head` pipeline that could die
# of SIGPIPE under pipefail.
source_up=$(awk '$1 == "sovereign_telemetry_source_up" { print $2; exit }' <<<"$metrics" || true)
if [[ "$source_up" == "0" || "$source_up" == "0.0" ]]; then
    warn "Exporter reports sovereign_telemetry_source_up 0 (no telemetry source); no load-shedding decision made (any existing pause is held)."
    exit 0
fi
soc=$(awk '$1 == "sovereign_battery_soc_percent" { print $2; exit }' <<<"$metrics" || true)
coolant_temp=$(awk '$1 == "sovereign_temp_coolant_celsius" { print $2; exit }' <<<"$metrics" || true)

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
