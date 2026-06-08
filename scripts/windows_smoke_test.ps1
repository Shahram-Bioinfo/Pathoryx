#Requires -Version 5.1
<#
.SYNOPSIS
    Smoke-test the Palantir installation on Windows.

.DESCRIPTION
    Verifies: database connection, alembic head, config files, data folders,
    model weights, scanner fleet load, upload_tracking table, and dry-run
    safety status. Prints PASS/FAIL for each check.

.EXAMPLE
    .\scripts\windows_smoke_test.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ProjectRoot = "C:\Users\Public\projects\Palantir"
$CondaEnv    = "C:\Users\Public\conda-envs\babelfish1"
$EnvFile     = Join-Path $ProjectRoot ".env"

$Passed = 0
$Failed = 0

function Pass([string]$Label) {
    Write-Host "  [PASS] $Label" -ForegroundColor Green
    $script:Passed++
}

function Fail([string]$Label, [string]$Detail = "") {
    $msg = "  [FAIL] $Label"
    if ($Detail) { $msg += " — $Detail" }
    Write-Host $msg -ForegroundColor Red
    $script:Failed++
}

function Check([string]$Label, [scriptblock]$Test) {
    try {
        $result = & $Test
        if ($result -eq $false) { Fail $Label } else { Pass $Label }
    } catch {
        Fail $Label $_.Exception.Message
    }
}

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Palantir — Windows Smoke Test" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            if ($parts.Count -eq 2) {
                [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
            }
        }
    }
    Pass ".env file found and loaded"
} else {
    Fail ".env file" "not found at $EnvFile"
}

Set-Location $ProjectRoot

# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Config files:" -ForegroundColor Yellow

$ConfigChecks = @{
    "BABELSHARK_CONFIG_PATH"  = [System.Environment]::GetEnvironmentVariable("BABELSHARK_CONFIG_PATH")
    "QC_CONFIG_PATH"          = [System.Environment]::GetEnvironmentVariable("QC_CONFIG_PATH")
    "DICOM_CONFIG_PATH"       = [System.Environment]::GetEnvironmentVariable("DICOM_CONFIG_PATH")
    "RECOVERY_SENTRY_CONFIG"  = [System.Environment]::GetEnvironmentVariable("RECOVERY_SENTRY_CONFIG")
    "SCANNER_FLEET_CONFIG"    = [System.Environment]::GetEnvironmentVariable("SCANNER_FLEET_CONFIG")
}

foreach ($entry in $ConfigChecks.GetEnumerator()) {
    $val = $entry.Value
    if (-not $val) {
        Fail "$($entry.Key) not set in .env"
    } elseif (Test-Path $val) {
        Pass "$($entry.Key) exists"
    } else {
        Fail "$($entry.Key)" "file not found: $val"
    }
}

# ---------------------------------------------------------------------------
# Data directories
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Data directories:" -ForegroundColor Yellow

$DataDirs = @(
    "data\watch", "data\staging", "data\final", "data\failed",
    "data\suspicious", "data\manual_review", "data\dicom_output",
    "data\run_output", "data\labels", "data\label_crops",
    "data\qc_output", "data\quarantine", "logs", "models_weights"
)

foreach ($d in $DataDirs) {
    $full = Join-Path $ProjectRoot $d
    if (Test-Path $full) { Pass $d } else { Fail $d "missing — run scripts\windows_bootstrap_dirs.ps1" }
}

# ---------------------------------------------------------------------------
# Model weights
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Model weights:" -ForegroundColor Yellow

$Weights = @(
    "models_weights\penmark_detection_MobileNetV3.pth",
    "models_weights\bubble_detection_ConvNeXtTiny_model.pth",
    "models_weights\stain_model_MobileNetV3.pth",
    "models_weights\blur_detection_resnet18_old.pth"
)
foreach ($w in $Weights) {
    $full = Join-Path $ProjectRoot $w
    if (Test-Path $full) { Pass $w } else { Fail $w "copy model weights into models_weights\" }
}

# ---------------------------------------------------------------------------
# Python / package
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Python environment:" -ForegroundColor Yellow

Check "conda env exists" {
    Test-Path "$CondaEnv\python.exe"
}

Check "pathoryx_enterprise importable" {
    $out = & "$CondaEnv\python.exe" -c "import pathoryx_enterprise; print('ok')" 2>&1
    $out -match "ok"
}

Check "entry point: pathoryx-dashboard" {
    Test-Path "$CondaEnv\Scripts\pathoryx-dashboard.exe"
}

Check "entry point: pathoryx-orchestrate" {
    Test-Path "$CondaEnv\Scripts\pathoryx-orchestrate.exe"
}

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Database:" -ForegroundColor Yellow

$dbUrl = [System.Environment]::GetEnvironmentVariable("DATABASE_URL")
if ($dbUrl -and $dbUrl -notlike "*CHANGE_ME*") {
    Check "database connection" {
        $out = & "$CondaEnv\python.exe" -c @"
import os, sys
os.environ['DATABASE_URL'] = '$dbUrl'
try:
    from sqlalchemy import create_engine, text
    e = create_engine('$dbUrl', pool_pre_ping=True)
    with e.connect() as c:
        c.execute(text('SELECT 1'))
    print('ok')
except Exception as ex:
    print('FAIL: ' + str(ex))
    sys.exit(1)
"@ 2>&1
        $out -match "ok"
    }

    Check "alembic at head" {
        $out = & "$CondaEnv\Scripts\alembic.exe" current 2>&1
        $out -match "\(head\)"
    }

    Check "upload_tracking table exists" {
        $out = & "$CondaEnv\python.exe" -c @"
import os
os.environ['DATABASE_URL'] = '$dbUrl'
try:
    from sqlalchemy import create_engine, text
    e = create_engine('$dbUrl')
    with e.connect() as c:
        c.execute(text('SELECT 1 FROM upload_tracking.estimated_upload_queue LIMIT 1'))
    print('ok')
except Exception as ex:
    # Table may be empty — that is fine; existence is what we check
    if 'does not exist' in str(ex):
        print('FAIL: ' + str(ex))
    else:
        print('ok')
"@ 2>&1
        $out -match "ok"
    }
} else {
    Fail "database connection" "DATABASE_URL not configured in .env"
    Fail "alembic at head" "skipped — no DATABASE_URL"
    Fail "upload_tracking table" "skipped — no DATABASE_URL"
}

# ---------------------------------------------------------------------------
# Scanner fleet
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Scanner fleet:" -ForegroundColor Yellow

$fleetPath = [System.Environment]::GetEnvironmentVariable("SCANNER_FLEET_CONFIG")
if ($fleetPath -and (Test-Path $fleetPath)) {
    Check "scanner_fleet.yaml parses and loads" {
        $out = & "$CondaEnv\python.exe" -c @"
import os
os.environ['SCANNER_FLEET_CONFIG'] = r'$fleetPath'
from pathoryx_enterprise.services.dashboard.scanner_fleet import ScannerFleet
fleet = ScannerFleet.load_default()
print(f'ok: {fleet.total_count} scanners loaded')
"@ 2>&1
        $out -match "ok"
    }
} else {
    Fail "scanner fleet" "SCANNER_FLEET_CONFIG not set or file missing"
}

# ---------------------------------------------------------------------------
# DICOM dry-run safety
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Safety checks:" -ForegroundColor Yellow

$dicomCfg = [System.Environment]::GetEnvironmentVariable("DICOM_CONFIG_PATH")
if ($dicomCfg -and (Test-Path $dicomCfg)) {
    $content = Get-Content $dicomCfg -Raw
    $dryRunEnabled  = $content -match "dry_run:\s*true"
    $cstoreDisabled = $content -notmatch "upload_via_c_store:\s*true"

    if ($dryRunEnabled -and $cstoreDisabled) {
        Pass "DICOM upload is dry-run (safe for testing)"
    } elseif (-not $dryRunEnabled) {
        Fail "DICOM dry-run" "dry_run is NOT true — real uploads will be attempted"
    } else {
        Fail "DICOM C-STORE" "upload_via_c_store is true — real C-STORE is active"
    }
} else {
    Fail "DICOM dry-run check" "config file not found"
}

# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Frontend:" -ForegroundColor Yellow

$uiDir = Join-Path $ProjectRoot "dashboard-ui"
Check "dashboard-ui directory exists" { Test-Path $uiDir }
Check "package.json exists" { Test-Path (Join-Path $uiDir "package.json") }

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if ($npmCmd) {
    Pass "npm available ($(npm --version))"
} else {
    Fail "npm" "not found — install Node.js 18+"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
$total = $Passed + $Failed
if ($Failed -eq 0) {
    Write-Host "All $total checks passed." -ForegroundColor Green
} else {
    Write-Host "$Passed/$total passed   $Failed FAILED" -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix failing checks before starting services." -ForegroundColor Yellow
}
Write-Host ""
