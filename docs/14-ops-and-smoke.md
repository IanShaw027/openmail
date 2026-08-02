# 14 · Ops & smoke (local-first)

Self-hosted single-instance operations: local dev, Docker, vault first-run, proxy/WARP, import formats, security checklist.

**Product model:** browser vault holds secrets; server is a fetch/send proxy + optional sealed cloud backup. No admin login UI.

Related: [architecture.md](architecture.md) · [16-warp-proxy-pool.md](16-warp-proxy-pool.md) · root [README.md](../README.md) · [SECURITY.md](../SECURITY.md)

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
| `CORS_ORIGINS` | e.g. `http://127.0.0.1:5173` when Vite is split from API |
| `PROXY_POOL` | Multi-line or `\|`-separated SOCKS/HTTP proxies |
| `LICENSE_TOKENS` | Optional codes to raise poll quota |
| `PUBLIC_BASE_URL` | Only if you still serve legacy `/api/v1/code/{token}` links |

See root [`.env.example`](../.env.example).

---

## 2. Docker

### 2.1 Published images

| Registry | Image | Notes |
|----------|-------|--------|
| **Docker Hub** | `ianshaw027/openmail:v0.1.0` / `:latest` | Every `v*` tag via Actions (`linux/amd64`) |

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
OPENMAIL_IMAGE=ianshaw027/openmail:v0.1.0
OPENMAIL_PULL_POLICY=always
```

`make docker-up` runs `docker compose up -d --build` (local build). For pull-only use the commands above.

UI + API: http://127.0.0.1:8000 (SPA served from the same process).

With WARP pool (needs `/dev/net/tun`):

```bash
./scripts/up-with-warp.sh
```

Production: terminate TLS at reverse proxy / Cloudflare; keep master key and DB volume private.

### 2.2 CI / publishing packages

- Tag workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) builds every `v*` and pushes **Docker Hub only**.
- Branch workflow [`.github/workflows/docker.yml`](../.github/workflows/docker.yml) pushes rolling `latest` / SHA on `main`.
- Requires repo secrets: `DOCKERHUB_TOKEN` (and optional `DOCKERHUB_USERNAME`, default `ianshaw027`).

---

## 3. First visit (vault)

1. Open the UI → **create vault password**
2. Save the **recovery key** offline (only way to recover if password is lost)
3. Import accounts (TXT formats) or add manually
4. Device registers automatically when vault unlocks (HMAC for cloud + proxy)

There is no `/admin` or `/register` product flow (legacy URLs redirect home).

---

## 4. Cloud sealed rows (optional)

- Console can upsert **client-sealed** account blobs under the vault device id
- Server cannot decrypt; background sync **skips** sealed rows
- Requires vault unlock + successful `/api/device/register`

Quota: free tier + optional `LICENSE_TOKENS`.

---

## 5. Proxy pool & WARP

See [16-warp-proxy-pool.md](16-warp-proxy-pool.md).

Quick check:

```bash
# After compose with warp profile
curl -s http://127.0.0.1:8000/api/health
```

Console batch fetch uses `POST /api/fetch/proxy` with credentials in the body (not stored when local-only).

---

## 6. Import formats (client-side)

```text
email----password----refresh_token----client_id
email----https://api...
email----password
imap----email----auth----host----port
```

Export: system snapshot / credentials TXT from the console (vault must be unlocked).

---

## 7. Smoke checklist

```bash
make smoke
# or
BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_api.sh
```

Manual:

| Step | Expect |
|------|--------|
| `GET /api/health` | `ok: true` |
| Open UI | Vault gate, not login form |
| Unlock vault | Console loads local accounts |
| Single proxy fetch | Messages or clear error; no hang forever |
| Lock vault | Secrets cleared from memory; re-lock required |

Backend tests:

```bash
cd backend && source .venv/bin/activate
export OPENMAIL_MASTER_KEY=...
pytest -q
```

Frontend: `cd frontend && npm run build`

---

## 8. Security ops

- Rotate `OPENMAIL_MASTER_KEY` only with a planned re-register of devices (registry is key-bound)
- Never commit `.env` or vault recovery keys
- Prefer HTTPS; CSP is set in `main.py` (vue-i18n needs `unsafe-eval`)
- Report issues via [SECURITY.md](../SECURITY.md)

---

## 9. Troubleshooting

| Symptom | Check |
|---------|--------|
| Device register 400 | Vault secret hashing must match server (raw secret → sha256) |
| Accounts 401 | Unlock vault; HMAC clock skew; device registered |
| CSP / EvalError | Deploy includes current CSP (unsafe-eval + CF Insights if used) |
| Fetch timeout | Network/proxy; 45s client abort; SSRF blocking private IPs |
| Import precheck stuck | Skip precheck or wait for abort; confirm still enabled |

Historical multi-user ops notes live under [archive/](archive/README.md) and are **not** authoritative.
