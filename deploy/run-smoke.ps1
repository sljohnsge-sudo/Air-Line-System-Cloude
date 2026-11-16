# Tick and Book - run the read-only Playwright smoke checks (e2e\) on this
# Windows PC with Docker Desktop (16/11/2026). The checks never choose a
# flight, book, pay or sign in. -Search adds ONE live flight search (results only).
#
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -Search
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -SiteUrl https://tickandbook.com -AdminUrl https://admin.tickandbook.com
#
# Defaults reach this PC's own sites from inside Docker (host.docker.internal).
param(
    [string]$SiteUrl = 'http://host.docker.internal:5176',
    [string]$AdminUrl = 'http://host.docker.internal:5177',
    [switch]$Search
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$image = 'mcr.microsoft.com/playwright:v1.48.2-noble'   # must match e2e\package.json
$searchFlag = if ($Search) { '1' } else { '' }

Write-Host "Smoke checks: customer site $SiteUrl, admin site $AdminUrl$(if ($Search) { ', with one live search' })"
& docker run --rm --ipc=host -e "SITE_URL=$SiteUrl" -e "ADMIN_URL=$AdminUrl" -e "SMOKE_SEARCH=$searchFlag" -e CI=1 `
    -v "${repo}\e2e:/e2e" -w /e2e $image `
    bash -lc "npm install --no-audit --no-fund --loglevel=error && npx playwright test"
exit $LASTEXITCODE
