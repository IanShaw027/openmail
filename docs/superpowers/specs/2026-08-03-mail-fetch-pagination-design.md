# Mail fetch, multi-folder, and pagination design

**Date:** 2026-08-03  
**Status:** Approved for implementation (product direction locked)  
**Scope:** Console fetch UX, per-folder incremental sync, load-older paging, mail.com list paging, cookie session reuse

---

## 1. Goals

1. **Folder tabs stay local to the panel:** switching inbox / spam / sent only filters local cache; “load more” and “clear & refetch” operate on the **current tab only**.
2. **Explicit fetch is multi-folder:** the primary **Fetch** button and **Batch fetch** pull **inbox + spam + sent** for each account (each folder independently).
3. **Per-folder policy:**
   - No local messages for that folder → pull **latest 20**.
   - Local messages exist → pull **all mail newer than the newest cached message** in that folder (`since = newestUtcIso(folder)`).
4. **Load older (current tab):** after first page, append **20** older messages per successful remote page until the provider returns no new ids.
5. **Session first:** cookie / rolling sessions must be **tried and reused** before password login. One user action must not produce multiple full logins when a session is still valid. Rapid re-clicks must not stampede logins.
6. **mail.com:** implement real list pagination (next page), not only first HTML page + local date filter.

Non-goals:

- Single backend HTTP that fetches three folders in one response.
- Changing My Mails search-page paging.
- Graph `@odata.nextLink` (date `before` is enough for v1).

---

## 2. User-visible behavior

| Action | Folders | Remote policy |
|--------|---------|----------------|
| Fetch (toolbar) | inbox, spam, sent | empty folder → max 20 recent; else `since=newest(folder)` until caught up |
| Batch fetch | same per selected account | same; silent (no panel steal) |
| Load more (list) | **current tab only** | `before=oldest(folder)`, max **20**; merge append |
| Clear & refetch | **current tab only** | wipe that folder cache → latest 20 |
| Folder tab click | none (local filter) | no network until Fetch / load more |

Constants:

- `MAIL_FIRST_PAGE = 20`
- `MAIL_LOAD_MORE = 20` (raise from current 10)

UI:

- Infinite scroll on `.mail-list-pane` (`@vueuse/core` `useInfiniteScroll` preferred) **and** keep a bottom “Load more” button as fallback.
- After multi-folder Fetch, refresh the **current tab** list from cache; other folders are available when the user switches tabs.
- One folder failing (e.g. no Sent) does not fail the whole account fetch; surface a short warning if useful.

---

## 3. Session / cookie reuse (hard requirement)

### Problem

mail.com (and similar cookie providers) currently may **full_login** when restore fails. User expectation:

> Cookie works → use cookie. Do not login three times if I click Fetch three times within 10 seconds, and do not login once per folder when one session covers all folders.

### Rules

1. **Restore before password.** Every cookie fetch path: send stored `sessionCookies` + `sessionMeta` → `try_restore` → only if restore fails and password exists → `full_login`.
2. **One login per process of work.** When Fetch runs three folders for one account:
   - Prefer **one** proxy/guest request path that reuses cookies **after the first folder succeeds**, **or**
   - Frontend serializes folder fetches for cookie accounts and **always passes back** `session_cookies` / `session_meta` from the previous folder response into the next request body **before** the next call.
3. **Write-back immediately.** On any successful cookie fetch, persist rolling cookies to the vault account row **before** the next folder request starts (already partially done in `applyFetchResult` / silent path; must be guaranteed for multi-folder).
4. **In-flight + min-interval guards.**
   - Client: per-account mutex so concurrent Fetch / Batch / Auto-detect / double-click cannot start overlapping multi-folder jobs for the same account.
   - Server: existing `fetch_guard` lease for **stored** accounts remains; guest `proxyFetch` has no DB lease — client mutex is mandatory for local-first.
5. **Do not force re-login.** Never send “clear session / force_new_sid” on normal Fetch or load-more. Only explicit user recovery paths may force password login.
6. **Optional short client cool-down (cookie only).** If a full password login just succeeded for account A, suppress another full_login attempt for ~15–30s on A unless restore is impossible and the user force-retries (toast: session just refreshed). Prefer reusing the cookies just written.

### Multi-folder orchestration (frontend)

```
async function fetchAccountAllFolders(acc, { silent }):
  acquireAccountFetchLock(acc.id)
  try:
    for folder in [inbox, spam, sent]:  // cookie: always serial
      opts = empty(folder) ? { forceRecent, max: 20 }
                           : { since: newest(folder), max: up to 100, maybe loop }
      await fetchOneFolder(acc, folder, opts)  // must pass latest cookies
      // on success: patch sessionCookies from response before next iteration
  finally:
    releaseAccountFetchLock(acc.id)
```

IMAP / OAuth may parallelize the three folders (cap 3) because they are not cookie-login-heavy; cookie / unknown-as-cookie **must be serial**.

### Backend cookie provider

- `fetch()` for a single folder still owns one HTTP client for that request.
- After restore or login, list pagination for that folder uses the **same client** (no second login).
- CredentialUpdates with full cookie dump returned every success so the client can chain folders.

---

## 4. Per-folder “catch up new mail”

For folder F with cache:

1. `since = mailCache.newestUtcIso(email, F)` (folder-scoped; never use another folder’s newest).
2. Request with `since`, no `before`, `max_messages` up to 100 (provider cap).
3. Merge results.
4. If returned count == max and provider may have more newer mail, repeat with updated `since = new newest` until count < max or zero new ids (safety: max 5 rounds per folder per user action).

Empty folder cache: one request `max_messages=20`, no `since` (recent window).

Silent batch uses the same rules per folder.

---

## 5. Load older (current tab)

1. Expand local window by 20 if cache has more than `mailVisibleCount`.
2. Else remote: `before = oldestUtcIso(email, currentFolder)`, `max_messages=20`.
3. Merge; expand visible count by newly added (or +20).
4. Zero new dedupe keys → set `mailNoMoreRemote` for this tab (reset when account/folder changes or clear&refetch).

Providers:

| Provider | Load older | Notes |
|----------|------------|--------|
| IMAP | `SEARCH BEFORE` + timestamp filter | already `since_before` |
| OAuth Graph | `$filter=receivedDateTime lt` | already |
| Cookie mail.com | multi-page list + local `before` | **new** paging |
| HttpApi | larger pull + local `before` | best-effort; document EOF if upstream is short |

---

## 6. mail.com list pagination

### Current gap

`fetch_message_list` opens the first messagelist HTML and parses up to `limit`. No next-page navigation.

### Design

1. Parse first listing page into messages (existing parsers).
2. Extract **next page** candidates from HTML/Wicket:
   - links whose href contains `messagelist` and page/offset/start parameters;
   - common “next” / “»” / aria-label patterns;
   - Wicket ajax markers if present in fixtures or live HTML.
3. Loop: GET next URL with **same client** → parse → append until:
   - collected messages after filters ≥ requested `limit`, or
   - no next link / empty page, or
   - hard cap (e.g. **10 pages** or **200 raw rows**) to avoid runaway.
4. Apply `since` / `before` via existing `filter_messages_by_time` after multi-page pull (pull wider when filters set, same as today).
5. Prefer real paging over “limit×3 single page” for load-older.

`time_paging` for cookie may remain `local_filter` until paging is proven stable; frontend already treats load-older with `before` for local_filter. Once multi-page works, set `supportsRemoteLoadOlder` true for cookie if product wants parity messaging (optional).

Tests: fixture with two pages + next link; assert second page only fetched when limit exceeds page 1.

---

## 7. Frontend architecture (units)

| Unit | Responsibility |
|------|----------------|
| `fetchOne` | Single folder, single request (existing); always pass cookies; write-back on success |
| `fetchAccountFolders` | Multi-folder policy + serial/parallel + lock (new or extracted from ConsolePage) |
| `onFetchSelected` / `onBatchFetch` | Call `fetchAccountFolders` instead of single-folder `fetchOne` |
| `onLoadMoreMails` | Current tab only; page size 20; infinite scroll hook |
| `onClearAndRefetch` | Current tab only (unchanged intent) |
| `providerCapabilities` | Keep time_paging; optionally mark cookie load-older after paging |
| `mailCache` | Unchanged merge / oldest / newest / clearMailboxFolder |

### Selection vs fetch folder

Today `fetchOne` uses `mailFolder.value` for the request. Multi-folder must pass an **explicit `folder` override** so silent/batch and multi-folder do not depend on the open tab. Load more and clear continue to use the open tab.

---

## 8. Error handling

- Folder-level: record last error on account only if **all** folders fail or a hard auth error occurs (need_reauth / wrong password). Soft “no spam folder” → skip.
- Auth failure on first folder of a multi-folder run: **abort remaining folders** for that account (password wrong → no point).
- Timeout on one folder: continue others for IMAP/OAuth; for cookie prefer abort if session looks dead.

---

## 9. Testing plan

**Backend**

- mail.com: multi-page list fixture → pagination collects beyond first page; stop at limit; same client (no second login mock).
- IMAP/Graph: existing since/before tests; ensure max_messages=20 honored.

**Frontend** (unit or lightweight)

- Multi-folder: empty vs non-empty folder chooses forceRecent vs since.
- Cookie path: second folder request includes cookies from first response (mock).
- Account lock: double Fetch does not start two multi-folder runs.
- Load more page size 20.

**Manual smoke**

- Cookie account: Fetch once (3 folders), network shows at most one full login; second Fetch within 10s restores cookies only.
- Load more until EOF on IMAP and on mail.com.

---

## 10. Implementation order

1. Frontend: `folder` override on `fetchOne`; multi-folder orchestrator + account lock; Fetch/Batch wire-up; `MAIL_LOAD_MORE=20`.
2. Frontend: infinite scroll + load-more semantics (append / EOF).
3. Backend: mail.com multi-page list parsing.
4. Cookie write-back guarantees + optional cool-down documentation in code comments.
5. Tests + light i18n if new strings (e.g. “Fetched 3 folders”).

---

## 11. Success criteria

- Fetch / Batch always attempt three folders with empty→20 / non-empty→since catch-up.
- Load more / clear only touch current tab; load more steps by 20.
- Rapid triple-click Fetch on mail.com does not produce three password logins when cookies remain valid; three folders of one Fetch share one restore/login.
- mail.com can return more than the first HTML page when loading older or catching up.
- Existing mail list rows are preserved across successful merges (merge-by-id, append older).
