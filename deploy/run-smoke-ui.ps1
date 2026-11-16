# Tick and Book - Playwright's own interface (UI mode) for the read-only smoke
# checks in e2e\ (16/11/2026). Runs in Microsoft's Playwright Docker image,
# nothing to install, and opens the interface in your browser:
#   http://localhost:9323
# There you see every check, run them one by one or all together (the play
# buttons), and step through each one with a picture of the page at each step.
# The checks never choose a flight, book, pay or sign in. -Search makes the
# ONE live flight search check runnable (results only, never "Select Flight").
#
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1                 (local system, 5176/5177)
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -SiteUrl http://host.docker.internal:8176 -AdminUrl http://host.docker.internal:8177   (Docker test)
#   powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Stop           (close it)
# Or double-click deploy\playwright-ui.bat.
param(
    [string]$SiteUrl = 'http://host.docker.internal:5176',
    [string]$AdminUrl = 'http://host.docker.internal:5177',
    [int]$Port = 9323,
    [switch]$Search,
    [switch]$Stop,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$image = 'mcr.microsoft.com/playwright:v1.48.2-noble'   # must match e2e\package.json
$name = 'tickandbook_playwright_ui'

if (docker ps -a -q --filter "name=^${name}$") {
    docker rm -f $name | Out-Null
    Write-Host "Closed the Playwright interface that was running."
}
if ($Stop) { exit 0 }

$searchFlag = if ($Search) { '1' } else { '' }
Write-Host "Playwright interface: customer site $SiteUrl, admin site $AdminUrl$(if ($Search) { ', search check on' })"
docker run -d --name $name --ipc=host -p "127.0.0.1:${Port}:${Port}" `
    -e "SITE_URL=$SiteUrl" -e "ADMIN_URL=$AdminUrl" -e "SMOKE_SEARCH=$searchFlag" `
    -v "${repo}\e2e:/e2e" -w /e2e $image `
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
Write-Host "  Left side: the checks. Press the play button at the top to run all, or the one next to a check."
Write-Host "  Click a check to see each step and a picture of the page."
Write-Host "Close it:  powershell -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Stop"
if (-not $NoBrowser) { Start-Process $url }
