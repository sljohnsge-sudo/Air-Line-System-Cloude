# Tick and Book - run the Playwright tests (e2e\) on this Windows PC with
# Docker Desktop (16/11/2026). No test ever chooses a flight, books, pays or
# issues a ticket.
#
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1                         smoke + sign-in checks
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -Accounts               + staff-role tests (temporary accounts, laptop only)
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -Routes -Project cmb-routes   CMB route test (52 searches)
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -Search                 + ONE live flight search in the smoke checks
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -SiteUrl https://tickandbook.com -AdminUrl https://admin.tickandbook.com
#
# Defaults reach this PC's own sites from inside Docker (host.docker.internal).
# -Accounts creates temporary test accounts in the LOCAL database before the
# run and always deletes them afterwards (e2e\tools\auth_accounts.py).
# -Routes: the CMB route searches; -RunId names e2e\route-results\<RunId>\
# (a destination already searched there is not searched again).
param(
    [string]$SiteUrl = 'http://host.docker.internal:5176',
    [string]$AdminUrl = 'http://host.docker.internal:5177',
    [string]$Project = '',
    [string]$RunId = '',
    [switch]$Search,
    [switch]$Routes,
    [switch]$Accounts
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$image = 'mcr.microsoft.com/playwright:v1.48.2-noble'   # must match e2e\package.json
$py = Join-Path $repo 'backend\venv\Scripts\python.exe'
$searchFlag = if ($Search) { '1' } else { '' }
$routesFlag = if ($Routes) { '1' } else { '' }
if ($Routes -and -not $RunId) { $RunId = 'cmb-' + (Get-Date -Format 'yyyyMMdd-HHmm') }
$projectArg = if ($Project) { "--project=$Project" } else { '' }

Write-Host "Playwright: customer site $SiteUrl, admin site $AdminUrl$(if ($Project) { ", project $Project" })$(if ($Routes) { ", CMB routes (results: e2e\route-results\$RunId)" })$(if ($Accounts) { ', with temporary test accounts' })"
if ($Accounts) { & $py (Join-Path $repo 'e2e\tools\auth_accounts.py') create; if ($LASTEXITCODE -ne 0) { exit 1 } }
try {
    & docker run --rm --ipc=host -e "SITE_URL=$SiteUrl" -e "ADMIN_URL=$AdminUrl" -e "SMOKE_SEARCH=$searchFlag" `
        -e "ROUTE_TESTS=$routesFlag" -e "ROUTE_RUN_ID=$RunId" -e CI=1 `
        -v "${repo}\e2e:/e2e" -w /e2e $image `
        bash -lc "npm install --no-audit --no-fund --loglevel=error && npx playwright test $projectArg"
    $code = $LASTEXITCODE
} finally {
    if ($Accounts) { & $py (Join-Path $repo 'e2e\tools\auth_accounts.py') delete }
}
exit $code
