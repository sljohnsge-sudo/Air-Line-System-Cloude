# ==========================================
# Airline Booking System - Frontend Dockerfile
# Stage 1: Multi-stage Build with Node.js 20
# Stage 2: Production Nginx Server
#
# One Dockerfile, two sites (16/11/2026 admin site split):
#   customer site: docker build .                       (defaults)
#   admin site:    docker build --build-arg SITE=admin --build-arg NGINX_CONF=nginx.admin.conf .
# ==========================================

# Stage 1: Build static bundle
FROM node:20-alpine AS builder
ARG SITE=public

WORKDIR /app

# Install dependencies first (layer caching)
COPY package*.json ./
RUN npm install

# Copy application source and compile (the admin build goes to dist-admin;
# both end up in /app/out)
COPY . .
RUN if [ "$SITE" = "admin" ]; then npm run build:admin && mv dist-admin out; else npm run build && mv dist out; fi

# Stage 2: Serve static bundle via Nginx
FROM nginx:alpine
ARG NGINX_CONF=nginx.conf

# Copy compiled SPA bundle
COPY --from=builder /app/out /usr/share/nginx/html

# Copy optimized Nginx configuration (nginx.admin.conf proxies to the admin backend)
COPY ${NGINX_CONF} /etc/nginx/conf.d/default.conf

# Expose HTTP port
EXPOSE 80

# Start Nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
