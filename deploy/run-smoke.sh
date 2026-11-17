#!/usr/bin/env bash
# Tick and Book - run the Playwright tests (e2e/) in Docker on a Linux server
# or CI runner (16/11/2026). Nothing to install: it uses Microsoft's Playwright
# image. No test ever chooses a flight, books, pays or issues a ticket. The
# staff-role tests need the laptop's temporary test accounts and are skipped
# here. SMOKE_SEARCH=1 adds ONE live flight search (results only);
# ROUTE_TESTS=1 runs the CMB route test (52 searches, search only).
#
#   bash deploy/run-smoke.sh                                   # this server's sites
#   SITE_URL=https://tickandbook.com ADMIN_URL=https://admin.tickandbook.com bash deploy/run-smoke.sh
#   SMOKE_SEARCH=1 bash deploy/run-smoke.sh                    # + one live search
set -euo pipefail
cd "$(dirname "$0")/.."

SITE_URL="${SITE_URL:-http://127.0.0.1:5176}"
ADMIN_URL="${ADMIN_URL:-http://127.0.0.1:5177}"
SMOKE_SEARCH="${SMOKE_SEARCH:-}"
ROUTE_TESTS="${ROUTE_TESTS:-}"
ROUTE_RUN_ID="${ROUTE_RUN_ID:-}"
IMAGE="mcr.microsoft.com/playwright:v1.64.1-noble"   # must match e2e/package.json

echo "Playwright: customer site $SITE_URL, admin site $ADMIN_URL${SMOKE_SEARCH:+, with one live search}${ROUTE_TESTS:+, with the CMB route test}"
docker run --rm --network host --ipc=host \
  -e SITE_URL="$SITE_URL" -e ADMIN_URL="$ADMIN_URL" -e SMOKE_SEARCH="$SMOKE_SEARCH" \
  -e ROUTE_TESTS="$ROUTE_TESTS" -e ROUTE_RUN_ID="$ROUTE_RUN_ID" -e CI=1 \
  -v "$PWD/e2e:/e2e" -v "$PWD/backend/main.py:/backend/main.py:ro" -w /e2e "$IMAGE" \
  bash -lc "npm install --no-audit --no-fund --loglevel=error && npx playwright test"
