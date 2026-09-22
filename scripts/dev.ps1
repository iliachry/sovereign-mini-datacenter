# ====================================================================
# Sovereign Mini Datacenter — Windows PowerShell Developer Tasks
# Usage: .\scripts\dev.ps1 <command> [options]
# ====================================================================
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "help", "setup", "doctor", "test-fast", "test", "test-subsystem",
        "lint", "format", "typecheck", "check", "quality-gate",
        "dashboard", "serve-twin", "sim", "benchmark", "docker-validate",
        "scad-export", "clean"
    )]
    [string]$Command = "help",

    [Parameter(Position = 1)]
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host '====================================================================' -ForegroundColor Cyan
    Write-Host 'Sovereign Mini Datacenter -- Windows Developer Task Runner' -ForegroundColor Cyan
    Write-Host '====================================================================' -ForegroundColor Cyan
    Write-Host 'Setup and Environment:' -ForegroundColor Yellow
    Write-Host '  .\scripts\dev.ps1 setup           - Bootstraps virtualenv, installs deps and pre-commit hooks'
    Write-Host '  .\scripts\dev.ps1 doctor          - Validates local system dependencies and service ports'
    Write-Host ''
    Write-Host 'Testing and Code Quality:' -ForegroundColor Yellow
    Write-Host '  .\scripts\dev.ps1 test-fast       - Runs test suite without coverage (ultra-fast inner loop)'
    Write-Host '  .\scripts\dev.ps1 test            - Runs full test suite with coverage enforcement (>=85%)'
    Write-Host '  .\scripts\dev.ps1 test-subsystem [name] - Runs tests matching pattern, e.g. cli, metaverse'
    Write-Host '  .\scripts\dev.ps1 lint            - Runs Ruff linter'
    Write-Host '  .\scripts\dev.ps1 format          - Formats code with Ruff and auto-fixes lint issues'
    Write-Host '  .\scripts\dev.ps1 typecheck       - Runs Mypy static type analysis'
    Write-Host '  .\scripts\dev.ps1 check           - Fast pre-push sanity check (format + lint + types + test-fast)'
    Write-Host '  .\scripts\dev.ps1 quality-gate    - Full multi-stage quality gates'
    Write-Host ''
    Write-Host 'Runtime and Services:' -ForegroundColor Yellow
    Write-Host '  .\scripts\dev.ps1 dashboard       - Launches Web Operations Dashboard and REST API (port 8080)'
    Write-Host '  .\scripts\dev.ps1 serve-twin      - Serves 3D WebGL Digital Twin and launches browser (port 8088)'
    Write-Host '  .\scripts\dev.ps1 sim             - Executes 5 cycles of 6-layer metaverse wireless simulation'
    Write-Host '  .\scripts\dev.ps1 benchmark       - Runs comparative RL benchmark (SA-PPO vs MD-PPO)'
    Write-Host ''
    Write-Host 'Validation and Maintenance:' -ForegroundColor Yellow
    Write-Host '  .\scripts\dev.ps1 docker-validate - Validates all Docker Compose stacks'
    Write-Host '  .\scripts\dev.ps1 scad-export     - Compiles OpenSCAD chassis to docs\cad\rack_enclosure.stl'
    Write-Host '  .\scripts\dev.ps1 clean           - Removes cache and coverage artifacts'
    Write-Host '====================================================================' -ForegroundColor Cyan
}

switch ($Command) {
    "help" {
        Show-Help
    }

    "setup" {
        Write-Host "`n--> Bootstrapping dependencies with uv..." -ForegroundColor Cyan
        uv sync --all-extras
        if (-not (Test-Path "software\.env")) {
            Copy-Item "software\env.example" "software\.env"
            Write-Host "Created software\.env from template" -ForegroundColor Green
        }
        uv run pre-commit install
        Write-Host "Development environment setup complete!" -ForegroundColor Green
    }

    "doctor" {
        uv run python -m sovereign_dc.cli doctor
    }

    "test-fast" {
        Write-Host "`n--> Running fast unit tests (no coverage)..." -ForegroundColor Cyan
        uv run pytest --no-cov tests/
    }

    "test" {
        Write-Host "`n--> Running full test suite with coverage..." -ForegroundColor Cyan
        uv run pytest tests/ --cov=sovereign_dc --cov-fail-under=85
    }

    "test-subsystem" {
        if (-not $Target) {
            Write-Host "Error: Specify target pattern, e.g. .\scripts\dev.ps1 test-subsystem cli" -ForegroundColor Red
            exit 1
        }
        Write-Host "`n--> Running tests matching '*$Target*'..." -ForegroundColor Cyan
        $files = Get-ChildItem -Path tests -Filter "test_*$Target*.py" | ForEach-Object { $_.FullName }
        if (-not $files) {
            Write-Host "No test files matching 'test_*$Target*.py' found." -ForegroundColor Yellow
            exit 0
        }
        uv run pytest --no-cov $files
    }

    "lint" {
        Write-Host "`n--> Checking linting with Ruff..." -ForegroundColor Cyan
        uv tool run ruff check src/ tests/
    }

    "format" {
        Write-Host "`n--> Formatting code with Ruff..." -ForegroundColor Cyan
        uv tool run ruff format src/ tests/
        uv tool run ruff check --fix src/ tests/
    }

    "typecheck" {
        Write-Host "`n--> Checking types with Mypy..." -ForegroundColor Cyan
        uv tool run mypy --ignore-missing-imports src/sovereign_dc
    }

    "check" {
        Write-Host "`n--> Running fast pre-push sanity checks..." -ForegroundColor Cyan
        uv tool run ruff format src/ tests/
        uv tool run ruff check src/ tests/
        uv tool run mypy --ignore-missing-imports src/sovereign_dc
        uv run pytest --no-cov tests/
        Write-Host "`nAll fast pre-push checks passed!" -ForegroundColor Green
    }

    "quality-gate" {
        & "$PSScriptRoot\quality_gate.ps1"
    }

    "dashboard" {
        Write-Host "`n--> Launching Web Operations Dashboard on http://localhost:8080..." -ForegroundColor Cyan
        uv run python -m sovereign_dc.cli dashboard --port 8080
    }

    "serve-twin" {
        Write-Host "`n--> Serving 3D WebGL Digital Twin on http://localhost:8088..." -ForegroundColor Cyan
        uv run python -m sovereign_dc.cli docs --serve --port 8088
    }

    "sim" {
        Write-Host "`n--> Running 6-layer wireless metaverse simulation..." -ForegroundColor Cyan
        uv run python -m sovereign_dc.cli sim run --cycles 5
    }

    "benchmark" {
        Write-Host "`n--> Running comparative RL benchmark..." -ForegroundColor Cyan
        uv run python -m sovereign_dc.cli sim benchmark --episodes 15 --steps 20
    }

    "docker-validate" {
        Write-Host "`n--> Validating Docker Compose configuration..." -ForegroundColor Cyan
        if (-not (Test-Path "software\.env")) {
            Copy-Item "software\env.example" "software\.env"
        }
        docker compose -f software/docker-compose.yml config --quiet
        Write-Host "Docker Compose configurations valid." -ForegroundColor Green
    }

    "scad-export" {
        Write-Host "`n--> Compiling OpenSCAD 3D chassis model..." -ForegroundColor Cyan
        if (-not (Test-Path "docs\cad")) {
            New-Item -ItemType Directory -Path "docs\cad" -Force | Out-Null
        }
        openscad --hardwarnings --export-format binstl -o docs\cad\rack_enclosure.stl cad\rack_enclosure.scad
        Write-Host "OpenSCAD STL export complete." -ForegroundColor Green
    }

    "clean" {
        Write-Host "`n--> Cleaning build and cache artifacts..." -ForegroundColor Cyan
        $paths = @(".pytest_cache", ".ruff_cache", ".mypy_cache", ".coverage", "htmlcov", "dist", "build")
        foreach ($p in $paths) {
            if (Test-Path $p) {
                Remove-Item -Recurse -Force $p
            }
        }
        Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Clean complete." -ForegroundColor Green
    }
}
