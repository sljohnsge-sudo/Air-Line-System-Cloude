# ==========================================
# Airline Booking System - Frontend Dockerfile
# Stage 1: Multi-stage Build with Node.js 20
# Stage 2: Production Nginx Server
# ==========================================

# Stage 1: Build static bundle
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (layer caching)
COPY package*.json ./
RUN npm install

# Copy application source and compile
COPY . .
RUN npm run build

# Stage 2: Serve static bundle via Nginx
FROM nginx:alpine

# Copy compiled SPA bundle
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy optimized Nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose HTTP port
EXPOSE 80

# Start Nginx in foreground
CMD ["nginx", "-g", "daemon off;"]

