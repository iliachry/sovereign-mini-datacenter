# ====================================================================
# Sovereign Mini Datacenter — bash-backed gates for the Windows runners
#
# Dot-sourced by dev.ps1 and quality_gate.ps1. Two gates exist only as bash
# scripts and are deliberately NOT reimplemented in PowerShell:
#   scripts/check_mirrors.sh     standalone copies under software/ == src/sovereign_dc
#   scripts/validate_compose.sh  Compose validation incl. the storage-identity invariant
# They run through bash when one is usable (Git Bash or WSL); otherwise they are
# reported as SKIPPED with the reason, never as passed.
# ====================================================================

function Test-BashUsable {
    if (-not (Get-Command bash -ErrorAction SilentlyContinue)) { return $false }
    # C:\Windows\System32\bash.exe is the WSL launcher: it exists even when no
    # distribution is installed, in which case it exits non-zero.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & bash -c "exit 0" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

# Runs scripts/<Script> with bash from the repository root.
# Returns $true if it ran and passed, $false if it was skipped. Exits 1 if it failed.
function Invoke-BashGate {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [string[]]$Requires = @()
    )
    if (-not (Test-BashUsable)) {
        Write-Host "SKIPPED: $Name (scripts/$Script) - no usable bash found. Install Git for Windows (Git Bash) or a WSL distribution; this gate is not reimplemented in PowerShell." -ForegroundColor Yellow
        return $false
    }
    $repoRoot = Split-Path -Parent $PSScriptRoot
    Push-Location $repoRoot
    try {
        foreach ($tool in $Requires) {
            & bash -c "command -v $tool >/dev/null 2>&1"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "SKIPPED: $Name (scripts/$Script) - '$tool' is not available inside bash." -ForegroundColor Yellow
                return $false
            }
        }
        # Out-Host: show the script's output instead of returning it from this function.
        & bash "scripts/$Script" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAILED: $Name (scripts/$Script exited with $LASTEXITCODE)." -ForegroundColor Red
            exit 1
        }
        return $true
    } finally {
        Pop-Location
    }
}
