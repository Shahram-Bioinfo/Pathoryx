#!/usr/bin/env bash
set -u

PROJECT_DIR="/home/shahram/Palantir"
BACKEND_PORT=8090
FRONTEND_PORT=5173
PID_FILE="/tmp/palantir.pid"
LOG_DIR="/tmp/palantir_logs"

ok(){ echo "[ OK  ] $*"; }
info(){ echo "[INFO ] $*"; }
warn(){ echo "[WARN ] $*"; }
fail(){ echo "[FAIL ] $*" >&2; }
die(){ fail "$1"; exit 1; }

port_in_use() {
  ss -tlnp 2>/dev/null | grep -q ":$1 "
}

stop_services() {
  if [[ -f "$PID_FILE" ]]; then
    source "$PID_FILE"
    [[ "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
    [[ "${ORCH_PID:-}" ]] && kill "$ORCH_PID" 2>/dev/null || true
    [[ "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
    rm -f "$PID_FILE"
    ok "Stopped Palantir services"
  else
    warn "No PID file found"
  fi
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_services
  exit 0
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     PALANTIR  —  Development Launcher    ║"
echo "╚══════════════════════════════════════════╝"
echo ""

[[ -d "$PROJECT_DIR" ]] || die "Project directory not found: $PROJECT_DIR"
[[ -f "$PROJECT_DIR/.env" ]] || die ".env not found: $PROJECT_DIR/.env"
command -v python >/dev/null 2>&1 || die "python not found"
command -v npm >/dev/null 2>&1 || die "npm not found"

ok "Project directory: $PROJECT_DIR"
ok "Python: $(python --version 2>&1)"
ok "Python executable: $(command -v python)"
ok "npm: $(npm --version)"

cd "$PROJECT_DIR" || exit 1

set -a
source .env
set +a
ok ".env loaded"

if [[ -z "${DATABASE_URL:-}" ]]; then
  die "DATABASE_URL is not set in .env"
fi
ok "DATABASE_URL is set"

if [[ -f "$PID_FILE" ]]; then
  warn "Existing PID file found. Run ./run_palantir.sh --stop if needed."
fi

if port_in_use "$BACKEND_PORT"; then
  warn "Backend port $BACKEND_PORT is already in use"
fi

if port_in_use "$FRONTEND_PORT"; then
  warn "Frontend port $FRONTEND_PORT is already in use"
fi

mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)

BACKEND_LOG="$LOG_DIR/backend_$TS.log"
ORCH_LOG="$LOG_DIR/orchestrator_$TS.log"
FRONTEND_LOG="$LOG_DIR/frontend_$TS.log"

info "Starting backend..."
(
  cd "$PROJECT_DIR"
  python -m pathoryx_enterprise.services.dashboard.main
) >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
ok "Backend PID: $BACKEND_PID"

info "Starting orchestrator..."
(
  cd "$PROJECT_DIR"
  pathoryx-orchestrate
) >"$ORCH_LOG" 2>&1 &
ORCH_PID=$!
ok "Orchestrator PID: $ORCH_PID"

info "Starting frontend..."
(
  cd "$PROJECT_DIR/dashboard-ui"
  npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT"
) >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
ok "Frontend PID: $FRONTEND_PID"

cat > "$PID_FILE" <<PIDEOF
BACKEND_PID=$BACKEND_PID
ORCH_PID=$ORCH_PID
FRONTEND_PID=$FRONTEND_PID
PIDEOF

sleep 5

URL="http://127.0.0.1:$FRONTEND_PORT"

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
fi

echo ""
ok "Palantir started"
echo "Dashboard:    $URL"
echo "API:          http://127.0.0.1:$BACKEND_PORT/dashboard/api"
echo "Swagger:      http://127.0.0.1:$BACKEND_PORT/dashboard/docs"
echo ""
echo "Logs:"
echo "  Backend:      $BACKEND_LOG"
echo "  Orchestrator: $ORCH_LOG"
echo "  Frontend:     $FRONTEND_LOG"
echo ""
echo "Stop:"
echo "  ./run_palantir.sh --stop"
echo ""
