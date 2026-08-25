# Instance mode + first-trusted-device admin bootstrap

Approved 2026-08-25. Implemented. Operator-facing source of truth:
[docs/14-ops-and-smoke.md §3](../../14-ops-and-smoke.md#3-instance-mode-admission-and-admin).

“Login” here means first vault unlock / device register.

## Mode

`OPENMAIL_INSTANCE_MODE=hosted` (default) | `solo`

| | hosted | solo |
|---|---|---|
| Quotas | current caps | unlimited |
| License UI | shown | hidden |
| Cloud sync / pool | `SYNC_*` | scheduled worker + UI off (`sync_enabled_global=false`); HTTP APIs remain |
| Admission | `first_trust` / `open` | forced `open` |
| First trusted device → admin | yes | yes |

Mode change requires process restart.

## Admin bootstrap

When the effective admin set is empty, the first **trusted** device that
**registers or calls `GET /api/device/me`** writes that `vk_…` and hot-reloads
(set `OPENMAIL_ADMIN_DEVICE_IDS` in-process + clear settings cache). No restart.
Pending devices never bootstrap.

Persist:

1. Always `data/admin_device_ids` (same volume as the DB).
2. `.env` when writable (`OPENMAIL_ENV_FILE` or cwd `.env`; skipped under pytest).

Load order (first non-empty wins):

1. Process / compose env `OPENMAIL_ADMIN_DEVICE_IDS` (operator recovery)
2. Same key from startup settings / dotenv
3. `data/admin_device_ids`
4. Empty → wait for first trusted register or `GET /api/device/me`

Lost admin device: put the new `vk_…` in `.env` and restart.

## API

`/api/config/public` adds `instance_mode`, `license_ui_enabled`, `sync_ui_enabled`,
effective `device_admission`, and `licensed`. Frontend hides license / quota /
cloud-sync chrome when `solo`.
