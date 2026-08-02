# OpenMail — multi-stage production image
# Builds Vue SPA + Python FastAPI into a single container serving on :8000

# ── Stage 1: frontend ──────────────────────────────────────────────
FROM node:22-alpine AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Same-origin API: empty VITE_API_BASE → relative /api/* paths
ENV VITE_API_BASE=
RUN npm run build

# ── Stage 2: backend runtime ───────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl for compose healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
# Production: skip test-only packages
RUN pip install --no-cache-dir \
    $(grep -vE '^(pytest|pytest-asyncio)' requirements.txt | grep -vE '^#|^$')

COPY backend/app ./app
COPY --from=frontend /frontend/dist/ ./app/static/

# Persistent SQLite lives here (compose mounts ./data → /data)
RUN mkdir -p /data ./app/static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
