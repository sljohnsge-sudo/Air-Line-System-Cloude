#!/usr/bin/env bash
# Tick and Book — load the image file made by deploy/build-images.ps1 and start
# both sites on the server (16/11/2026).
#
#   cd /var/www/Air-Line-System-Cloude          # the folder with docker-compose.yml and backend/.env
#   bash load-on-server.sh /path/to/tickandbook-images.tar
#
# Starts: airline_backend (127.0.0.1:8005), airline_frontend (:5176),
#         airline_admin_backend (127.0.0.1:8006), airline_admin_frontend (:5177)
# It never books, pays or tickets anything; it only starts the containers and
# checks they answer.
set -euo pipefail

TAR="${1:-}"
if [ -z "$TAR" ] || [ ! -f "$TAR" ]; then
  echo "Usage: bash load-on-server.sh /path/to/tickandbook-images.tar" >&2
  exit 1
fi
[ -f docker-compose.yml ] || { echo "Run this in the folder that has docker-compose.yml" >&2; exit 1; }
[ -f backend/.env ] || { echo "backend/.env is missing (see deploy/README.md)" >&2; exit 1; }

# Settings both sites need, checked without printing any value.
need() { grep -qE "^$1=.{$2,}" backend/.env || { echo "backend/.env: $1 is missing or shorter than $2 characters" >&2; exit 1; }; }
need JWT_SECRET_KEY 32
need ADMIN_JWT_SECRET_KEY 32
need TP_API_BASE 8
if [ "$(grep -E '^JWT_SECRET_KEY=' backend/.env | cut -d= -f2-)" = "$(grep -E '^ADMIN_JWT_SECRET_KEY=' backend/.env | cut -d= -f2-)" ]; then
  echo "backend/.env: ADMIN_JWT_SECRET_KEY must be different from JWT_SECRET_KEY" >&2; exit 1
fi
if grep -qE '^[A-Z0-9_]+= ' backend/.env; then
  echo "WARNING: backend/.env has a space after '=' on these lines; Docker keeps that space:" >&2
  grep -nE '^[A-Z0-9_]+= ' backend/.env | cut -d= -f1 >&2
fi

if [ -f "$(dirname "$TAR")/SHA256.txt" ]; then
  echo "== Checking the image file is complete"
  (cd "$(dirname "$TAR")" && sha256sum -c SHA256.txt)
fi

echo "== Loading images"
docker load -i "$TAR"

export DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-tickbook}"
echo "== Starting containers (images from the file, nothing pulled or built)"
docker compose up -d --no-build --pull never --remove-orphans

echo "== Waiting for all four containers to be healthy (up to 3 minutes)"
for i in $(seq 1 36); do
  unhealthy=$(docker compose ps --format '{{.Name}} {{.Health}}' | grep -vc ' healthy$' || true)
  [ "$unhealthy" = "0" ] && break
  sleep 5
done
docker compose ps

echo "== Quick checks"
check() { printf '%-40s %s\n' "$1" "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$2")"; }
check "website        http://127.0.0.1:5176/" http://127.0.0.1:5176/
check "admin site     http://127.0.0.1:5177/" http://127.0.0.1:5177/
check "website API    http://127.0.0.1:8005/" http://127.0.0.1:8005/
check "admin API      http://127.0.0.1:8006/" http://127.0.0.1:8006/
echo
echo "Next: run the read-only smoke checks:  bash deploy/run-smoke.sh"
