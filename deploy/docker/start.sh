#!/usr/bin/env bash
# Tick and Book container start (16/11/2026).
#
# Each image holds one site, frontend and backend kept apart:
#   /app/frontend  the built website (customer site or admin site)
#   /app/backend   the Python API (APP_MODE=public or admin)
# and runs as TWO containers, chosen by ROLE (docker-compose.yml sets it):
#   ROLE=frontend  nginx, port 80: the website, and /api/ + /uploads/ passed to
#                  the backend container (API_UPSTREAM, host:port)
#   ROLE=backend   the API (uvicorn, port 8000 on the Docker network only),
#                  run as the ordinary user "tickbook"
# Each can be started and stopped on its own.
set -euo pipefail

say() { printf '%s\n' "$@" >&2; }

ROLE="${ROLE:-}"

case "$ROLE" in

frontend)
  if [ "${APP_MODE:-public}" = "admin" ]; then api_default="airline_admin_backend:8000"
  else api_default="airline_site_backend:8000"; fi
  api="${API_UPSTREAM:-$api_default}"
  if ! printf '%s' "$api" | grep -Eq '^[A-Za-z0-9._-]+:[0-9]{1,5}$'; then
    say "Tick and Book: API_UPSTREAM must be host:port (got '${api}')."
    exit 78
  fi
  sed "s/__API_UPSTREAM__/${api}/" /app/nginx/site.conf.template > /etc/nginx/conf.d/tickandbook.conf
  nginx -t -q
  say "Tick and Book ${APP_MODE:-public} FRONTEND: website on port 80, API at ${api}"
  exec nginx -g 'daemon off;'
  ;;

backend)
  # 1. Settings. backend/.env (database, Travelport, keys) is never built into
  #    the image; docker compose passes it. Docker Desktop's Run button does not.
  if [ -z "${JWT_SECRET_KEY:-}" ]; then
    say "=====================================================================" \
        " Tick and Book: this backend container was started WITHOUT its settings" \
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

  say "Tick and Book ${APP_MODE:-public} BACKEND: API on port 8000 (Docker network only)"
  cd /app/backend
  exec setpriv --reuid=tickbook --regid=tickbook --init-groups \
    uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log --timeout-keep-alive 30
  ;;

*)
  say "=====================================================================" \
      " Tick and Book: say which part to run - ROLE=frontend (website) or" \
      " ROLE=backend (API). Start both with docker compose, in the project" \
      " folder (docker compose up -d --no-build), or deploy\\local-test.bat." \
      "====================================================================="
  exit 78
  ;;
esac
