# OpenMail Backend

FastAPI + SQLite API for the OpenMail **local-first** mailbox console.

The browser holds vault-encrypted credentials. This process mainly:

1. Proxies fetch/send with ephemeral credentials (`POST /api/fetch/proxy`, `/api/fetch/send`)
2. Optionally stores **client-sealed** cloud rows per vault device (`X-Device-Id` + HMAC)
3. Serves the built SPA from `app/static/` in Docker
4. Runs an optional background `SyncWorker` for non-sealed server rows with `sync_enabled`

There is **no** user registration or admin password UI.

## Requirements

- Python 3.12+
- `OPENMAIL_MASTER_KEY` — AES-GCM for device registry / optional server wraps (not the browser vault KEK)

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export OPENMAIL_MASTER_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

OpenAPI: http://127.0.0.1:8000/docs  
Health: `GET /api/health` or `GET /health`

## Tests

```bash
export OPENMAIL_MASTER_KEY=...   # any 32-byte key
pytest -q
```

## Active API (summary)

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/health`, `/health` | public |
| GET | `/api/config/public` | public; cloud usage needs vault HMAC |
| POST | `/api/device/register` | public; binds `vk_*` secret once |
| * | `/api/accounts*` | vault device HMAC |
| POST | `/api/fetch/proxy`, `/api/fetch/send` | vault device HMAC + poll quota |
| GET/POST | `/api/v1/code/{token}` | legacy token if row exists |

Removed product surfaces return **404/410** (auth, admin, server mail search create, code-api create).

## Layout

```text
app/
  main.py              # factory, CSP, SPA mount
  config.py            # Settings
  crypto.py            # server AES-GCM
  db.py / models.py / schemas.py
  deps.py / deps_device.py
  providers/           # oauth, imap, cookie, http_api
  routers/             # HTTP
  services/            # fetch, send, ssrf, device_auth, sync_worker, …
tests/
```

## Environment

See repo root [`.env.example`](../.env.example). Common:

| Variable | Purpose |
|----------|---------|
| `OPENMAIL_MASTER_KEY` | Server crypto |
| `OPENMAIL_DATABASE_URL` | Default SQLite file |
| `CORS_ORIGINS` | Dev Vite origin if split |
| `PROXY_POOL` | SOCKS/HTTP channels |
| `LICENSE_TOKENS` | Optional quota unlock codes |
| `PUBLIC_BASE_URL` | Absolute base for legacy code-API URLs |

Full ops notes: [docs/14-ops-and-smoke.md](../docs/14-ops-and-smoke.md).
