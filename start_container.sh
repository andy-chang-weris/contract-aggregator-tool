#!/bin/sh
set -eu

: "${FRONTEND_PORT:=8000}"
: "${PROXY_PORT:=5000}"
: "${CHAT_SERVER_PORT:=5055}"
: "${CHAT_SERVER_HOST:=0.0.0.0}"
: "${DB_SECRET_NAME:=contract-aggregator/dev/database}"
: "${AWS_DEFAULT_REGION:=us-east-1}"

export FRONTEND_PORT PROXY_PORT CHAT_SERVER_PORT CHAT_SERVER_HOST DB_SECRET_NAME AWS_DEFAULT_REGION

pids=""

shutdown() {
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
}
trap shutdown EXIT INT TERM

echo "Starting proxy.py on http://0.0.0.0:${PROXY_PORT}"
gunicorn proxy:app --bind "0.0.0.0:${PROXY_PORT}" &
pids="$pids $!"

echo "Starting chat_server.py on http://${CHAT_SERVER_HOST}:${CHAT_SERVER_PORT}"
(
  cd /app/agent
  python interactive-ui/chat_server.py
) &
pids="$pids $!"

echo "Serving frontend on http://0.0.0.0:${FRONTEND_PORT}"
python -m http.server "${FRONTEND_PORT}" -d /app &
pids="$pids $!"

wait