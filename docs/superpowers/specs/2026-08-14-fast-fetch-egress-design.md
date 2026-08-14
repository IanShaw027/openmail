# Fast fetch: parallel folders, direct-first egress, IMAP header-then-body

**Date:** 2026-08-14  
**Status:** Implemented on `feat/fast-fetch-egress` (including Graph folder membership, OAuth access-token reuse, IMAP greeting timeout split, and durable pending bodies)  
**Reviewers:** Claude Opus 5 (Approve with changes), GPT-5.6-sol (Approve with changes)  
**Scope:** Interactive fetch latency. Not a rewrite of mail cache, vault, or cookie SSO.

---

## 1. Problem

Interactive 取件 feels slow. Three stacked costs:

1. **WARP-first egress.** `list_proxy_candidates` returns `[sticky warp, other warps…, None(direct)]`. Interactive fetch walks that list (capped at 3). Happy path already pays a WARP hop. A dead node can burn ~30s before direct is tried.
2. **IMAP downloads full `RFC822` per UID, sequentially, on one connection.** The folder JSON waits for every MIME body (attachments included).
3. **Frontend already fires inbox/spam/sent in parallel for IMAP/OAuth**, but `fetchFolderCatchUp` passes `silent: true`, which disables the existing per-folder panel refresh in `fetchOne` (`nested && !opts.silent`). The user sees nothing new until the slowest folder (and its catch-up chain) finishes. Cookie accounts stay serial (correct).

Send is **already direct** unless the client pinned a proxy. `send_mail` does not call `list_proxy_candidates`. Do not add send retries.

---

## 2. Goals

1. **Three-way parallel.** Prefer 3 in-flight folder requests. **Whichever folder completes first is rendered immediately.**
2. **Direct first** for interactive IMAP/OAuth. WARP only if direct is blocked/abnormal, **or** for bulk jobs (batch fetch, import precheck, `sync_worker`).
3. **IMAP: headers first, then bodies newest-first, multiple returns** on the first-window path (empty cache / clear-refetch, 20 messages). Catch-up stays one request per page.

Success criteria:

- First-window IMAP/OAuth: subject/from/date list paints before bodies finish.
- Newest inbox body (OTP) is the first body fetched after headers.
- Direct-reachable IMAP/OAuth mailboxes never open WARP on the happy path.
- Auth failures never walk the WARP pool.
- Cookie / mail.com session reuse and sticky affinity stay intact.
- Existing catch-up / load-more / uidvalidity / mailCache merge rules stay valid.
- One interactive first-window fetch costs **6 HTTP requests max** (3 headers + 3 bodies), not hundreds.

Non-goals:

- Persistent server-side IMAP connection pool.
- BODYSTRUCTURE + part fetch (v2).
- Changing `FETCH_CONCURRENCY` / `SYNC_CONCURRENCY`.
- Graph `@odata.deltaLink`.
- Automatic send failover / phasing.

---

## 3. Research (online)

| Source | Takeaway |
|--------|----------|
| [RFC 4549](https://www.rfc-editor.org/rfc/rfc4549) | Summary FETCH first (`FLAGS` / size / structure). Do **not** include `RFC822` in the summary. Then fetch bodies as needed. |
| [RFC 3501](https://datatracker.ietf.org/doc/html/rfc3501) §5.5 / §6.4.5 | One selected mailbox per connection. `BODY.PEEK[]` does not set `\Seen`. Python `imaplib` does not pipeline well. |
| Thunderbird / Dovecot | Summary: `UID FETCH … BODY.PEEK[HEADER.FIELDS (From To Subject Date Message-ID …)]`. Then `BODY.PEEK[]` when the body is wanted. |
| [imapwiki OpenMessage](https://imapwiki.org/ClientImplementation/OpenMessage) | Avoid full MIME when attachments are huge. High-latency: a second connection can beat tiny partial fetches. |
| [MS Graph message](https://learn.microsoft.com/en-us/graph/api/resources/message) | `$select` only what you need. Including `body` on the list worsens TTFB. |
| NDJSON / SSE | Best TTFB on one socket. **Rejected as primary:** Cloudflare / nginx buffering can hold the stream until complete. |

---

## 4. Current code (verified)

- Egress: `backend/app/services/proxy.py` `list_proxy_candidates` — WARP pool then `None`.
- Cap: `backend/app/services/fetch_service.py` `_cap_egress_candidates` — **strips `None` and appends it last**. A `prefer_direct=True` list `[None, w1, w2, …]` becomes `[w1, w2, None]` after cap. This must change or direct-first is a no-op.
- Walk: `fetch_proxy` (line 729), `fetch_account` (line 286). Retry: `_is_retryable_egress_error` (string markers; written for WARP→direct).
- Stored-account lease: `account_fetch_slot` is **per account id**, plus `fetch_min_interval_seconds` default **3.0**. Three parallel folder requests on `/api/accounts/{id}/fetch` already race: one wins, others get `FetchInFlightError`. Prerequisite fix: lease key `(account_id, folder)`.
- IMAP: `_select_folder` uses `conn.select(..., readonly=True)` (EXAMINE). Today's `RFC822` therefore should **not** persist `\Seen` on a compliant server. Switching to `BODY.PEEK[]` is still correct; do not claim a visible `\Seen` bug.
- IMAP limits: provider defaults 15/50; `fetch_proxy` quick default 20. Interactive catch-up sends `CATCH_UP_PAGE = 100` up to `CATCH_UP_MAX_ROUNDS = 20` **sequential** rounds **per folder**.
- Graph: `_SELECT` includes `body,uniqueBody` on the list.
- Frontend: `fetchOne` already refreshes the open tab from cache when `nested && !opts.silent`. `fetchFolderCatchUp` sets `silent: true`, so that path never runs. Cache **is** written per folder; only the **panel reload** waits.
- Client timeout: `proxyFetchMail` 55s IMAP/OAuth, 90s cookie. `fetchServerAccount` uses `apiRequest` default (no explicit timeout).
- Send: no pool walk; only request/account `proxy`.
- Poll quota: `check_poll_quota` ~120/hour unlicensed, SQLite `BEGIN IMMEDIATE` per device.

---

## 5. Approaches (reviewed)

| | A discrete two-phase HTTP | B NDJSON/SSE | C 3 IMAP conns into one folder |
|--|--|--|--|
| CDN-safe | yes | no (CF/nginx buffer) | n/a |
| Extra LOGIN | yes (headers + bodies) | no | triple |
| Reviewers | keep for **first-window only** | reject as primary | reject |

**v1 recommendation (consensus):**

- **Egress + per-folder paint + IMAP summary-then-body on one connection** for every request.
- **Two-phase HTTP only on the first-window path** (empty cache / clear-refetch, max 20). Headers request returns list with no full bodies; bodies request fills newest-first on one connection.
- **Catch-up / load-more stay `phase=full`** (one HTTP, one IMAP login, internal header-then-body). This avoids 100×20×body-rounds.

---

## 6. Design

### 6.1 Egress policy

| Mode | When | Order after order-preserving cap (max 3) |
|------|------|------------------------------------------|
| `interactive` | Console single-account fetch, panel fetch, load-more | IMAP/OAuth: `direct → sticky WARP → one alternate` |
| `bulk` | Batch fetch, import precheck, `sync_worker` | today’s `sticky WARP → alternate → direct` |

Rules:

- **`_cap_egress_candidates` must preserve input order** (including leading `None`). Today it does not. This is the first implementation task.
- Explicit `account.proxy` / request `proxy` still pins (unchanged).
- Direct→WARP uses a **new narrow classifier** (TCP fail, timeout, connection refused, unreachable, TLS reset, IMAP greeting `421`/`NO`, SOCKS fail). Do **not** reuse `_is_retryable_egress_error` as-is (it matches `"请求失败"`, `"login failed"`, `"session"`, Graph 429 text, etc. and would walk WARP for mailbox-down / throttle).
- **Cookie / mail.com stays WARP-first in both modes for v1.** Multi-step SSO + sticky IP; `MAX_EGRESS_ATTEMPTS_COOKIE = 2` means a wrong first hop burns the budget. Existing WARP-bound cookies must not be tried on a new IP first.
- Empty `PROXY_POOL`: `[None]` only.
- **Send: no change.** SMTP/Graph send is not idempotent; a timeout after DATA / `sendMail` plus WARP retry can duplicate mail. Send is already direct by default.

`bulk` is **not** a client-forced “use WARP” switch on `/api/fetch/proxy`. Derive bulk from the server-side call site (`sync_worker`, import). Console batch fetch may send a hint that only **allows** WARP-first; it must not be required to reach WARP, and must not override a pinned proxy.

### 6.2 Stored-account lease (prerequisite)

Change `account_fetch_slot` to key `(account_id, folder)` so inbox/spam/sent can run together on `/api/accounts/{id}/fetch`.

Keep the 3s min-interval **per (account, folder)**, not per account.

A bodies-phase request on the same folder within 3s must be allowed: either `force=true` for `phase=bodies`, or the min-interval applies only to `full`/`headers` phases.

### 6.3 Request / response (first-window only)

No new route. Optional fields; default = today’s `full`.

```text
phase: "full" | "headers" | "bodies"     # default "full"
body_ids: string[]                       # phase=bodies
uidvalidity: int | null                  # IMAP bodies: must match headers response
folder: string                           # already present; bodies must use the same folder
```

```text
phase: "full" | "headers" | "bodies"
pending_body_ids: string[]               # headers: ids still needing a body
uidvalidity: int | null
partial: bool
```

When to send `phase=headers` / `phase=bodies`:

- Empty folder cache or clear-refetch (`MAIL_FIRST_PAGE = 20`) on IMAP/OAuth.
- Cookie / http_api: `full` only.
- Catch-up (`CATCH_UP_PAGE`) and load-more: `full` only.

**Do not put a full newest `BODY.PEEK[]` in the headers response.** One large newest message (attachments) would delay the entire JSON and defeat header-first paint.

IMAP headers-only rows will have **empty `body_preview`** (preview is derived from MIME body today). Accept that for the first-window list; Graph still has `bodyPreview` on `$select`.

### 6.4 IMAP provider

On **one connection** within one HTTP request:

1. Existing UID selection (`_recent_uids` / `_uids_since` / `_uids_before`).
2. **Summary FETCH** (never `RFC822` / `BODY[]`):

   ```
   UID FETCH uid1,uid2,… (UID FLAGS INTERNALDATE RFC822.SIZE
     BODY.PEEK[HEADER.FIELDS (From To Subject Date Message-ID Content-Type)])
   ```

   Key each literal by the **UID in that FETCH response**, never by request-list position (server order is not guaranteed; unsolicited untagged lines exist).

3. **Bodies** with `BODY.PEEK[]`, newest-first:
   - `phase=headers`: stop after the summary. Return `pending_body_ids` (all UIDs).
   - `phase=bodies`: FETCH the requested ids newest-first in server-side chunks of ~5 on this same connection; return when the batch (or a wall-clock budget) is done. Cap list length in Pydantic. Dedupe. Reject non-digit UIDs (`_UID_RE`).
   - `phase=full`: summary then all bodies newest-first on this connection (compat + catch-up).

`phase=bodies` must receive the headers-phase `uidvalidity`. On SELECT, if UIDVALIDITY differs, fail the request (`ok=false`, no merge) instead of attaching bodies to the wrong rows.

### 6.5 Graph / OAuth

- `phase=headers`: `$select` without `body,uniqueBody`. Return list + `pending_body_ids`. Run `annotate_message_code` on `bodyPreview` so some OTPs land without a body GET.
- `phase=bodies`: GET each id newest-first. **Percent-encode** the id (`urllib.parse.quote(id, safe="")`). Cap length/count. Verify the message’s folder / parentFolderId matches the requested folder before caching under that folder. Graph ids are opaque, not `_UID_RE`.
- `phase=full`: today’s list-with-body + gap-fill.
- IMAP-transport OAuth uses the IMAP path after **one** token refresh.

**Token reuse:** do not refresh the refresh-token on every folder/phase. Parallel folder requests already race Microsoft’s rotating refresh token (`patchAccount` last-writer-wins). v1: refresh once per account per user action when possible; persist `access_token` from `credential_updates` and send it on the bodies request. If a clean once-per-cycle refresh is too large, serialize OAuth folder requests (keep IMAP 3-way parallel).

### 6.6 Frontend

`fetchAccountFolders` (IMAP/OAuth):

1. Three folder chains in parallel (unchanged). Cookie: serial `full`.
2. **Paint on arrival:** stop passing `silent: true` in a way that blocks panel refresh. After each folder’s `fetchOne` writes cache, if that account is still selected, `loadMessagesFromCache(..., { preserveVisible: true })` — including when the completed folder is not the open tab (badge/count can update; list refresh only if it **is** the open tab).
3. First-window IMAP/OAuth: `phase=headers` then immediately `phase=bodies` with `pending_body_ids` + `uidvalidity`. Merge bodies; refresh open tab. Headers success + bodies failure: **keep header rows**, do not set account `error`.
4. Catch-up / load-more: `phase=full` as today (but IMAP internally header-then-body).
5. Spinner: drop the blocking overlay when the **open tab’s headers** (or `full`) land. Body fill may show a light state on the open message only.
6. Hard auth error: set `failState.hardFail` and **abort in-flight siblings** with `AbortController` (today the flag does not cancel already-started fetches).
7. Batch: same per-folder paint; server-side/bulk hint for WARP-first; do not wait for the whole batch to paint one row.

Durable pending bodies (first-window): keep `pending_body_ids` on the in-memory folder fetch state until bodies succeed or the user clears the folder. A failed bodies request must **not** advance a “folder complete” cursor that would skip those ids on the next incremental fetch. Catch-up `since` is driven by newest **cached** date — header rows have dates, so `since` can still move; that is OK because those rows are already in cache. Do not treat missing bodies as “caught up, never retry.” Retry bodies on the next explicit fetch if still pending.

Quota: first-window is 6 proxy calls worst case. Catch-up stays 1 call per page. Do not add a bodies round per catch-up page.

### 6.7 Timeouts

- imaplib uses **one** socket timeout for connect, greeting, and FETCH. Split requires `settimeout` after connect in `_IMAP4SSLSni._create_socket` / `_IMAP4Sock._create_socket` and after `open_proxied_tcp`.
- Interactive **direct** path: ~10s TCP connect + ~5s IMAP greeting deadline (`* OK` / `421` / `NO`). Then restore ~30s for FETCH. Greeting `421`/`NO` / TLS reset → immediate WARP failover (no full 30s wait).
- WARP / bulk: keep today’s ~30s.
- `fetchServerAccount` must pass an explicit timeout (parity with `proxyFetchMail` 55s).
- Headers client timeout 55s (should finish much sooner). Bodies 55s. Bodies-only failure does not `_mark_error` / write the code cache.

### 6.8 Code cache and partial phases

`fetch_account` must **not** run `_pick_best_code` / `_write_short_cache` on `phase=headers` (subject-only or empty code would become the 90s cached answer). Headers phase is barred from the code-API cache path. `phase=bodies` / `full` may update the code as today.

### 6.9 Testing

Backend (TDD first):

- `_cap_egress_candidates` preserves `[None, sticky, alt]` → `[None, sticky, alt]`.
- Interactive walk tries direct first; WARP only after the **narrow** classifier; never after bad password / invalid_grant.
- Bulk walk stays WARP-first.
- Cookie interactive stays WARP-first.
- `account_fetch_slot` allows three folders of the same account concurrently; min-interval is per folder; `phase=bodies` is not blocked by the headers request’s 3s gate.
- IMAP mock: summary uses `BODY.PEEK[HEADER.FIELDS` / not `RFC822`; headers phase returns N subjects, empty bodies, `pending_body_ids`; bodies phase requires matching uidvalidity and refuses a mismatch; FETCH literals keyed by response UID.
- Graph: headers `$select` omits `body`; bodies phase percent-encodes ids; rejects `../` style ids; folder mismatch not merged.
- Send: no new retry tests that walk the pool (behavior unchanged).

Frontend:

- First folder headers refresh the open tab before the third folder settles.
- `silent` no longer blocks that refresh for multi-folder fetch.
- Failed bodies keep header rows.

Docs: update `docs/16-warp-proxy-pool.md` for interactive vs bulk.

---

## 7. Reviewer Q&A (locked for v1)

1. **Double LOGIN vs single-request full?** Two HTTP requests on first-window only. Catch-up stays one request. Do not bundle a full newest body into headers.
2. **Direct timeout?** ~10s connect + ~5s greeting, then 30s. Needs `settimeout` split.
3. **mail.com direct-first?** No in v1 — WARP-first. Revisit with per-domain success stats later.
4. **HEADER.FIELDS vs ENVELOPE?** HEADER.FIELDS. Reuse existing RFC 2047 / gb18030 decode. No cc/bcc unless UI needs them.
5. **Batch UID body FETCH?** Newest-first chunks of ~5 **inside one bodies HTTP request**, same connection. Associate by response UID. Do not drive one HTTP request per 5 UIDs on catch-up.
6. **Graph list without body?** Yes. Preview may already contain the OTP. Newest body is the first GET in the bodies request, not bundled into headers.
7. **Send?** No change.
8. **`body_ids` security?** IMAP: digits + folder + uidvalidity + max length. Graph: percent-encode, length/count cap, folder membership. Same credentials do not make a raw id safe in a URL.

---

## 8. Implementation order

1. Tests + fix `_cap_egress_candidates` order; interactive vs bulk walk; narrow direct→WARP classifier. Cookie stays WARP-first.
2. Tests + per-`(account, folder)` lease; bodies-phase interval exemption.
3. Frontend: per-folder paint (`silent` gate) + AbortController on hard fail. No API change yet — already a user-visible win.
4. IMAP summary FETCH + newest-first `BODY.PEEK[]` inside `phase=full` (catch-up/compat).
5. First-window `phase=headers|bodies` (IMAP + Graph), uidvalidity / Graph id rules, code-cache skip on headers, frontend two-wave.
6. Changelog + `docs/16-warp-proxy-pool.md`.

---

## 9. Out of scope / later

- BODYSTRUCTURE + `BODY.PEEK[1]` to skip attachments.
- CONDSTORE / QRESYNC.
- Server IMAP connection cache.
- NDJSON stream on non-CF deploys.
- Cookie direct-first after measuring success rate.
- Send pre-submission-only failover (connect/token only), if ever needed.
