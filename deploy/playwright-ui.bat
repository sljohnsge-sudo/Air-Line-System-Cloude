@echo off
title Tick and Book - Playwright interface
REM Opens Playwright's own interface with ALL the tests (smoke checks, sign-in
REM and staff-role tests, CMB route test) in your browser:
REM http://localhost:9323 (use Edge or Chrome). Needs Docker Desktop running.
REM Options 1 and 2 also create the temporary sign-in test accounts in the
REM LOCAL database; option 3 closes the interface and deletes them again.
REM No test ever chooses a flight, books, pays or issues a ticket.
cd /d "%~dp0.."
echo Which sites should Playwright check?
echo   1 = Docker test   (http://localhost:8176 and 8177, started by deploy\local-test.bat)
echo   2 = Local system  (http://localhost:5176 and 5177, started by start_all.bat)
echo   3 = Close the Playwright interface (and delete the test accounts)
choice /c 123 /n /m "Press 1, 2 or 3: "
if errorlevel 3 (
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Stop
    pause
    exit /b
)
if errorlevel 2 (
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Accounts
    pause
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-smoke-ui.ps1 -Accounts -SiteUrl http://host.docker.internal:8176 -AdminUrl http://host.docker.internal:8177
pause
