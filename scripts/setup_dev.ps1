# ====================================================================
# Sovereign Mini Datacenter — One-Shot Windows Developer Setup
# Usage: .\scripts\setup_dev.ps1
# ====================================================================
$ErrorActionPreference = "Stop"

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "🚀 Sovereign Mini Datacenter — Developer Environment Setup" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan

# 1. Check for uv package manager
Write-Host "`n[Step 1/4] Checking for uv package manager..." -ForegroundColor Yellow
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "⚠️ 'uv' is not found on your system. Installing via Astral installer..." -ForegroundColor Yellow
    try {
        irm https://astral.sh/uv/install.ps1 | iex
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
    } catch {
        Write-Host "❌ Failed to install uv automatically. Please install uv manually: https://github.com/astral-sh/uv" -ForegroundColor Red
        exit 1
    }
}
$uvVersion = uv --version
Write-Host "✅ uv is available: $uvVersion" -ForegroundColor Green

# 2. Synchronize Python virtual environment
Write-Host "`n[Step 2/4] Installing dependencies & preparing .venv..." -ForegroundColor Yellow
uv sync --all-extras
Write-Host "✅ Virtual environment synced with all dev and test dependencies." -ForegroundColor Green

# 3. Environment configuration template
Write-Host "`n[Step 3/4] Checking environment configurations..." -ForegroundColor Yellow
if (-not (Test-Path "software\.env")) {
    if (Test-Path "software\env.example") {
        Copy-Item "software\env.example" "software\.env"
        Write-Host "✅ Created software\.env from software\env.example template." -ForegroundColor Green
    }
} else {
    Write-Host "✅ software\.env already exists." -ForegroundColor Green
}

# 4. Install Git Pre-Commit Hooks
Write-Host "`n[Step 4/4] Installing Git pre-commit hooks..." -ForegroundColor Yellow
if (Test-Path ".git") {
    uv run pre-commit install
    Write-Host "✅ Pre-commit git hooks installed." -ForegroundColor Green
} else {
    Write-Host "⚠️ Not a git working tree; skipping git hook installation." -ForegroundColor Yellow
}

Write-Host "`n====================================================================" -ForegroundColor Cyan
Write-Host "🎉 ENVIRONMENT SETUP COMPLETE!" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "You can now run tasks with:" -ForegroundColor White
Write-Host "  • Fast Inner-Loop Tests: .\scripts\dev.ps1 test-fast" -ForegroundColor Cyan
Write-Host "  • Pre-Push Check:        .\scripts\dev.ps1 check" -ForegroundColor Cyan
Write-Host "  • Operations Dashboard:  .\scripts\dev.ps1 dashboard" -ForegroundColor Cyan
Write-Host "  • 3D Digital Twin:       .\scripts\dev.ps1 serve-twin" -ForegroundColor Cyan
Write-Host "  • System Diagnostics:    .\scripts\dev.ps1 doctor" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
