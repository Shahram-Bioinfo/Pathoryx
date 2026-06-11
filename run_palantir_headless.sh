#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# run_palantir_headless.sh — Palantir non-interactive launcher (tmux/server)
#
# Identical to run_palantir.sh with these differences:
#   • No browser is opened
#   • All conflict/duplicate prompts exit rather than asking interactively
#   • Suitable for tmux sessions, SSH, or headless servers
#
# Usage:
#   chmod +x run_palantir_headless.sh
#   ./run_palantir_headless.sh           — start all services
#   ./run_palantir_headless.sh --stop    — stop previously launched services
#
# Recommended tmux workflow:
#   tmux new-session -d -s palantir './run_palantir_headless.sh'
#   tmux split-window -t palantir 'tail -f /tmp/palantir_logs/backend_*.log'
#   tmux split-window -t palantir 'tail -f /tmp/palantir_logs/frontend_*.log'
#   tmux attach -t palantir
# ═══════════════════════════════════════════════════════════════════════════════

# ── Configuration ─────────────────────────────────────────────────────────────

readonly PROJECT_DIR="/home/shahram/Palantir"
readonly CONDA_ENV="palantir"
readonly BACKEND_PORT=8090
readonly FRONTEND_PORT=5173
readonly PID_FILE="/tmp/palantir.pid"
readonly LOG_DIR="/tmp/palantir_logs"
readonly BACKEND_READY_TIMEOUT=35
readonly FRONTEND_READY_TIMEOUT=40

# ── Logging (no colours — output may be piped to a log file) ──────────────────

info()    { echo "[$(date '+%H:%M:%S') INFO ] $*"; }
ok()      { echo "[$(date '+%H:%M:%S')  OK  ] $*"; }
warn()    { echo "[$(date '+%H:%M:%S') WARN ] $*" >&2; }
fail()    { echo "[$(date '+%H:%M:%S') FAIL ] $*" >&2; }
section() { echo ""; echo "── $* ──"; }
die()     { fail "$1"; exit "${2:-1}"; }

# ── Port detection ────────────────────────────────────────────────────────────

port_in_use() {
  local port=$1
  if command -v ss &>/dev/null; then
    ss -tlnp 2>/dev/null | grep -q ":${port}[[:space:]]"
  elif command -v lsof &>/dev/null; then
    lsof -ti ":${port}" &>/dev/null
  else
    return 1
  fi
}

wait_for_port() {
  local port=$1 name=$2 timeout=${3:-25}
  local elapsed=0
  info "Waiting for $name on port $port …"
  while ! port_in_use "$port"; do
    if (( elapsed >= timeout )); then
      warn "$name did not become ready within ${timeout}s — check log files."
      return 1
    fi
    sleep 1
    elapsed=$(( elapsed + 1 ))
  done
  ok "$name is listening on port $port (${elapsed}s)"
  return 0
}

# ── Kill a named process from the PID file ────────────────────────────────────

_try_kill() {
  local label=$1 pid=${2:-0}
  if [[ "${pid}" -gt 0 ]] && kill -0 "$pid" 2>/dev/null; then
    if kill "$pid" 2>/dev/null; then
      ok "Stopped $label (PID $pid)"
    else
      warn "Failed to send TERM to $label (PID $pid)"
    fi
  else
    warn "$label (PID ${pid}) is not running"
  fi
}

# ── --stop handler ────────────────────────────────────────────────────────────

do_stop() {
  section "Stopping Palantir Services"

  if [[ ! -f "$PID_FILE" ]]; then
    warn "No PID file found at $PID_FILE — nothing to stop."
    exit 0
  fi

  local backend_pid orch_pid frontend_pid
  backend_pid=$(grep '^BACKEND_PID='   "$PID_FILE" | cut -d= -f2 || echo 0)
  orch_pid=$(   grep '^ORCH_PID='      "$PID_FILE" | cut -d= -f2 || echo 0)
  frontend_pid=$(grep '^FRONTEND_PID=' "$PID_FILE" | cut -d= -f2 || echo 0)

  _try_kill "Backend"      "$backend_pid"
  _try_kill "Orchestrator" "$orch_pid"
  _try_kill "Frontend"     "$frontend_pid"

  rm -f "$PID_FILE"
  ok "Done.  Log files remain in $LOG_DIR"
  exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────────────

case "${1:-}" in
  --stop) do_stop ;;
  --help|-h)
    grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  '') ;;
  *) die "Unknown argument: $1   (try --stop or --help)" ;;
esac

# ══════════════════════════════════════════════════════════════════════════════
# START SEQUENCE
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "═══════════════════════════════════════════════"
echo " PALANTIR  —  Headless Launcher  ($(date '+%F %T'))"
echo "═══════════════════════════════════════════════"
echo ""

# ── Preflight validation ──────────────────────────────────────────────────────

section "Preflight Checks"

[[ -d "$PROJECT_DIR" ]] || die "Project directory not found: $PROJECT_DIR"
ok "Project directory: $PROJECT_DIR"

command -v conda &>/dev/null || die "conda not found.  Install Miniconda/Anaconda."
ok "conda: $(conda --version 2>&1)"

command -v npm &>/dev/null || die "npm not found.  Install Node.js."
ok "npm: v$(npm --version)"

[[ -f "$PROJECT_DIR/.env" ]] || \
  die ".env not found at $PROJECT_DIR/.env\nCopy from: $PROJECT_DIR/.env.example"
ok ".env found"

# ── Duplicate-launch detection (non-interactive: exit on conflict) ────────────

section "Checking for Existing Instance"

if [[ -f "$PID_FILE" ]]; then
  _bp=$(grep '^BACKEND_PID='  "$PID_FILE" | cut -d= -f2 || echo 0)
  _op=$(grep '^ORCH_PID='     "$PID_FILE" | cut -d= -f2 || echo 0)
  _fp=$(grep '^FRONTEND_PID=' "$PID_FILE" | cut -d= -f2 || echo 0)

  RUNNING_PIDS=""
  for _pid in "$_bp" "$_op" "$_fp"; do
    if [[ "${_pid:-0}" -gt 0 ]] && kill -0 "$_pid" 2>/dev/null; then
      RUNNING_PIDS="$RUNNING_PIDS $_pid"
    fi
  done

  if [[ -n "$RUNNING_PIDS" ]]; then
    die "Palantir is already running (PIDs:$RUNNING_PIDS).\nRun: ./run_palantir_headless.sh --stop   then restart."
  else
    info "Stale PID file found — removing and starting fresh."
    rm -f "$PID_FILE"
  fi
else
  ok "No existing instance detected"
fi

# ── Activate conda environment ────────────────────────────────────────────────

section "Activating Conda Environment"

CONDA_BASE=$(conda info --base 2>/dev/null)
[[ -n "${CONDA_BASE:-}" ]] || die "Could not determine conda base directory."

# shellcheck source=/dev/null
source "${CONDA_BASE}/etc/profile.d/conda.sh" \
  || die "Could not source ${CONDA_BASE}/etc/profile.d/conda.sh"

conda activate "$CONDA_ENV" 2>/dev/null \
  || die "Conda environment '$CONDA_ENV' not found.\nCreate it: conda create -n $CONDA_ENV python=3.12"
ok "Conda environment active: $CONDA_ENV  (python: $(python --version 2>&1))"

# ── Load environment variables from .env ─────────────────────────────────────

section "Loading Environment Variables"

cd "$PROJECT_DIR"
set -a
# shellcheck source=.env
source .env
set +a
ok "Loaded .env"

if [[ -z "${DATABASE_URL:-}" ]]; then
  die "DATABASE_URL is not set in .env — backend will fail to start."
fi
ok "DATABASE_URL is set"

# ── Port conflict checks (non-interactive: exit on conflict) ──────────────────

section "Port Conflict Check"

if port_in_use "$BACKEND_PORT"; then
  die "Port $BACKEND_PORT (backend) is already in use.\nStop the existing process or change PATHORYX_DASHBOARD_PORT in .env."
fi
ok "Port $BACKEND_PORT is free"

if port_in_use "$FRONTEND_PORT"; then
  die "Port $FRONTEND_PORT (frontend) is already in use.\nStop the existing Vite process or choose a different port."
fi
ok "Port $FRONTEND_PORT is free"

# ── Prepare log directory ─────────────────────────────────────────────────────

mkdir -p "$LOG_DIR"
LAUNCH_TS=$(date +%Y%m%d_%H%M%S)
BACKEND_LOG="${LOG_DIR}/backend_${LAUNCH_TS}.log"
ORCH_LOG="${LOG_DIR}/orchestrator_${LAUNCH_TS}.log"
FRONTEND_LOG="${LOG_DIR}/frontend_${LAUNCH_TS}.log"

# ── Launch services ───────────────────────────────────────────────────────────

section "Starting Services"

info "Starting dashboard backend (port $BACKEND_PORT) …"
(cd "$PROJECT_DIR" && exec python -m pathoryx_enterprise.services.dashboard.main) \
  >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
ok "Backend started (PID $BACKEND_PID)"

info "Starting orchestrator …"
(cd "$PROJECT_DIR" && exec pathoryx-orchestrate) \
  >> "$ORCH_LOG" 2>&1 &
ORCH_PID=$!
ok "Orchestrator started (PID $ORCH_PID)"

info "Starting frontend dev server (port $FRONTEND_PORT) …"
(cd "$PROJECT_DIR/dashboard-ui" && exec npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT") \
  >> "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
ok "Frontend started (PID $FRONTEND_PID)"

# ── Persist PIDs ─────────────────────────────────────────────────────────────

cat > "$PID_FILE" <<EOF
BACKEND_PID=${BACKEND_PID}
ORCH_PID=${ORCH_PID}
FRONTEND_PID=${FRONTEND_PID}
LAUNCH_TS=${LAUNCH_TS}
BACKEND_LOG=${BACKEND_LOG}
ORCH_LOG=${ORCH_LOG}
FRONTEND_LOG=${FRONTEND_LOG}
EOF
info "PIDs written to $PID_FILE"

# ── Wait for services to become ready ────────────────────────────────────────

section "Waiting for Services"

wait_for_port "$BACKEND_PORT"  "Backend (FastAPI)"  "$BACKEND_READY_TIMEOUT"
wait_for_port "$FRONTEND_PORT" "Frontend (Vite)"    "$FRONTEND_READY_TIMEOUT"

# ── Summary ───────────────────────────────────────────────────────════════════

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Palantir Stack Running"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Dashboard    http://127.0.0.1:${FRONTEND_PORT}"
echo "  API          http://127.0.0.1:${BACKEND_PORT}/dashboard/api"
echo "  Swagger      http://127.0.0.1:${BACKEND_PORT}/dashboard/docs"
echo ""
echo "  Logs"
echo "    Backend:       $BACKEND_LOG"
echo "    Orchestrator:  $ORCH_LOG"
echo "    Frontend:      $FRONTEND_LOG"
echo ""
echo "  Follow logs (tmux panes suggested):"
echo "    tail -f $BACKEND_LOG"
echo "    tail -f $ORCH_LOG"
echo "    tail -f $FRONTEND_LOG"
echo ""
echo "  Stop:  ./run_palantir_headless.sh --stop"
echo ""
