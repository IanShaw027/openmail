# Architecture (current)

OpenMail is a **local-first** multi-source mailbox console.

```
Browser (Vue 3)                         Server (FastAPI)
─────────────────                       ────────────────
Vault (PBKDF2→AES-GCM)                  Device registry (encrypted on disk)
  accounts / 2FA / mailCache            Optional cloud Account rows (sealed)
Pinia + localStorage (ciphertext)       Proxy fetch / send (ephemeral creds)
     │ HMAC + sealed blobs                     │
     └──────────── HTTPS ──────────────────────┘
                                               │
                                    Graph / IMAP / SMTP / HttpApi / mail.com
```

## Repository layout

```
openmail/
├── backend/                 # FastAPI app + pytest
│   ├── app/
│   │   ├── main.py          # App factory, CSP, SPA static
│   │   ├── config.py        # Settings
│   │   ├── crypto.py        # Server AES-GCM (master key)
│   │   ├── db.py / models.py / schemas.py
│   │   ├── deps.py / deps_device.py
│   │   ├── providers/       # oauth, imap, cookie, http_api
│   │   ├── routers/         # HTTP endpoints
│   │   └── services/        # fetch, send, ssrf, device_auth, vault-related helpers
│   └── tests/
├── frontend/                # Vue 3 + Vite + Pinia + vue-i18n
│   └── src/
│       ├── pages/           # Console, Mails, 2FA, Settings, legal
│       ├── stores/          # accounts, vault, twofa, mailCache, settings
│       ├── utils/           # cryptoVault, sanitizeHtml, totp, import
│       └── api/             # fetch client + device headers
├── docs/                    # Architecture, ops, legal (see archive/)
├── scripts/                 # master key, smoke, warp up
├── docker-compose.yml
└── Dockerfile               # multi-stage: frontend build → backend image
```

## Active API surface

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/health` | public |
| GET | `/api/config/public` | public; `cloud_used` only with vault HMAC |
| POST | `/api/device/register` | public; binds `vk_*` to secret (no takeover) |
| * | `/api/accounts*` | vault HMAC |
| POST | `/api/fetch/proxy` | vault HMAC + poll quota |
| POST | `/api/fetch/send` | vault HMAC + poll quota |
| GET/POST | `/api/v1/code/{token}` | legacy token (if row exists) |

## What we intentionally do not do

- Multi-user registration / admin password UI
- Server-side full-text mail product (browser `mailCache` instead)
- Zero-knowledge proxy (server must see secrets briefly to call upstream)

## Module boundaries

| Layer | Responsibility |
|-------|----------------|
| `providers/*` | Protocol adapters only |
| `services/fetch_service` | Orchestration, cookies write-back, proxy rotation |
| `services/ssrf` | Outbound host policy + pin |
| `services/device_auth` | Vault device identity |
| Frontend `stores/vault` | Client encryption + lock lifecycle |

## Historical docs

Early product docs assumed multi-user pools. They live under [`docs/archive/`](archive/README.md) and are **not** the source of truth for the running product.

## Maintainability

See [`maintainability.md`](maintainability.md) for dead-code inventory, modularization targets (`ConsolePage.vue`, accounts store), and overdesign notes (SyncWorker vs sealed cloud, legacy pools).
