# 14 · Ops & smoke (local-first)

Self-hosted single-instance operations: local dev, Docker, **instance mode**, device admission, admin bootstrap, vault first-run, proxy/WARP, import formats, security checklist.

**Product model:** browser vault holds secrets; server is a fetch/send proxy + optional sealed cloud backup. There is no login / password admin UI. “Who is admin” is a **vault device id** (`vk_…`).

Related: [architecture.md](architecture.md) · [16-warp-proxy-pool.md](16-warp-proxy-pool.md) · root [README.md](../README.md) · [SECURITY.md](../SECURITY.md) · design [2026-08-25-instance-mode-admin-bootstrap-design.md](superpowers/specs/2026-08-25-instance-mode-admin-bootstrap-design.md)

---

## 1. Local development

### 1.1 Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENMAIL_MASTER_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or: `make dev-backend` (set `OPENMAIL_MASTER_KEY` in the environment or `.env`).

- Health: `GET http://127.0.0.1:8000/api/health`
- OpenAPI: http://127.0.0.1:8000/docs
- Public flags: `GET /api/config/public` → `instance_mode`, `license_ui_enabled`, `sync_ui_enabled`, `device_admission`, `licensed`

### 1.2 Frontend

```bash
cd frontend
npm install
npm run dev
# http://127.0.0.1:5173 — /api → :8000
```

Or: `make dev-frontend`

### 1.3 Environment (summary)

| Variable | Notes |
|----------|--------|
| `OPENMAIL_MASTER_KEY` | **Required** for device registry / server crypto |
| `OPENMAIL_INSTANCE_MODE` | `hosted` (code default if unset) or `solo`. [§3](#3-instance-mode-admission-and-admin) |
| `OPENMAIL_DEVICE_ADMISSION` | `first_trust` (code default) or `open`. Ignored in `solo` (forced `open`) |
| `OPENMAIL_ADMIN_DEVICE_IDS` | Recovery / override list of admin `vk_…`. Empty → first trusted device becomes admin |
| `OPENMAIL_ENV_FILE` | Optional path used when bootstrap writes `OPENMAIL_ADMIN_DEVICE_IDS` into a dotenv file |
| `OPENMAIL_DATA_DIR` | Directory for `device_registry.json` and `admin_device_ids` (Docker uses `/data`) |
| `CORS_ORIGINS` | Empty by default (same-origin SPA). Set e.g. `http://127.0.0.1:5173` when Vite is split from the API |
| `PROXY_POOL` | Multi-line or `\|`-separated SOCKS/HTTP proxies |
| `LICENSE_TOKENS` | Hosted only: optional codes that lift client quotas |
| `QUOTA_*` | Hosted caps (defaults 100 / 50 / 1000). Ignored in `solo` |
| `FETCH_CONCURRENCY` | Code default `5`; Compose / `.env.example` use `10` |
| `SYNC_*` | Interval / folders / concurrency. Code `SYNC_CONCURRENCY` default `2`; Compose uses `10`. In `solo`, scheduled cycles are off |
| `CODE_API_MAX_FETCH_PER_HOUR` | Public code-API cap per token per hour; `0` = no limit (code default `60`) |
| `CODE_API_MAX_REFRESH_PER_HOUR` | Cap for `refresh=1`; `0` = no limit (code default `15`) |
| `ALLOW_PRIVATE_PROXY` | Default `false`; client proxies to private/loopback |
| `PUBLIC_BASE_URL` | Legacy `/api/v1/code/{token}` absolute origin. Code default `http://127.0.0.1:8000` |
| `AUTH_UI_ENABLED` | Legacy login/register chrome. Default `false`. Distinct from `license_ui_enabled` |

See root [`.env.example`](../.env.example). Copying that file sets **`solo` + `open`** for typical single-user self-host. Leaving the vars unset uses **`hosted` + `first_trust`**.

---

## 2. Docker

### 2.1 Published images

| Registry | Image | Notes |
|----------|-------|--------|
| **Docker Hub** | `ianshaw027/openmail:v1.0.0` / `:latest` | Every `v*` tag via Actions (`linux/amd64`) |

Compose default: `ianshaw027/openmail:v1.0.0`.

```bash
cp .env.example .env
./scripts/gen-master-key.sh   # paste into OPENMAIL_MASTER_KEY

# Prefer pull (no local Node/Python build) — compose defaults to Hub
docker compose pull
docker compose up -d

# Or build from this tree
docker compose up -d --build
```

Override image:

```bash
# .env
OPENMAIL_IMAGE=ianshaw027/openmail:v1.0.0
OPENMAIL_PULL_POLICY=always
```

`make docker-up` runs `docker compose up -d --build` (local build). For pull-only use the commands above.

UI + API: http://127.0.0.1:8000 (SPA served from the same process).

With WARP pool (needs `/dev/net/tun`):

```bash
./scripts/up-with-warp.sh
```

Production: terminate TLS at reverse proxy / Cloudflare; keep master key and DB volume private.

**Volume to back up:** `./data` (or `/data` in the container) — SQLite, `device_registry.json`, and `admin_device_ids`.

### 2.2 CI / publishing packages

- Tag workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) builds every `v*` and pushes **Docker Hub only**.
- Branch workflow [`.github/workflows/docker.yml`](../.github/workflows/docker.yml) pushes rolling `latest` / SHA on `main`.
- Requires repo secrets: `DOCKERHUB_TOKEN` (and optional `DOCKERHUB_USERNAME`, default `ianshaw027`).

---

## 3. Instance mode, admission, and admin

These three settings decide quotas, who can fetch, and who can issue license codes. Read this before exposing a public URL.

### 3.1 `OPENMAIL_INSTANCE_MODE`

One master switch. Changing it requires a **process / container restart**.

| | `hosted` (code default if unset) | `solo` (`.env.example` for single-user) |
|---|---|---|
| Quotas | `QUOTA_*` caps unless licensed | Unlimited (`licensed: true`) |
| License / quota UI | Shown in Settings | Hidden |
| Cloud import / upload / scheduled sync | Follows `SYNC_*` | UI + scheduled worker off. `/api/accounts*` and `/api/sync/*` still exist |
| Device admission | `OPENMAIL_DEVICE_ADMISSION` | Forced **`open`** |
| First trusted device → admin | Yes | Yes |

`GET /api/config/public` exposes:

- `instance_mode`
- `license_ui_enabled` (`false` in `solo`)
- `sync_ui_enabled` (`false` in `solo`)
- `device_admission` (effective value; `solo` → `open`)
- `licensed`

### 3.2 Device admission (`OPENMAIL_DEVICE_ADMISSION`)

A **device** is a browser vault identity (`vk_…`), not a physical computer. Clearing the site data, another browser, or another profile is a **new** device.

| Mode | Behaviour |
|------|-----------|
| `first_trust` (code default) | While **no trusted devices exist**, the next register is `trusted`. Later devices are `pending` and **cannot** call privileged APIs (accounts, fetch, send, device list/approve) until a trusted device approves them in Settings. On a normal fresh install this is the first register. |
| `open` | Every successful register is `trusted`. Re-register under `open` also **promotes leftover `pending` → `trusted`** (so switching a stuck single-user install unblocks fetch). |
| `solo` instance | Admission is forced `open` regardless of this variable. |

Existing on-disk registry rows without a status are treated as `trusted` on upgrade so multi-device installs are not locked out.

Typical choices:

- **Personal / family box:** `solo`, or `hosted` + `open`
- **Shared / public hosted box:** `hosted` + `first_trust`, and unlock **your** vault first (see admin below)

### 3.3 Admin bootstrap (first trusted unlock)

There is no admin password. Admin is a list of `vk_…` ids that may issue / revoke license codes in Settings (`/api/admin/licenses`) and are treated as licensed.

**When the effective admin set is empty**, the first **trusted** device that registers (or calls `GET /api/device/me`) is written as admin and **hot-reloaded** (no restart):

1. Always persist `data/admin_device_ids` (same volume as the DB).
2. Also write `.env` when `OPENMAIL_ENV_FILE` is set, or when a writable cwd `.env` exists (skipped under pytest).
3. Set `OPENMAIL_ADMIN_DEVICE_IDS` in-process and clear the settings cache.

Later trusted devices are **not** admin.

**Load order** (first non-empty wins):

1. Process / compose env `OPENMAIL_ADMIN_DEVICE_IDS` (operator **recovery**)
2. Settings loaded from dotenv (same key)
3. `data/admin_device_ids`
4. Empty → wait for the next trusted register / `/me`

**Lost admin device** (phone wiped, vault reset): copy the new `vk_…` from Settings → Trusted devices on a working browser, put it in `.env` as `OPENMAIL_ADMIN_DEVICE_IDS`, **recreate / restart** the container. Env wins over the data file.

### 3.4 Public / shared instance warning

On a reachable URL, **whoever unlocks a vault first becomes admin** if the admin set is still empty. Before you advertise the instance:

1. Set `OPENMAIL_INSTANCE_MODE` (`hosted` if you want quotas / license codes; `solo` only if every visitor should be unlimited), and
2. Either unlock **your** vault first, **or** pre-set `OPENMAIL_ADMIN_DEVICE_IDS`.

Pending devices never become admin even if listed, until they are trusted.

---

## 4. First visit (vault)

1. Open the UI → **create vault password**
2. Save the **recovery key** offline (only way to recover if password is lost)
3. Import accounts (TXT formats) or add manually
4. Device registers automatically when the vault unlocks (HMAC for cloud + proxy)
5. **This trusted device becomes admin** if nobody is admin yet. In `hosted` mode you can issue license codes in Settings. In `solo` those panels are hidden (already unlimited).

There is no `/admin` or `/register` product flow (legacy URLs redirect home).

Factory reset (Settings) wipes **this browser’s** vault only — not `./data`.

### HTML mail

Bodies render in a **sandboxed iframe** (no `allow-same-origin` / `allow-popups`). Remote images are **off** until you click “Show remote images” on that message. If a layout looks cropped, hard-refresh after upgrade — the iframe height follows the document.

---

## 5. Cloud sealed rows (optional, hosted)

- Console can upsert **client-sealed** account blobs under the vault device id
- Server cannot decrypt; background sync **skips** sealed rows
- Requires vault unlock + successful `/api/device/register`
- **`solo` hides import-to-cloud / upload-to-cloud / cloud delta polling** and forces `sync_enabled_global=false`

Quota (hosted, unlicensed): `QUOTA_MAX_*` plus optional `LICENSE_TOKENS` or admin-issued codes.

---

## 6. Proxy pool & WARP

See [16-warp-proxy-pool.md](16-warp-proxy-pool.md).

Quick check:

```bash
# After compose with warp profile
curl -s http://127.0.0.1:8000/api/health
```

Console batch fetch uses `POST /api/fetch/proxy` with credentials in the body (not stored when local-only).

---

## 7. Import formats (client-side)

```text
email----password----refresh_token----client_id
email----https://api...
email----password
imap----email----auth----host----port
```

Export: system snapshot / credentials TXT from the console (vault must be unlocked).

---

## 8. Smoke checklist

```bash
make smoke
# or
BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_api.sh
```

Automated checks: health → public config → `POST /api/device/register` → HMAC-signed
`GET /api/accounts` (200) → unsigned `GET /api/accounts` rejected (401). Requires `curl`,
`openssl`, and `od`.

Manual:

| Step | Expect |
|------|--------|
| `GET /api/health` | `200` with `ok: true`. Returns `503` + `database_writable: false` when the data directory has become unwritable, so the container healthcheck fails instead of serving traffic it cannot handle |
| `GET /api/config/public` | `instance_mode` / `license_ui_enabled` / `sync_ui_enabled` match `.env` |
| Open UI | Vault gate, not login form |
| Unlock vault | Console loads local accounts; `GET /api/device/me` → `is_admin: true` on a fresh instance |
| Single proxy fetch | Messages or clear error; no hang forever |
| Lock vault | Secrets cleared from memory; re-lock required |

Backend tests:

```bash
cd backend && source .venv/bin/activate
export OPENMAIL_MASTER_KEY=...
pytest -q
```

Frontend: `cd frontend && npm test && npm run build`

---

## 9. Security ops

- Rotate `OPENMAIL_MASTER_KEY` only with a planned re-register of devices (registry is key-bound)
- Never commit `.env` or vault recovery keys
- Prefer HTTPS; CSP is set in `main.py` (vue-i18n needs `unsafe-eval`)
- Back up `./data` (`openmail.db`, `device_registry.json`, `admin_device_ids`)
- On a public host, do not leave admin unset — unlock yourself first or pre-set `OPENMAIL_ADMIN_DEVICE_IDS`
- Report issues via [SECURITY.md](../SECURITY.md)

---

## 10. Troubleshooting

| Symptom | Check |
|---------|--------|
| `device pending approval from a trusted device` | Admission is `first_trust` and this `vk_…` is **not trusted** (other browser, cleared site data, or not yet approved). Approve from Settings on a trusted device, or set `OPENMAIL_DEVICE_ADMISSION=open` / `OPENMAIL_INSTANCE_MODE=solo`, recreate, then **unlock again** (re-register promotes leftover pending under `open`). |
| Fetch 401 / not admin after unlock | Another device already bootstrapped admin. Recover: put **this** `vk_…` in `OPENMAIL_ADMIN_DEVICE_IDS` and restart. |
| License / quota / “import to cloud” missing | `instance_mode=solo` — expected. Switch to `hosted` and restart if you want those surfaces. |
| `master_key_configured: false` | `OPENMAIL_MASTER_KEY` empty/invalid; recreate container after fix |
| Device register 400 | Vault secret hashing must match server (raw secret → sha256) |
| Accounts 401 | Unlock vault; HMAC clock skew; device registered and **trusted** |
| CSP / EvalError | Deploy includes current CSP (unsafe-eval + CF Insights if used) |
| Fetch timeout | Network/proxy; 45s client abort; SSRF blocking private IPs |
| HTML body looks cropped / only “Hello,” | Hard-refresh the SPA. Click “Show remote images” for CDN logos. |
| Import precheck stuck | Skip precheck or wait for abort; confirm still enabled |

Historical multi-user ops notes live under [archive/](archive/README.md) and are **not** authoritative.
