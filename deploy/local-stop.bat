@echo off
title Tick and Book - stop local Docker test
REM Stops and removes the two containers started by deploy\local-test.bat.
REM The images and the uploads volume are kept.
cd /d "%~dp0.."
set DOCKERHUB_USERNAME=tickbook
docker compose down
echo [OK] Stopped airline_site and airline_admin.
pause
