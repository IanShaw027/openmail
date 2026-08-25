# OpenMail Backend

FastAPI + SQLite API for the OpenMail **local-first** mailbox console.

The browser holds vault-encrypted credentials. This process mainly:

1. Proxies fetch/send with ephemeral credentials (`POST /api/fetch/proxy`, `/api/fetch/send`)
2. Optionally stores **client-sealed** cloud rows per vault device (`X-Device-Id` + HMAC)
3. Serves the built SPA from `app/static/` in Docker
4. Runs an optional background `SyncWorker` for non-sealed server rows with `sync_enabled`

There is **no** user registration or admin password UI. Admin is a `vk_…` device id (first trusted unlock if the list is empty).

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
| GET | `/api/config/public` | public; `instance_mode` / `license_ui_enabled` / `sync_ui_enabled` / `device_admission` (effective; solo→`open`) / `licensed` (solo→`true` without token); also fetch concurrency / lookback / retention / `auth_ui_enabled`; cloud usage needs vault HMAC |
| POST | `/api/device/register` | public; binds `vk_*` secret once; first trusted device may become admin |
| GET | `/api/device/me` | vault HMAC (trusted or pending); may bootstrap admin |
| GET/POST | `/api/device/list`, `/approve`, `/reject`, `/revoke` | trusted vault HMAC |
| * | `/api/accounts*` | trusted vault HMAC |
| POST | `/api/accounts/{id}/fetch` | trusted HMAC + poll quota (`solo` skips quota) |
| GET | `/api/accounts/meta/quota` | trusted HMAC |
| POST | `/api/fetch/proxy`, `/api/fetch/send` | trusted vault HMAC + poll quota (`solo` skips quota) |
| GET | `/api/sync/status`, `/api/sync/delta` | trusted HMAC |
| * | `/api/transfer/*` | vault QR transfer (short-lived ciphertext) |
| * | `/api/admin/licenses*` | trusted **admin** device HMAC |
| GET/POST | `/api/v1/code/{token}` | legacy token if row exists |

410 stubs: `/api/me/mails/*`, `POST /api/me/sync`, `POST /api/accounts/{id}/send`.

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
  services/            # fetch, send, ssrf, device_auth, admin_ids, license, sync_worker, …
tests/
```

## Environment

See repo root [`.env.example`](../.env.example). Common:

| Variable | Purpose |
|----------|---------|
| `OPENMAIL_MASTER_KEY` | Server crypto |
| `OPENMAIL_INSTANCE_MODE` | `hosted` (unset default) or `solo` — see [ops §3](../docs/14-ops-and-smoke.md#3-instance-mode-admission-and-admin) |
| `OPENMAIL_DEVICE_ADMISSION` | `first_trust` (unset default) or `open`; ignored in `solo` |
| `OPENMAIL_ADMIN_DEVICE_IDS` | Recovery / override admin `vk_…`. Empty: first trusted unlock becomes admin (`data/admin_device_ids`) |
| `OPENMAIL_DATABASE_URL` | Default SQLite file |
| `CORS_ORIGINS` | Empty by default; set Vite origin if the UI is split from the API |
| `PROXY_POOL` | SOCKS/HTTP channels |
| `LICENSE_TOKENS` | Hosted only: optional quota unlock codes |
| `CODE_API_MAX_FETCH_PER_HOUR` | Public code-API cap per token per hour; `0` = no limit |
| `CODE_API_MAX_REFRESH_PER_HOUR` | Stricter cap for `refresh=1`; `0` = no limit |
| `SYNC_*` | Interval / folders / concurrency. In `solo`, scheduled cycles off (`sync_enabled_global=false`) |
| `PUBLIC_BASE_URL` | Absolute base for legacy code-API URLs |

Full ops notes: [docs/14-ops-and-smoke.md](../docs/14-ops-and-smoke.md).
