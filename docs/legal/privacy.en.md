# OpenMail Privacy Policy

> Scope: visitors and operators of a **self-hosted single instance**.  
> Last updated: 2026-08-02  
> Chinese: [privacy.zh.md](privacy.zh.md) · Terms: [terms.en.md](terms.en.md)

---

## 1. Overview

OpenMail is a **local-first** multi-source mailbox console:

- **Primary path:** credentials, 2FA, and mail cache are encrypted in your **browser vault** (password / recovery key never leave the browser).  
- **Server:** self-hosted proxy that briefly uses request credentials against upstream mail APIs; optional **client-sealed** cloud rows (operators cannot decrypt without the vault key).  
- **Not SaaS:** there is no central multi-tenant cloud; the **controller** is usually whoever deploys the instance (“operator”).

Using the service means you understand and accept this policy.

---

## 2. Data we process

### 2.1 Browser (default)

| Category | Notes |
|----------|--------|
| Vault ciphertext | Accounts, 2FA, mailCache (AES-GCM in localStorage) |
| Device secret | Local secret for vault device HMAC (server stores hash only) |
| UI prefs | Locale, layout, filters |

### 2.2 Server (possible)

| Category | Notes |
|----------|--------|
| Proxy fetch/send | Credentials in the body used **ephemerally**; not stored by default |
| Client-sealed rows | Optional backup blobs; server cannot decrypt |
| Device registry | `vk_*` id + secret hash for HMAC |
| Legacy code-API | Historical tokens may still resolve if present |
| Logs | Redacted; **no** plaintext passwords, refresh tokens, or full cookies |

### 2.3 What we do not do

- Multi-user registration / admin login UI (removed).  
- Platform OAuth consent on behalf of users.  
- Ads, profiling, or resale of mail content.  
- Zero-knowledge proxy guarantees (memory briefly holds secrets during upstream calls).

---

## 3. Control

- Vault data is under **your** control; lost password **and** recovery key means unrecoverable ciphertext.  
- Operators control `OPENMAIL_MASTER_KEY`, database, egress proxies, and access logs.  
- Sealed cloud rows: operators can store/delete but not read plaintext without the vault key.

---

## 4. Retention

| Data | Guidance |
|------|----------|
| Browser ciphertext | Clear site data / vault-related keys |
| Proxy requests | Not long-term credential storage |
| Sealed cloud | User delete in console; operator may wipe DB |
| Device registry | Re-register after master-key or registry wipe |

Console retention days prune **local** mail cache.

---

## 5. Security (summary)

Browser PBKDF2 + AES-GCM; lock clears in-memory secrets; HTTPS recommended; device HMAC; SSRF checks; HTML sanitization. See [SECURITY.md](../../SECURITY.md).

---

## 6. Third parties

Fetch connects to mail providers and optional HTTP/SOCKS proxies (including WARP). Compliance is the operator’s and user’s responsibility.

---

## 7. Children & misuse

Intended for technical self-hosting. Do not use to access others’ mailboxes unlawfully.

---

## 8. Changes

Updates appear in the “Last updated” line. Material changes should be noted in releases or the instance UI.

---

## 9. Contact

Vulnerabilities: [SECURITY.md](../../SECURITY.md). Operational questions: the **instance operator**.
