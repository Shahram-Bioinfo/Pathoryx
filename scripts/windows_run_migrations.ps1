#Requires -Version 5.1
<#
.SYNOPSIS
    Load .env and run Alembic database migrations.

.DESCRIPTION
    Activates the conda environment, loads .env, and runs
    'alembic upgrade head' from the project root.

.EXAMPLE
    .\scripts\windows_run_migrations.ps1
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
# Verify DATABASE_URL is set
# ---------------------------------------------------------------------------
$dbUrl = [System.Environment]::GetEnvironmentVariable("DATABASE_URL")
if (-not $dbUrl -or $dbUrl -like "*CHANGE_ME*") {
    Write-Error "DATABASE_URL is not configured in .env. Set a real PostgreSQL connection string."
}
Write-Host "DATABASE_URL: $($dbUrl -replace ':([^:@]+)@', ':***@')" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Run migrations
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Running: alembic upgrade head" -ForegroundColor Cyan
Set-Location $ProjectRoot

& "$CondaEnv\Scripts\alembic.exe" upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Error "alembic upgrade head failed (exit code $LASTEXITCODE)."
}

Write-Host ""
Write-Host "Migrations complete." -ForegroundColor Green
Write-Host "Verify with: psql `"$dbUrl`" -c `"\dn`"" -ForegroundColor Yellow
Write-Host ""
