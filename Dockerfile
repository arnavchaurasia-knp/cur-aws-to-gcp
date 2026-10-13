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
FROM golang:1.24-bookworm AS backend-builder
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
    && rm -rf /var/lib/apt/lists/*

# Install required Python dependencies
RUN pip install --no-cache-dir \
    duckdb \
    pydantic \
    pdfplumber

# Copy backend binary from Stage 2
COPY --from=backend-builder /app/cur-web /app/cur-web

# Copy built frontend assets from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Copy skills / projection scripts and data
COPY skill/ /app/skill/

# Default Environment Variables
ENV PORT=8080
ENV WEB_ROOT=/app/frontend/dist
ENV DATA_DIR=/data
ENV AGY_PATH=agy

# Create data directory for jobs and databases
RUN mkdir -p /data

EXPOSE 8080

CMD ["/app/cur-web"]
