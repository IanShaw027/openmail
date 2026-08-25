# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest release tag (`v*`) | Yes |
| `main` | Yes (rolling) |

There is no commercial SLA; security fixes land on `main` and are tagged when appropriate.

## Threat model (summary)

OpenMail is a **local-first** multi-mailbox fetch console:

- Vault password and recovery key **never leave the browser**.
- Cloud rows (optional) store **client-sealed** blobs; operators with DB access should not obtain plaintext secrets without the vault key.
- Proxy fetch/send requires a **registered, trusted** vault device (`vk_*` + HMAC). Under `first_trust` admission, later devices stay `pending` until approved.
- There is **no admin password**. If `OPENMAIL_ADMIN_DEVICE_IDS` is empty, the **first trusted unlock** becomes admin. On a public URL, unlock yourself first or pre-set that variable.
- `OPENMAIL_INSTANCE_MODE=solo` lifts quotas and hides license / cloud-sync UI; `hosted` keeps caps unless a license code is presented.
- Outbound HTTP/IMAP/SMTP apply **SSRF** host/IP checks (with DNS pin where practical).

Proxy fetch still requires the server process to **temporarily** hold credentials in memory while talking to upstream mail APIs. Do not run untrusted plugins on the host.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for exploitable bugs.

**Preferred:** use GitHub’s private vulnerability reporting on this repository:

→ [Report a vulnerability](https://github.com/IanShaw027/openmail/security/advisories/new)

Private reporting is enabled on this repo. If that link is unavailable, contact the maintainer via the GitHub profile listed on the repository.

Include:

- Affected commit / image tag (`ianshaw027/openmail:vX.Y.Z`)
- Reproduction steps
- Impact (credential theft, SSRF, XSS, open relay, etc.)

You should receive an acknowledgement within a few days when possible.

## Hardening checklist for operators

- [ ] Set a strong `OPENMAIL_MASTER_KEY` (32-byte base64) before first start
- [ ] Terminate TLS at a reverse proxy (or Cloudflare) and prefer HTTPS only
- [ ] Do not expose `/docs` on the public internet without a network ACL if you care about surface area
- [ ] Keep WARP/proxy nodes on a private Docker network; do not publish SOCKS ports
- [ ] Back up `./data` including `device_registry.json`, `admin_device_ids`, and SQLite; protect filesystem permissions
- [ ] Choose `OPENMAIL_INSTANCE_MODE` (`hosted` vs `solo`) before exposing the URL; restart after changing it
- [ ] On a public host, unlock your vault first **or** pre-set `OPENMAIL_ADMIN_DEVICE_IDS` (first trusted unlocker is admin; bootstrap hot-reloads, no restart)
- [ ] Under `first_trust`, pending devices cannot fetch / use privileged APIs until approved (`solo` forces admission `open`)
- [ ] Rotate license tokens (`LICENSE_TOKENS` or admin-issued codes) if leaked (`hosted` only)
- [ ] Keep `OPENMAIL_ADMIN_DEVICE_IDS` limited to devices you control; recover a lost admin by writing the new `vk_…` and restarting
- [ ] See [docs/14-ops-and-smoke.md §3](docs/14-ops-and-smoke.md#3-instance-mode-admission-and-admin) for admission, bootstrap, and `OPENMAIL_ENV_FILE`

## Vault session resume

While the vault is unlocked, `sessionStorage['openmail.vault.session.v1']` holds
a random AES key (`sk`) and the DEK wrapped with that key (`pkg`). Same-origin
script (XSS) can read both and recover the DEK — wrapping is **not** a
confidentiality boundary against XSS. It only keeps the DEK out of
`localStorage` so a later visit after the tab is closed still requires the
password. Locking the vault deletes this wrap. Treat XSS in the SPA as vault
compromise.
