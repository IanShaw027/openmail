# OpenMail Frontend

Vue 3 console for OpenMail — **local-first** multi-source mailbox UI (no user/admin login).

## Stack

- Vite + Vue 3 + TypeScript
- Vue Router, Pinia, vue-i18n (zh-CN / en)
- Web Crypto vault (`cryptoVault.ts`), TOTP (`otpauth` + `jsqr`)
- Custom CSS design tokens (no Element Plus)

## Setup

```bash
cd frontend
npm install
npm run dev
```

Dev server: http://127.0.0.1:5173 — `/api` proxied to the backend (`vite.config.ts`).

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Vite dev server |
| `npm run build` | `vue-tsc` + production build |
| `npm run preview` | Preview production build |

## Environment

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE` | API base (no trailing slash). Empty = same-origin. |

## Routes

| Path | Page |
|------|------|
| `/` | Console — import, batch fetch/send, groups, notes |
| `/mails` | Local mail cache search |
| `/2fa` | TOTP/HOTP manager |
| `/settings` | Vault lock, license, fetch policy |
| `/privacy`, `/terms` | Legal |

Legacy paths (`/login`, `/register`, `/admin`, `/me/*`) redirect to `/` or `/mails` / `/settings`.

## Data model (browser)

| Store | Persistence |
|-------|-------------|
| `vault` | Cipher packages in `localStorage`; DEK only in memory while unlocked |
| `accounts` | Encrypted with vault; optional cloud sealed upsert |
| `twofa` | Encrypted with vault |
| `mailCache` | Encrypted with vault (local history after fetch) |

Fetch uses `POST /api/fetch/proxy` with credentials in the body. Device HMAC headers are attached after vault unlock + device register.

## Layout

```text
src/
  pages/          Console, Mails, 2FA, Settings, legal
  stores/         accounts, vault, twofa, mailCache, settings
  utils/          cryptoVault, device, sanitizeHtml, import/export, totp
  api/            client + accounts
  components/     VaultGate, Toast, UiSelect
```

See [docs/architecture.md](../docs/architecture.md) and [docs/maintainability.md](../docs/maintainability.md).
