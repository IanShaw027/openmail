<div align="center">

<img src="assets/logo-icon.svg" alt="OpenMail" width="96" />

# OpenMail

**Local-first multi-source mailbox console**

[![CI](https://github.com/IanShaw027/openmail/actions/workflows/ci.yml/badge.svg)](https://github.com/IanShaw027/openmail/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB.svg)](backend/pyproject.toml)
[![Vue 3](https://img.shields.io/badge/vue-3-42b883.svg)](frontend/package.json)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)](Dockerfile)
[![Release](https://img.shields.io/github/v/release/IanShaw027/openmail?include_prereleases)](https://github.com/IanShaw027/openmail/releases)

English · [中文](README_CN.md)

Import credentials in the browser, proxy fetch/send through a self-hosted FastAPI backend, and keep secrets encrypted with a vault password. Optional cloud rows are **client-sealed** — operators with DB access should not obtain plaintext vault secrets.

<br />

### 🚀 Live demo

**[https://mail.clomio.ai](https://mail.clomio.ai)** — public test instance

> Create your own vault password on first visit. Secrets stay in **your browser**; the demo server only proxies fetch/send. Do not use production-critical mailboxes on a shared demo.

<br />

<img src="assets/logo.svg" alt="OpenMail wordmark" width="360" />

</div>

---

## ⚠️ Before you use

- **Self-hosted tool**, not a multi-tenant SaaS. You (or your operator) control the instance, keys, and compliance.
- **Vault password & recovery key never leave the browser.** Lose both → ciphertext cannot be recovered.
- Proxy fetch/send briefly uses credentials **in server memory** to call Graph / IMAP / SMTP / upstream APIs. Deploy only on hosts you trust.
- Use only mailboxes you are authorized to access. Operators are responsible for their deployment.

See [SECURITY.md](SECURITY.md) and [docs/legal/](docs/legal/).

---

## Why OpenMail?

| Need | OpenMail |
|------|----------|
| Many throwaway / work mailboxes | One console, batch fetch |
| Don’t trust the server with secrets | **Local vault** (PBKDF2 + AES-GCM) |
| Graph / IMAP / mail.com / Worker APIs | Pluggable providers |
| Run on your VPS | Single Docker image (SPA + API) |

## Features

- **Providers**: Microsoft Graph OAuth, IMAP (+ SMTP send), mail.com cookie session, HTTP API / CF Worker
- **Local vault**: recovery key; auto-lock clears secrets from memory
- **2FA manager**: TOTP/HOTP, QR / paste / bulk URI, bind to mailboxes
- **Console**: import formats, batch fetch, codes, groups, notes, local mail cache
- **Security**: vault device HMAC, SSRF checks, HTML sanitization, CSP
- **Ops**: optional 10× WARP SOCKS pool for concurrent egress

## Not in scope

- User registration / multi-tenant admin SaaS  
- Full webmail product  
- Platform-hosted OAuth consent for Microsoft  

---

## Quick start (Docker)

```bash
git clone https://github.com/IanShaw027/openmail.git
cd openmail
cp .env.example .env
./scripts/gen-master-key.sh    # paste into OPENMAIL_MASTER_KEY

docker compose up -d --build
# UI + API: http://127.0.0.1:8000
curl -s http://127.0.0.1:8000/api/health
```

Or: `./scripts/install.sh` (copies `.env`, generates master key if missing, builds & starts compose).

First visit: **create vault password** → save **recovery key** → import accounts.

Try the hosted demo first: **[mail.clomio.ai](https://mail.clomio.ai)**

With WARP pool (needs `/dev/net/tun`):

```bash
./scripts/up-with-warp.sh
```

## Development

```bash
# Backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENMAIL_MASTER_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
```

Tests:

```bash
cd backend && source .venv/bin/activate && export OPENMAIL_MASTER_KEY=... && pytest -q
cd frontend && npm run build
```

## Configuration

See [`.env.example`](.env.example). Important:

| Variable | Purpose |
|----------|---------|
| `OPENMAIL_MASTER_KEY` | Server AES key (device registry, optional server-side wraps) |
| `LICENSE_TOKENS` | Optional comma-separated license codes (quota unlock) |
| `PROXY_POOL` | Multi SOCKS/HTTP channels (`\|` or newlines) |
| `PUBLIC_BASE_URL` | Public origin if you still use legacy code tokens |

## Repository layout

```
assets/      Brand logo (SVG)
backend/     FastAPI + providers + tests
frontend/    Vue 3 + Vite + Pinia
docs/        architecture, ops, legal
scripts/     install, keygen, smoke, warp
.github/     CI, issue/PR templates
```

Docs: [architecture](docs/architecture.md) · [ops](docs/14-ops-and-smoke.md) · [WARP](docs/16-warp-proxy-pool.md) · [maintainability](docs/maintainability.md)

## Brand assets

| File | Use |
|------|-----|
| [`assets/logo-icon.svg`](assets/logo-icon.svg) | App icon / avatar |
| [`assets/logo.svg`](assets/logo.svg) | Wordmark (light) |
| [`assets/logo-dark.svg`](assets/logo-dark.svg) | Wordmark (dark UI) |
| [`assets/social-banner.svg`](assets/social-banner.svg) | Social / OG style banner |

## Security

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)

## Legal (instance operators)

- Privacy: [docs/legal/privacy.zh.md](docs/legal/privacy.zh.md) · [en](docs/legal/privacy.en.md)  
- Terms: [docs/legal/terms.zh.md](docs/legal/terms.zh.md) · [en](docs/legal/terms.en.md)  

Operators are responsible for their deployment, keys, and compliance.
