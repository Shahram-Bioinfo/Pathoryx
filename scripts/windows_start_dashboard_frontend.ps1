#Requires -Version 5.1
<#
.SYNOPSIS
    Start the Palantir dashboard frontend dev server (Vite / React).

.DESCRIPTION
    Runs 'npm install' (if node_modules is absent) then 'npm run dev'
    in the dashboard-ui directory.
    Dev server is accessible at http://localhost:5173 by default.

.EXAMPLE
    .\scripts\windows_start_dashboard_frontend.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot  = "C:\Users\Public\projects\Palantir"
$UiDir        = Join-Path $ProjectRoot "dashboard-ui"
$NodeModules  = Join-Path $UiDir "node_modules"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if (-not (Test-Path $UiDir)) {
    Write-Error "dashboard-ui directory not found at $UiDir"
}

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Error "npm not found. Install Node.js 18+ from https://nodejs.org/ and ensure it is on PATH."
}

Write-Host ""
Write-Host "Node.js: $(node --version)" -ForegroundColor DarkGray
Write-Host "npm:     $(npm --version)"  -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# Install dependencies if needed
# ---------------------------------------------------------------------------
if (-not (Test-Path $NodeModules)) {
    Write-Host "node_modules not found — running npm install ..." -ForegroundColor Yellow
    Set-Location $UiDir
    npm install
    if ($LASTEXITCODE -ne 0) { Write-Error "npm install failed." }
} else {
    Write-Host "node_modules present — skipping npm install." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Start dev server
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Starting Vite dev server..." -ForegroundColor Cyan
Write-Host "  Open browser: http://localhost:5173"
Write-Host "  Press Ctrl+C to stop."
Write-Host ""

Set-Location $UiDir
npm run dev
