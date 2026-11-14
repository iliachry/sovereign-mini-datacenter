# ====================================================================
# Sovereign Mini Datacenter — Windows PowerShell Quality Gates Runner
# ====================================================================
$ErrorActionPreference = "Stop"

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "🛡️  Running Sovereign Mini Datacenter Quality Gates" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan

. "$PSScriptRoot\bash_gates.ps1"

# Native commands do not raise on a non-zero exit code, so each gate checks it.
function Assert-GatePassed {
    param([string]$Name)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
}

$skipped = @()

# Gate 1: Ruff Formatter
Write-Host "`n[Gate 1/6] Checking Code Formatting (Ruff)..." -ForegroundColor Yellow
uv tool run ruff format --check src/ tests/
Assert-GatePassed "Ruff format"
Write-Host "✅ Code formatting is compliant." -ForegroundColor Green

# Gate 2: Ruff Linter
Write-Host "`n[Gate 2/6] Checking Code Linting (Ruff)..." -ForegroundColor Yellow
uv tool run ruff check src/ tests/
Assert-GatePassed "Ruff lint"
Write-Host "✅ Code linting passed with zero errors." -ForegroundColor Green

# Gate 3: Mypy Static Type Analysis
Write-Host "`n[Gate 3/6] Checking Static Types (Mypy)..." -ForegroundColor Yellow
uv tool run mypy --ignore-missing-imports src/sovereign_dc
Assert-GatePassed "Mypy"
Write-Host "✅ Static type analysis passed." -ForegroundColor Green

# Gate 4: Pytest & Coverage Enforcement (>=85%)
Write-Host "`n[Gate 4/6] Running Pytest Suite with Coverage Enforcement (>=85%)..." -ForegroundColor Yellow
uv run pytest tests/ --cov=sovereign_dc --cov-fail-under=85
Assert-GatePassed "Pytest"
Write-Host "✅ All unit tests passed with required coverage." -ForegroundColor Green

# Gate 5: standalone copies under software/ match src/sovereign_dc (bash script)
Write-Host "`n[Gate 5/6] Checking standalone copies under software/ against src/sovereign_dc..." -ForegroundColor Yellow
if (-not (Invoke-BashGate -Name "Mirror gate" -Script "check_mirrors.sh")) {
    $skipped += "mirror gate (scripts/check_mirrors.sh)"
}

# Gate 6: Docker Compose Stack Integrity incl. the storage-identity invariant (bash script)
Write-Host "`n[Gate 6/6] Validating Docker Compose Multi-Stack..." -ForegroundColor Yellow
if (-not (Invoke-BashGate -Name "Compose validation and invariant" -Script "validate_compose.sh" -Requires @("docker", "python3"))) {
    $skipped += "Compose validation and storage-identity invariant (scripts/validate_compose.sh)"
}

Write-Host "`n====================================================================" -ForegroundColor Cyan
if ($skipped.Count -gt 0) {
    Write-Host "Gates 1-4 passed. NOT RUN on this machine:" -ForegroundColor Yellow
    foreach ($gate in $skipped) { Write-Host "  - $gate" -ForegroundColor Yellow }
    Write-Host "Run them with bash (Git Bash / WSL) or rely on CI before pushing." -ForegroundColor Yellow
} else {
    Write-Host "🎉 ALL QUALITY GATES SATISFIED! Ready to commit and push." -ForegroundColor Green
}
Write-Host "====================================================================" -ForegroundColor Cyan
