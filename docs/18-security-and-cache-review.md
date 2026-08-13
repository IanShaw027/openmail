# Security & cache review notes (2026-08)

Living summary of multi-module cross-reviews and follow-up fixes.  
Does **not** replace threat modeling for multi-tenant SaaS.

## Architecture posture

| Layer | Posture |
|-------|---------|
| Browser vault | AES-GCM (PBKDF2); accounts / 2FA / mail cache encrypted at rest in localStorage |
| Cloud accounts | Optional; prefer **client-sealed** so server cannot decrypt |
| Device auth | `vk_*` + HMAC; body-bound signature on mutating requests |
| Fetch lock | DB `lease_token` + TTL (multi-worker safe) |
| Poll quota | Durable `device_poll_events` (hourly window) |

## Completed hardening (high level)

### Durability / cache

- Critical paths `await flushPersist` (notes, fetch mail, import, delete)
- Folder-scoped `firstFullDone` / `sinceFor` (no cross-folder cursor bleed)
- Mail cache keys: `folder` + optional IMAP `uidvalidity`
- Cookie/HttpApi local date filter (`time_paging=local_filter`)
- Clear&refetch clears **current folder only**
- Fetch generation token: switching accounts does not apply stale results
- Dual-write: note/proxy/status; full credential snapshot only when usable secrets present
- Credential PATCH **deep-merges** on server (partial keys no longer wipe blob)

### Security

- Device HMAC: `{ts}.{METHOD}.{path}.{body_sha256}[.{nonce}]` + `X-Device-Body-Sha256`; mutating replays inside the timestamp window are rejected
- GET/HEAD legacy path-only HMAC still accepted without body hash
- HTML email sanitizer: CSS allowlist, strip `url()`, `expression`, etc. Mail bodies render in a sandboxed iframe (no `allow-same-origin` / `allow-popups`)
- Secret copy UX: explicit “full secret copied” toast
- Code API short-cache TTL (`CODE_API_CACHE_TTL_SECONDS`, default 90s)
- Fetch lock lease + token ownership (crash recovery; no permanent stuck lock)
- `CORS_ORIGINS` empty by default; parent CSP has no unused `unsafe-inline` / Cloudflare Insights
- IMAP/SMTP egress honours `credentials["proxy"]` (SOCKS5 / HTTP CONNECT to the SSRF-pinned IP)

### Ops / multi-worker

- Poll quota stored in DB (`device_poll_events`), not process memory alone
- Fetch lock acquire via conditional `UPDATE` + `lease_token`

## Residual risks (known)

| Item | Notes |
|------|--------|
| Multi-tab vault last-writer-wins | No BroadcastChannel merge |
| pagehide async encrypt | Best-effort; critical paths already await |
| Session wrap ≈ DEK in sessionStorage | Same-origin XSS that can read sessionStorage recovers the DEK; lock deletes the wrap. Not a confidentiality boundary against XSS. |
| Parent CSP still needs `unsafe-eval` | vue-i18n compiles messages via `new Function`. `script-src` no longer includes `unsafe-inline` or Cloudflare Insights. |
| Cookie true pagination | Local filter only; not infinite history |
| IndexedDB mail cache | Still localStorage ciphertext blobs |
| Redis fetch lock | Optional; DB lease is enough for typical multi-worker SQLite/Postgres |

HMAC mutating requests now bind an optional `X-Device-Nonce` and reject replays of the same signature inside the timestamp window. Client-supplied proxies are SSRF-checked and hostname-pinned; IMAP/SMTP as well as Graph/cookie honour `credentials["proxy"]` (operator WARP pool included). `CORS_ORIGINS` defaults to empty (same-origin SPA). Viewing the recovery key and exporting 2FA re-ask the vault password.

## Suggested operator settings

```bash
# Code API cache
CODE_API_CACHE_TTL_SECONDS=90

# Fetch lock lease (seconds)
FETCH_LOCK_LEASE_SECONDS=180

# Unlicensed poll cap per device per hour
QUOTA_MAX_POLL_PER_HOUR=1000
```

## Verification

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run build
```

## Related reviews

Scratch-session cross-review artifacts (not in git):

- Module reviews: vault, mail-ui, backend-fetch, backend-security, ui-ux, contract
- Summary id example: `cross-review-SUMMARY-*.md` under `$TMPDIR/grok-*`

## Changelog (implementation waves)

1. **Durability P0** — flush, dual-write note, heal serverId, linked delete, upload flush  
2. **Fetch correctness** — folder cursors, uidvalidity, local time filter, clear/race  
3. **Security P1** — HMAC body, credential merge, XSS style, clipboard/i18n  
4. **Ops P2** — poll quota DB, document multi-worker lock model  
