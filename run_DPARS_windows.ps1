# run_palantir_windows.ps1
# DPARS / Pathoryx Enterprise Windows Development Launcher
#
# Usage:
#   conda activate palantir
#   cd D:\Slides\Palantir
#   powershell -ExecutionPolicy Bypass -File .\run_palantir_windows.ps1
#
# Stop:
#   powershell -ExecutionPolicy Bypass -File .\run_palantir_windows.ps1 -Stop
#
# Intranet / laboratory monitor:
#   This launcher starts the frontend on 0.0.0.0 so other computers/signage
#   devices on the intranet can open:
#     http://<SERVER_IP>:5173
#     http://<SERVER_IP>:5173/wallboard

param(
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

# =============================================================================
# Project
# =============================================================================
$PROJECT_DIR   = "D:\Slides\Palantir"
$BACKEND_PORT  = 8090
$FRONTEND_PORT = 5173

$PID_FILE = Join-Path $env:TEMP "palantir_windows_pids.json"
$LOG_DIR  = Join-Path $env:TEMP "palantir_logs"

# =============================================================================
# Binary / DLL paths confirmed on this Windows machine
# =============================================================================
$POSTGRES_BIN          = "C:\Program Files\PostgreSQL\18\bin"
$OPENSLIDE_DLL_PATH   = "D:\Slides\WSI-Babel-Shark\config\openslide-bin-4.0.0.8-windows-x64\bin"
$WSIDICOMIZER_SCRIPTS = "C:\Users\Public\conda-envs\wsidicomizer\Scripts"
$WSIDICOMIZER_LIB_BIN = "C:\Users\Public\conda-envs\wsidicomizer\Library\bin"
$WSIDICOMIZER_EXE     = "C:\Users\Public\conda-envs\wsidicomizer\Scripts\wsidicomizer.exe"
$TURBOJPEG_DLL        = "C:\Users\Public\conda-envs\wsidicomizer\Library\bin\turbojpeg.dll"
$DCMTK_BIN_DIR        = "C:\Program Files\dcmtk-3.7.0-win64-dynamic\bin"

# =============================================================================
# Active config files confirmed in D:\Slides\Palantir\configs
# =============================================================================
$BABELSHARK_CONFIG_PATH    = "D:\Slides\Palantir\configs\babelshark_config.windows.yaml"
$QC_CONFIG_PATH            = "D:\Slides\Palantir\configs\qc_config.windows.yaml"
$QC_SERVICE_CONFIG         = "D:\Slides\Palantir\configs\qc_service.yaml"
$DICOM_CONFIG_PATH         = "D:\Slides\Palantir\configs\dicom_config.windows.yaml"
$RECOVERY_SENTRY_CONFIG    = "D:\Slides\Palantir\configs\recovery_sentry.yaml"
$SCANNER_FLEET_CONFIG      = "D:\Slides\Palantir\configs\scanner_fleet.yaml"

# =============================================================================
# Helpers
# =============================================================================
function Ok($msg)   { Write-Host "[ OK  ] $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "[INFO ] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[WARN ] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[FAIL ] $msg" -ForegroundColor Red }
function Die($msg)  { Fail $msg; exit 1 }

function Add-ToPathIfExists($PathToAdd) {
    if (Test-Path $PathToAdd) {
        if (($env:PATH -split ';') -notcontains $PathToAdd) {
            $env:PATH += ";$PathToAdd"
        }
        Ok "PATH includes: $PathToAdd"
    } else {
        Warn "Path not found: $PathToAdd"
    }
}

function Test-PortInUse($Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conn
}

function Get-ServerIPv4 {
    try {
        $ip = (
            Get-NetIPAddress -AddressFamily IPv4 |
            Where-Object {
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254*" -and
                $_.PrefixOrigin -ne "WellKnown"
            } |
            Sort-Object InterfaceMetric |
            Select-Object -First 1 -ExpandProperty IPAddress
        )
        if ($ip) { return $ip }
    } catch {
        Warn "Could not auto-detect intranet IP: $($_.Exception.Message)"
    }
    return "UNKNOWN"
}

function Stop-PalantirServices {
    if (-not (Test-Path $PID_FILE)) {
        Warn "No PID file found: $PID_FILE"
        return
    }

    $pids = Get-Content $PID_FILE -Raw | ConvertFrom-Json

    foreach ($name in @("BackendPid", "OrchestratorPid", "FrontendPid")) {
        $pidValue = $pids.$name
        if ($pidValue) {
            $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $pidValue -Force
                Ok "Stopped $name PID=$pidValue"
            }
        }
    }

    Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
    Ok "Stopped DPARS services"
}

# =============================================================================
# Stop mode
# =============================================================================
if ($Stop) {
    Stop-PalantirServices
    exit 0
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════════════════════════╗"
Write-Host "║   DPARS / Pathoryx Enterprise — Windows Development Launcher                    ║"
Write-Host "╚══════════════════════════════════════════════════════════════════════════════════╝"
Write-Host ""

# =============================================================================
# Preconditions
# =============================================================================
if (-not (Test-Path $PROJECT_DIR)) {
    Die "Project directory not found: $PROJECT_DIR"
}

Set-Location $PROJECT_DIR

# Add required executable/DLL folders to PATH
Add-ToPathIfExists $POSTGRES_BIN
Add-ToPathIfExists $WSIDICOMIZER_SCRIPTS
Add-ToPathIfExists $WSIDICOMIZER_LIB_BIN
Add-ToPathIfExists $DCMTK_BIN_DIR
Add-ToPathIfExists $OPENSLIDE_DLL_PATH

# =============================================================================
# Environment variables
# =============================================================================
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:password@localhost:5432/pathoryx"

$env:OPENSLIDE_DLL_PATH = $OPENSLIDE_DLL_PATH
$env:TURBOJPEG = $TURBOJPEG_DLL
$env:DCMTK_BIN_DIR = $DCMTK_BIN_DIR

$env:BABELSHARK_CONFIG_PATH = $BABELSHARK_CONFIG_PATH
$env:QC_CONFIG_PATH = $QC_CONFIG_PATH
$env:QC_SERVICE_CONFIG = $QC_SERVICE_CONFIG
$env:DICOM_CONFIG_PATH = $DICOM_CONFIG_PATH
$env:RECOVERY_SENTRY_CONFIG = $RECOVERY_SENTRY_CONFIG
$env:SCANNER_FLEET_CONFIG = $SCANNER_FLEET_CONFIG

# Sectra / PACS values inferred from previous successful dcmsendim log
$env:DICOM_PERFORM_UPLOAD = "true"
$env:SECTRA_HOST = "path-pacs2"
$env:SECTRA_PORT = "32001"
$env:SECTRA_REMOTE_AE = "DICOM_STORAGE"
$env:SECTRA_LOCAL_AE = "DICOM_STORAGE"

# Current code may use storescu if C-STORE is later wired.
# NOTE: Current upload_service was found to finalize DB status only; real C-STORE
# still needs code-level fix if not implemented by DICOM service.
$env:SECTRA_CSTORE_BIN = "storescu"

# Encoding / logging
$env:LOG_LEVEL = "INFO"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PGCLIENTENCODING = "UTF8"

# =============================================================================
# Validation
# =============================================================================
if (-not ($env:CONDA_DEFAULT_ENV -eq "palantir" -or $env:CONDA_PREFIX -like "*palantir*")) {
    Warn "Current Conda env does not look like palantir."
    Warn "Recommended first command: conda activate palantir"
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { Die "python not found. Activate conda env: conda activate palantir" }

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) { Die "npm not found" }

$requiredFilesOrDirs = @(
    $env:OPENSLIDE_DLL_PATH,
    $env:TURBOJPEG,
    $env:DCMTK_BIN_DIR,
    $WSIDICOMIZER_EXE,
    $env:BABELSHARK_CONFIG_PATH,
    $env:QC_CONFIG_PATH,
    $env:QC_SERVICE_CONFIG,
    $env:DICOM_CONFIG_PATH,
    $env:RECOVERY_SENTRY_CONFIG,
    $env:SCANNER_FLEET_CONFIG
)

foreach ($item in $requiredFilesOrDirs) {
    if (-not (Test-Path $item)) {
        Die "Required path not found: $item"
    }
}

Ok "Project directory: $PROJECT_DIR"
Ok "Python: $(& python --version 2>&1)"
Ok "Python executable: $($pythonCmd.Source)"
Ok "npm: $(& npm --version)"
Ok "DATABASE_URL is set"
Ok "OPENSLIDE_DLL_PATH: $env:OPENSLIDE_DLL_PATH"
Ok "TURBOJPEG: $env:TURBOJPEG"
Ok "DCMTK_BIN_DIR: $env:DCMTK_BIN_DIR"
Ok "wsidicomizer: $WSIDICOMIZER_EXE"
Ok "BABELSHARK_CONFIG_PATH: $env:BABELSHARK_CONFIG_PATH"
Ok "QC_CONFIG_PATH: $env:QC_CONFIG_PATH"
Ok "QC_SERVICE_CONFIG: $env:QC_SERVICE_CONFIG"
Ok "DICOM_CONFIG_PATH: $env:DICOM_CONFIG_PATH"
Ok "RECOVERY_SENTRY_CONFIG: $env:RECOVERY_SENTRY_CONFIG"
Ok "SCANNER_FLEET_CONFIG: $env:SCANNER_FLEET_CONFIG"
Ok "Sectra target: $env:SECTRA_HOST:$env:SECTRA_PORT"
Ok "Sectra AE: local=$env:SECTRA_LOCAL_AE remote=$env:SECTRA_REMOTE_AE"

try { & psql --version | Out-Host } catch { Warn "psql not available or failed" }
try { & dcmdump --version | Select-Object -First 1 | Out-Host } catch { Die "dcmdump failed. Check DCMTK path." }
try { & dcmodify --version | Select-Object -First 1 | Out-Host } catch { Die "dcmodify failed. Check DCMTK path." }

if (Test-PortInUse $BACKEND_PORT) {
    Warn "Backend port $BACKEND_PORT is already in use"
}
if (Test-PortInUse $FRONTEND_PORT) {
    Warn "Frontend port $FRONTEND_PORT is already in use"
}
if (Test-Path $PID_FILE) {
    Warn "Existing PID file found. Stop old services first if needed:"
    Warn "powershell -ExecutionPolicy Bypass -File .\run_palantir_windows.ps1 -Stop"
}

New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
$TS = Get-Date -Format "yyyyMMdd_HHmmss"

$BACKEND_LOG  = Join-Path $LOG_DIR "backend_$TS.log"
$ORCH_LOG     = Join-Path $LOG_DIR "orchestrator_$TS.log"
$FRONTEND_LOG = Join-Path $LOG_DIR "frontend_$TS.log"

# =============================================================================
# Start services
# =============================================================================
Info "Starting backend..."
$backend = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m", "pathoryx_enterprise.services.dashboard.main" `
    -WorkingDirectory $PROJECT_DIR `
    -RedirectStandardOutput $BACKEND_LOG `
    -RedirectStandardError $BACKEND_LOG `
    -PassThru `
    -WindowStyle Hidden
Ok "Backend PID: $($backend.Id)"

Info "Starting orchestrator..."
$orchestrator = Start-Process `
    -FilePath "pathoryx-orchestrate" `
    -WorkingDirectory $PROJECT_DIR `
    -RedirectStandardOutput $ORCH_LOG `
    -RedirectStandardError $ORCH_LOG `
    -PassThru `
    -WindowStyle Hidden
Ok "Orchestrator PID: $($orchestrator.Id)"

Info "Starting frontend for local + intranet access..."
$frontend = Start-Process `
    -FilePath "npm" `
    -ArgumentList "run", "dev", "--", "--host", "0.0.0.0", "--port", "$FRONTEND_PORT" `
    -WorkingDirectory (Join-Path $PROJECT_DIR "dashboard-ui") `
    -RedirectStandardOutput $FRONTEND_LOG `
    -RedirectStandardError $FRONTEND_LOG `
    -PassThru `
    -WindowStyle Hidden
Ok "Frontend PID: $($frontend.Id)"

@{
    BackendPid      = $backend.Id
    OrchestratorPid = $orchestrator.Id
    FrontendPid     = $frontend.Id
    StartedAt       = (Get-Date).ToString("s")
    ProjectDir      = $PROJECT_DIR
    BackendLog      = $BACKEND_LOG
    OrchestratorLog = $ORCH_LOG
    FrontendLog     = $FRONTEND_LOG
} | ConvertTo-Json | Set-Content -Path $PID_FILE -Encoding UTF8

Start-Sleep -Seconds 5

$ServerIP = Get-ServerIPv4

$LOCAL_DASHBOARD_URL = "http://127.0.0.1:$FRONTEND_PORT"
$LOCAL_WALLBOARD_URL = "http://127.0.0.1:$FRONTEND_PORT/wallboard"
$LOCAL_API_URL       = "http://127.0.0.1:$BACKEND_PORT/dashboard/api"
$LOCAL_SWAGGER_URL   = "http://127.0.0.1:$BACKEND_PORT/dashboard/docs"

$INTRANET_DASHBOARD_URL = "http://$($ServerIP):$FRONTEND_PORT"
$INTRANET_WALLBOARD_URL = "http://$($ServerIP):$FRONTEND_PORT/wallboard"
$INTRANET_API_URL       = "http://$($ServerIP):$BACKEND_PORT/dashboard/api"

try {
    Start-Process $LOCAL_DASHBOARD_URL
} catch {
    Warn "Could not open browser automatically: $($_.Exception.Message)"
}

Write-Host ""
Ok "DPARS started"

Write-Host ""
Write-Host "Local Access"
Write-Host "------------"
Write-Host "Dashboard:"
Write-Host "  $LOCAL_DASHBOARD_URL"
Write-Host ""
Write-Host "Wallboard:"
Write-Host "  $LOCAL_WALLBOARD_URL"
Write-Host ""
Write-Host "API:"
Write-Host "  $LOCAL_API_URL"
Write-Host ""
Write-Host "Swagger:"
Write-Host "  $LOCAL_SWAGGER_URL"
Write-Host ""

Write-Host "Intranet Access"
Write-Host "---------------"
Write-Host "Server IP:"
Write-Host "  $ServerIP"
Write-Host ""
Write-Host "Dashboard:"
Write-Host "  $INTRANET_DASHBOARD_URL"
Write-Host ""
Write-Host "Wallboard:"
Write-Host "  $INTRANET_WALLBOARD_URL"
Write-Host ""
Write-Host "API:"
Write-Host "  $INTRANET_API_URL"
Write-Host ""

Write-Host "Laboratory Monitor"
Write-Host "------------------"
Write-Host "Open this URL on the monitor or signage player:"
Write-Host ""
Write-Host "  $INTRANET_WALLBOARD_URL"
Write-Host ""

Write-Host "Chrome Kiosk Mode"
Write-Host "-----------------"
Write-Host "chrome.exe --kiosk $INTRANET_WALLBOARD_URL"
Write-Host ""

Write-Host "Firewall Note"
Write-Host "-------------"
Write-Host "If another intranet computer cannot open the URLs, allow ports:"
Write-Host "  $FRONTEND_PORT/tcp for dashboard + wallboard"
Write-Host "  $BACKEND_PORT/tcp for API"
Write-Host ""

Write-Host "Logs:"
Write-Host "  Backend:      $BACKEND_LOG"
Write-Host "  Orchestrator: $ORCH_LOG"
Write-Host "  Frontend:     $FRONTEND_LOG"
Write-Host ""

Write-Host "Stop:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\run_palantir_windows.ps1 -Stop"
Write-Host ""
