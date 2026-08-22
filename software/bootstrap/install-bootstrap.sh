#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Autonomous Bootstrap Service Installer
# Installs and enables systemd service for power-on self-provisioning
# ====================================================================
set -euo pipefail

GREEN="\033[1;32m"
CYAN="\033[1;36m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "\n${BOLD}${CYAN}=== Sovereign Bootstrap Service Installer ===${RESET}\n"

SERVICE_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/smdc-bootstrap.service"
SERVICE_DST="/etc/systemd/system/smdc-bootstrap.service"

if [[ $EUID -ne 0 ]]; then
   echo -e "⚠️  This installer must be run as root (sudo ./install-bootstrap.sh)"
   exit 1
fi

echo -e "📦 Installing systemd service to ${SERVICE_DST}..."
cp "$SERVICE_SRC" "$SERVICE_DST"
chmod 644 "$SERVICE_DST"

echo -e "🔄 Reloading systemd daemon..."
systemctl daemon-reload

echo -e "⚡ Enabling smdc-bootstrap.service for autonomous boot execution..."
systemctl enable smdc-bootstrap.service

echo -e "\n${GREEN}✅ Sovereign Bootstrap Provisioner successfully installed!${RESET}"
echo -e "On next power-on or cold-start, the node will autonomously discover hardware,"
echo -e "connect to the mesh overlay, provision services, and notify human technicians.\n"
