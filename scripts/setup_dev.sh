#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — One-Shot Unix / macOS Developer Setup
# Usage: ./scripts/setup_dev.sh
# ====================================================================
set -euo pipefail

CYAN='\033[1;36m'
YELLOW='\033[1;33m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

echo -e "${CYAN}====================================================================${NC}"
echo -e "${CYAN}🚀 Sovereign Mini Datacenter — Developer Environment Setup${NC}"
echo -e "${CYAN}====================================================================${NC}"

# 1. Check for uv
echo -e "\n${YELLOW}[Step 1/4] Checking for uv package manager...${NC}"
if ! command -v uv &>/dev/null; then
    echo -e "${YELLOW}⚠️ 'uv' not found. Installing via Astral installer...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi
echo -e "${GREEN}✅ uv is available: $(uv --version)${NC}"

# 2. Virtual environment and dependencies
echo -e "\n${YELLOW}[Step 2/4] Installing dependencies & preparing .venv...${NC}"
uv sync --all-extras
echo -e "${GREEN}✅ Virtual environment synced with all dev and test dependencies.${NC}"

# 3. Environment configuration template
echo -e "\n${YELLOW}[Step 3/4] Checking environment configurations...${NC}"
if [ ! -f "software/.env" ] && [ -f "software/env.example" ]; then
    cp software/env.example software/.env
    echo -e "${GREEN}✅ Created software/.env from template.${NC}"
else
    echo -e "${GREEN}✅ software/.env already exists.${NC}"
fi

# 4. Pre-commit hooks
echo -e "\n${YELLOW}[Step 4/4] Installing Git pre-commit hooks...${NC}"
if [ -d ".git" ]; then
    uv run pre-commit install
    echo -e "${GREEN}✅ Pre-commit git hooks installed.${NC}"
else
    echo -e "${YELLOW}⚠️ Not a git working tree; skipping hook installation.${NC}"
fi

echo -e "\n${CYAN}====================================================================${NC}"
echo -e "${GREEN}🎉 ENVIRONMENT SETUP COMPLETE!${NC}"
echo -e "${CYAN}====================================================================${NC}"
echo -e "You can now run tasks with:"
echo -e "  • Fast Inner-Loop Tests: make test-fast"
echo -e "  • Pre-Push Check:        make check"
echo -e "  • Operations Dashboard:  make dashboard"
echo -e "  • 3D Digital Twin:       make serve-twin"
echo -e "  • System Diagnostics:    make doctor"
echo -e "${CYAN}====================================================================${NC}"
