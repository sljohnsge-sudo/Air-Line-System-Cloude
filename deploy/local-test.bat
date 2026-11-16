@echo off
title Tick and Book - local Docker test
REM Starts the two images on this laptop as four containers (docker compose,
REM with backend\.env and MySQL on this PC), waits until all four are healthy
REM and opens both sites. Stop them with deploy\local-stop.bat.
REM   image tickbook/airline-site    airline_site_frontend   http://localhost:8176
REM                                  airline_site_backend    (its API)
REM   image tickbook/airline-admin   airline_admin_frontend  http://localhost:8177
REM                                  airline_admin_backend   (admin API)
REM Docker Desktop: Containers tab, group "tickandbook" - Start / Stop each one.
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

REM Remove containers from an earlier layout, or started with Docker Desktop's
REM Run button (no settings). The four "tickandbook" containers are kept.
powershell -NoProfile -Command "$known = 'airline_site','airline_admin','airline_frontend','airline_backend','airline_site_frontend','airline_site_backend','airline_admin_frontend','airline_admin_backend'; $keep = @(docker ps -a --filter label=com.docker.compose.project=tickandbook --format '{{.Names}}'); docker ps -a --format '{{.Names}}|{{.Image}}' | ForEach-Object { $n, $i = $_ -split '\|', 2; if ((($known -contains $n) -or ($i -match '^tickbook/airline-(site|admin)')) -and ($keep -notcontains $n)) { docker rm -f $n | Out-Null; Write-Host ('[INFO] Removed ' + $n + ' (earlier layout or Run button)') } }"

set DOCKERHUB_USERNAME=tickbook
set SITE_PORT=8176
set ADMIN_PORT=8177
echo [INFO] Starting the four containers (websites on 8176 / 8177) ...
docker compose up -d --no-build --pull never
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start - see the message above.
    pause
    exit /b 1
)

echo [INFO] Waiting until all four are healthy (up to 3 minutes) ...
powershell -NoProfile -Command "for($i=0;$i -lt 36;$i++){ $h = @(docker compose ps -a --format '{{.Name}} {{.Health}}'); if($h.Count -eq 4 -and -not ($h | Where-Object { $_ -notmatch ' healthy$' })){ exit 0 }; Start-Sleep 5 }; exit 1"
if errorlevel 1 (
    echo [ERROR] Not all healthy yet - the last log lines:
    docker compose ps -a
    docker compose logs --tail 15
    pause
    exit /b 1
)

docker compose ps -a
start "" http://localhost:8176
start "" http://localhost:8177
echo.
echo [OK] Docker test   website http://localhost:8176   admin http://localhost:8177
echo      Local system  website http://localhost:5176   admin http://localhost:5177  (start_all.bat)
echo.
echo Each container on its own (or Start / Stop in Docker Desktop, group "tickandbook"):
echo     docker compose stop  airline_site_backend
echo     docker compose start airline_site_backend
echo.
echo Playwright checks (read-only) on the Docker test:
echo     powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -SiteUrl http://host.docker.internal:8176 -AdminUrl http://host.docker.internal:8177
echo Stop all four:  deploy\local-stop.bat
pause
