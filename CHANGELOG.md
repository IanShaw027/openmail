# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Security

- Strict vault device HMAC for cloud accounts, proxy fetch, and send
- Device register rejects takeover / public_id aliasing
- IMAP/SMTP/HTTP SSRF checks; DNS pin for HTTP and mail endpoints where possible
- Client vault (PBKDF2 + AES-GCM), recovery key, auto-lock clears Pinia secrets
- DOMParser-based HTML sanitization for mail bodies
- CSP headers compatible with vue-i18n and Cloudflare Insights

### Changed

- Product model is **local-first** (no user/admin login UI)
- Cloud credentials prefer client-sealed blobs (server cannot decrypt)
- Repository layout docs and open-source meta files (LICENSE, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT)
- Legal docs rewritten for local-first; CI + issue/PR templates under `.github/`
- Console modularization: shared labels/`mapPool`, send & group modals; accounts mappers module

### Removed / deferred

- User registration, admin console, server mail search UI
- Code-API **create** (legacy token URLs may still resolve if present in DB)
- Dead client `api/mails.ts` and unused `app/security.py`
- Obsolete multi-user docs moved to `docs/archive/`

## 0.1.0

- Initial multi-provider fetch console (OAuth Graph, IMAP, mail.com cookie, HttpApi)
- Docker single-image SPA + API
- Optional in-stack WARP SOCKS pool
