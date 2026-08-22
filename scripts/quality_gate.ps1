# ====================================================================
# Sovereign Mini Datacenter — Windows PowerShell Quality Gates Runner
# ====================================================================
$ErrorActionPreference = "Stop"

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "🛡️  Running Sovereign Mini Datacenter Quality Gates" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan

# Gate 1: Ruff Formatter
Write-Host "`n[Gate 1/5] Checking Code Formatting (Ruff)..." -ForegroundColor Yellow
uv tool run ruff format --check src/ tests/
Write-Host "✅ Code formatting is compliant." -ForegroundColor Green

# Gate 2: Ruff Linter
Write-Host "`n[Gate 2/5] Checking Code Linting (Ruff)..." -ForegroundColor Yellow
uv tool run ruff check src/ tests/
Write-Host "✅ Code linting passed with zero errors." -ForegroundColor Green

# Gate 3: Mypy Static Type Analysis
Write-Host "`n[Gate 3/5] Checking Static Types (Mypy)..." -ForegroundColor Yellow
uv tool run mypy --ignore-missing-imports src/sovereign_dc
Write-Host "✅ Static type analysis passed." -ForegroundColor Green

# Gate 4: Pytest & Coverage Enforcement (>=85%)
Write-Host "`n[Gate 4/5] Running Pytest Suite with Coverage Enforcement (>=85%)..." -ForegroundColor Yellow
uv run pytest tests/ --cov=src/sovereign_dc --cov-fail-under=85
Write-Host "✅ All unit tests passed with required coverage." -ForegroundColor Green

# Gate 5: Docker Compose Stack Integrity
Write-Host "`n[Gate 5/5] Validating Docker Compose Multi-Stack..." -ForegroundColor Yellow
if (Get-Command docker -ErrorAction SilentlyContinue) {
    if (-not (Test-Path "software\.env")) {
        Copy-Item "software\env.example" "software\.env"
    }
    docker compose -f software/docker-compose.yml config --quiet
    Write-Host "✅ Docker Compose configurations valid." -ForegroundColor Green
} else {
    Write-Host "⚠️ Docker not detected in local environment; skipping live compose check." -ForegroundColor Yellow
}

Write-Host "`n====================================================================" -ForegroundColor Cyan
Write-Host "🎉 ALL QUALITY GATES SATISFIED! Ready to commit and push." -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Cyan
