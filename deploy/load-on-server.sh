#!/usr/bin/env bash
# Tick and Book - load the two image files made by deploy/build-images.ps1 and
# start both sites on the server (16/11/2026).
#
#   bash load-on-server.sh [folder-with-the-tar-files]     (default: this script's folder)
#
# Run it in the folder that has docker-compose.yml and backend/.env.
# Starts:  airline_site   (image airline-site,  port 5176)  website + its API
#          airline_admin  (image airline-admin, port 5177)  admin site + admin API
# It never books, pays or tickets anything; it only starts the containers and
# checks they answer.
set -euo pipefail

DIR="${1:-$(cd "$(dirname "$0")" && pwd)}"
for f in airline-site.tar airline-admin.tar; do
  [ -f "$DIR/$f" ] || { echo "Missing $DIR/$f" >&2; exit 1; }
done
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

if [ -f "$DIR/SHA256.txt" ]; then
  echo "== Checking the image files are complete"
  (cd "$DIR" && sha256sum -c SHA256.txt)
fi

echo "== Loading the two images"
docker load -i "$DIR/airline-site.tar"
docker load -i "$DIR/airline-admin.tar"

export DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-tickbook}"
echo "== Starting both containers (images from the files, nothing pulled or built)"
docker compose up -d --no-build --pull never --remove-orphans

echo "== Waiting for both to be healthy (up to 3 minutes)"
for i in $(seq 1 36); do
  unhealthy=$(docker compose ps --format '{{.Name}} {{.Health}}' | grep -vc ' healthy$' || true)
  [ "$unhealthy" = "0" ] && break
  sleep 5
done
docker compose ps

echo "== Quick checks"
check() { printf '%-46s %s\n' "$1" "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$2")"; }
check "website        http://127.0.0.1:5176/" http://127.0.0.1:5176/
check "website API    http://127.0.0.1:5176/api/feature-flags" http://127.0.0.1:5176/api/feature-flags
check "admin site     http://127.0.0.1:5177/" http://127.0.0.1:5177/
check "admin API      (401 = up, needs sign-in)" http://127.0.0.1:5177/api/admin/go-live-switches
echo
echo "Next: the read-only Playwright checks:  bash deploy/run-smoke.sh"
