@echo off
title Final Travels - Backend Server
color 0A

echo.
echo  ============================================
echo    Final Travels - Backend Server (API)
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

echo [INFO] Starting FastAPI backend with uvicorn...
echo [INFO] API Docs available at: http://localhost:8000/docs
echo [INFO] Press CTRL+C to stop the server.
echo.

venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

echo.
echo [INFO] Backend server stopped.
pause
