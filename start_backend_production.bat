@echo off
title Tick and Book - Backend Server (production)
color 0A

echo.
echo  ============================================
echo    Tick and Book - Backend Server (production)
echo    Running on http://localhost:8000
echo  ============================================
echo.

cd /d "%~dp0backend"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at backend\venv\
    echo Please run: python -m venv venv  ^&^&  venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM Production mode: no --reload (no file watching or restarts) and no
REM per-request access log. ONE worker on purpose: the double-booking guard,
REM per-booking cancel / payment locks and the unpaid-extras sweep live in
REM process memory, so a second worker could let a double submit through.
REM Move those to the database before raising --workers.
echo [INFO] Starting FastAPI backend (production mode)...
echo [INFO] Press CTRL+C to stop the server.
echo.

venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log --timeout-keep-alive 30

echo.
echo [INFO] Backend server stopped.
pause
