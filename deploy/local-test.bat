@echo off
title Tick and Book - local Docker test
REM Starts the two images on this laptop the right way (docker compose, with
REM backend\.env and MySQL on this PC), waits until both are healthy and opens
REM both sites. Stop them with deploy\local-stop.bat.
REM   website  http://localhost:5176   (image tickbook/airline-site)
REM   admin    http://localhost:5177   (image tickbook/airline-admin)
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

set DOCKERHUB_USERNAME=tickbook
echo [INFO] Starting airline_site and airline_admin ...
docker compose up -d --no-build --pull never
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start. If it says "port is already allocated", the dev servers
    echo         from start_all.bat are using 5176/5177 - close those windows first.
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
start "" http://localhost:5176
start "" http://localhost:5177
echo.
echo [OK] Website  http://localhost:5176
echo [OK] Admin    http://localhost:5177
echo.
echo Playwright checks (read-only):
echo     powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1
echo Stop:  deploy\local-stop.bat
pause
