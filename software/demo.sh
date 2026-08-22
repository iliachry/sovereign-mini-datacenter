#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — 1-Click Local Demonstration Launcher
# ====================================================================
set -euo pipefail

echo "===================================================================="
echo "🚀 Launching Sovereign Mini Datacenter Local Demonstration Sandbox"
echo "===================================================================="

# Check if Python / uv environment is available
if command -v uv >/dev/null 2>&1; then
    uv run python -m sovereign_dc demo --steps 4
elif command -v python3 >/dev/null 2>&1; then
    python3 -m sovereign_dc demo --steps 4
else
    echo "❌ Python 3 or uv is required to run the demonstration sandbox."
    exit 1
fi
