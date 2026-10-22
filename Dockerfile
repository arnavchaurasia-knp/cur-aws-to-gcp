# ====================================================================
# Stage 1: Build the React / Vite frontend
# ====================================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ====================================================================
# Stage 2: Build the Go backend binary
# ====================================================================
FROM golang:1.25-bookworm AS backend-builder
WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /app/cur-web ./cmd/server

# ====================================================================
# Stage 3: Final runtime environment (Debian with Python 3 & DuckDB)
# ====================================================================
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies (git is required for job workspaces)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    jq \
    gzip \
    procps \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install DuckDB CLI binary for orchestration gates and checks
RUN curl -fsSL https://github.com/duckdb/duckdb/releases/download/v1.1.3/duckdb_cli-linux-amd64.zip -o /tmp/duckdb.zip \
    && unzip /tmp/duckdb.zip -d /usr/local/bin \
    && rm /tmp/duckdb.zip \
    && chmod +x /usr/local/bin/duckdb

# Install required Python dependencies
RUN pip install --no-cache-dir \
    duckdb \
    pydantic \
    pdfplumber \
    openpyxl

# Copy backend binary from Stage 2
COPY --from=backend-builder /app/cur-web /app/cur-web

# Copy built frontend assets from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Copy skills / projection scripts and data
COPY skill/ /app/skill/

# Gate build on GCP catalog freshness (default max age 14 days)
ARG CATALOG_MAX_AGE_DAYS=14
RUN python /app/skill/aws-gcp-cost-projection/scripts/check_catalog_age.py \
      --assert-fresh --max-age-days ${CATALOG_MAX_AGE_DAYS}

# Compile catalog.duckdb index from bundled SKU datasets so it is permanently baked into the image
RUN python /app/skill/aws-gcp-cost-projection/scripts/build_catalog_index.py

# Create agy stub script so PATH lookup and stat succeed in container environment
RUN printf '#!/bin/sh\nexec python3 "$@"\n' > /usr/local/bin/agy && chmod +x /usr/local/bin/agy

# Default Environment Variables
ENV PORT=8080
ENV WEB_ROOT=/app/frontend/dist
ENV DATA_DIR=/data
ENV AGY_PATH=/usr/local/bin/agy
ENV DEV_AUTH_BYPASS=false
ENV SESSION_SECRET=cur-web-session-secret-key-at-least-32-bytes-long-12345
ENV SKILL_DIR=/app/skill/aws-gcp-cost-projection

# Create data directory for jobs and databases
RUN mkdir -p /data

EXPOSE 8080

CMD ["/app/cur-web"]
