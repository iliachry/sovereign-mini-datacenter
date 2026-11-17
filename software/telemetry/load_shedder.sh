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

current_boot_id() {
    local b=""
    if [[ -r /proc/sys/kernel/random/boot_id ]]; then
        IFS= read -r b </proc/sys/kernel/random/boot_id || b=""
        b="${b//[^A-Za-z0-9-]/}"
    elif command -v sysctl >/dev/null 2>&1; then
        b="$(sysctl -n kern.boottime 2>/dev/null || true)"
        if [[ "$b" =~ sec[[:space:]]*=[[:space:]]*([0-9]+) ]]; then
            b="mac-${BASH_REMATCH[1]}"
        else
            b=""
        fi
    elif [[ -x /usr/sbin/sysctl ]]; then
        b="$(/usr/sbin/sysctl -n kern.boottime 2>/dev/null || true)"
        if [[ "$b" =~ sec[[:space:]]*=[[:space:]]*([0-9]+) ]]; then
            b="mac-${BASH_REMATCH[1]}"
        else
            b=""
        fi
    fi
    printf '%s' "$b"
}

BOOT_ID="$(current_boot_id)"
LOCK_FILE="${STATE_FILE}.lock"
PENDING_FILE="${STATE_FILE}.pending"
LOCK_VALUE="$$:${RANDOM:-0}.${SECONDS:-0}${BOOT_ID:+:boot=${BOOT_ID}}"
SELF_REAP_VALUE="$$${BOOT_ID:+:boot=${BOOT_ID}}"
LOCK_HELD=0
LOCK_TICKS_USED=0
PENDING_WORKER_INFLIGHT=0
REAP_CLAIM=""
REAP_GUARD=""

extract_lock_pid() {
    local raw="${1:-}" pid=""
    pid="${raw%%:*}"
    if [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]]; then
        printf '%s' "$pid"
    fi
}

extract_lock_boot() {
    local raw="${1:-}"
    if [[ "$raw" == *":boot="* ]]; then
        printf '%s' "${raw##*:boot=}"
    fi
}

sanitize_lock_tag() {
    local raw="${1:-}" clean=""
    clean="${raw//[^A-Za-z0-9._-]/_}"
    printf '%s' "${clean:-empty}"
}

process_alive() {
    local pid="$1" err="" alive=0 pstat="" pstate="" ps_stat=""
    [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] || return 1
    if kill -0 "$pid" 2>/dev/null; then
        alive=1
    else
        err="$(kill -0 "$pid" 2>&1 || true)"
        case "$err" in
            *"not permitted"* | *"Not permitted"* | *"EPERM"*) alive=1 ;;
        esac
    fi
    [[ "$alive" -eq 1 ]] || return 1
    if [[ -r "/proc/${pid}/stat" ]]; then
        if { IFS= read -r pstat <"/proc/${pid}/stat"; } 2>/dev/null; then
            pstate="${pstat##*) }"
            case "$pstate" in
                Z* | X*) return 1 ;;
            esac
        fi
    elif command -v ps >/dev/null 2>&1; then
        ps_stat="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
        ps_stat="${ps_stat#"${ps_stat%%[![:space:]]*}"}"
        case "$ps_stat" in
            Z*) return 1 ;;
        esac
    fi
    return 0
}

lock_value_alive() {
    local raw="${1:-}" pid="" holder_boot=""
    pid="$(extract_lock_pid "$raw")"
    [[ -n "$pid" ]] || return 1
    holder_boot="$(extract_lock_boot "$raw")"
    if [[ -n "$holder_boot" && -n "$BOOT_ID" && "$holder_boot" != "$BOOT_ID" ]]; then
        return 1
    fi
    process_alive "$pid"
}

cleanup_reap_links() {
    rm -f "${LOCK_FILE}.reap.$$" "${LOCK_FILE}.claim.$$"
    if [[ -n "$REAP_GUARD" && "$(readlink "$REAP_GUARD" 2>/dev/null || true)" == "$SELF_REAP_VALUE" ]]; then
        rm -f "$REAP_GUARD"
    fi
    REAP_GUARD=""
    if [[ -n "$REAP_CLAIM" && "$(readlink "$REAP_CLAIM" 2>/dev/null || true)" == "$SELF_REAP_VALUE" ]]; then
        rm -f "$REAP_CLAIM"
    fi
    REAP_CLAIM=""
}

release_run_lock() {
    rm -f "${PENDING_FILE}.go.$$" "${STATE_FILE}.tmp.$$" "${PENDING_FILE}.tmp.$$"
    cleanup_reap_links
    if [[ "$LOCK_HELD" -eq 1 ]]; then
        if [[ "$(readlink "$LOCK_FILE" 2>/dev/null || true)" == "$LOCK_VALUE" ]]; then
            rm -f "$LOCK_FILE"
        fi
        LOCK_HELD=0
    fi
}

try_reap_stale_lock() {
    local holder_raw="$1" holder_tag="" tk_raw=""
    local rc_link="" rc_try=0 rc_claimed=0 prev_link="" prev_raw="" rp_raw=""
    local now_raw=""
    holder_tag="$(sanitize_lock_tag "$holder_raw")"

    if ln -s "$SELF_REAP_VALUE" "${LOCK_FILE}.reap" 2>/dev/null; then
        REAP_CLAIM="${LOCK_FILE}.reap"
    else
        if [[ ! -L "${LOCK_FILE}.reap" && -e "${LOCK_FILE}.reap" ]]; then
            if ln -s "$SELF_REAP_VALUE" "${LOCK_FILE}.reap.dir.${holder_tag}" 2>/dev/null; then
                REAP_GUARD="${LOCK_FILE}.reap.dir.${holder_tag}"
                if [[ ! -L "${LOCK_FILE}.reap" && -e "${LOCK_FILE}.reap" ]]; then
                    rm -rf "${LOCK_FILE}.reap"
                fi
                cleanup_reap_links
            fi
            return 1
        fi
        tk_raw="$(readlink "${LOCK_FILE}.reap" 2>/dev/null || true)"
        if [[ -z "$tk_raw" ]] || lock_value_alive "$tk_raw"; then
            return 1
        fi
        rc_link="${LOCK_FILE}.reap.guard.${holder_tag}.$(sanitize_lock_tag "$tk_raw")"
        prev_link="${LOCK_FILE}.reap"
        prev_raw="$tk_raw"
        while [[ "$rc_try" -lt 4 ]]; do
            if ln -s "$SELF_REAP_VALUE" "$rc_link" 2>/dev/null; then
                REAP_GUARD="$rc_link"
                rc_claimed=1
                break
            fi
            rp_raw="$(readlink "$rc_link" 2>/dev/null || true)"
            if [[ -z "$rp_raw" ]] || lock_value_alive "$rp_raw"; then
                break
            fi
            prev_link="$rc_link"
            prev_raw="$rp_raw"
            rc_link="${rc_link}.$(sanitize_lock_tag "$rp_raw")"
            rc_try=$((rc_try + 1))
        done
        if [[ "$rc_claimed" -ne 1 ]]; then
            return 1
        fi
        if [[ "$(readlink "$LOCK_FILE" 2>/dev/null || true)" != "$holder_raw" ]] ||
            [[ ! -L "${LOCK_FILE}.reap" ]] ||
            [[ "$(readlink "${LOCK_FILE}.reap" 2>/dev/null || true)" != "$tk_raw" ]] ||
            [[ ! -L "$prev_link" ]] ||
            [[ "$(readlink "$prev_link" 2>/dev/null || true)" != "$prev_raw" ]]; then
            cleanup_reap_links
            return 1
        fi
        rm -f "${LOCK_FILE}.reap.$$"
        if [[ -L "${LOCK_FILE}.reap" && -d "${LOCK_FILE}.reap" ]]; then
            rm -f "${LOCK_FILE}.reap"
        fi
        if ! ln -s "$SELF_REAP_VALUE" "${LOCK_FILE}.reap.$$" 2>/dev/null ||
            ! mv -f "${LOCK_FILE}.reap.$$" "${LOCK_FILE}.reap" 2>/dev/null; then
            cleanup_reap_links
            return 1
        fi
        REAP_CLAIM="${LOCK_FILE}.reap"
    fi

    if [[ ! -L "${LOCK_FILE}.reap" ]] || [[ "$(readlink "${LOCK_FILE}.reap" 2>/dev/null || true)" != "$SELF_REAP_VALUE" ]]; then
        cleanup_reap_links
        return 1
    fi

    now_raw="$(readlink "$LOCK_FILE" 2>/dev/null || true)"
    if [[ -n "$holder_raw" ]]; then
        if [[ ! -L "$LOCK_FILE" ]] || [[ "$now_raw" != "$holder_raw" ]] || lock_value_alive "$now_raw"; then
            cleanup_reap_links
            return 1
        fi
        if [[ -L "$LOCK_FILE" && -d "$LOCK_FILE" ]]; then
            rm -f "$LOCK_FILE"
        fi
    else
        if [[ -L "$LOCK_FILE" || ! -e "$LOCK_FILE" ]]; then
            cleanup_reap_links
            return 1
        fi
        rm -rf "$LOCK_FILE"
    fi

    rm -f "${LOCK_FILE}.claim.$$"
    if ! ln -s "$LOCK_VALUE" "${LOCK_FILE}.claim.$$" 2>/dev/null ||
        ! mv -f "${LOCK_FILE}.claim.$$" "$LOCK_FILE" 2>/dev/null; then
        cleanup_reap_links
        return 1
    fi
    if [[ "$(readlink "$LOCK_FILE" 2>/dev/null || true)" != "$LOCK_VALUE" ]]; then
        cleanup_reap_links
        return 1
    fi

    LOCK_HELD=1
    rm -f "${LOCK_FILE}.reap.guard."* "${LOCK_FILE}.reap.dir."*
    cleanup_reap_links
    return 0
}

acquire_run_lock() {
    local max_ticks waited_ticks=0 holder_raw="" holder_pid=""
    LOCK_TICKS_USED=0
    mkdir -p "$(dirname "$LOCK_FILE")"
    max_ticks="$(awk -v t="$LOCK_TIMEOUT" 'BEGIN { m = int(t / 0.05 + 0.999999); print (m < 1 ? 1 : m) }')"
    while true; do
        if ln -s "$LOCK_VALUE" "$LOCK_FILE" 2>/dev/null; then
            LOCK_HELD=1
            LOCK_TICKS_USED="$waited_ticks"
            return 0
        fi
        holder_raw="$(readlink "$LOCK_FILE" 2>/dev/null || true)"
        if { [[ -n "$holder_raw" ]] && ! lock_value_alive "$holder_raw"; } ||
            { [[ -z "$holder_raw" ]] && [[ ! -L "$LOCK_FILE" && -e "$LOCK_FILE" ]]; }; then
            if try_reap_stale_lock "$holder_raw"; then
                LOCK_TICKS_USED="$waited_ticks"
                return 0
            fi
            holder_raw="$(readlink "$LOCK_FILE" 2>/dev/null || true)"
        fi
        holder_pid="$(extract_lock_pid "$holder_raw")"
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

parsed_container=""
parsed_soc=0
parsed_thermal=0
parsed_worker=""

read_marker_file() {
    local path="$1" line seen_key=0
    parsed_container=""
    parsed_soc=0
    parsed_thermal=0
    parsed_worker=""
    [[ -f "$path" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            container=*) parsed_container="${line#container=}"; seen_key=1 ;;
            cause=soc) parsed_soc=1; seen_key=1 ;;
            cause=thermal) parsed_thermal=1; seen_key=1 ;;
            worker=*) parsed_worker="${line#worker=}"; seen_key=1 ;;
            "") ;;
            *)
                # Legacy marker: the first line is the bare container name.
                if [[ "$seen_key" -eq 0 && -z "$parsed_container" ]]; then
                    parsed_container="$line"
                fi
                ;;
        esac
    done <"$path"
    [[ -n "$parsed_container" ]] || parsed_container="$CONTAINER"
    # No recorded cause (legacy or damaged marker): assume both.
    if [[ "$parsed_soc" -eq 0 && "$parsed_thermal" -eq 0 ]]; then
        parsed_soc=1
        parsed_thermal=1
    fi
}

read_marker() {
    read_marker_file "$STATE_FILE"
    marker_container="$parsed_container"
    marker_soc="$parsed_soc"
    marker_thermal="$parsed_thermal"
}

# write_marker_file <path> <container> <soc 0|1> <thermal 0|1> [worker]  (atomic replace)
write_marker_file() {
    local path="$1" container="$2" w_soc="$3" w_thermal="$4" w_worker="${5:-}" dir tmp
    dir="$(dirname "$path")"
    mkdir -p "$dir"
    tmp="${path}.tmp.$$"
    {
        printf 'container=%s\n' "$container"
        if [[ "$w_soc" -eq 1 ]]; then printf 'cause=soc\n'; fi
        if [[ "$w_thermal" -eq 1 ]]; then printf 'cause=thermal\n'; fi
        if [[ -n "$w_worker" ]]; then printf 'worker=%s\n' "$w_worker"; fi
    } >"$tmp"
    mv -f "$tmp" "$path"
}

# write_marker <container> <soc 0|1> <thermal 0|1>  (atomic replace)
write_marker() {
    write_marker_file "$STATE_FILE" "$1" "$2" "$3"
}

causes_text() { # causes_text <soc 0|1> <thermal 0|1>
    local out=""
    if [[ "$1" -eq 1 ]]; then out="soc"; fi
    if [[ "$2" -eq 1 ]]; then out="${out:+${out}+}thermal"; fi
    printf '%s' "${out:-none}"
}

# Reconcile an interrupted pre-pause intent written before `docker pause`.
reconcile_pending_pause() {
    PENDING_WORKER_INFLIGHT=0
    [[ -f "$PENDING_FILE" ]] || return 0
    read_marker_file "$PENDING_FILE"
    local pend_container="$parsed_container" pend_soc="$parsed_soc" pend_thermal="$parsed_thermal" pend_worker="$parsed_worker"
    if [[ -n "$pend_worker" ]] && lock_value_alive "$pend_worker"; then
        local max_ticks rem_ticks waited=0
        max_ticks="$(awk -v t="$LOCK_TIMEOUT" 'BEGIN { m = int(t / 0.05 + 0.999999); print (m < 1 ? 1 : m) }')"
        rem_ticks=$((max_ticks - LOCK_TICKS_USED))
        if [[ "$rem_ticks" -lt 1 ]]; then rem_ticks=1; fi
        while [[ "$waited" -lt "$rem_ticks" ]] && lock_value_alive "$pend_worker"; do
            sleep 0.05
            waited=$((waited + 1))
        done
        if lock_value_alive "$pend_worker"; then
            PENDING_WORKER_INFLIGHT=1
            warn "In-flight docker pause worker (PID $(extract_lock_pid "$pend_worker")) from ${PENDING_FILE} is still running after ${LOCK_TIMEOUT}s; keeping pending pause marker."
            return 0
        fi
    fi
    rm -f "${PENDING_FILE}.go."*
    if [[ ! -f "$PENDING_FILE" ]]; then
        return 0
    fi
    read_marker_file "$PENDING_FILE"
    pend_container="$parsed_container"
    pend_soc="$parsed_soc"
    pend_thermal="$parsed_thermal"
    if [[ -f "$STATE_FILE" ]]; then
        read_marker
        if [[ "$marker_container" == "$pend_container" ]]; then
            local merged_soc=$((marker_soc | pend_soc)) merged_thermal=$((marker_thermal | pend_thermal))
            if [[ "$merged_soc" -ne "$marker_soc" || "$merged_thermal" -ne "$marker_thermal" ]]; then
                write_marker "$marker_container" "$merged_soc" "$merged_thermal"
            fi
            rm -f "$PENDING_FILE"
            return 0
        fi
        local prev_status=""
        prev_status="$(container_status "$marker_container")"
        if [[ "$prev_status" == "paused" || "$prev_status" == "unknown" ]]; then
            rm -f "$PENDING_FILE"
            return 0
        fi
    fi
    local pstatus=""
    pstatus="$(container_status "$pend_container")"
    case "$pstatus" in
        paused)
            write_marker "$pend_container" "$pend_soc" "$pend_thermal"
            rm -f "$PENDING_FILE"
            log "Recovered interrupted sentinel pause marker for ${pend_container} (cause: $(causes_text "$pend_soc" "$pend_thermal"))."
            ;;
        unknown)
            warn "Cannot determine whether interrupted pause of ${pend_container} took effect (docker unavailable or 'docker inspect' failed); holding pending pause marker."
            ;;
        *)
            rm -f "$PENDING_FILE"
            ;;
    esac
}

# Log-only mode never touches docker. Say so when an earlier pause-mode run
# left a paused workload behind, because this mode will not resume it.
note_leftover_marker() {
    if [[ -f "$STATE_FILE" ]]; then
        read_marker
        warn "A pause marker from an earlier LOAD_SHED_ACTION=pause run exists (${STATE_FILE}: ${marker_container}, paused for $(causes_text "$marker_soc" "$marker_thermal")). LOAD_SHED_ACTION=log leaves it and the container untouched. To clear it: run with LOAD_SHED_ACTION=pause (resumes after verified recovery), or by hand: docker unpause ${marker_container} && rm -f '${STATE_FILE}'."
    elif [[ -f "$PENDING_FILE" ]]; then
        read_marker_file "$PENDING_FILE"
        warn "An interrupted pending pause marker from an earlier LOAD_SHED_ACTION=pause run exists (${PENDING_FILE}: ${parsed_container}, cause $(causes_text "$parsed_soc" "$parsed_thermal")). LOAD_SHED_ACTION=log leaves it and the container untouched. To reconcile it: run with LOAD_SHED_ACTION=pause, or remove '${PENDING_FILE}' after inspecting the container."
    fi
}

# -- Shed: at least one cause is active --------------------------------
shed() { # shed <soc active 0|1> <thermal active 0|1>
    local active_soc="$1" active_thermal="$2" status
    if [[ "$ACTION" != "pause" ]]; then
        log "LOAD_SHED_ACTION=log: decision logged only, no workload was touched (set LOAD_SHED_ACTION=pause to pause ${CONTAINER})."
        note_leftover_marker
        return 0
    fi
    if [[ "$PENDING_WORKER_INFLIGHT" -eq 1 && -f "$PENDING_FILE" ]]; then
        read_marker_file "$PENDING_FILE"
        if ! lock_value_alive "$parsed_worker"; then
            reconcile_pending_pause
        fi
    fi
    if [[ "$PENDING_WORKER_INFLIGHT" -eq 1 && -f "$PENDING_FILE" ]]; then
        read_marker_file "$PENDING_FILE"
        if [[ "$parsed_container" == "$CONTAINER" ]]; then
            local pend_new_soc=$((parsed_soc | active_soc)) pend_new_thermal=$((parsed_thermal | active_thermal))
            if [[ "$pend_new_soc" -ne "$parsed_soc" || "$pend_new_thermal" -ne "$parsed_thermal" ]]; then
                write_marker_file "$PENDING_FILE" "$CONTAINER" "$pend_new_soc" "$pend_new_thermal" "$parsed_worker"
                log "In-flight docker pause worker still active for ${CONTAINER}; recorded additional cause on pending marker (now: $(causes_text "$pend_new_soc" "$pend_new_thermal"))."
            else
                log "In-flight docker pause worker still active for ${CONTAINER} (cause: $(causes_text "$pend_new_soc" "$pend_new_thermal"))."
            fi
        else
            warn "In-flight docker pause worker still active for ${parsed_container}; leaving ${CONTAINER} untouched on this run."
        fi
        return 0
    fi
    status="$(container_status "$CONTAINER")"
    case "$status" in
        running)
            if [[ -f "$STATE_FILE" ]]; then
                read_marker
                if [[ "$marker_container" != "$CONTAINER" ]]; then
                    local prev_status=""
                    prev_status="$(container_status "$marker_container")"
                    if [[ "$prev_status" == "paused" || "$prev_status" == "unknown" ]]; then
                        warn "${CONTAINER} is running, but the sentinel's pause marker still holds ${marker_container} (state: ${prev_status}); leaving ${CONTAINER} and the marker alone."
                        return 0
                    fi
                    rm -f "$STATE_FILE"
                fi
            fi
            local parent_pid="$$" gate_file="${PENDING_FILE}.go.$$" pause_pid="" worker_val="" pause_rc=0
            rm -f "$gate_file"
            (
                while [[ ! -f "$gate_file" ]]; do
                    kill -0 "$parent_pid" 2>/dev/null || exit 1
                    sleep 0.01
                done
                rm -f "$gate_file"
                exec docker pause "$CONTAINER" >/dev/null
            ) &
            pause_pid=$!
            worker_val="${pause_pid}${BOOT_ID:+:boot=${BOOT_ID}}"
            write_marker_file "$PENDING_FILE" "$CONTAINER" "$active_soc" "$active_thermal" "$worker_val"
            : >"$gate_file"
            wait "$pause_pid" || pause_rc=$?
            rm -f "$gate_file"
            if [[ "$pause_rc" -eq 0 ]]; then
                write_marker "$CONTAINER" "$active_soc" "$active_thermal"
                rm -f "$PENDING_FILE"
                log "Paused ${CONTAINER} (cause: $(causes_text "$active_soc" "$active_thermal")). It is resumed only after every recorded cause has verifiably cleared (SoC >= ${SOC_THRESHOLD_NORMAL}%; coolant reading <= ${TEMP_COOLANT_RESUME}°C)."
            else
                local post_status=""
                post_status="$(container_status "$CONTAINER")"
                if [[ "$post_status" == "paused" ]]; then
                    write_marker "$CONTAINER" "$active_soc" "$active_thermal"
                    rm -f "$PENDING_FILE"
                    log "Paused ${CONTAINER} (cause: $(causes_text "$active_soc" "$active_thermal")). It is resumed only after every recorded cause has verifiably cleared (SoC >= ${SOC_THRESHOLD_NORMAL}%; coolant reading <= ${TEMP_COOLANT_RESUME}°C)."
                elif [[ "$post_status" == "unknown" ]]; then
                    warn "docker pause ${CONTAINER} failed and state is unknown; keeping pending pause marker (${PENDING_FILE}) to reconcile on the next run."
                else
                    rm -f "$PENDING_FILE"
                    warn "docker pause ${CONTAINER} failed; nothing was shed."
                fi
            fi
            ;;
        paused)
            if [[ ! -f "$STATE_FILE" && -f "$PENDING_FILE" ]]; then
                reconcile_pending_pause
            fi
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
            if [[ -f "$STATE_FILE" ]]; then
                read_marker
                if [[ "$marker_container" == "$CONTAINER" ]]; then
                    local new_soc=$((marker_soc | active_soc)) new_thermal=$((marker_thermal | active_thermal))
                    if [[ "$new_soc" -ne "$marker_soc" || "$new_thermal" -ne "$marker_thermal" ]]; then
                        write_marker "$CONTAINER" "$new_soc" "$new_thermal"
                        warn "Cannot determine the state of ${CONTAINER} (docker unavailable or 'docker inspect' failed); recorded additional cause on existing pause marker (now: $(causes_text "$new_soc" "$new_thermal"))."
                    else
                        warn "Cannot determine the state of ${CONTAINER} (docker unavailable or 'docker inspect' failed); holding existing pause marker (cause: $(causes_text "$new_soc" "$new_thermal"))."
                    fi
                    return 0
                fi
            elif [[ -f "$PENDING_FILE" ]]; then
                read_marker_file "$PENDING_FILE"
                if [[ "$parsed_container" == "$CONTAINER" ]]; then
                    local new_soc=$((parsed_soc | active_soc)) new_thermal=$((parsed_thermal | active_thermal))
                    if [[ "$new_soc" -ne "$parsed_soc" || "$new_thermal" -ne "$parsed_thermal" ]]; then
                        write_marker_file "$PENDING_FILE" "$CONTAINER" "$new_soc" "$new_thermal" "$parsed_worker"
                        warn "Cannot determine the state of ${CONTAINER} (docker unavailable or 'docker inspect' failed); recorded additional cause on pending pause marker (now: $(causes_text "$new_soc" "$new_thermal"))."
                    else
                        warn "Cannot determine the state of ${CONTAINER} (docker unavailable or 'docker inspect' failed); holding pending pause marker (cause: $(causes_text "$new_soc" "$new_thermal"))."
                    fi
                    return 0
                fi
            fi
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
    if [[ "$ACTION" != "pause" ]]; then
        [[ -f "$STATE_FILE" || -f "$PENDING_FILE" ]] || return 1
        note_leftover_marker
        return 0
    fi
    if [[ "$PENDING_WORKER_INFLIGHT" -eq 1 && -f "$PENDING_FILE" ]]; then
        read_marker_file "$PENDING_FILE"
        if ! lock_value_alive "$parsed_worker"; then
            reconcile_pending_pause
        fi
    fi
    if [[ "$PENDING_WORKER_INFLIGHT" -eq 1 ]]; then
        return 0
    fi
    if [[ ! -f "$STATE_FILE" && -f "$PENDING_FILE" ]]; then
        reconcile_pending_pause
    fi
    if [[ ! -f "$STATE_FILE" && -f "$PENDING_FILE" ]]; then
        return 0
    fi
    [[ -f "$STATE_FILE" ]] || return 1
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
    trap 'release_run_lock; exit 129' HUP
    trap 'release_run_lock; exit 130' INT
    trap 'release_run_lock; exit 143' TERM
    acquire_run_lock
    reconcile_pending_pause
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
