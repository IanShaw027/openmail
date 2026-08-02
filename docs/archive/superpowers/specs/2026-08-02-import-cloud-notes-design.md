# Import storage (local/cloud) + account notes

**Date:** 2026-08-02  
**Status:** approved → implementing

## Summary

- Import modal chooses **local** (browser) or **cloud** (server, keyed by `X-Device-Id`).
- Cloud import can enable **hourly poll** (`sync_enabled`) and shows **quota used/limits**.
- Failed import rows are **inline-editable** then re-validated.
- Account table has a **note** column with **quick templates**.

## Backend

- `owner_user_id = device_id` isolates rows (no login).
- CRUD: `GET/POST/PATCH/DELETE /api/accounts` device-scoped; credentials encrypted.
- `POST /api/accounts/{id}/fetch` restored for owned rows.
- Quota snapshot adds `cloud_used` (count of device rows).

## Frontend

- Import target radio + cloud poll checkbox + quota bar.
- Confirm: local → `importPartials`; cloud → create/update API + mirror in store.
- List: note column, chips for templates, PATCH note for cloud.

## Out of scope

- User login, mail-level notes, public pool, code-api create UI.
