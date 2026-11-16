@echo off
title Tick and Book - local Docker test
REM Starts the two images on this laptop the right way (docker compose, with
REM backend\.env and MySQL on this PC), waits until both are healthy and opens
REM both sites. Stop them with deploy\local-stop.bat.
REM   website  http://localhost:8176   (image tickbook/airline-site)
REM   admin    http://localhost:8177   (image tickbook/airline-admin)
REM Ports 8176/8177 so the normal local system (start_all.bat: 5176/5177)
REM can run at the same time. On the server the images use 5176/5177.
cd /d "%~dp0.."

docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not running. Start it and wait for "Engine running".
    pause
    exit /b 1
)

powershell -NoProfile -Command "exit [int](-not (Get-NetTCPConnection -State Listen -LocalPort 3306 -ErrorAction SilentlyContinue))"
if errorlevel 1 (
    echo [ERROR] MySQL is not running on port 3306 - start XAMPP MySQL first.
    pause
    exit /b 1
)

if not exist "backend\.env" (
    echo [ERROR] backend\.env is missing.
    pause
    exit /b 1
)

REM Remove leftovers started with Docker Desktop's Run button (no settings).
powershell -NoProfile -Command "docker ps -a --filter ancestor=tickbook/airline-site:latest --filter ancestor=tickbook/airline-admin:latest --format '{{.Names}}' | Where-Object { $_ -notin 'airline_site','airline_admin' } | ForEach-Object { docker rm -f $_ | Out-Null; Write-Host ('[INFO] Removed ' + $_ + ' (started with the Run button, no settings)') }"

set DOCKERHUB_USERNAME=tickbook
set SITE_PORT=8176
set ADMIN_PORT=8177
echo [INFO] Starting airline_site (8176) and airline_admin (8177) ...
docker compose up -d --no-build --pull never
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start - see the message above.
    pause
    exit /b 1
)

echo [INFO] Waiting until both are healthy (up to 3 minutes) ...
powershell -NoProfile -Command "for($i=0;$i -lt 36;$i++){ $h = docker compose ps --format '{{.Name}} {{.Health}}'; if($h -and -not ($h | Where-Object { $_ -notmatch ' healthy$' })){ exit 0 }; Start-Sleep 5 }; exit 1"
if errorlevel 1 (
    echo [ERROR] Not healthy yet - the last log lines:
    docker compose logs --tail 25
    pause
    exit /b 1
)

docker compose ps
start "" http://localhost:8176
start "" http://localhost:8177
echo.
echo [OK] Docker test   website http://localhost:8176   admin http://localhost:8177
echo      Local system  website http://localhost:5176   admin http://localhost:5177  (start_all.bat)
echo.
echo Playwright checks (read-only) on the Docker test:
echo     powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -SiteUrl http://host.docker.internal:8176 -AdminUrl http://host.docker.internal:8177
echo Stop:  deploy\local-stop.bat
pause
