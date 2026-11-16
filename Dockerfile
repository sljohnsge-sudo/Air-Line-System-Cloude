# ==========================================================================
# Tick and Book - one Dockerfile, two images (16/11/2026)
#
#   airline-site   customer website + its API    docker build --build-arg SITE=public -t tickbook/airline-site .
#   airline-admin  admin site + the admin API     docker build --build-arg SITE=admin  -t tickbook/airline-admin .
#
# Inside each image, frontend and backend are kept apart:
#   /app/frontend   the built website (customer site or admin site)
#   /app/backend    the Python API (FastAPI), APP_MODE=public or admin
# nginx (port 80) serves /app/frontend and passes /api/ and /uploads/ to the
# API, which listens only inside the container (127.0.0.1:8000).
# backend/.env is never copied in (.dockerignore); the server passes it at run time.
# ==========================================================================

# ---- Stage 1: build the website (Node) ----------------------------------
FROM node:20-alpine AS frontend
ARG SITE=public
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN if [ "$SITE" = "admin" ]; then npm run build:admin && mv dist-admin /out; else npm run build && mv dist /out; fi

# ---- Stage 2: API + nginx -------------------------------------------------
FROM python:3.11-slim-bookworm
ARG SITE=public
ARG REVISION=unknown
LABEL org.opencontainers.image.title="tickandbook-${SITE}" \
      org.opencontainers.image.revision="${REVISION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_MODE=${SITE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

# Backend: Python packages, then the code
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY backend/ ./

# Frontend: the built site from stage 1
COPY --from=frontend /out /app/frontend

# nginx for this site, and the start script (API + nginx)
COPY deploy/docker/nginx-${SITE}.conf /etc/nginx/conf.d/tickandbook.conf
COPY deploy/docker/start.sh /app/start.sh

# The API runs as an ordinary user that owns /app/backend (uploads/ is the
# shared volume; config/seat_rules.json is written at run time).
RUN useradd --system --uid 10001 --home-dir /app/backend tickbook \
 && mkdir -p /app/backend/uploads/packages \
 && chown -R tickbook:tickbook /app/backend \
 && chmod +x /app/start.sh \
 && nginx -t

EXPOSE 80

# Healthy = the website and the API both answer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1/', timeout=4); u.urlopen('http://127.0.0.1:8000/', timeout=4)" || exit 1

CMD ["/app/start.sh"]
