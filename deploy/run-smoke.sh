#!/usr/bin/env bash
# Tick and Book - run the read-only Playwright smoke checks (e2e/) in Docker
# on a Linux server or CI runner (16/11/2026). Nothing to install: it uses
# Microsoft's Playwright image. The checks never choose a flight, book, pay or
# sign in. SMOKE_SEARCH=1 adds ONE live flight search (results only).
#
#   bash deploy/run-smoke.sh                                   # this server's sites
#   SITE_URL=https://tickandbook.com ADMIN_URL=https://admin.tickandbook.com bash deploy/run-smoke.sh
#   SMOKE_SEARCH=1 bash deploy/run-smoke.sh                    # + one live search
set -euo pipefail
cd "$(dirname "$0")/.."

SITE_URL="${SITE_URL:-http://127.0.0.1:5176}"
ADMIN_URL="${ADMIN_URL:-http://127.0.0.1:5177}"
SMOKE_SEARCH="${SMOKE_SEARCH:-}"
IMAGE="mcr.microsoft.com/playwright:v1.48.2-noble"   # must match e2e/package.json

echo "Smoke checks: customer site $SITE_URL, admin site $ADMIN_URL${SMOKE_SEARCH:+, with one live search}"
docker run --rm --network host --ipc=host \
  -e SITE_URL="$SITE_URL" -e ADMIN_URL="$ADMIN_URL" -e SMOKE_SEARCH="$SMOKE_SEARCH" -e CI=1 \
  -v "$PWD/e2e:/e2e" -w /e2e "$IMAGE" \
  bash -lc "npm install --no-audit --no-fund --loglevel=error && npx playwright test"
