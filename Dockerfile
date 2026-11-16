# ==========================================================================
# Tick and Book - one Dockerfile, two images (16/11/2026)
#
#   airline-site   customer website + its API    docker build --build-arg SITE=public -t tickbook/airline-site .
#   airline-admin  admin site + the admin API     docker build --build-arg SITE=admin  -t tickbook/airline-admin .
#
# Inside each image, frontend and backend are kept apart:
#   /app/frontend   the built website (customer site or admin site)
#   /app/backend    the Python API (FastAPI), APP_MODE=public or admin
# Each image runs as TWO containers (docker-compose.yml), chosen by ROLE:
#   ROLE=frontend   nginx, port 80: serves /app/frontend, passes /api/ and
#                   /uploads/ to the backend container
#   ROLE=backend    the API, port 8000 (Docker network only, never published)
# backend/.env is never copied in (.dockerignore); compose passes it to the
# backend containers only.
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
# Debian 13 (trixie): Debian 12 no longer gets fixes for nginx and several
# base libraries (security scan 16/11/2026); Debian 13 has them.
FROM python:3.11-slim-trixie
ARG SITE=public
ARG REVISION=unknown
LABEL org.opencontainers.image.title="tickandbook-${SITE}" \
      org.opencontainers.image.revision="${REVISION}"

# MYSQL_HOST: MySQL runs on the machine itself, outside Docker
# (docker-compose sets the same; Docker Desktop knows this name by itself).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_MODE=${SITE} \
    MYSQL_HOST=host.docker.internal

# Latest Debian security updates, then nginx.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends nginx \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

# Backend: Python packages (exact versions, backend/requirements.txt), then the
# code. pip, setuptools and wheel are only needed to install, so they are
# removed afterwards: nothing in the running app uses them, and the scanner
# flagged old copies bundled inside them.
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y setuptools wheel \
 && python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.11/ensurepip
COPY backend/ ./

# Frontend: the built site from stage 1
COPY --from=frontend /out /app/frontend

# nginx for this site (start.sh fills in the backend address), and the start
# script (ROLE=frontend: nginx, ROLE=backend: the API)
COPY deploy/docker/nginx-${SITE}.conf /app/nginx/site.conf.template
COPY deploy/docker/start.sh /app/start.sh

# The API runs as an ordinary user that owns /app/backend (uploads/ is the
# shared volume; config/seat_rules.json is written at run time).
RUN useradd --system --uid 10001 --home-dir /app/backend tickbook \
 && mkdir -p /app/backend/uploads/packages \
 && chown -R tickbook:tickbook /app/backend \
 && chmod +x /app/start.sh \
 && sed 's/__API_UPSTREAM__/127.0.0.1:8000/' /app/nginx/site.conf.template > /etc/nginx/conf.d/tickandbook.conf \
 && nginx -t

EXPOSE 80 8000

# Healthy = the frontend container's website answers (port 80), or the
# backend container's API answers (port 8000).
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1/' if os.environ.get('ROLE') == 'frontend' else 'http://127.0.0.1:8000/', timeout=4)" || exit 1

CMD ["/app/start.sh"]
