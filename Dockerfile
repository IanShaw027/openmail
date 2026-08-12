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

# Injected by CI on tag builds (e.g. 0.2.0).
ARG OPENMAIL_VERSION=
ENV OPENMAIL_VERSION=${OPENMAIL_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl for compose healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
# Production: install only what appears above the test-only marker.
#
# This used to exclude packages by name with an unanchored pattern, which meant
# a future `pytest-cov` or `httpx2-extra` would have been dropped silently —
# and a missing runtime dependency does not show up until the image runs.
# Splitting on the section header the file already declares keeps the two lists
# in one place, and the build fails loudly if that header ever goes away.
RUN awk '/^# ── Test-only/{found=1; exit} /^[^#]/ && NF {print} END{if(!found) exit 1}' \
        requirements.txt > /tmp/requirements-prod.txt \
    && pip install --no-cache-dir -r /tmp/requirements-prod.txt

COPY backend/app ./app
COPY --from=frontend /frontend/dist/ ./app/static/

# Pin __version__ in the image when build-arg is provided
RUN if [ -n "$OPENMAIL_VERSION" ]; then \
      sed -i "s/^__version__ = .*/__version__ = \"${OPENMAIL_VERSION}\"/" app/__init__.py; \
    fi

# Persistent SQLite lives here (compose mounts ./data → /data)
# The app itself runs as uid 10001; the entrypoint starts as root only long
# enough to reconcile ownership of the mounted data dir, then drops privileges.
RUN mkdir -p /data ./app/static \
    && groupadd --gid 10001 openmail \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin openmail \
    && chown -R openmail:openmail /data /app

# The entrypoint can drop to whatever uid owns the mounted data dir, which need
# not have a passwd entry at all. Anything that expands `~` would then fall back
# to pwd.getpwuid() and raise, and the inherited HOME points at a directory that
# uid cannot write. /tmp exists, is world-writable, and holds nothing worth
# keeping — a cache directory is all HOME is used for here.
ENV HOME=/tmp

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

LABEL org.opencontainers.image.title="OpenMail" \
      org.opencontainers.image.description="Local-first multi-source mail console" \
      org.opencontainers.image.source="https://github.com/IanShaw027/openmail"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
