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

## Security & durability notes

See **[18-security-and-cache-review.md](./18-security-and-cache-review.md)** for
HMAC body binding, poll quota durability, fetch leases, and residual risks.

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
| GET | `/api/config/public` | public; `instance_mode`, `license_ui_enabled`, `sync_ui_enabled`, `device_admission` (effective; solo→`open`), `licensed` (solo→`true` without token), plus `fetch_concurrency` / lookback / retention / `auth_ui_enabled`; `cloud_used` only with vault HMAC |
| POST | `/api/device/register` | public; binds `vk_*` to secret (no takeover); first trusted may become admin |
| GET | `/api/device/me` | vault HMAC (trusted or pending) |
| * | `/api/device/list`, `/approve`, `/reject`, `/revoke` | trusted HMAC |
| * | `/api/accounts*` | trusted vault HMAC |
| POST | `/api/accounts/{id}/fetch` | trusted HMAC + poll quota (`solo` unlimited) |
| GET | `/api/accounts/meta/quota` | trusted HMAC |
| POST | `/api/fetch/proxy` | trusted HMAC + poll quota (`solo` unlimited) |
| POST | `/api/fetch/send` | trusted HMAC + poll quota (`solo` unlimited) |
| GET | `/api/sync/status`, `/api/sync/delta` | trusted HMAC |
| * | `/api/transfer/*` | vault transfer (short-lived ciphertext) |
| * | `/api/admin/licenses*` | trusted **admin** HMAC |
| GET/POST | `/api/v1/code/{token}` | legacy token (if row exists) |

410 compatibility stubs (not product surfaces): `/api/me/mails/*`, `POST /api/me/sync`, `POST /api/accounts/{id}/send`.

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
| `services/device_auth` | Vault device identity + admission (`first_trust` / `open`; `solo` forces `open`) |
| `services/admin_ids` | Effective admin set + first-trusted bootstrap |
| `services/license` | Tokens / HMAC / issued codes; `solo` always licensed |
| Frontend `stores/vault` | Client encryption + lock lifecycle |
| Frontend `stores/settings` | Public config: mode, license UI, sync UI, quotas |

Operator contract (mode / admission / admin recovery): [14-ops-and-smoke.md](14-ops-and-smoke.md#3-instance-mode-admission-and-admin).

## Historical docs

Early product docs assumed multi-user pools. They live under [`docs/archive/`](archive/README.md) and are **not** the source of truth for the running product.

## Maintainability

See [`maintainability.md`](maintainability.md) for dead-code inventory, modularization targets (`ConsolePage.vue`, accounts store), and overdesign notes (SyncWorker vs sealed cloud, legacy pools).
