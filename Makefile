# OpenMail — common operator targets
# Usage: make dev-backend | dev-frontend | test | smoke | docker-up | docker-down

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BACKEND := $(ROOT)/backend
FRONTEND := $(ROOT)/frontend
BASE_URL ?= http://127.0.0.1:8000

.PHONY: help dev-backend dev-frontend test docker-up docker-pull docker-down smoke

help:
	@echo "OpenMail targets:"
	@echo "  make dev-backend   - run FastAPI (uvicorn :8000, reload)"
	@echo "  make dev-frontend  - run Vite dev server"
	@echo "  make test          - backend pytest"
	@echo "  make smoke         - API smoke (BASE_URL=$(BASE_URL))"
	@echo "  make docker-pull   - pull published image + up -d"
	@echo "  make docker-up     - docker compose up -d --build (local build)"
	@echo "  make docker-down   - docker compose down"

dev-backend:
	@cd "$(BACKEND)" && \
	  if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi && \
	  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	@cd "$(FRONTEND)" && npm run dev

test:
	@cd "$(BACKEND)" && \
	  if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi && \
	  pytest -q

smoke:
	@BASE_URL="$(BASE_URL)" bash "$(ROOT)/scripts/smoke_api.sh"

# Prefer compose.yaml / docker-compose.yml at repo root when present
docker-pull:
	@cd "$(ROOT)" && \
	  if [ -f compose.yaml ] || [ -f compose.yml ] || [ -f docker-compose.yml ] || [ -f docker-compose.yaml ]; then \
	    docker compose pull && docker compose up -d; \
	  else \
	    echo "No compose file at $(ROOT)."; exit 1; \
	  fi

docker-up:
	@cd "$(ROOT)" && \
	  if [ -f compose.yaml ] || [ -f compose.yml ] || [ -f docker-compose.yml ] || [ -f docker-compose.yaml ]; then \
	    docker compose up -d --build; \
	  else \
	    echo "No compose file at $(ROOT) (compose.yaml / docker-compose.yml)."; \
	    echo "Use make dev-backend / dev-frontend, or add Compose later."; \
	    exit 1; \
	  fi

docker-down:
	@cd "$(ROOT)" && \
	  if [ -f compose.yaml ] || [ -f compose.yml ] || [ -f docker-compose.yml ] || [ -f docker-compose.yaml ]; then \
	    docker compose down; \
	  else \
	    echo "No compose file; nothing to tear down."; \
	    exit 0; \
	  fi
