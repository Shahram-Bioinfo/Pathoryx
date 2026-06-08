#Requires -Version 5.1
<#
.SYNOPSIS
    Start all Palantir pipeline services via the orchestrator.

.DESCRIPTION
    Loads .env and starts pathoryx-orchestrate, which manages BabelShark,
    QC, DICOM, Uploader, and RecoverySentry as child processes.

    To start services individually instead, open separate PowerShell windows
    and run the corresponding entry points (see WINDOWS_RUNBOOK.md).

.EXAMPLE
    .\scripts\windows_start_orchestrator.ps1
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
# Verify required config files exist
# ---------------------------------------------------------------------------
$Configs = @(
    [System.Environment]::GetEnvironmentVariable("BABELSHARK_CONFIG_PATH"),
    [System.Environment]::GetEnvironmentVariable("DICOM_CONFIG_PATH"),
    [System.Environment]::GetEnvironmentVariable("QC_CONFIG_PATH"),
    [System.Environment]::GetEnvironmentVariable("RECOVERY_SENTRY_CONFIG")
)

$Missing = @()
foreach ($cfg in $Configs) {
    if ($cfg -and -not (Test-Path $cfg)) {
        $Missing += $cfg
    }
}
if ($Missing.Count -gt 0) {
    Write-Warning "The following config files are missing:"
    $Missing | ForEach-Object { Write-Warning "  $_" }
    Write-Warning "The orchestrator may fail to start affected services."
}

# ---------------------------------------------------------------------------
# Check dry-run safety
# ---------------------------------------------------------------------------
$DicomCfg = [System.Environment]::GetEnvironmentVariable("DICOM_CONFIG_PATH")
if ($DicomCfg -and (Test-Path $DicomCfg)) {
    $DicomContent = Get-Content $DicomCfg -Raw
    if ($DicomContent -match "dry_run:\s*false" -or $DicomContent -match "upload_via_c_store:\s*true") {
        Write-Warning ""
        Write-Warning "ATTENTION: DICOM config has real upload enabled (dry_run: false or upload_via_c_store: true)."
        Write-Warning "Confirm this is intentional before proceeding."
        Write-Warning ""
    } else {
        Write-Host "  [safe] DICOM upload is in dry-run mode." -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# Start orchestrator
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Starting Palantir orchestrator..." -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop all services."
Write-Host ""

Set-Location $ProjectRoot
& "$CondaEnv\Scripts\pathoryx-orchestrate.exe"
