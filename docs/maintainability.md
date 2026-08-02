# Maintainability: simplify · modularize · overdesign

Inventory after the local-first + open-source hygiene pass. Use this when prioritizing refactors — **do not** big-bang rewrite without tests.

Verification baseline (last hygiene pass): backend **98 passed**, frontend **`npm run build` OK**.

---

## 1. Already cleaned (safe)

| Item | Action taken |
|------|----------------|
| Multi-user design docs | Archived under `docs/archive/` |
| Open-source meta | `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, root README |
| Auth/admin deps | Removed from runtime path; 410/404 stubs for old URLs |
| `passlib` / bcrypt | Dropped from requirements |
| Dead frontend `api/mails.ts` | Removed (local `mailCache` only) |
| Unused `app/security.py` | Removed (token generator unused after code-api create 410) |
| Bloated `schemas` / `deps` | Slimmed to active models |
| Empty `deploy/` | Removed earlier |

---

## 2. Safe to simplify further (low risk, no feature loss)

| Target | Why | Suggested approach |
|--------|-----|-------------------|
| `routers/mails.py` + `routers/sync.py` | Only return 410 | Keep **or** delete and accept 404; tests already allow both |
| `code_api` create endpoint | Always 410 | Collapse to a single shared “gone” helper or drop route |
| `AccountPool` enum (`public` / `user_private`) | Legacy labels | Keep column for DB compat; stop branching on pool in new code |
| Dynamic imports in vault/stores | Vite warns “ineffective dynamic import” | Use static imports; drop cycle workarounds carefully |
| `SyncWorker` docstring “into MailIndex” | MailIndex product removed | Rename comments; worker only updates account fetch status for **non-sealed** server rows |
| Duplicate health paths | `/health` and `/api/health` | Keep both for ops scripts or document one canonical |
| Legal docs still describe multi-user | Misleading for operators | Add banner: product is local-first; full rewrite later |

---

## 3. Modularize (best practice, medium effort)

### 3.1 Frontend — highest ROI

| File | Size (approx) | Split proposal |
|------|---------------|----------------|
| `pages/ConsolePage.vue` | ~4700 lines | Extract: `ImportPanel`, `AccountTable`, `BatchBar`, `MailPreview`, `SendDialog`, `GroupFilter`, composables `useBatchFetch`, `useAccountSelection` |
| `stores/accounts.ts` | ~880 lines | Split: local CRUD / cloud sync / import merge / selection helpers |
| `pages/TwoFaPage.vue` | ~830 lines | Extract QR scanner, bulk import, bind-to-account list |

**Rule:** no behavior change; move template blocks + pure helpers first; keep Pinia as orchestration.

### 3.2 Backend — good boundaries already

| Layer | Status |
|-------|--------|
| `providers/*` | Good isolation |
| `services/ssrf.py` | Keep centralized |
| `services/device_auth.py` | Keep separate from routers |
| `services/fetch_service.py` (~750) | Optional: cookie write-back vs provider dispatch modules |
| `providers/cookie_mailcom.py` (~1280) | Domain-specific complexity; extract session restore / folder list only if tests cover |

---

## 4. Overdesign / legacy weight (decide consciously)

| Smell | Reality | Recommendation |
|-------|---------|----------------|
| Server `Account` + encrypted password/credential columns | Primary path is **client-sealed** or pure proxy | Prefer sealed or ephemeral; avoid new plaintext-server features |
| Background `SyncWorker` + `SyncRun` + settings service | Useful only for non-sealed server accounts | Document as optional; default product is manual/console fetch |
| Dual host tables `imap_hosts` / `smtp_hosts` | Necessary for multi-provider | Keep; not overdesign |
| Code-API public tokens | Legacy automation | Keep read path; no create in UI |
| `owner_user_id` column | Now = vault device id | Rename in docs only; DB rename is high cost |
| Soft “guest without HMAC” | Removed (strict HMAC) | Do not reintroduce |
| Admin password / session cookies in config | May still exist in settings for old env | Ignore in UI; remove dead config keys in a dedicated PR after grep |
| `fetch_guard` poll limits | Needed for abuse control | Keep |

---

## 5. Explicit non-goals (avoid over-engineering)

- Multi-tenant SaaS auth rewrite  
- Server-side full-text mail product (browser `mailCache` is enough)  
- Zero-knowledge proxy (server must see secrets briefly to call IMAP/Graph)  
- Microservices split for a single-node Docker app  

---

## 6. Suggested refactor order

1. **Docs truth** (legal local-first rewrite) — done  
2. **ConsolePage extract** — started: `mapPool`, `consoleAccountLabels`, `ConsoleSendModal`, `ConsoleGroupModal`  
3. **accounts store split** — started: `stores/accounts/mappers.ts`  
4. Further ConsolePage panels (import modal, table, mail pane) — next  
5. **cookie_mailcom** only when changing mail.com behavior  
6. **Drop 410 stubs** only when no external clients depend on status code  

---

## 7. How to verify after any refactor

```bash
cd backend && export OPENMAIL_MASTER_KEY=... && pytest -q
cd frontend && npm run build
# optional: make smoke with stack up
```
