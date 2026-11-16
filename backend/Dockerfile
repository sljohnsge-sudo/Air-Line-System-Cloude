# ==========================================
# Airline Booking System - Backend Dockerfile
# Travelport TripServices API Gateway (FastAPI)
#
# One image, two containers (16/11/2026 admin site split, docker-compose.yml):
#   airline_backend        APP_MODE=public  (tickandbook.com)
#   airline_admin_backend  APP_MODE=admin   (admin.tickandbook.com)
# ==========================================
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install Python dependencies (all requirements have prebuilt wheels)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Run as an ordinary user, not root (security review 16/11/2026). It owns
# /app so it can write uploads/ (shared volume) and config/seat_rules.json.
RUN useradd --system --uid 10001 --home-dir /app tickbook && \
    mkdir -p /app/uploads/packages && \
    chown -R tickbook:tickbook /app
USER tickbook

# Expose internal FastAPI port
EXPOSE 8000

# Docker restarts the container if the API stops answering ("/" is served in
# both modes and needs no database).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)" || exit 1

# Start Uvicorn Server (Single worker to guarantee in-memory booking lock consistency)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
