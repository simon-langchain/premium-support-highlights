#!/usr/bin/env bash
# Start the Premium Support Highlights dashboard.
# Usage: ./start.sh [-v]
#   -v  Stream service logs to the terminal. Default writes to logs/
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
LOG_DIR="$SCRIPT_DIR/logs"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

_missing=()
command -v uv   >/dev/null 2>&1 || _missing+=("uv   — https://docs.astral.sh/uv/getting-started/installation/")
command -v node >/dev/null 2>&1 || _missing+=("node — https://nodejs.org")

if [ ${#_missing[@]} -gt 0 ]; then
  echo "Missing prerequisites:" >&2
  for item in "${_missing[@]}"; do echo "  $item" >&2; done
  exit 1
fi

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

VERBOSE=false
for arg in "$@"; do
  case "$arg" in -v|--verbose) VERBOSE=true ;; esac
done

# ---------------------------------------------------------------------------
# Environment — create .env if missing, prompt for any empty required vars
# ---------------------------------------------------------------------------

# Write VAR=VALUE to .env (update existing line, or append if absent)
_save_env_var() {
  local var="$1" value="$2"
  if grep -qE "^${var}=" "$ENV_FILE"; then
    if [[ "$(uname)" == "Darwin" ]]; then
      sed -i '' "s|^${var}=.*|${var}=${value}|" "$ENV_FILE"
    else
      sed -i "s|^${var}=.*|${var}=${value}|" "$ENV_FILE"
    fi
  else
    echo "${var}=${value}" >> "$ENV_FILE"
  fi
}

REQUIRED_VARS=(
  "PYLON_API_TOKEN|Pylon API token — https://app.usepylon.com/settings/api-tokens"
  "ANTHROPIC_API_KEY|Anthropic API key — https://console.anthropic.com"
)

if [ ! -f "$ENV_FILE" ]; then
  cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
  echo "Created .env from .env.example"
fi

set -a; source "$ENV_FILE"; set +a

_env_changed=false
for entry in "${REQUIRED_VARS[@]}"; do
  var="${entry%%|*}"
  desc="${entry#*|}"
  if [ -z "${!var:-}" ]; then
    printf "\n%s\n  %s\n  Enter value: " "$var" "$desc"
    read -r value
    [ -z "$value" ] && { echo "Error: $var is required." >&2; exit 1; }
    _save_env_var "$var" "$value"
    _env_changed=true
  fi
done

# ---------------------------------------------------------------------------
# LangSmith tracing — if enabled but API key is missing, confirm or disable
# ---------------------------------------------------------------------------

if [ "${LANGSMITH_TRACING:-false}" = "true" ] && [ -z "${LANGSMITH_API_KEY:-}" ]; then
  printf "\nLangSmith tracing is enabled but LANGSMITH_API_KEY is not set.\n"
  printf "  Enable tracing? (y/n) "
  read -r _ls_enable
  if [[ "$_ls_enable" =~ ^[Yy] ]]; then
    printf "\n  LANGSMITH_API_KEY — https://smith.langchain.com\n  Enter value: "
    read -r _ls_key
    if [ -z "$_ls_key" ]; then
      echo "  No key entered — disabling tracing."
      _save_env_var "LANGSMITH_TRACING" "false"
    else
      _save_env_var "LANGSMITH_API_KEY" "$_ls_key"
      export LANGSMITH_API_KEY="$_ls_key"
      printf "\n  Other LangSmith settings (edit .env to change):\n"
      printf "    LANGSMITH_PROJECT  = %s\n" "${LANGSMITH_PROJECT:-premium-support-highlights}"
      printf "    LANGSMITH_ENDPOINT = %s\n" "${LANGSMITH_ENDPOINT:-https://api.smith.langchain.com}"
    fi
  else
    printf "  Tracing disabled.\n"
    _save_env_var "LANGSMITH_TRACING" "false"
  fi
  _env_changed=true
fi

if [ "$_env_changed" = true ]; then
  set -a; source "$ENV_FILE"; set +a
  echo; echo "Settings saved to .env"
fi

# ---------------------------------------------------------------------------
# Dependencies (fast no-op if already up to date)
# ---------------------------------------------------------------------------

echo
echo "Checking dependencies..."
(cd "$SCRIPT_DIR/backend" && uv sync --quiet)
if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
  echo "Installing frontend dependencies..."
  (cd "$SCRIPT_DIR/frontend" && npm install --silent)
fi

# ---------------------------------------------------------------------------
# Start services
# ---------------------------------------------------------------------------

mkdir -p "$LOG_DIR"

# Poll a port until it accepts connections, printing a checklist item.
# Usage: _wait_for_port LABEL PORT TIMEOUT_SECS LOG_FILE
# Falls back gracefully if /dev/tcp is unavailable.
_wait_for_port() {
  local label="$1" port="$2" timeout="$3" logfile="$4"
  printf "  %-14s" "$label"
  local attempts=$((timeout * 2))
  for _ in $(seq 1 $attempts); do
    (echo > /dev/tcp/localhost/"$port") 2>/dev/null && { echo "✓"; return; }
    sleep 0.5
  done
  echo "slow to start — check $logfile"
}

# Poll the langgraph /ok health endpoint until the Python app is fully ready.
# Usage: _wait_for_backend LABEL PORT TIMEOUT_SECS LOG_FILE
_wait_for_backend() {
  local label="$1" port="$2" timeout="$3" logfile="$4"
  printf "  %-14s" "$label"
  local attempts=$((timeout * 2))
  for _ in $(seq 1 $attempts); do
    if curl -sf "http://localhost:${port}/ok" >/dev/null 2>&1; then
      echo "✓"; return
    fi
    sleep 0.5
  done
  echo "slow to start — check $logfile"
}

echo

if [ "$VERBOSE" = true ]; then
  # Use exec inside the subshells so BACKEND_PID/FRONTEND_PID point to the
  # actual processes rather than a bash wrapper. This lets `wait` in _cleanup
  # properly block until they fully finish their graceful shutdown.
  echo "Starting services (verbose — logs to terminal)..."
  bash -c "cd '$SCRIPT_DIR' && \
    exec uv run --project '$SCRIPT_DIR/backend' langgraph dev --port 8000 --no-browser" &
  BACKEND_PID=$!
  _wait_for_backend "Backend"   8000 60 "/dev/stderr"
  bash -c "cd '$SCRIPT_DIR/frontend' && \
    NODE_NO_WARNINGS=1 exec npm run dev" &
  FRONTEND_PID=$!
else
  # Start both services in the background directly (not via $() subshell, which
  # would hang waiting for the long-running processes to exit before returning).
  echo "Starting services..."

  bash -c "cd '$SCRIPT_DIR' && \
    uv run --project '$SCRIPT_DIR/backend' langgraph dev --port 8000 --no-browser \
    >> '$LOG_DIR/backend.log' 2>&1" &
  BACKEND_PID=$!
  _wait_for_backend "Backend"   8000 60 "$LOG_DIR/backend.log"

  bash -c "cd '$SCRIPT_DIR/frontend' && \
    NODE_NO_WARNINGS=1 npm run dev \
    >> '$LOG_DIR/frontend.log' 2>&1" &
  FRONTEND_PID=$!
  _wait_for_port "Dashboard" 3000 60 "$LOG_DIR/frontend.log"
fi

echo
echo "Running → http://localhost:3000  (Ctrl+C to stop)"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

_cleanup() {
  echo; echo "Stopping..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  exit 0
}
trap _cleanup INT TERM

wait
