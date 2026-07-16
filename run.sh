#!/usr/bin/env bash
# Start Article Factory API (+ background orchestrator) + admin web UI.
#
# Usage:
#   ./run.sh              # API + admin UI (LAN-friendly bind)
#   ./run.sh --local      # bind 127.0.0.1 only
#   ./run.sh --no-restart # fail if ports are busy instead of stopping old processes
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BIND="0.0.0.0"
NO_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --local) BIND="127.0.0.1" ;;
    --no-restart) NO_RESTART=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./run.sh [--local] [--no-restart]

  Starts the Article Factory API (with orchestrator loop) and admin UI.

  --local       Bind web UI to 127.0.0.1 only (default: 0.0.0.0 for LAN access)
  --no-restart  Exit with an error if API/web ports are already in use

Environment:
  PORT      API port (default: 8100, or from .env)
  WEB_PORT  Admin UI port (default: 5174)
  HOST      API bind host (default: 127.0.0.1, or from .env)
  CMS_URL   Showroom CMS base URL (set in .env)
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 1
      ;;
  esac
done

VENV="$ROOT/.venv"
PYTHON="python3"
API_PID=""
WEB_PID=""

die() {
  echo "" >&2
  echo "ERROR: $*" >&2
  echo "" >&2
  exit 1
}

info() { echo "==> $*"; }

cleanup() {
  trap - EXIT INT TERM
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

port_listener_pid() {
  local port=$1
  ss -tlnp 2>/dev/null | grep ":${port} " | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1
}

proc_cmdline() {
  local pid=$1
  [[ -n "$pid" && -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline"
}

is_factory_api_pid() {
  local pid=$1
  local cmd
  cmd="$(proc_cmdline "$pid" 2>/dev/null || true)"
  [[ "$cmd" == *article-factory* || "$cmd" == *article_factory* ]]
}

is_factory_web_pid() {
  local pid=$1
  local cmd
  cmd="$(proc_cmdline "$pid" 2>/dev/null || true)"
  [[ "$cmd" == *vite* && "$cmd" == *article-factory* ]]
}

free_port() {
  local port=$1
  local kind=$2
  local pid
  pid="$(port_listener_pid "$port" || true)"
  [[ -z "$pid" ]] && return 0

  local cmd
  cmd="$(proc_cmdline "$pid" 2>/dev/null || echo unknown)"

  if [[ "$kind" == api ]] && is_factory_api_pid "$pid"; then
    if [[ "$NO_RESTART" == 1 ]]; then
      die "API port ${port} is already in use by article-factory (PID ${pid}). Stop it first or omit --no-restart."
    fi
    info "Stopping previous API on port ${port} (PID ${pid}) ..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      [[ -z "$(port_listener_pid "$port" || true)" ]] && return 0
      sleep 0.3
    done
    die "Could not free API port ${port} (PID ${pid} still listening)."
  fi

  if [[ "$kind" == web ]] && is_factory_web_pid "$pid"; then
    if [[ "$NO_RESTART" == 1 ]]; then
      die "Admin UI port ${port} is already in use by Vite (PID ${pid}). Stop it first or omit --no-restart."
    fi
    info "Stopping previous admin UI on port ${port} (PID ${pid}) ..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      [[ -z "$(port_listener_pid "$port" || true)" ]] && return 0
      sleep 0.3
    done
    die "Could not free admin UI port ${port} (PID ${pid} still listening)."
  fi

  die "Port ${port} is already in use by another program (PID ${pid}: ${cmd}). Change PORT / WEB_PORT or stop that process."
}

wait_http() {
  local url=$1
  local name=$2
  local max="${3:-45}"
  local i
  for ((i = 1; i <= max; i++)); do
    if curl -sf --connect-timeout 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "${name} did not respond at ${url} within ${max}s. Check logs above."
}

# --- Prerequisites ---
if [[ -x "$VENV/bin/python" ]]; then
  PYTHON="$VENV/bin/python"
elif [[ ! -d "$VENV" ]]; then
  info "Creating Python virtualenv in .venv ..."
  python3 -m venv "$VENV" || die "Failed to create .venv (is python3 installed?)"
  PYTHON="$VENV/bin/python"
else
  die ".venv exists but $VENV/bin/python is missing. Remove .venv and re-run ./run.sh"
fi

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  info "Created .env from .env.example — edit CONTROL_PLANE_URL, DEFAULT_PULLER, DEFAULT_MODEL, CMS_API_KEY"
fi

info "Installing Python package (editable) ..."
"$VENV/bin/pip" install -q -e ".[dev]" || die "pip install failed"

mkdir -p "$ROOT/data"

API_HOST="${HOST:-127.0.0.1}"
API_PORT="${PORT:-8100}"
WEB_PORT="${WEB_PORT:-5174}"
WEB_HOST="${WEB_HOST:-$BIND}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  API_PORT="${PORT:-$API_PORT}"
fi

if [[ "$BIND" == "0.0.0.0" ]]; then
  API_HOST="0.0.0.0"
  WEB_HOST="0.0.0.0"
else
  API_HOST="${HOST:-127.0.0.1}"
  WEB_HOST="127.0.0.1"
fi

if [[ ! -d "$ROOT/web/node_modules" ]]; then
  info "Installing web dependencies (npm install) ..."
  (cd "$ROOT/web" && npm install) || die "npm install failed in web/"
fi

info "Initializing database (if needed) ..."
"$VENV/bin/article-factory" init-db

FACTORY_API_KEY_DISPLAY="$("$PYTHON" - <<'PY' 2>/dev/null || true
from article_factory.db import SessionLocal
from article_factory.services.runtime_settings import get_effective_factory_api_key, get_or_create_factory_settings
from article_factory.services.factory_api_key_cache import warm_factory_api_key_cache
from article_factory.services.api_keys import is_real_api_key

db = SessionLocal()
try:
    get_or_create_factory_settings(db)
    warm_factory_api_key_cache(db)
    key = get_effective_factory_api_key(db)
    if is_real_api_key(key):
        print(key)
finally:
    db.close()
PY
)"

free_port "$API_PORT" api
free_port "$WEB_PORT" web

info "Starting Article Factory API on ${API_HOST}:${API_PORT}"
"$VENV/bin/article-factory" serve --host "$API_HOST" --port "$API_PORT" >"$ROOT/.api.log" 2>&1 &
API_PID=$!

if ! kill -0 "$API_PID" 2>/dev/null; then
  die "API process exited immediately. Log:\n$(tail -30 "$ROOT/.api.log" 2>/dev/null || echo '(no log)')"
fi

wait_http "http://127.0.0.1:${API_PORT}/api/health" "Article Factory API"

info "Starting admin UI on ${WEB_HOST}:${WEB_PORT} (proxies /api → 127.0.0.1:${API_PORT})"
(cd "$ROOT/web" && npm run dev -- --host "$WEB_HOST" --port "$WEB_PORT") >"$ROOT/.web.log" 2>&1 &
WEB_PID=$!

sleep 1
if ! kill -0 "$WEB_PID" 2>/dev/null; then
  die "Admin UI process exited immediately. Log:\n$(tail -40 "$ROOT/.web.log" 2>/dev/null || echo '(no log)')"
fi

wait_http "http://127.0.0.1:${WEB_PORT}/" "Article Factory admin UI"

HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
LAN_IP="$("$PYTHON" -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(('8.8.8.8', 80))
    print(s.getsockname()[0])
finally:
    s.close()
" 2>/dev/null || echo "")"

echo ""
echo "========================================"
echo "  Article Factory is running"
echo "========================================"
echo "  Admin UI (open in browser):"
echo "    http://127.0.0.1:${WEB_PORT}/"
if [[ -n "$LAN_IP" && "$WEB_HOST" == "0.0.0.0" ]]; then
  echo "    http://${LAN_IP}:${WEB_PORT}/"
  echo "    http://${HOSTNAME_FQDN}:${WEB_PORT}/"
fi
echo ""
echo "  Factory API:"
echo "    http://127.0.0.1:${API_PORT}/api/health"
if [[ -n "$LAN_IP" && "$API_HOST" == "0.0.0.0" ]]; then
  echo "    http://${LAN_IP}:${API_PORT}/api/health"
fi
echo ""
echo "  Logs: ${ROOT}/.api.log  ${ROOT}/.web.log"
if [[ -n "${FACTORY_API_KEY_DISPLAY:-}" ]]; then
  echo ""
  echo "  Factory API key (paste in Settings → Use this key in this browser):"
  echo "    ${FACTORY_API_KEY_DISPLAY}"
fi
echo "  Press Ctrl+C to stop both."
echo "========================================"
echo ""

wait "$API_PID" "$WEB_PID" 2>/dev/null || true
