@echo off
title Final Travels - Frontend (Vite Dev Server)
color 0B

echo.
echo  ============================================
echo    Final Travels - Frontend Dev Server
echo    Running on http://localhost:5173
echo  ============================================
echo.

cd /d "%~dp0frontend"

if not exist "node_modules" (
    echo [INFO] node_modules not found. Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed. Please check your Node.js installation.
        pause
        exit /b 1
    )
)

echo [INFO] Starting Vite dev server...
echo [INFO] App available at: http://localhost:5173
echo [INFO] Press CTRL+C to stop the server.
echo.

call npm run dev

echo.
echo [INFO] Frontend server stopped.
pause
