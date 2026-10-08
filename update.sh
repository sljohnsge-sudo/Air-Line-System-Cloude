#!/usr/bin/env bash
set -e

echo "=================================================="
echo "      Airline System Automated Update Script      "
echo "=================================================="

APP_DIR="/var/www/Air-Line-System-Cloude"
cd "$APP_DIR"

echo "[1/7] Stashing runtime artifacts and pulling latest GitHub code..."
git checkout -- .
git clean -fd backend/__pycache__ backend/logs backend/services/__pycache__ amadeus-backend/__pycache__ amadeus-backend/logs amadeus-backend/services/__pycache__ 2>/dev/null || true
git pull origin main

echo "[2/7] Updating Backend Python dependencies..."
cd "$APP_DIR/backend"
if [ -f "requirements.txt" ]; then
    venv/bin/pip install -q -r requirements.txt
fi

echo "[3/7] Updating Amadeus Backend Python dependencies..."
cd "$APP_DIR/amadeus-backend"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
if [ -f "requirements.txt" ]; then
    venv/bin/pip install -q -r requirements.txt
fi

echo "[4/7] Applying Database Schema Migrations (if any)..."
cd "$APP_DIR/backend"
venv/bin/python -c "import database; database.init_db()" 2>/dev/null || true
cd "$APP_DIR/amadeus-backend"
venv/bin/python -c "import database; database.init_db()" 2>/dev/null || true

echo "[5/7] Ensuring relative /api frontend routing..."
python3 -c "
import re
app_path = '$APP_DIR/frontend/src/App.jsx'
with open(app_path, 'r', encoding='utf-8') as f:
    t = f.read()
t = re.sub(r'const API_BASE\s*=\s*[^;\n]+;', \"const API_BASE = '/api';\", t)
with open(app_path, 'w', encoding='utf-8') as f:
    f.write(t)

ndc_path = '$APP_DIR/frontend/src/NdcTicketing.jsx'
with open(ndc_path, 'r', encoding='utf-8') as f:
    t2 = f.read()
t2 = re.sub(r'const API_HOST\s*=\s*[^;\n]+;', \"const API_HOST = '';\", t2)
with open(ndc_path, 'w', encoding='utf-8') as f:
    f.write(t2)
"

echo "[6/7] Building Frontend bundle (React/Vite)..."
cd "$APP_DIR/frontend"
npm install --silent
npm run build
echo 'Test#123' | sudo -S chmod -R o+rX "$APP_DIR/frontend/dist"

echo "[7/7] Restarting FastAPI Airline + Amadeus Backend Services..."
echo 'Test#123' | sudo -S systemctl restart fastapi_airline
echo 'Test#123' | sudo -S systemctl restart amadeus_backend

echo ""
echo "=================================================="
echo "   Update Complete! Site is live on Port 5173     "
echo "   URL: http://172.16.7.41:5173/                  "
echo "=================================================="
