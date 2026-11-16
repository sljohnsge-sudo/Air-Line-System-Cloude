@echo off
title Tick and Book - Admin Site (port 5177)
REM Admin site (16/11/2026): staff pages only, built from the same code with
REM "vite --mode admin". Calls the admin backend on port 8006.
cd /d "%~dp0frontend"
if not exist "node_modules" call npm install
echo [INFO] Admin site on http://localhost:5177
call npm run dev:admin
echo [INFO] Admin site stopped.
pause
