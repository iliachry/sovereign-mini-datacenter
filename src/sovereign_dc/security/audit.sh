#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Automated Security & Compliance Auditor
# Checks Docker CIS benchmarks, kernel hardening, firewall & permissions
#
# Exit codes:
#   0  all checks passed (warnings allowed unless --strict)
#   1  warnings present and --strict was requested
#   2  one or more critical checks FAILED
#
# Portable across GNU/Linux (coreutils stat -c) and BSD/macOS (stat -f).
# ====================================================================
set -euo pipefail

GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
CYAN="\033[1;36m"
BOLD="\033[1m"
RESET="\033[0m"

STRICT=false
for arg in "$@"; do
    case "$arg" in
        --strict) STRICT=true ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--strict]"
            echo "  --strict   Treat warnings as failures (exit 1)"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 64 ;;
    esac
done

echo -e "\n${BOLD}${CYAN}=== Sovereign Mini Datacenter — Security Audit ===${RESET}\n"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

check_item() {
    local desc="$1"
    local status="$2"
    local note="${3:-}"
    if [[ "$status" == "PASS" ]]; then
        echo -e "  [ ${GREEN}PASS${RESET} ] $desc $note"
        PASS_COUNT=$((PASS_COUNT + 1))
    elif [[ "$status" == "WARN" ]]; then
        echo -e "  [ ${YELLOW}WARN${RESET} ] $desc $note"
        WARN_COUNT=$((WARN_COUNT + 1))
    else
        echo -e "  [ ${RED}FAIL${RESET} ] $desc $note"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# file_mode <path>
# Prints the octal permission bits of <path> (e.g. "600"), trying GNU coreutils
# `stat -c '%a'` first and falling back to BSD/macOS `stat -f '%Lp'`.
# Prints "unknown" when neither variant is available.
file_mode() {
    local path="$1"
    local mode=""
    if command -v stat >/dev/null 2>&1; then
        mode=$(stat -c '%a' "$path" 2>/dev/null || true)
        if [[ -z "$mode" ]]; then
            mode=$(stat -f '%Lp' "$path" 2>/dev/null || true)
        fi
    fi
    if [[ -z "$mode" ]]; then
        echo "unknown"
    else
        # Normalise to the last three octal digits (strip setuid/setgid/sticky)
        echo "${mode: -3}"
    fi
}

# others_bits <mode>  -> prints the "others" octal digit of a 3-digit mode
others_bits() {
    local mode="$1"
    echo "${mode: -1}"
}

# ---------------------------------------------------------------------
echo -e "${BOLD}[1] Linux Kernel & Network Hardening:${RESET}"

# ASLR (critical: fully disabled ASLR is a FAIL)
if [[ -f /proc/sys/kernel/randomize_va_space ]]; then
    read -r val < /proc/sys/kernel/randomize_va_space || val="0"
    if [[ "$val" -eq 2 ]]; then
        check_item "Address Space Layout Randomization (ASLR)" "PASS" "(Level 2)"
    elif [[ "$val" -eq 1 ]]; then
        check_item "Address Space Layout Randomization (ASLR)" "WARN" "(Level 1 - recommended 2)"
    else
        check_item "Address Space Layout Randomization (ASLR)" "FAIL" "(Disabled - set kernel.randomize_va_space=2)"
    fi
else
    check_item "Address Space Layout Randomization (ASLR)" "WARN" "(procfs not available on this platform)"
fi

# TCP SYN Cookies
if [[ -f /proc/sys/net/ipv4/tcp_syncookies ]]; then
    read -r val < /proc/sys/net/ipv4/tcp_syncookies || val="0"
    if [[ "$val" -eq 1 ]]; then
        check_item "TCP SYN Flood Protection (tcp_syncookies)" "PASS"
    else
        check_item "TCP SYN Flood Protection (tcp_syncookies)" "FAIL" "(Disabled - set net.ipv4.tcp_syncookies=1)"
    fi
fi

# Reverse Path Filtering
if [[ -f /proc/sys/net/ipv4/conf/all/rp_filter ]]; then
    read -r val < /proc/sys/net/ipv4/conf/all/rp_filter || val="0"
    if [[ "$val" -ge 1 ]]; then
        check_item "IP Spoofing Protection (rp_filter)" "PASS"
    else
        check_item "IP Spoofing Protection (rp_filter)" "WARN" "(Disabled - recommended net.ipv4.conf.all.rp_filter=1)"
    fi
fi

# ---------------------------------------------------------------------
echo -e "\n${BOLD}[2] Docker & Container Security:${RESET}"

# Docker daemon socket permissions (critical: world-writable socket = root on host)
if [[ -S /var/run/docker.sock ]]; then
    sock_perm=$(file_mode /var/run/docker.sock)
    if [[ "$sock_perm" == "unknown" ]]; then
        check_item "Docker Socket Permissions" "WARN" "(could not determine mode)"
    elif [[ "$sock_perm" == "660" || "$sock_perm" == "600" ]]; then
        check_item "Docker Socket Permissions" "PASS" "($sock_perm)"
    elif [[ "$(others_bits "$sock_perm")" =~ [2367] ]]; then
        check_item "Docker Socket Permissions" "FAIL" "($sock_perm - world-writable; chmod 660 /var/run/docker.sock)"
    else
        check_item "Docker Socket Permissions" "WARN" "($sock_perm - recommended 660)"
    fi
else
    check_item "Docker Socket Present" "PASS" "(Containerized or Rootless mode)"
fi

if command -v docker >/dev/null 2>&1; then
    containers=$(docker ps --format "{{.Names}}" 2>/dev/null || true)
    if [[ -n "$containers" ]]; then
        check_item "Active Sovereign Containers Detected" "PASS"

        # Privileged containers (critical). cAdvisor legitimately requires
        # privileged mode for cgroup / GPU accounting and is allow-listed.
        privileged=""
        while IFS= read -r name; do
            [[ -z "$name" || "$name" == "sovereign_cadvisor" ]] && continue
            is_priv=$(docker inspect --format '{{.HostConfig.Privileged}}' "$name" 2>/dev/null || echo "false")
            if [[ "$is_priv" == "true" ]]; then
                privileged="${privileged} ${name}"
            fi
        done <<< "$containers"
        if [[ -n "$privileged" ]]; then
            check_item "Unprivileged Container Isolation" "FAIL" "(privileged:${privileged})"
        else
            check_item "Unprivileged Container Isolation" "PASS"
        fi

        # Containers running as root user with the Docker socket mounted (critical)
        sock_mounted=""
        while IFS= read -r name; do
            [[ -z "$name" || "$name" == "sovereign_traefik" ]] && continue
            mounts=$(docker inspect --format '{{range .Mounts}}{{.Source}} {{end}}' "$name" 2>/dev/null || true)
            if [[ "$mounts" == *"/var/run/docker.sock"* ]]; then
                sock_mounted="${sock_mounted} ${name}"
            fi
        done <<< "$containers"
        if [[ -n "$sock_mounted" ]]; then
            check_item "Docker Socket Exposure to Containers" "FAIL" "(mounted in:${sock_mounted})"
        else
            check_item "Docker Socket Exposure to Containers" "PASS" "(only Traefik, read-only)"
        fi
    else
        check_item "Containers Running" "WARN" "(Stack is stopped)"
    fi
else
    check_item "Docker CLI" "WARN" "(Docker CLI not found in current path)"
fi

# ---------------------------------------------------------------------
echo -e "\n${BOLD}[3] Firewall & Zero-Trust Mesh Exposure:${RESET}"
if command -v ufw >/dev/null 2>&1; then
    ufw_status=$(ufw status 2>/dev/null || true)
    if echo "$ufw_status" | grep -iq "Status: active"; then
        check_item "UFW Host Firewall" "PASS" "(Active)"
    else
        check_item "UFW Host Firewall" "WARN" "(Inactive)"
    fi
elif command -v nft >/dev/null 2>&1 || command -v iptables >/dev/null 2>&1; then
    check_item "Host Firewall Detection" "PASS" "(iptables / nftables available)"
elif command -v pfctl >/dev/null 2>&1; then
    check_item "Host Firewall Detection" "PASS" "(BSD pf available)"
else
    check_item "Host Firewall Detection" "WARN" "(No firewall tooling detected)"
fi

# ---------------------------------------------------------------------
echo -e "\n${BOLD}[4] Secrets & Configuration Hygiene:${RESET}"

# Resolve the deployment .env file. This script lives either at
# software/security/audit.sh or at src/sovereign_dc/security/audit.sh.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file=""
for candidate in \
    "${SMDC_ENV_FILE:-}" \
    "${script_dir}/../.env" \
    "${script_dir}/../../../software/.env"; do
    if [[ -n "$candidate" && -f "$candidate" ]]; then
        env_file="$candidate"
        break
    fi
done

if [[ -n "$env_file" ]]; then
    env_perm=$(file_mode "$env_file")
    if [[ "$env_perm" == "unknown" ]]; then
        check_item "Environment Secrets File (.env) Permissions" "WARN" "(could not determine mode)"
    elif [[ "$env_perm" == "600" || "$env_perm" == "400" ]]; then
        check_item "Environment Secrets File (.env) Permissions" "PASS" "($env_perm)"
    elif [[ "$(others_bits "$env_perm")" != "0" ]]; then
        check_item "Environment Secrets File (.env) Permissions" "FAIL" "($env_perm - world-accessible; chmod 600 $env_file)"
    else
        check_item "Environment Secrets File (.env) Permissions" "WARN" "($env_perm - recommended chmod 600)"
    fi

    # Default / placeholder secrets left in production .env (critical)
    placeholders=$(grep -Eic '^[A-Z0-9_]+=.*(change_?me|changeme|replace_?me|your_.*_here)' "$env_file" || true)
    if [[ "$placeholders" -gt 0 ]]; then
        check_item "Default Placeholder Secrets in .env" "FAIL" "($placeholders unchanged placeholder value(s))"
    else
        check_item "Default Placeholder Secrets in .env" "PASS"
    fi
else
    check_item "Environment Secrets File (.env)" "WARN" "(not found - run software/setup.sh to generate)"
fi

# ---------------------------------------------------------------------
echo -e "\n---------------------------------------------------------------------"
echo -e "  Audit Complete: ${GREEN}${PASS_COUNT} Passed${RESET}, ${YELLOW}${WARN_COUNT} Warnings${RESET}, ${RED}${FAIL_COUNT} Failed${RESET}"
echo -e "=====================================================================\n"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo -e "${RED}${BOLD}✖ Security audit FAILED: ${FAIL_COUNT} critical check(s) did not pass.${RESET}\n"
    exit 2
fi
if [[ "$STRICT" == "true" && "$WARN_COUNT" -gt 0 ]]; then
    echo -e "${YELLOW}${BOLD}⚠ Strict mode: ${WARN_COUNT} warning(s) treated as failure.${RESET}\n"
    exit 1
fi
echo -e "${GREEN}${BOLD}✔ Security audit passed.${RESET}\n"
exit 0
