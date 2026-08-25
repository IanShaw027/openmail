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

Import credentials in the browser, proxy fetch/send through a self-hosted FastAPI backend, and keep secrets encrypted with a vault password. Optional cloud rows are **client-sealed** — operators with DB access should not obtain plaintext vault secrets without your vault key.

<br />

<img src="assets/social-banner.svg" alt="OpenMail banner" width="720" />

### Live demo

**[https://mail.clomio.ai](https://mail.clomio.ai)** — public test instance (`solo` mode)

> Create your own vault password on first visit. Secrets stay in **your browser**. Do **not** put production-critical mailboxes on a shared demo. On a fresh instance the **first trusted unlock becomes admin**.

</div>

---

## Screenshots

| Vault setup | Recovery key (save once) |
|:-----------:|:------------------------:|
| ![Vault setup](assets/screenshots/vault-setup.png) | ![Recovery key](assets/screenshots/vault-recovery.png) |

| Console + import | 2FA manager |
|:----------------:|:-----------:|
| ![Console](assets/screenshots/console.png) | ![2FA](assets/screenshots/twofa.png) |

| Settings | Local mail cache |
|:--------:|:----------------:|
| ![Settings](assets/screenshots/settings.png) | ![Mails](assets/screenshots/mails.png) |

---

## Table of contents

- [Screenshots](#screenshots)
- [Before you use](#before-you-use)
- [How it works](#how-it-works)
- [Features](#features)
- [Providers](#providers)
- [Quick start (Docker)](#quick-start-docker)
- [First visit](#first-visit)
- [Instance mode, devices, and admin](#instance-mode-devices-and-admin)
- [Import formats](#import-formats)
- [Configuration](#configuration)
- [Upgrade](#upgrade)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Release](#release-maintainers)
- [Limitations](#limitations)
- [Security & license](#security)

---

## Before you use

| Rule | Detail |
|------|--------|
| Self-hosted | Not multi-tenant SaaS. You own keys, TLS, backups, compliance. |
| Vault secrets stay in the browser | Password + recovery key never uploaded. Lose both → ciphertext unrecoverable. |
| Server sees credentials briefly | Proxy fetch/send must use Graph / IMAP / SMTP / APIs → secrets live in **process memory** for that request. Deploy only on hosts you trust. |
| Authorized mailboxes only | You are responsible for accounts you import. |

Full policy: [SECURITY.md](SECURITY.md) · legal templates under [docs/legal/](docs/legal/).

---

## How it works

```
┌──────────────────────────┐         HTTPS + vault device HMAC         ┌──────────────────────────┐
│  Browser (Vue 3)         │ ────────────────────────────────────────► │  Server (FastAPI)         │
│  • Vault PBKDF2 + AES-GCM│                                           │  • Device registry        │
│  • Accounts / 2FA / cache│ ◄──────────────────────────────────────── │  • Optional sealed cloud  │
│  • localStorage cipher   │         proxy fetch / send                │  • SSRF checks + DNS pin  │
└──────────────────────────┘                                           └────────────┬─────────────┘
                                                                                    │
                                                              Graph · IMAP/SMTP · mail.com · HttpApi
```

1. You create a **vault password** (and get a **recovery key** once).
2. Accounts live in the browser vault (ciphertext). Optionally sync **sealed** blobs to the server (still not readable without the vault).
3. Fetch/send: browser unlocks → sends credentials for that request with HMAC device identity → server talks upstream → returns mail; secrets not persisted as plaintext.

Stack: **Vue 3 + Vite + Pinia** frontend, **FastAPI + SQLite** backend, single Docker image (SPA served by the API).

---

## Features

- **Multi-provider** mailbox console (see table below)
- **Local vault** with recovery key, session resume (same tab), factory reset
- **Device QR transfer** — PC ↔ phone vault sync (short-lived server ciphertext)
- **2FA manager** — TOTP/HOTP/Steam, QR / paste / bulk URI, brand icons, filter + drag reorder, bind to mailboxes
- **Console** — batch import, brand chips, groups, notes, batch fetch, code extract, send, body expand modal
- **Mail list** — first 20 messages; load more (10 older) / clear & refetch
- **Folders** — inbox / junk / sent (IMAP names include non-ASCII via modified UTF-7)
- **Security** — vault device HMAC, first-trust or open device admission, sandboxed HTML iframe (remote images opt-in), SSRF, CSP, client-sealed cloud rows
- **Instance modes** — `hosted` (quotas + license codes + optional cloud sync) or `solo` (unlimited, those UIs hidden)
- **Admin** — first trusted vault device becomes admin automatically; recover by editing `OPENMAIL_ADMIN_DEVICE_IDS`
- **Ops** — optional 10× Cloudflare WARP SOCKS pool for concurrent egress
- **i18n** — Chinese + English UI

### Not in scope

- User registration / multi-tenant admin SaaS  
- Full webmail (composer threads, server search index, etc.)  
- Platform-hosted Microsoft OAuth consent screens for you  

---

## Providers

| Provider | Type | What you need | Fetch | Send |
|----------|------|---------------|-------|------|
| **Microsoft Graph** | `oauth` | email + `client_id` + `refresh_token` | ✅ | ✅ (Graph) |
| **IMAP** | `imap` | email + password/app password + host/port | ✅ | ✅ if SMTP resolved |
| **mail.com cookie** | `cookie` | session cookie material | ✅ | limited / provider rules |
| **HTTP API / CF Worker** | `http_api` | API URL (+ optional secret) | ✅ | depends on worker |

IMAP host defaults exist for common domains (Gmail, Outlook, etc.); you can override host/port in the UI.

---

## Quick start (Docker)

**Image (Docker Hub only):**

```text
ianshaw027/openmail:v1.0.0
ianshaw027/openmail:latest
```

- Architecture: **`linux/amd64`**
- Every git tag `vX.Y.Z` → GitHub Actions rebuilds and pushes Hub tags

### A) Compose from this repo (recommended)

```bash
git clone https://github.com/IanShaw027/openmail.git
cd openmail
cp .env.example .env
./scripts/gen-master-key.sh    # paste into OPENMAIL_MASTER_KEY in .env

docker compose pull
docker compose up -d

# UI + API
open http://127.0.0.1:8000
curl -s http://127.0.0.1:8000/api/health
# {"ok":true,"version":"1.0.0","master_key_configured":true,...}
```

Compose default:

```yaml
image: ${OPENMAIL_IMAGE:-ianshaw027/openmail:v1.0.0}
```

Useful `.env` overrides:

```bash
OPENMAIL_IMAGE=ianshaw027/openmail:latest
OPENMAIL_PORT=8000
OPENMAIL_PULL_POLICY=always
```

Data persists in `./data` (SQLite + device registry). **Back up this directory.**

### B) One container (no clone)

```bash
docker run -d --name openmail -p 8000:8000 \
  -e OPENMAIL_MASTER_KEY="$(openssl rand -base64 32)" \
  -e OPENMAIL_DATABASE_URL=sqlite:////data/openmail.db \
  -v openmail-data:/data \
  ianshaw027/openmail:v1.0.0
```

### C) Build from source

```bash
docker compose up -d --build
# or: ./scripts/install.sh
```

### WARP egress pool (optional)

Host needs `/dev/net/tun`:

```bash
./scripts/up-with-warp.sh
# starts openmail + 10× warp SOCKS nodes (compose profile)
```

Details: [docs/16-warp-proxy-pool.md](docs/16-warp-proxy-pool.md).

---

## First visit

1. Open the UI → **create vault password** (strong, memorable only to you).  
2. **Copy the recovery key** somewhere offline (shown once; also viewable later while unlocked under Settings).  
3. Import accounts (paste TXT or upload) **or** add one mailbox manually.  
4. Select accounts → **Fetch**. Codes / mail list appear in the console; HTML bodies render in a sandboxed iframe (click **Show remote images** for CDN logos).  
5. The first trusted unlock on a new instance becomes **admin**. In `hosted` mode you can paste / issue license codes in Settings. In `solo` those panels are hidden (already unlimited).  

Factory reset (Settings) wipes **this browser’s** vault/storage only — not the server DB volume.

---

## Instance mode, devices, and admin

This is the important operator contract. Full tables and recovery steps: [docs/14-ops-and-smoke.md](docs/14-ops-and-smoke.md#3-instance-mode-admission-and-admin).

### Mode (`OPENMAIL_INSTANCE_MODE`)

| | `hosted` (code default if the var is unset) | `solo` (what [`.env.example`](.env.example) copies for single-user) |
|---|---|---|
| Quotas | `QUOTA_*` unless a license code is presented | Unlimited |
| License / quota UI | Shown | Hidden |
| Cloud import / scheduled sync | Follows `SYNC_*` | UI + scheduled worker off (`sync_enabled_global=false`). Cloud HTTP APIs still exist |
| New-device admission | `OPENMAIL_DEVICE_ADMISSION` | Forced `open` |
| First trusted device → admin | Yes | Yes |

Restart the process after changing mode.

### Device admission (`OPENMAIL_DEVICE_ADMISSION`)

A **device** is the browser vault id `vk_…`, not the physical PC. Another browser, profile, or cleared site data is a new device.

- **`first_trust`** (code default): while **no trusted devices exist**, the next register is trusted; later devices stay `pending` and cannot fetch until approved in Settings.
- **`open`**: every register is trusted. Switching to `open` also promotes leftover `pending` devices on **re-register** (unlock the vault again).
- **`solo`**: admission is forced `open`.

If fetch says *device pending approval from a trusted device*, approve it from a trusted browser, or set `open` / `solo`, recreate the container, then **unlock the vault again**.

### Admin (no password)

When no admin is configured, the **first trusted** vault unlock / register (or `GET /api/device/me`) is written to `data/admin_device_ids` and hot-loaded. A writable `.env` / `OPENMAIL_ENV_FILE` may also be updated. Later devices are not admin.

**Lost that browser:** put the new `vk_…` (Settings → copy device id) in `OPENMAIL_ADMIN_DEVICE_IDS` and **restart**. A non-empty env value wins over the data file.

**Public URL:** whoever unlocks first becomes admin if the list is still empty. Unlock yourself first, or pre-set `OPENMAIL_ADMIN_DEVICE_IDS`, before advertising the instance.

---

## Import formats

One account per line. Fields separated by **`----`** (four hyphens).

| Kind | Line shape | Example |
|------|------------|---------|
| Graph OAuth | `email----password----client_id----refresh_token` | `u@x.com----x----abc----0.AXxxx` |
| IMAP (auto host) | `email----password` | `u@gmail.com----xxxx xxxx xxxx xxxx` |
| IMAP (explicit) | `imap----email----password----host----port` | `imap----u@x.com----pw----imap.x.com----993` |
| HttpApi | `https://worker…` or `email----https://…` or `url----secret` | `https://mail.example.workers.dev` |
| mail.com style | cookie / provider-specific paste accepted by parser | (use UI placeholder hints) |

Also supported: full **system snapshot** JSON export/import from the console (credentials + groups + notes layout).

---

## Configuration

Copy [`.env.example`](.env.example) → `.env`. Most important:

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENMAIL_MASTER_KEY` | **yes** | 32-byte AES key (base64/hex). Device registry + server wraps. |
| `OPENMAIL_INSTANCE_MODE` | no | **Important.** `hosted` if unset (quotas + license UI + optional cloud sync). `solo`: unlimited, hide those UIs, force admission `open`. [`.env.example`](.env.example) copies `solo`. Restart after change. |
| `OPENMAIL_DEVICE_ADMISSION` | no | `first_trust` if unset; `open` admits every register. Ignored in `solo`. Device = `vk_…` vault id, not a PC. |
| `OPENMAIL_ADMIN_DEVICE_IDS` | no | Admin `vk_…` (issue license codes). Empty: first trusted unlock becomes admin (`data/admin_device_ids`). Recover: set this and restart (env wins). |
| `OPENMAIL_IMAGE` | no | Compose image override (default `ianshaw027/openmail:v1.0.0`) |
| `OPENMAIL_PORT` | no | Host port (default `8000`) |
| `OPENMAIL_DATABASE_URL` | no | Compose/Docker: `sqlite:////data/openmail.db`. Bare `uvicorn` code default: `sqlite:///./openmail.db` |
| `OPENMAIL_DATA_DIR` | no | Registry + `admin_device_ids` directory (Docker `/data`). Not a Settings field |
| `OPENMAIL_ENV_FILE` | no | Optional path bootstrap may write `OPENMAIL_ADMIN_DEVICE_IDS` to. Does not replace pydantic `.env` load |
| `OPENMAIL_MASTER_KEY_FALLBACKS` | no | Previous master keys (comma/newline) for decrypt after rotation only |
| `LICENSE_TOKENS` | no | Hosted only: comma-separated codes that unlock client quotas |
| `LICENSE_HMAC_SECRET` | no | Optional: token may be HMAC-SHA256(`X-Device-Id`, secret) hex |
| `QUOTA_*` | no | Hosted caps: local 100 / cloud 50 / polls 1000. Ignored in `solo` |
| `ALLOW_PRIVATE_PROXY` | no | Default `false`. If `true`, client-supplied proxies may target private/loopback IPs |
| `FETCH_CONCURRENCY` | no | Suggested batch/import concurrency. Code default `5`; Compose / `.env.example` use `10` |
| `PROXY_POOL` | no | SOCKS/HTTP exits (`\|` or newlines). Empty unless you set it or run `up-with-warp.sh` |
| `SYNC_*` | no | Interval / folders / concurrency. Code `SYNC_CONCURRENCY` default `2`; Compose/`.env.example` use `10`. In `solo`, scheduled cycles are off |
| `PUBLIC_BASE_URL` | no | Public origin for legacy code-token URLs |
| `CODE_API_MAX_FETCH_PER_HOUR` | no | Public code-API cap per token per hour; `0` = no limit |
| `CODE_API_MAX_REFRESH_PER_HOUR` | no | Stricter cap for `refresh=1`; `0` = no limit |

Generate master key:

```bash
./scripts/gen-master-key.sh
```

---

## Upgrade

```bash
cd openmail
git pull
# pin version in .env if you want: OPENMAIL_IMAGE=ianshaw027/openmail:v1.0.0
docker compose pull
docker compose up -d
curl -s http://127.0.0.1:8000/api/health
```

Keep `./data` mounted across upgrades. Read [CHANGELOG.md](CHANGELOG.md) for breaking changes.

### Upgrade notes: the app no longer runs as root

Images up to `v0.3.6` ran as root, so an existing `./data` and `openmail.db` are
owned by `root`. Newer images run the app as an unprivileged uid instead. You do
**not** need to change anything: the container starts as root only long enough to
fix ownership of the mounted data directory, then drops privileges. A
bind-mounted `./data` keeps its current owner; only a `root`-owned directory is
reassigned.

Two things to be aware of:

- If you pin `user:` in `docker-compose.yml` (or pass `--user`), the container
  cannot repair ownership and will refuse to start unless that user already owns
  `./data`. The error message names the uid and the `chown` to run.
- `OPENMAIL_UID`/`OPENMAIL_GID` are no longer used. Leaving them in `.env` is
  harmless.

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `master_key_configured: false` | `OPENMAIL_MASTER_KEY` empty/invalid in `.env`; recreate container after fix |
| `attempt to write a readonly database` / container restart loop | `./data` is not writable by the container. Upgrade to the latest image (it self-heals), or `sudo chown -R 10001:10001 ./data`. Remove any `user:` override that does not own `./data` |
| Image pull fails on Apple Silicon | Image is **amd64-only** today — use a remote amd64 host, or `docker compose up --build` on arm (local build) |
| IMAP SSL / cert errors | Fixed for DNS-pinned IP + SNI in recent builds; pull latest image |
| IMAP `ascii codec` on folder names | Non-ASCII mailbox names need modified UTF-7 — use latest image |
| `device pending approval…` on Fetch | This `vk_…` is **not trusted** (`first_trust`). Approve from Settings, or set `open` / `solo`, recreate, then **unlock again** (re-register promotes leftover pending). Another browser is another device. |
| Not admin / cannot issue codes | Another device already bootstrapped. Put this `vk_…` in `OPENMAIL_ADMIN_DEVICE_IDS` and restart. |
| No license / “import to cloud” UI | Instance is `solo` — expected. |
| HTML body cropped / missing images | Hard-refresh. Use **Show remote images** for CDN assets. |
| Vault password every refresh | Same-tab session should resume; hard refresh / other browser needs password or recovery key |
| Recovery key never shown | Create flow must stay on gate until key acknowledged; update if on very old build |
| Demo feels “empty” | Each browser has its **own** vault — nothing is shared across visitors |
| 502 behind nginx | Upstream not up / wrong port; check `docker compose ps` and `curl localhost:8000/api/health` |

Smoke script (API only):

```bash
make smoke BASE_URL=http://127.0.0.1:8000
```

---

## Development

```bash
# Backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENMAIL_MASTER_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
uvicorn app.main:app --reload --port 8000

# Frontend (proxies /api → :8000)
cd frontend && npm install && npm run dev
```

Tests:

```bash
cd backend && source .venv/bin/activate && export OPENMAIL_MASTER_KEY=... && pytest -q
cd frontend && npm run build
```

Layout:

```
assets/      Brand SVG
backend/     FastAPI + providers + pytest
frontend/    Vue 3 + Vite + Pinia + i18n
docs/        architecture, ops, WARP, legal
scripts/     install, keygen, smoke, release, warp
.github/     CI + release workflows
```

---

## Release (maintainers)

```bash
# clean main → bump VERSION sources → commit → tag → push
make release V=1.0.0
```

On tag `v1.0.0` CI will:

1. Bake version into the image (`/api/health` → `1.0.0`)  
2. Push Docker Hub: `ianshaw027/openmail:v1.0.0`, `:1.0.0`, `:latest`  
3. Publish GitHub Release notes  

Needs secrets: `DOCKERHUB_TOKEN` (+ optional `DOCKERHUB_USERNAME`).

`main` is protected for non-admins (PR + CI). **Repository owner may push `main` directly.**

---

## Limitations

- Published image is **linux/amd64** only (no multi-arch yet).  
- Not a full mail client (no server-side FTS product, no multi-user tenancy).  
- Proxy path is **not** zero-knowledge while a request runs.  
- Microsoft OAuth: you bring your own app registration / refresh tokens.  
- Some providers (cookie / worker) depend on upstream HTML/API stability.  

Roadmap ideas (not promised): arm64 images, richer send UX, more import presets.

---

## Security

Report vulnerabilities **privately** via [GitHub Security Advisories](https://github.com/IanShaw027/openmail/security/advisories/new) — see [SECURITY.md](SECURITY.md). Do not open public issues for exploitable bugs.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## License

[MIT](LICENSE)

## Legal (instance operators)

- Privacy: [zh](docs/legal/privacy.zh.md) · [en](docs/legal/privacy.en.md)  
- Terms: [zh](docs/legal/terms.zh.md) · [en](docs/legal/terms.en.md)  

Operators are responsible for their deployment, keys, and compliance.

---

## Extra docs (optional depth)

| Doc | Topic |
|-----|--------|
| [docs/architecture.md](docs/architecture.md) | Module boundaries, API surface |
| [docs/14-ops-and-smoke.md](docs/14-ops-and-smoke.md) | Ops: instance mode, admission, admin bootstrap, smoke |
| [docs/16-warp-proxy-pool.md](docs/16-warp-proxy-pool.md) | WARP profile |
| [docs/17-release.md](docs/17-release.md) | Release internals |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

Brand files: [`assets/logo-icon.svg`](assets/logo-icon.svg) · [`logo.svg`](assets/logo.svg) · [`logo-dark.svg`](assets/logo-dark.svg) · [`social-banner.svg`](assets/social-banner.svg)
