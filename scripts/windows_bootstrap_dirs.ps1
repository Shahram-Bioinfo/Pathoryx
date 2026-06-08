#Requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap all required data and log directories for Palantir on Windows.

.DESCRIPTION
    Creates the full directory tree under C:\Users\Public\projects\Palantir\.
    Safe to re-run: uses -Force so existing directories are not overwritten.

.EXAMPLE
    .\scripts\windows_bootstrap_dirs.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Public\projects\Palantir"

$Dirs = @(
    "data\watch",
    "data\scanner_fake",
    "data\staging",
    "data\final",
    "data\failed",
    "data\suspicious",
    "data\manual_review",
    "data\dicom_output",
    "data\run_output",
    "data\labels",
    "data\label_crops",
    "data\qc_output",
    "data\quarantine",
    "data\roi_debug",
    "data\roi_debug_parts",
    "data\special_cases",
    "logs",
    "models_weights"
)

Write-Host ""
Write-Host "Palantir — bootstrapping data directories under $ProjectRoot" -ForegroundColor Cyan
Write-Host ""

$Created = 0
$Existing = 0

foreach ($Rel in $Dirs) {
    $Full = Join-Path $ProjectRoot $Rel
    if (Test-Path $Full) {
        Write-Host "  [exists]  $Full" -ForegroundColor DarkGray
        $Existing++
    } else {
        New-Item -ItemType Directory -Path $Full -Force | Out-Null
        Write-Host "  [created] $Full" -ForegroundColor Green
        $Created++
    }
}

Write-Host ""
Write-Host "Done. Created: $Created  Already existed: $Existing" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Copy model weights into $ProjectRoot\models_weights\"
Write-Host "  2. Drop test slides into $ProjectRoot\data\watch\"
Write-Host "  3. Run: .\scripts\windows_run_migrations.ps1"
Write-Host ""
