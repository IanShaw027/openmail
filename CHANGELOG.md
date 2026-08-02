# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Added

- Tag-driven release: each `v*` tag bakes version, pushes **Docker Hub**, creates GitHub Release (`make release V=…`)

### Changed

- Canonical image is Docker Hub only (`ianshaw027/openmail`); GHCR publish removed from CI
- README / compose / install document the Hub image inline (no GHCR default)

## 0.1.0

### Security

- Strict vault device HMAC for cloud accounts, proxy fetch, and send
- Device register rejects takeover / public_id aliasing
- IMAP/SMTP/HTTP SSRF checks; DNS pin for HTTP and mail endpoints where possible
- Client vault (PBKDF2 + AES-GCM), recovery key, auto-lock clears Pinia secrets
- DOMParser-based HTML sanitization for mail bodies
- CSP headers compatible with vue-i18n and Cloudflare Insights
- IMAP TLS SNI when connecting via DNS-pinned IP; modified UTF-7 mailbox names

### Added

- Published image: `ianshaw027/openmail:v0.1.0` (Docker Hub), GHCR via CI
- Factory reset of local vault environment; body expand modal; sent folder
- Open-source meta: LICENSE, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, CI

### Changed

- Product model is **local-first** (no user/admin login UI)
- Cloud credentials prefer client-sealed blobs (server cannot decrypt)
- `docker-compose.yml` defaults to pull `ianshaw027/openmail:v0.1.0`

### Removed / deferred

- User registration, admin console, server mail search UI
- Code-API **create** (legacy token URLs may still resolve if present in DB)
