@echo off
title Tick and Book - start everything
REM Starts the whole local system in order: MySQL (XAMPP) -> backend (8005)
REM -> website (5176). Windows runs this at sign-in (a small call to it sits
REM in the user's Startup folder); it can also be double-clicked any time. A
REM part that is already running is left alone, so running it twice is safe.

cd /d "%~dp0"

REM 1. MySQL on 3306
powershell -NoProfile -Command "exit [int](-not (Get-NetTCPConnection -State Listen -LocalPort 3306 -ErrorAction SilentlyContinue))"
if errorlevel 1 (
    echo [INFO] Starting MySQL...
    start "MySQL (XAMPP)" /min cmd /c "C:\xampp\mysql_start.bat"
    REM wait up to 60 seconds for it to accept connections
    powershell -NoProfile -Command "for($i=0;$i -lt 30;$i++){ if(Get-NetTCPConnection -State Listen -LocalPort 3306 -ErrorAction SilentlyContinue){exit 0}; Start-Sleep 2 }; exit 1"
    if errorlevel 1 echo [WARN] MySQL did not start within 60 seconds - open XAMPP Control Panel to check.
) else (
    echo [INFO] MySQL already running.
)

REM 2. Backend on 8005
powershell -NoProfile -Command "exit [int](-not (Get-NetTCPConnection -State Listen -LocalPort 8005 -ErrorAction SilentlyContinue))"
if errorlevel 1 (
    echo [INFO] Starting backend...
    start "Tick and Book - Backend" /min cmd /c "%~dp0start_backend.bat"
) else (
    echo [INFO] Backend already running.
)

REM 3. Website on 5176
powershell -NoProfile -Command "exit [int](-not (Get-NetTCPConnection -State Listen -LocalPort 5176 -ErrorAction SilentlyContinue))"
if errorlevel 1 (
    echo [INFO] Starting website...
    start "Tick and Book - Website" /min cmd /c "%~dp0start_frontend.bat"
) else (
    echo [INFO] Website already running.
)

echo [INFO] Done. Website: http://localhost:5176
timeout /t 5 >nul
