# Tick and Book - Playwright's own interface (UI mode) for all the tests in
# e2e\ (16/11/2026): smoke checks, sign-in tests and the CMB route test. Runs in
# Microsoft's Playwright Docker image, nothing to install, and opens the
# interface in your browser (Edge or Chrome):
#   http://localhost:9323
# There you see every test, run them one by one or all together (the play
# buttons), and click one to see its steps and details. No test ever chooses a
# flight, books, pays or issues a ticket.
#
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1                 (local system, 5176/5177)
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -SiteUrl http://host.docker.internal:8176 -AdminUrl http://host.docker.internal:8177   (Docker test)
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Stop           (close it; also deletes test accounts)
# Or double-click deploy\playwright-ui.bat.
#
# The CMB route tests (52 Travelport searches, search only) are runnable here.
# -RunId <id> shows the searches saved under e2e\route-results\<id>\ instead of
# searching again (e.g. -RunId cmb-20261116-night); without it each route is
# searched fresh when you run it. -Accounts creates the temporary sign-in test
# accounts in the LOCAL database for the staff-role tests (laptop only); -Stop
# deletes them. -Search makes the ONE smoke search runnable.
param(
    [string]$SiteUrl = 'http://host.docker.internal:5176',
    [string]$AdminUrl = 'http://host.docker.internal:5177',
    [int]$Port = 9323,
    [string]$RunId = '',
    [switch]$Search,
    [switch]$Accounts,
    [switch]$Stop,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$image = 'mcr.microsoft.com/playwright:v1.64.1-noble'   # must match e2e\package.json
$name = 'tickandbook_playwright_ui'
$py = Join-Path $repo 'backend\venv\Scripts\python.exe'
$accountsTool = Join-Path $repo 'e2e\tools\auth_accounts.py'

if (docker ps -a -q --filter "name=^${name}$") {
    docker rm -f $name | Out-Null
    Write-Host "Closed the Playwright interface that was running."
}
if ($Stop) {
    if (Test-Path (Join-Path $repo 'e2e\.auth-accounts.json')) { & $py $accountsTool delete }
    exit 0
}
if ($Accounts) { & $py $accountsTool create; if ($LASTEXITCODE -ne 0) { exit 1 } }
if (-not $RunId) { $RunId = 'cmb-ui-' + (Get-Date -Format 'yyyyMMdd-HHmm') }

$searchFlag = if ($Search) { '1' } else { '' }
Write-Host "Playwright interface: customer site $SiteUrl, admin site $AdminUrl, route results e2e\route-results\$RunId$(if ($Accounts) { ', with test accounts' })"
docker run -d --name $name --ipc=host -p "127.0.0.1:${Port}:${Port}" `
    -e "SITE_URL=$SiteUrl" -e "ADMIN_URL=$AdminUrl" -e "SMOKE_SEARCH=$searchFlag" `
    -e "ROUTE_TESTS=1" -e "ROUTE_RUN_ID=$RunId" `
    -v "${repo}\e2e:/e2e" -v "${repo}\backend\main.py:/backend/main.py:ro" -w /e2e $image `
    bash -lc "npm install --no-audit --no-fund --loglevel=error && npx playwright test --ui-host=0.0.0.0 --ui-port=$Port" | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "Could not start it - is Docker Desktop running?" -ForegroundColor Red; exit 1 }

Write-Host "Starting (up to 2 minutes) ..."
$url = "http://localhost:$Port"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $url | Out-Null; $ready = $true; break } catch { Start-Sleep 2 }
    if (-not (docker ps -q --filter "name=^${name}$")) { break }
}
if (-not $ready) {
    Write-Host "It did not start. Its last lines:" -ForegroundColor Red
    docker logs --tail 20 $name
    exit 1
}
Write-Host ""
Write-Host "Ready: $url" -ForegroundColor Green
Write-Host "  Left side: every test (768), in seven files: 01 website, 02 website sign-in, 03 admin site,"
Write-Host "  04 admin sign-in and roles, 05 CMB routes (52 destinations), 06 API (222 APIs),"
Write-Host "  07 unpaid bookings (no PNR, pay pop-up, loyalty, live check - needs -Accounts)."
Write-Host "  Click the > arrows to open them; a test's request and reply are under Attachments."
Write-Host "  Press the play button at the top to run all, or the one next to a test or group."
Write-Host "  Click a test to see its steps and details (for routes: fare, bags, seat, meals)."
Write-Host "Close it:  powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Stop"
if (-not $NoBrowser) { Start-Process $url }
