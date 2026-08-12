# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Device admission is first-trust by default.** The first vault device that
  registers with a server is trusted automatically; later devices land as
  `pending` and cannot call privileged APIs until a trusted device approves
  them. Set `OPENMAIL_DEVICE_ADMISSION=open` to restore the previous open
  registration behaviour. Existing registry entries without a status are
  treated as trusted on upgrade so multi-device installs are not locked out.

### Fixed

- **Upgrade from a root-run install no longer crash-loops.** Images before this
  change ran as root and created `data/openmail.db` as `root:root`; the switch to
  an unprivileged uid then made SQLite fail with `attempt to write a readonly
  database` (WAL writes at connect time, so even reads failed) with nothing in
  the logs pointing at ownership. The container now starts as root only long
  enough to reconcile ownership of the mounted data directory, then drops to an
  unprivileged uid before running the app.
- A bind-mounted `./data` keeps its existing owner instead of being reassigned to
  uid 10001, so the host user who created the directory can still read it.
- Enabling WAL is best-effort: a non-writable database directory now logs a
  warning and falls back to the rollback journal instead of preventing startup.
- Pinning `user:`/`--user` on a data directory that user does not own now fails
  with an explicit message naming the uid and the `chown` to run, rather than an
  opaque SQLite error.
- SQLite migrations no longer leave foreign key enforcement disabled on the
  connection they used. `PRAGMA foreign_keys` is a no-op inside a transaction,
  so the pragma meant to re-enable it after dropping legacy tables never took
  effect, and the connection went back to the pool with constraints off.
- `CODE_API_MAX_FETCH_PER_HOUR=0` / `CODE_API_MAX_REFRESH_PER_HOUR=0` now mean
  "no limit" instead of being read as unset and replaced by the default.
- Requests for unknown or disabled code-API tokens are rate limited per client
  IP. Only existing tokens could be charged before, leaving the not-found path
  unmetered and usable for token enumeration.
- The code-API rate limit can no longer be exceeded by concurrent requests; it
  now runs in its own transaction so the count-and-insert is serialized.
- A URL containing an encoded null byte returns the SPA instead of a 500.
- Idle auto-lock counts reading as activity (scroll wheel, pointer movement,
  touch), warns about a minute beforehand, flushes pending vault writes first,
  and waits while a compose draft has unsaved text. It could previously fire
  mid-session on someone who was only reading, with no warning, discarding an
  unsent draft along with the rest of the page state.
- Shrinking mail retention while the vault is still locked now warns that mail
  will be deleted. The confirmation used to be skipped entirely, because the
  encrypted cache reports zero messages until it is decrypted.
- Declining that retention confirmation no longer discards the lookback and
  concurrency edits saved alongside it.

### Changed

- `docker-compose.yml` no longer sets `user:`; the entrypoint decides which uid
  to run as. `OPENMAIL_UID`/`OPENMAIL_GID` are no longer consulted (harmless if
  still present in your `.env`). Pin `user:` yourself only if you also own
  `./data` — see "Upgrade notes" in the README.

### Security

- Docker Hub publishes now require the test suite to pass first. Nothing
  previously connected the two, which is how a broken image shipped.
- `release.yml` no longer interpolates the dispatch `tag` input into a shell
  script, where it ran before the validation that was supposed to constrain it.

## [0.3.6] — 2026-08-06

### Added

- **Server-decryptable cloud poll**: upload/import with poll stores master-key credentials (`sync_enabled`); SyncWorker upserts `mail_items` + `sync_cursors`
- **`GET /api/sync/status`** and **`GET /api/sync/delta`**: device HMAC incremental pull into local mailCache on vault unlock (+ 3‑minute poll)
- Shared **stable_id** (`p:` / `m:` / `wh_`+sha256) for local and cloud merge

### Fixed

- SyncWorker catch-up no longer stops on a full page of already-known (overlap) mail; pages older via `before=` within the since window
- Cloud delta ack uses mail `updated_at`+`id` keyset instead of wall-clock `server_time` (avoids skipped rows)

### Changed

- Cloud poll is no longer client-sealed by default (sealed remains backup-only / non-poll)

## [0.3.5] — 2026-08-05

### Added

- **mail.com cookie send**: same web session as fetch — oauthbridge SPA grant (`urn:mam:oauth:grant-type:spa`, Basic `client_id:*******`, `sid` query) then `webmail-cats` `mailsubmission` with Bearer `qX{JWT}` and `no_cache=auth_id`
- **Display settings**: default timezone `Asia/Shanghai` (configurable); theme light / dark / system

### Changed

- Cookie / `@mail.com` accounts send via session API, not SMTP; `accountCanSend` allows session cookies without password re-entry

## [0.3.4] — 2026-08-04

### Fixed

- Parse mail.com UI dates (`Tuesday, August 04, 2026 at 10:56 AM`) for sort and since/before
- CF temp-mail (ian10-mail-admin): map `created_at` → date, `recipient` → to
- Mail list newest-first; load-more older mail appends below

### Changed

- Server-side body slim on fetch; local vault quota drops oldest mail by date first


## [0.3.3] — 2026-08-03

### Fixed

- Email HTML links with `/?redirectUrl=…` (OpenAI trackers, etc.): unwrap real URL, confirm, then open; landing on `mail.clomio.ai/?redirectUrl=…` also confirms and navigates
- **清空重拉**: atomically **replace** the folder cache (no leftover mails after a short page)
- Short page (&lt; requested 20): mark **no older mail** and stop infinite-scroll pull-up (clear-refetch, first window, load-more)

### Changed

- Unified multi-folder fetch policy for list-row / panel / batch: empty folder → latest 20; has cache → catch-up since newest (up to 100×20 rounds)
- Catch-up `since` uses newest mail − 2 minutes (clock skew)

## [0.3.2] — 2026-08-03

### Added

- Multi-folder fetch (inbox / spam / sent): empty folder → latest 20; else catch-up since newest
- Mail list **infinite scroll** (pull-up load older); top refresh / bottom loading status without a load-more button
- SMTP host table for **GMX / Zoho**; after SMTP send, best-effort **IMAP APPEND** to Sent
- NetEase **IMAP ID** after login (fixes 126/163 `Unsafe Login` on SELECT)
- mail.com **messagelist multi-page** paging for load-older / wider windows
- Client-sealed cloud accounts: **re-seal** envelope when password/tokens change

### Changed

- Credential TXT export rebuilds lines from **current** secrets (no stale `rawLine`)
- Vault unlock migrates stale `rawLine`; fetch merge **reparses** cached OTP codes
- Delete account clears unused **mailCache**; system import keeps cache only for restored accounts
- 2FA cards: wider layout, multi-line account names (less aggressive ellipsis)
- Verification parser: reject years / marketing words (`purchase`, `two-factor`, bare `one-time`)

### Fixed

- Clear & refetch keeps the list until a **successful** response, then replaces
- Load-more never wipes existing messages; append only after success
- Sticky false `verification_code` on mailCache merge
- Panel verification **banner** removed (less redundant UI)

## [0.3.1] — 2026-08-03

### Fixed

- vue-i18n **Invalid linked format** crash when rendering import placeholder emails (`user@…` must use `{'@'}` escape)

## [0.3.0] — 2026-08-03

### Security

- Device HMAC **body binding** (`X-Device-Body-Sha256`); mutating methods (POST/PUT/PATCH/DELETE) require body hash
- **Transfer** routes require registered vault device; status limited to host/guest/claim-token; HTTP-level auth tests
- **Cloud account quota** floored on live `COUNT(accounts)` + conditional UPDATE; IntegrityError reconcile
- **Poll quota** durable in DB with per-device serialization (SQLite `BEGIN IMMEDIATE` when available)
- **Fetch lease** token-owned DB lock with expiry, post-lease min-interval recheck, `retry_after` on contention
- Client-sealed accounts reject accidental server-side credential unseal (require `_om_unwrap_sealed` or `client_sealed`)
- HttpApi **streamed** response body limit (no full buffer before cap); IMAP BEFORE candidate budget
- Unique `(owner, email)` migrate fail-closed with duplicate-key diagnostics

### Added

- Durable `device_poll_events` / poll quota state; cloud `device_quota_state`
- Fetch lock `lease_token`; code-API cache TTL config
- Docs: security & cache review (`docs/18-security-and-cache-review.md`)
- Frontend: provider capability helpers, account UI meta utilities

### Changed

- Credential PATCH **deep-merge** (empty string clears a key; partial patch no longer wipes blob)
- mailCache keys include IMAP **UIDVALIDITY**; upgrade path drops legacy same-folder keys
- Transfer status no longer enumerable by arbitrary registered devices

### Fixed

- Concurrent cloud create / poll quota races under multi-worker
- Permanent fetch in-flight after worker crash (lease expiry)
- Transfer anonymous create / unauthenticated status

## [0.2.1] — 2026-08-03

### Added

- Unified **brand logo registry** (`brandLogos.ts`, 100+ Simple Icons / multi-color marks) shared by mail chips, 2FA, and purpose shortcuts
- Expanded 2FA service presets (OpenAI, Claude, Cloudflare, Docker, Stripe, …)
- CF temp-mail **expand/collapse** under the API source row (children not flat peers)

### Fixed

- **mail.com** HTML body via lightmailer `mailbody` iframe hydrate
- Mainstream **body extraction**: nested MIME prefer-HTML, Graph `body`/`uniqueBody`, HttpApi field aliases
- Incremental fetch no longer uses `lastFetchAt` as `since` (empty pull after cache)
- Purpose shortcuts: idle → on → used(gray) cycle, live text sync, `~~tag~~` disabled form
- Manage-groups modal layout consistency
- CF temp children: read-only credentials, per-child fetch, re-parent orphans on sync
- **localStorage QuotaExceeded** on `openmail.userSettings` — drop unused `lastFetchAt`, cap `firstFullDone`, safe persist + prune

### Changed

- Brand filter includes `cf_temp`, GMX, Proton, Zoho, DuckMail
- ChatGPT purpose / 2FA marks resolve to OpenAI glyph

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

[Unreleased]: https://github.com/IanShaw027/openmail/compare/v0.3.4...HEAD
[0.3.4]: https://github.com/IanShaw027/openmail/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/IanShaw027/openmail/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/IanShaw027/openmail/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/IanShaw027/openmail/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/IanShaw027/openmail/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/IanShaw027/openmail/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/IanShaw027/openmail/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/IanShaw027/openmail/releases/tag/v0.1.0
