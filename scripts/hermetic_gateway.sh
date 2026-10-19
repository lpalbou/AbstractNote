#!/usr/bin/env bash
# A hermetic AbstractGateway with AbstractNote installed, for development and smoke tests.
#
#   scripts/hermetic_gateway.sh start   # gateway + scripted model, two user accounts, prints their tokens
#   scripts/hermetic_gateway.sh status
#   scripts/hermetic_gateway.sh stop    # stops both processes (the data dir stays until you delete it)
#
# Settings (environment of THIS script only; the gateway itself starts with a clean environment):
#   VENV        python venv with abstractgateway (the abstractnote-seam worktree) and abstractnote installed
#   ROOT        scratch directory for data, home and logs (default: ./untracked/hermetic)
#   PORT        gateway port, 18800-18899 (default 18851); STUB_PORT scripted model port (default 18852)
#   MAX_ATTACHMENT_BYTES  upload limit for the 413 case (default 1048576)
#   STUB_DELAY_S          seconds the scripted model waits per answer (default 1: a drain is visibly busy)
#
# Every request the scripted model receives is saved under $ROOT/logs/stub_requests/<n>-<schema>.json
# ("chat" when it recognised no AbstractNote schema: the answer was a plain sentence).
#
# The gateway never sees the caller's environment (env -i): no ABSTRACTGATEWAY_AUTH_TOKEN, no provider
# keys, no live flows dir. Its model is scripts/stub_llm.py (OpenAI-compatible, no weights). User
# accounts are on; two users (alice, bob) are created through the admin API with the bootstrap token.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="${VENV:?set VENV to the venv that has abstractgateway and abstractnote installed}"
ROOT="${ROOT:-$REPO/untracked/hermetic}"
PORT="${PORT:-18851}"
STUB_PORT="${STUB_PORT:-18852}"
MAX_ATTACHMENT_BYTES="${MAX_ATTACHMENT_BYTES:-1048576}"
DATA="$ROOT/data"
SCRATCH_HOME="$ROOT/home"
LOGS="$ROOT/logs"
GW="http://127.0.0.1:$PORT"

for p in "$PORT" "$STUB_PORT"; do
  if [[ "$p" == "8080" || "$p" -lt 18800 || "$p" -gt 18899 ]]; then
    echo "Use scratch ports in 18800-18899 (never 8080)." >&2
    exit 2
  fi
done

pid_alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

start() {
  mkdir -p "$DATA" "$SCRATCH_HOME" "$LOGS" "$ROOT/tmp"
  if pid_alive "$ROOT/gateway.pid"; then echo "already running: $GW"; exit 0; fi
  for p in "$PORT" "$STUB_PORT"; do
    if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "Port $p is already in use; choose PORT/STUB_PORT or stop what listens there." >&2
      exit 1
    fi
  done
  nohup env -i PATH="$VENV/bin:/usr/bin:/bin" HOME="$SCRATCH_HOME" \
    "$VENV/bin/python" "$HERE/stub_llm.py" --port "$STUB_PORT" --delay "${STUB_DELAY_S:-1}" \
    --log-requests "$LOGS/stub_requests" >"$LOGS/stub_llm.log" 2>&1 &
  echo $! >"$ROOT/stub.pid"
  disown || true
  # The text default every blank provider/model pin resolves to: the scripted model, in the scratch
  # HOME's AbstractCore config (never the operator's). Local engines point at a closed port so a
  # stray call fails at once instead of reaching a real LM Studio or Ollama.
  local dead="http://127.0.0.1:9/v1"
  env -i PATH="$VENV/bin:/usr/bin:/bin" HOME="$SCRATCH_HOME" LMSTUDIO_BASE_URL="$dead" OLLAMA_BASE_URL="$dead" \
    "$VENV/bin/abstractcore" config set-default output.text --provider openai-compatible --model note-stub >"$LOGS/abstractcore-config.log" 2>&1
  nohup env -i PATH="$VENV/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$SCRATCH_HOME" \
    XDG_CONFIG_HOME="$SCRATCH_HOME/.config" HF_HOME="$SCRATCH_HOME/hf" TMPDIR="$ROOT/tmp" LANG=C.UTF-8 \
    LMSTUDIO_BASE_URL="$dead" OLLAMA_BASE_URL="$dead" \
    ABSTRACTGATEWAY_DATA_DIR="$DATA" \
    OPENAI_BASE_URL="http://127.0.0.1:$STUB_PORT/v1" OPENAI_API_KEY=stub \
    ABSTRACTGATEWAY_MAX_ATTACHMENT_BYTES="$MAX_ATTACHMENT_BYTES" \
    ABSTRACTGATEWAY_AUTO_PUBLISH_SHIPPED=0 ABSTRACTGATEWAY_POLL_S=0.2 \
    "$VENV/bin/abstractgateway" serve --host 127.0.0.1 --port "$PORT" --no-tray --no-print-token \
      --data-dir "$DATA" >"$LOGS/gateway.log" 2>&1 &
  echo $! >"$ROOT/gateway.pid"
  disown || true
  for _ in $(seq 1 120); do
    if curl -fsS "$GW/api/health" >/dev/null 2>&1; then break; fi
    if ! pid_alive "$ROOT/gateway.pid"; then echo "the gateway exited; see $LOGS/gateway.log" >&2; exit 1; fi
    sleep 0.5
  done
  curl -fsS "$GW/api/health" >/dev/null || { echo "the gateway did not answer; see $LOGS/gateway.log" >&2; exit 1; }
  for _ in $(seq 1 60); do [[ -s "$DATA/auth/bootstrap-admin-token" ]] && break; sleep 0.5; done
  local admin
  admin="$(cat "$DATA/auth/bootstrap-admin-token")"
  : >"$ROOT/tokens.env"
  for user in alice bob; do
    local token
    token="$(curl -fsS -X POST "$GW/api/gateway/admin/users" -H "Authorization: Bearer $admin" \
      -H 'Content-Type: application/json' -d "{\"user_id\": \"$user\", \"roles\": [\"user\"]}" \
      | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
    echo "${user}=${token}" >>"$ROOT/tokens.env"
  done
  echo "gateway: $GW (data $DATA, logs $LOGS)"
  echo "alice:  --token $(grep '^alice=' "$ROOT/tokens.env" | cut -d= -f2-)"
  echo "bob:    --token $(grep '^bob=' "$ROOT/tokens.env" | cut -d= -f2-)"
}

stop() {
  for name in gateway stub; do
    if pid_alive "$ROOT/$name.pid"; then
      kill "$(cat "$ROOT/$name.pid")" 2>/dev/null || true
      for _ in $(seq 1 40); do pid_alive "$ROOT/$name.pid" || break; sleep 0.25; done
      pid_alive "$ROOT/$name.pid" && kill -9 "$(cat "$ROOT/$name.pid")" 2>/dev/null || true
    fi
    rm -f "$ROOT/$name.pid"
  done
  echo "stopped"
}

status() {
  for name in gateway stub; do
    if pid_alive "$ROOT/$name.pid"; then echo "$name: running (pid $(cat "$ROOT/$name.pid"))"; else echo "$name: stopped"; fi
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
