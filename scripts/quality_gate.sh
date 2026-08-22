#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter — Local Quality Gates Runner
# ====================================================================
set -euo pipefail

echo "===================================================================="
echo "🛡️  Running Sovereign Mini Datacenter Quality Gates"
echo "===================================================================="

# Gate 1: Ruff Formatter
echo -e "\n[Gate 1/5] Checking Code Formatting (Ruff)..."
uv tool run ruff format --check src/ tests/
echo "✅ Code formatting is compliant."

# Gate 2: Ruff Linter
echo -e "\n[Gate 2/5] Checking Code Linting (Ruff)..."
uv tool run ruff check src/ tests/
echo "✅ Code linting passed with zero errors."

# Gate 3: Mypy Static Type Analysis
echo -e "\n[Gate 3/5] Checking Static Types (Mypy)..."
uv tool run mypy --ignore-missing-imports src/sovereign_dc
echo "✅ Static type analysis passed."

# Gate 4: Pytest & Coverage Enforcement (>=85%)
echo -e "\n[Gate 4/5] Running Pytest Suite with Coverage Enforcement (>=85%)..."
uv run pytest tests/ --cov=sovereign_dc --cov-fail-under=85
echo "✅ All unit tests passed with required coverage."

# Gate 5: Docker Compose Stack Integrity
echo -e "\n[Gate 5/5] Validating Docker Compose Multi-Stack..."
if command -v docker >/dev/null 2>&1; then
    (cd software && cp -n env.example .env 2>/dev/null || true)
    docker compose -f software/docker-compose.yml config --quiet
    echo "✅ Docker Compose configurations valid."
else
    echo "⚠️ Docker not detected in local environment; skipping live compose check."
fi

echo -e "\n===================================================================="
echo "🎉 ALL QUALITY GATES SATISFIED! Ready to commit and push."
echo "===================================================================="
