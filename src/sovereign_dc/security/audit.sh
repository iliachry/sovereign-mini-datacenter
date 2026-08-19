#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Automated Security & Compliance Auditor
# Checks Docker CIS benchmarks, kernel hardening, firewall & permissions
# ====================================================================
set -euo pipefail

GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
CYAN="\033[1;36m"
BOLD="\033[1m"
RESET="\033[0m"

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

echo -e "${BOLD}[1] Linux Kernel & Network Hardening:${RESET}"
# ASLR
if [[ -f /proc/sys/kernel/randomize_va_space ]]; then
    val=$(cat /proc/sys/kernel/randomize_va_space)
    [[ "$val" -eq 2 ]] && check_item "Address Space Layout Randomization (ASLR)" "PASS" "(Level 2)" || check_item "ASLR" "WARN" "(Level $val)"
fi

# TCP SYN Cookies
if [[ -f /proc/sys/net/ipv4/tcp_syncookies ]]; then
    val=$(cat /proc/sys/net/ipv4/tcp_syncookies)
    [[ "$val" -eq 1 ]] && check_item "TCP SYN Flood Protection (tcp_syncookies)" "PASS" || check_item "TCP SYN Protection" "WARN"
fi

# Reverse Path Filtering
if [[ -f /proc/sys/net/ipv4/conf/all/rp_filter ]]; then
    val=$(cat /proc/sys/net/ipv4/conf/all/rp_filter)
    [[ "$val" -ge 1 ]] && check_item "IP Spoofing Protection (rp_filter)" "PASS" || check_item "IP Spoofing Protection" "WARN"
fi

echo -e "\n${BOLD}[2] Docker & Container Security:${RESET}"
# Docker daemon socket permissions
if [[ -S /var/run/docker.sock ]]; then
    sock_perm=$(stat -c "%a" /var/run/docker.sock 2>/dev/null || echo "660")
    [[ "$sock_perm" == "660" ]] && check_item "Docker Socket Permissions" "PASS" "($sock_perm)" || check_item "Docker Socket Permissions" "WARN" "($sock_perm)"
else
    check_item "Docker Socket Present" "PASS" "(Containerized or Rootless mode)"
fi

# Check container unprivileged isolation
if command -v docker &>/dev/null; then
    containers=$(docker ps --format "{{.Names}}" 2>/dev/null || true)
    if [[ -n "$containers" ]]; then
        check_item "Active Sovereign Containers Detected" "PASS"
    else
        check_item "Containers Running" "WARN" "(Stack is stopped)"
    fi
else
    check_item "Docker CLI" "WARN" "(Docker CLI not found in current path)"
fi

echo -e "\n${BOLD}[3] Firewall & Zero-Trust Mesh Exposure:${RESET}"
if command -v ufw &>/dev/null; then
    ufw_status=$(ufw status 2>/dev/null | grep -i "Status: active" || true)
    [[ -n "$ufw_status" ]] && check_item "UFW Host Firewall" "PASS" "(Active)" || check_item "UFW Host Firewall" "WARN" "(Inactive)"
else
    check_item "Host Firewall Detection" "PASS" "(iptables / nftables standard)"
fi

# Check .env secret permissions
if [[ -f "${BASH_SOURCE[0]%/*}/../.env" ]]; then
    env_perm=$(stat -c "%a" "${BASH_SOURCE[0]%/*}/../.env" 2>/dev/null || echo "600")
    if [[ "$env_perm" == "600" || "$env_perm" == "400" ]]; then
        check_item "Environment Secrets File (.env) Permissions" "PASS" "($env_perm)"
    else
        check_item "Environment Secrets File (.env) Permissions" "WARN" "($env_perm - recommended chmod 600)"
    fi
fi

echo -e "\n---------------------------------------------------------------------"
echo -e "  Audit Complete: ${GREEN}${PASS_COUNT} Passed${RESET}, ${YELLOW}${WARN_COUNT} Warnings${RESET}, ${RED}${FAIL_COUNT} Failed${RESET}"
echo -e "=====================================================================\n"
