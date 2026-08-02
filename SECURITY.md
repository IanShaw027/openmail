# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` (self-hosted) | Yes |

There is no commercial SaaS support channel; security fixes land on `main`.

## Threat model (summary)

OpenMail is a **local-first** multi-mailbox fetch console:

- Vault password and recovery key **never leave the browser**.
- Cloud rows (optional) store **client-sealed** blobs; operators with DB access should not obtain plaintext secrets without the vault key.
- Proxy fetch/send requires a **registered vault device** (`vk_*` + HMAC).
- Outbound HTTP/IMAP/SMTP apply **SSRF** host/IP checks (with DNS pin where practical).

Proxy fetch still requires the server process to **temporarily** hold credentials in memory while talking to upstream mail APIs. Do not run untrusted plugins on the host.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for exploitable bugs.

1. Email the maintainer listed in the repository profile, **or**
2. Open a private security advisory on GitHub if the repo has that feature enabled.

Include:

- Affected commit / image tag
- Reproduction steps
- Impact (credential theft, SSRF, XSS, open relay, etc.)

You should receive an acknowledgement within a few days when possible.

## Hardening checklist for operators

- [ ] Set a strong `OPENMAIL_MASTER_KEY` (32-byte base64) before first start
- [ ] Terminate TLS at a reverse proxy (or Cloudflare) and prefer HTTPS only
- [ ] Do not expose `/docs` on the public internet without a network ACL if you care about surface area
- [ ] Keep WARP/proxy nodes on a private Docker network; do not publish SOCKS ports
- [ ] Back up `./data` including `device_registry.json` and SQLite; protect filesystem permissions
- [ ] Rotate license tokens (`LICENSE_TOKENS`) if leaked
