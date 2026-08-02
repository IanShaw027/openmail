# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-08-03

### Added

- **Device QR transfer** — PC ↔ phone vault sync via short-lived server ciphertext package (approve / reject / overwrite ack)
- **Brand taxonomy** — CF temp-mail + regular brands (Gmail, Outlook, QQ, 163, mail.com, GMX, Proton, Zoho, DuckMail, …) with multi-color SVG chips on the console
- **2FA service brand SVGs** — Google, Microsoft, GitHub, Apple, Amazon, Discord, X, Facebook, Dropbox, Steam, Binance (+ fallback mark)
- **2FA service filter chips** and **drag-to-reorder** with `sortOrder` persisted in the vault
- **2FA circular countdown** (amber ≤10s / red ≤5s) instead of a thin bar
- **Mail load-more** — first fetch 20 messages; “load more” shows more from cache then pulls **10 older** (`before` + `max_messages` on proxy fetch; IMAP / Graph)
- **Clear & refetch** — wipe local mailbox cache and pull latest 20
- **Auto-detect** unknown accounts every 5s (small concurrent batches)
- **CF Worker root URL** expands to known mail API paths; HttpApi auth styles expanded
- Top-nav **GitHub** icon → [IanShaw027/openmail](https://github.com/IanShaw027/openmail)
- Console: purpose note chips, folder tabs (inbox / spam / sent), full-bleed topnav / resizable panes polish
- Docs: expanded README, ops, screenshots, Dependabot, security advisory path

### Changed

- Mobile console **actions column**: no sticky freeze; **collapsed ⋯** expand per row
- Copying email / secret / code / 2FA **also selects** that mailbox so the mail panel follows
- Default quick fetch window raised toward **20** messages; proxy supports `before` + `max_messages`
- Published image remains **Docker Hub only** (`ianshaw027/openmail`)

### Fixed

- **Client-sealed** accounts never hit server decrypt; clear local-secrets errors
- WARP / egress retries capped so proxy fetch stays under client timeout
- **mail.com**: faster first login; `logout/?ls=wd` treated as wrong password; body hydrate via detail URL
- **CF Worker ConnectError** — TLS SNI uses original host (not pin-to-IP alone)
- 2FA form: dropdowns selectable, simplified fields, full SHA/SHA3, webcam + image QR
- Brand marks and IMAP host classification for multi-color chips

### Security

- Sample / demo credentials scrubbed from docs and fixtures where present
- Vault transfer packages are opaque ciphertext; short TTL on the server

## [0.1.0]

### Security

- Strict vault device HMAC for cloud accounts, proxy fetch, and send
- Device register rejects takeover / public_id aliasing
- IMAP/SMTP/HTTP SSRF checks; DNS pin for HTTP and mail endpoints where possible
- Client vault (PBKDF2 + AES-GCM), recovery key, auto-lock clears Pinia secrets
- DOMParser-based HTML sanitization for mail bodies
- CSP headers compatible with vue-i18n and Cloudflare Insights
- IMAP TLS SNI when connecting via DNS-pinned IP; modified UTF-7 mailbox names

### Added

- Published image: `ianshaw027/openmail:v0.1.0` (Docker Hub)
- Factory reset of local vault environment; body expand modal; sent folder
- Open-source meta: LICENSE, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, CI

### Changed

- Product model is **local-first** (no user/admin login UI)
- Cloud credentials prefer client-sealed blobs (server cannot decrypt)
- `docker-compose.yml` defaults to pull `ianshaw027/openmail:v0.1.0`

### Removed / deferred

- User registration, admin console, server mail search UI
- Code-API **create** (legacy token URLs may still resolve if present in DB)

[Unreleased]: https://github.com/IanShaw027/openmail/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/IanShaw027/openmail/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/IanShaw027/openmail/releases/tag/v0.1.0
