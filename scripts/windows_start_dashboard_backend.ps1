#Requires -Version 5.1
<#
.SYNOPSIS
    Start the Palantir dashboard backend (FastAPI / uvicorn).

.DESCRIPTION
    Loads .env, activates conda env, and starts the dashboard backend.
    Dashboard is accessible at http://127.0.0.1:8090 by default.

.EXAMPLE
    .\scripts\windows_start_dashboard_backend.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Public\projects\Palantir"
$CondaEnv    = "C:\Users\Public\conda-envs\babelfish1"
$EnvFile     = Join-Path $ProjectRoot ".env"

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) {
    Write-Error ".env not found at $EnvFile — copy .env.windows.example to .env and fill in CHANGE_ME values."
}

Write-Host "Loading $EnvFile ..." -ForegroundColor Cyan
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line -split "=", 2
        if ($parts.Count -eq 2) {
            [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
        }
    }
}

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
$host_  = if ([System.Environment]::GetEnvironmentVariable("PATHORYX_DASHBOARD_HOST")) { [System.Environment]::GetEnvironmentVariable("PATHORYX_DASHBOARD_HOST") } else { "127.0.0.1" }
$port   = if ([System.Environment]::GetEnvironmentVariable("PATHORYX_DASHBOARD_PORT")) { [System.Environment]::GetEnvironmentVariable("PATHORYX_DASHBOARD_PORT") } else { "8090" }
$dbUrl  = [System.Environment]::GetEnvironmentVariable("DATABASE_URL")

Write-Host ""
Write-Host "Dashboard backend starting..." -ForegroundColor Cyan
Write-Host "  Host:         $host_:$port"
Write-Host "  DATABASE_URL: $($dbUrl -replace ':([^:@]+)@', ':***@')"
Write-Host "  Open browser: http://$host_:$port"
Write-Host ""

Set-Location $ProjectRoot

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
& "$CondaEnv\Scripts\pathoryx-dashboard.exe"
