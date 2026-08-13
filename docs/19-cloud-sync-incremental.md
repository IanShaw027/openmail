# Cloud sync + incremental fetch (implementation contract)

**Status:** In progress  
**Product lock:** Cloud credentials are **server-decryptable** (master-key). Client-sealed is backup-only, not poll.  
**Goal:** Minimize re-transfer of known mail; avoid silent omissions across three paths.

## Three paths (same identity + water-marks)

| Path | Direction | Water-mark | Exclude known |
|------|-----------|------------|---------------|
| A Server poll | upstream → `mail_items` | `sync_cursors` per account×folder | provider since/uid/delta + upsert by `stable_id` |
| B Local←cloud | server → `mailCache` | client `lastDeltaAck` / `since_seq` | `WHERE updated_at > ack` + client merge |
| C Local proxy | upstream → `mailCache` | folder newest/oldest | `since` / `before` + stable_id dedupe |

## `stable_id` (dedupe key)

Priority:

1. `provider_id` if present (Graph id, IMAP `uidvalidity:uid`, provider message id)
2. Normalized `Internet-Message-ID` / `message_id`
3. Weak fingerprint: `sha256(from|date|subject|size)` — only if 1–2 missing

Unique:

- Server: `(account_id, folder, stable_id)`
- Client: existing mailCache key + soft fingerprint merge (align with server rules)

## Constants

```
OVERLAP_SECONDS = 120          # time-cursor overlap
PAGE_DEFAULT = 20
PAGE_CATCHUP = 50
DELTA_PAGE = 200
MAX_CATCHUP_ROUNDS = 50
```

Cursor advance (time mode): never use wall-clock `last_fetch_at` alone.

```
next: received_at > high_water_time
   OR (received_at == high_water_time AND id not in high_water_ids)
# weak providers: since = high_water_time - OVERLAP; dedupe on write
```

Transaction order:

- Server: upsert mail_items **then** advance cursor (same txn preferred)
- Client delta: merge mailCache **then** ack

## Tables

### `sync_cursors`

- `id`, `account_id` FK CASCADE, `folder` (inbox|spam|sent|junk→spam)
- `mode`: `time` | `uid` | `delta`
- `cursor_json` TEXT (delta_link, uidvalidity, last_uid, high_water_time, high_water_ids)
- `updated_at`
- UNIQUE(account_id, folder)

### `mail_items`

- `id` PK, `account_id` FK CASCADE, `folder`, `stable_id`
- `provider_id`, `message_id`, `content_hash` nullable
- `received_at`, `from_addr`, `to_addrs` (JSON/text), `subject`
- `preview`, `verification_code` nullable — **encrypted at rest** (master-key
  AES-GCM, same as bodies). Delta decrypts for the device; legacy plaintext
  rows still read. Subject / from stay plaintext.
- `body_text_enc`, `body_html_enc` nullable (master-key JSON/text encrypt helpers)
- `has_attachments` bool, `size` int nullable
- `fetched_at`, `updated_at` (delta clock — bump on any content change)
- `deleted_at` nullable tombstone
- UNIQUE(account_id, folder, stable_id)
- INDEX(account_id, updated_at), INDEX(account_id, folder, received_at)

## API

### `GET /api/sync/status` (device HMAC)

```json
{
  "worker_alive": true,
  "sync_enabled_global": true,
  "accounts": [
    {
      "id": "acc_…",
      "email": "…",
      "sync_enabled": true,
      "last_sync_at": "…",
      "last_sync_error": null,
      "mail_count": 0
    }
  ]
}
```

### `GET /api/sync/delta?since_seq=&since=&limit=`

- Auth: device HMAC; only rows for `owner_user_id == device_id`
- Prefer monotonic `sync_seq` INTEGER on mail_items if easy; else `(updated_at, id)` keyset
- Response:

```json
{
  "server_time": "ISO",
  "server_seq": 12400,
  "has_more": false,
  "mails": [
    {
      "account_id": "acc_…",
      "email": "…",
      "folder": "inbox",
      "stable_id": "…",
      "id": "mid_…",
      "subject": "…",
      "from_addr": "…",
      "to_addrs": ["…"],
      "date": "ISO",
      "preview": "…",
      "verification_code": null,
      "body_text": null,
      "body_html": null,
      "updated_at": "ISO",
      "deleted": false
    }
  ],
  "accounts": [
    {
      "id": "acc_…",
      "latest_verification_code": "…",
      "latest_code_at": "…",
      "last_sync_at": "…",
      "last_sync_error": null
    }
  ]
}
```

Default delta **omits full html** (preview + code + meta). Optional `include_body=1` later.

## Cloud upload (frontend)

**Default for “upload to cloud poll” / cloud import with poll:**

- Do **NOT** call `sealForCloud`
- Send server-side credential fields (password/refresh/cookies/…) so master-key encrypts
- `sync_enabled: true`

Optional advanced: sealed backup only → `sync_enabled: false`, no poll badge as “polling”.

## SyncWorker

1. Skip / disable poll if `is_client_sealed_blob`
2. For each folder in `sync_folders` (default inbox,junk):
   - Load cursor; `window_since = high_water_time - OVERLAP` (or null on first run)
   - Fetch newest-first pages with `max_messages=PAGE_CATCHUP`:
     - page 0: `since=window_since`, no `before`
     - while full page: next page `before=oldest(page)`, same `since` (walk older in window)
   - **Do not stop** just because a full page was all already-known (overlap); only stop on short page / empty / no `before` progress
   - Upsert mail_items; advance high_water from max(received_at) after each page
3. Credential write-back (cookies/refresh) as today
4. Keep `last_sync_at` / `last_sync_error`

## Client delta ack

- Prefer `setSyncAck(\`${last.updated_at}\\t${last.id}\`)` after `has_more` drained
- Next pull: `since` + `since_id` keyset — **not** wall-clock `server_time` alone

## Frontend delta pull

- On vault unlock + periodic (e.g. 2–5 min) + manual “同步云端”
- `GET /api/sync/delta` loop while `has_more`
- Merge into mailCache by (email, folder, stable_id); LWW by updated_at; prefer body if other empty
- Persist ack (`openmail.syncAck.v1` in settings or localStorage)

## Out of scope (this iteration)

- Graph deltaLink persistence (time since OK v1)
- Web Push
- include_body negotiation
- Changing local load-more UX constants beyond stable_id alignment
