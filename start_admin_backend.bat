@echo off
title Tick and Book - Admin Backend (port 8006)
REM Admin site backend (16/11/2026): the same code as the website's backend,
REM run with APP_MODE=admin. Serves only the staff routes; staff accounts are
REM in the admin database (ADMIN_MYSQL_DATABASE). Settings live in the main
REM database, so changes made here reach the website at once.
cd /d "%~dp0backend"
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at backend\venv\
    pause
    exit /b 1
)
set APP_MODE=admin
echo [INFO] Admin backend on http://127.0.0.1:8006 (APP_MODE=admin)
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8006 --reload
echo [INFO] Admin backend stopped.
pause
