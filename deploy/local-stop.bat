@echo off
title Tick and Book - stop local Docker test
REM Stops and removes the four containers started by deploy\local-test.bat
REM (airline_site_frontend/_backend, airline_admin_frontend/_backend).
REM The two images and the uploads volume are kept.
cd /d "%~dp0.."
set DOCKERHUB_USERNAME=tickbook
docker compose down
echo [OK] Stopped the four Tick and Book containers.
pause
