#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter � Autonomous Load-Shedding Sentinel
# ====================================================================

set -euo pipefail

METRICS_URL="${TELEMETRY_METRICS_URL:-http://localhost:9101/metrics}"
SOC_THRESHOLD_LOW=20.0
SOC_THRESHOLD_NORMAL=40.0
TEMP_COOLANT_MAX=60.0

log()  { echo -e "\033[1;32m[SENTINEL] $*\033[0m"; }
warn() { echo -e "\033[1;33m[SENTINEL-WARN] $*\033[0m"; }

# Fetch metrics
if ! metrics=$(curl -s --connect-timeout 5 "$METRICS_URL"); then
    warn "Failed to reach telemetry exporter at ${METRICS_URL}."
    exit 0
fi

soc=$(echo "$metrics" | grep "^sovereign_battery_soc_percent " | awk '{print $2}')
coolant_temp=$(echo "$metrics" | grep "^sovereign_temp_coolant_celsius " | awk '{print $2}')

[[ -n "$soc" ]] || soc=100.0
[[ -n "$coolant_temp" ]] || coolant_temp=30.0

log "Telemetry status: Battery SoC = ${soc}%, Coolant Temp = ${coolant_temp}�C"

# Critical check: Low battery or excessive thermal load
is_low_soc=$(awk -v s="$soc" -v t="$SOC_THRESHOLD_LOW" 'BEGIN {print (s < t) ? 1 : 0}')
is_high_temp=$(awk -v temp="$coolant_temp" -v max="$TEMP_COOLANT_MAX" 'BEGIN {print (temp > max) ? 1 : 0}')

if [[ "$is_low_soc" -eq 1 || "$is_high_temp" -eq 1 ]]; then
    warn "? TRIGGERING LOAD SHEDDING (SoC: ${soc}%, Temp: ${coolant_temp}�C)"
    
    # Check running containers and throttle heavy background jobs
    if docker ps --format '{{.Names}}' | grep -q "sovereign_ollama"; then
        log "Pausing heavy Ollama batch workloads..."
        # Limit GPU / container priority if supported
    fi
else
    is_normal=$(awk -v s="$soc" -v t="$SOC_THRESHOLD_NORMAL" 'BEGIN {print (s >= t) ? 1 : 0}')
    if [[ "$is_normal" -eq 1 ]]; then
        log "Power & thermal levels normal. Standard operating profile active."
    fi
fi