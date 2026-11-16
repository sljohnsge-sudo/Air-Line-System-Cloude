#!/usr/bin/env bash
# Tick and Book container start (16/11/2026). Each image holds one site:
#   /app/frontend  the built website (customer site or admin site)
#   /app/backend   the Python API (APP_MODE=public or admin)
# This starts the API (inside the container only, 127.0.0.1:8000, as the
# ordinary user "tickbook") and nginx (port 80: the site + /api + /uploads).
# If either stops, the container stops and Docker restarts it.
set -euo pipefail

say() { printf '%s\n' "$@" >&2; }

# 1. Settings. backend/.env (database, Travelport, keys) is never built into
#    the image; docker compose passes it. Docker Desktop's Run button does not.
if [ -z "${JWT_SECRET_KEY:-}" ]; then
  say "=====================================================================" \
      " Tick and Book: this container was started WITHOUT its settings" \
      " (backend/.env: database, Travelport, keys - never built into the image)." \
      "" \
      " Start it with docker compose, in the project folder:" \
      "     docker compose up -d --no-build" \
      " or double-click deploy\\local-test.bat on the laptop." \
      " Docker Desktop's Run button does not pass backend/.env." \
      "====================================================================="
  exit 78
fi

# 2. MySQL. Checked first so the log says plainly what is wrong.
db_host="${MYSQL_HOST:-host.docker.internal}"
db_port="${MYSQL_PORT:-3306}"
for i in 1 2 3 4 5 6; do
  if timeout 3 bash -c "</dev/tcp/${db_host}/${db_port}" 2>/dev/null; then break; fi
  if [ "$i" = 6 ]; then
    say "=====================================================================" \
        " Tick and Book: cannot reach MySQL at ${db_host}:${db_port}." \
        " - Laptop: is XAMPP MySQL running?" \
        " - Server: MySQL must accept connections from Docker (bind-address," \
        "   user allowed from 172.%) - see deploy/README.md, step 2." \
        "====================================================================="
    exit 1
  fi
  say "Waiting for MySQL at ${db_host}:${db_port} ..."
  sleep 5
done

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
