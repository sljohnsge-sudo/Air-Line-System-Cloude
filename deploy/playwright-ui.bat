@echo off
title Tick and Book - Playwright interface
REM Opens Playwright's own interface for the read-only smoke checks in your
REM browser (http://localhost:9323). Needs Docker Desktop running.
cd /d "%~dp0.."
echo Which sites should Playwright check?
echo   1 = Docker test   (http://localhost:8176 and 8177, started by deploy\local-test.bat)
echo   2 = Local system  (http://localhost:5176 and 5177, started by start_all.bat)
echo   3 = Close the Playwright interface
choice /c 123 /n /m "Press 1, 2 or 3: "
if errorlevel 3 (
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Stop
    pause
    exit /b
)
if errorlevel 2 (
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1
    pause
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -SiteUrl http://host.docker.internal:8176 -AdminUrl http://host.docker.internal:8177
pause
