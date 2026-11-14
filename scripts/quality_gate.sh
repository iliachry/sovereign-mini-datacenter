#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Local Quality Gates Runner
# ====================================================================
set -euo pipefail

echo "===================================================================="
echo "🛡️  Running Sovereign Mini Datacenter Quality Gates"
echo "===================================================================="

# Gate 1: Ruff Formatter
echo -e "\n[Gate 1/6] Checking Code Formatting (Ruff)..."
uv tool run ruff format --check src/ tests/
echo "✅ Code formatting is compliant."

# Gate 2: Ruff Linter
echo -e "\n[Gate 2/6] Checking Code Linting (Ruff)..."
uv tool run ruff check src/ tests/
echo "✅ Code linting passed with zero errors."

# Gate 3: Mypy Static Type Analysis
echo -e "\n[Gate 3/6] Checking Static Types (Mypy)..."
uv tool run mypy --ignore-missing-imports src/sovereign_dc
echo "✅ Static type analysis passed."

# Gate 4: Pytest & Coverage Enforcement (>=85%)
echo -e "\n[Gate 4/6] Running Pytest Suite with Coverage Enforcement (>=85%)..."
uv run pytest tests/ --cov=sovereign_dc --cov-fail-under=85
echo "✅ All unit tests passed with required coverage."

# Gate 5: standalone copies under software/ match src/sovereign_dc
echo -e "\n[Gate 5/6] Checking standalone copies under software/ against src/sovereign_dc..."
bash "$(dirname "${BASH_SOURCE[0]}")/check_mirrors.sh"

# Gate 6: Docker Compose Stack Integrity
echo -e "\n[Gate 6/6] Validating Docker Compose Multi-Stack..."
if command -v docker >/dev/null 2>&1; then
    bash "$(dirname "${BASH_SOURCE[0]}")/validate_compose.sh"
else
    echo "⚠️ Docker not detected in local environment; skipping live compose check."
fi

echo -e "\n===================================================================="
echo "🎉 ALL QUALITY GATES SATISFIED! Ready to commit and push."
echo "===================================================================="
