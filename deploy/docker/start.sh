#!/usr/bin/env bash
# Tick and Book container start (16/11/2026). Each image holds one site:
#   /app/frontend  the built website (customer site or admin site)
#   /app/backend   the Python API (APP_MODE=public or admin)
# This starts the API (inside the container only, 127.0.0.1:8000, as the
# ordinary user "tickbook") and nginx (port 80: the site + /api + /uploads).
# If either stops, the container stops and Docker restarts it.
set -euo pipefail

cd /app/backend
setpriv --reuid=tickbook --regid=tickbook --init-groups \
  uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log --timeout-keep-alive 30 &
api=$!

nginx -g 'daemon off;' &
web=$!

trap 'kill -TERM "$api" "$web" 2>/dev/null || true' TERM INT
set +e
wait -n "$api" "$web"
code=$?
kill -TERM "$api" "$web" 2>/dev/null
wait
exit "$code"
