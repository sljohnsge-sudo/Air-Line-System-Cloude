# ==========================================
# Airline Booking System - Backend Dockerfile
# Travelport TripServices API Gateway (FastAPI)
# ==========================================
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Expose internal FastAPI port
EXPOSE 8000

# Start Uvicorn Server (Single worker to guarantee in-memory booking lock consistency)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

