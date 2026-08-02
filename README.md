<div align="center">

<img src="assets/logo-icon.svg" alt="OpenMail" width="96" />

# OpenMail

**Local-first multi-source mailbox console**

[![CI](https://github.com/IanShaw027/openmail/actions/workflows/ci.yml/badge.svg)](https://github.com/IanShaw027/openmail/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/ianshaw027/openmail)](https://hub.docker.com/r/ianshaw027/openmail)
[![Docker Image](https://img.shields.io/docker/v/ianshaw027/openmail?sort=semver&label=docker%20hub)](https://hub.docker.com/r/ianshaw027/openmail)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB.svg)](backend/pyproject.toml)
[![Vue 3](https://img.shields.io/badge/vue-3-42b883.svg)](frontend/package.json)
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

Published image (**Docker Hub only**):

```text
ianshaw027/openmail:v0.1.0
ianshaw027/openmail:latest
```

Platform: `linux/amd64`. Every git tag `vX.Y.Z` rebuilds and pushes those tags via GitHub Actions.

### A) Compose from this repo (recommended)

```bash
git clone https://github.com/IanShaw027/openmail.git
cd openmail
cp .env.example .env
./scripts/gen-master-key.sh    # paste the value into OPENMAIL_MASTER_KEY in .env

docker compose pull
docker compose up -d
# UI + API: http://127.0.0.1:8000
curl -s http://127.0.0.1:8000/api/health
# → {"ok":true,"version":"0.1.0",...}
```

`docker-compose.yml` already pins:

```yaml
image: ${OPENMAIL_IMAGE:-ianshaw027/openmail:v0.1.0}
```

Optional overrides in `.env`:

```bash
OPENMAIL_IMAGE=ianshaw027/openmail:latest   # or another tag
OPENMAIL_PORT=8000
OPENMAIL_PULL_POLICY=always                 # force re-pull
```

### B) One container (no clone)

```bash
docker run -d --name openmail -p 8000:8000 \
  -e OPENMAIL_MASTER_KEY="$(openssl rand -base64 32)" \
  -v openmail-data:/data \
  ianshaw027/openmail:v0.1.0
```

### C) Build from source (no registry pull)

```bash
docker compose up -d --build
# or: ./scripts/install.sh
```

First visit: **create vault password** → save **recovery key** → import accounts.

With WARP pool (needs `/dev/net/tun` on the host):

```bash
./scripts/up-with-warp.sh
```

---

## Release (maintainers)

```bash
# from a clean main: sync VERSION → commit → tag → push → CI builds Hub image
make release V=0.2.0
```

What happens on tag `v0.2.0`:

1. Bake `0.2.0` into the image (`/api/health` → `"version":"0.2.0"`)
2. Push Docker Hub: `ianshaw027/openmail:v0.2.0`, `:0.2.0`, `:latest`
3. Create/update the GitHub Release notes

Requires repo secrets `DOCKERHUB_TOKEN` and optional `DOCKERHUB_USERNAME` (default `ianshaw027`).

---

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
| `OPENMAIL_MASTER_KEY` | Server AES key (device registry, optional server-side wraps) — **required** |
| `OPENMAIL_IMAGE` | Override compose image (default `ianshaw027/openmail:v0.1.0`) |
| `OPENMAIL_PORT` | Host port (default `8000`) |
| `LICENSE_TOKENS` | Optional comma-separated license codes (quota unlock) |
| `PROXY_POOL` | Multi SOCKS/HTTP channels (`\|` or newlines) |
| `PUBLIC_BASE_URL` | Public origin if you still use legacy code tokens |

## Repository layout

```
assets/      Brand logo (SVG)
backend/     FastAPI + providers + tests
frontend/    Vue 3 + Vite + Pinia
docs/        architecture, ops, legal
scripts/     install, keygen, smoke, release, warp
.github/     CI, issue/PR templates
```

More detail (optional): [architecture](docs/architecture.md) · [ops](docs/14-ops-and-smoke.md) · [WARP](docs/16-warp-proxy-pool.md) · [release internals](docs/17-release.md)

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
