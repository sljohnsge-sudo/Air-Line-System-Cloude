# Tick and Book - run the read-only Playwright smoke checks (e2e\) on this
# Windows PC with Docker Desktop (16/11/2026). The checks never search, book,
# pay or sign in.
#
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -SiteUrl https://tickandbook.com -AdminUrl https://admin.tickandbook.com
#
# Defaults reach this PC's own sites from inside Docker (host.docker.internal).
param(
    [string]$SiteUrl = 'http://host.docker.internal:5176',
    [string]$AdminUrl = 'http://host.docker.internal:5177'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$image = 'mcr.microsoft.com/playwright:v1.48.2-noble'   # must match e2e\package.json

Write-Host "Smoke checks: customer site $SiteUrl, admin site $AdminUrl"
& docker run --rm --ipc=host -e "SITE_URL=$SiteUrl" -e "ADMIN_URL=$AdminUrl" -e CI=1 `
    -v "${repo}\e2e:/e2e" -w /e2e $image `
    bash -lc "npm install --no-audit --no-fund --loglevel=error && npx playwright test"
exit $LASTEXITCODE
