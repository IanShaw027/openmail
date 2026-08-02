# Contributing to OpenMail

Thanks for helping. Keep changes small, testable, and aligned with **local-first** (no multi-tenant user login).

## Development setup

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # or backend/.env
# set OPENMAIL_MASTER_KEY
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # http://127.0.0.1:5173 → proxies /api to :8000
```

Docker (production-like):

```bash
cp .env.example .env
./scripts/gen-master-key.sh   # paste into .env
docker compose up -d --build
```

## Tests

```bash
cd backend && source .venv/bin/activate
export OPENMAIL_MASTER_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
pytest -q

cd frontend && npm run build   # vue-tsc + vite
```

## Code style

- **Python**: keep modules focused; prefer explicit errors over silent `except`. No password/token logging.
- **Vue**: prefer composables/stores over growing `ConsolePage.vue` further.
- **i18n**: add both `zh-CN` and `en` keys for user-visible strings.
- **Security-sensitive paths** (device HMAC, SSRF, vault, sanitize): add or update tests.

## Pull requests

1. Describe *why* and risk surface if security-related.
2. Note how you tested (pytest / build / manual).
3. Do not commit `.env`, `data/*.db`, or real credentials.
4. Prefer MIT-compatible dependencies only.

## Architecture notes

See [docs/architecture.md](docs/architecture.md) for the current layout after the local-first cut.
