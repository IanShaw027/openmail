/**
 * Stable mail identity for local fetch + cloud delta merge.
 *
 * Priority (docs/19-cloud-sync-incremental.md):
 * 1. Provider native id (message.id when it looks like a provider id)
 * 2. Internet Message-ID / message_id header when present
 * 3. Weak fingerprint: hash of from|date|subject|approx size
 *
 * Sync: pure/sync so mailCache soft-dedupe stays sync. Weak ids use FNV-1a hex
 * (not SubtleCrypto) — collision-resistant enough for client soft merge.
 */

/** Fields used for stable_id / soft fingerprint (works for MailMessage + delta DTOs). */
export type MailIdentityInput = {
  id?: string | null
  /** Explicit provider id when separate from list `id` */
  provider_id?: string | null
  /** Internet Message-ID header */
  message_id?: string | null
  /** Alias some APIs use */
  internet_message_id?: string | null
  subject?: string | null
  from?: string | null
  from_address?: string | null
  from_addr?: string | null
  date?: string | number | null
  body_preview?: string | null
  preview?: string | null
  body_text?: string | null
  body_html?: string | null
  /** Byte/char size when known */
  size?: number | null
  folder?: string | null
  uidvalidity?: number | null
}

const WEAK_PREFIX = 'w:'
const MSGID_PREFIX = 'm:'
const PROVIDER_PREFIX = 'p:'

/** FNV-1a 32-bit → 8 hex chars (sync, deterministic). */
function fnv1aHex(input: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

/** Expand to 16 hex via two FNV passes (closer to short sha256 prefix feel). */
function weakHashHex(input: string): string {
  const a = fnv1aHex(input)
  const b = fnv1aHex(`om1|${input}|${a}`)
  return a + b
}

function normStr(v: unknown): string {
  return String(v ?? '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')
}

/**
 * Normalize Internet Message-ID: strip surrounding <>, collapse space, lower.
 */
export function normalizeMessageId(raw: string | null | undefined): string {
  let s = String(raw ?? '').trim()
  if (!s) return ''
  if (s.startsWith('<') && s.endsWith('>')) s = s.slice(1, -1).trim()
  return s.toLowerCase().replace(/\s+/g, '')
}

/**
 * True when `id` looks like a real provider id (not empty / not pure weak hash).
 * Accepts: IMAP UIDs (digits), Graph/outlook-ish tokens, uidvalidity:uid, long hex.
 */
export function looksLikeProviderId(id: string | null | undefined): boolean {
  const s = String(id ?? '').trim()
  if (!s) return false
  // Already-tagged stable ids
  if (s.startsWith(PROVIDER_PREFIX) || s.startsWith(MSGID_PREFIX) || s.startsWith(WEAK_PREFIX)) {
    return false
  }
  // Pure whitespace / placeholder
  if (/^(unknown|null|undefined|none|-)$/i.test(s)) return false
  // IMAP UID or short numeric
  if (/^\d{1,12}$/.test(s)) return true
  // uidvalidity:uid or folder-scoped forms
  if (/^\d+:\d+$/.test(s)) return true
  // Graph / cookie / API ids: alphanumeric, length ≥ 6, not all spaces
  if (s.length >= 6 && /[A-Za-z0-9_\-@.:/]/.test(s)) return true
  // Short but non-trivial tokens (mail.com style)
  if (s.length >= 3 && /[A-Za-z]/.test(s) && /[0-9A-Za-z]/.test(s)) return true
  return false
}

function pickFrom(m: MailIdentityInput): string {
  return normStr(m.from_address || m.from_addr || m.from || '')
}

function pickDate(m: MailIdentityInput): string {
  if (m.date == null || m.date === '') return ''
  if (typeof m.date === 'number' && Number.isFinite(m.date)) {
    const ms = m.date < 1e12 ? m.date * 1000 : m.date
    return String(Math.floor(ms))
  }
  return String(m.date).trim().toLowerCase().replace(/\s+/g, ' ')
}

/**
 * Approx size for weak fingerprint. Prefer explicit size, then preview length.
 * Do NOT use full body_text/html — detail fetch would change weak stable_id
 * and break list↔detail soft merge.
 */
function approxSize(m: MailIdentityInput): number {
  if (m.size != null && Number.isFinite(Number(m.size)) && Number(m.size) >= 0) {
    return Math.floor(Number(m.size))
  }
  const preview = String(m.preview || m.body_preview || '')
  if (preview.length > 0) return preview.length
  // Last resort when no preview yet (rare): capped body length bucket
  const text = String(m.body_text || '')
  const html = String(m.body_html || '')
  const n = text.length || html.length
  if (n <= 0) return 0
  // Bucket so minor body trims do not churn the id
  return Math.min(n, 280)
}

/**
 * Compute stable_id for a mail-like object (local fetch message or delta row).
 * Prefer calling with full message so weak fingerprints stay stable.
 */
export function computeMailStableId(m: MailIdentityInput): string {
  // 1) Explicit provider_id
  const pid = String(m.provider_id ?? '').trim()
  if (pid && looksLikeProviderId(pid)) {
    return `${PROVIDER_PREFIX}${pid}`
  }

  // 1b) message.id when it looks like a provider id
  const id = String(m.id ?? '').trim()
  if (id && looksLikeProviderId(id)) {
    // Scope IMAP UIDs with uidvalidity when present (matches mailCache keying)
    if (/^\d{1,12}$/.test(id) && m.uidvalidity != null && Number.isFinite(Number(m.uidvalidity))) {
      return `${PROVIDER_PREFIX}${Number(m.uidvalidity)}:${id}`
    }
    return `${PROVIDER_PREFIX}${id}`
  }

  // 2) Internet Message-ID
  const mid = normalizeMessageId(m.message_id || m.internet_message_id)
  if (mid) {
    return `${MSGID_PREFIX}${mid}`
  }

  // 3) Weak fingerprint: from|date|subject|approx size
  const from = pickFrom(m)
  const date = pickDate(m)
  const subject = normStr(m.subject || '')
  const size = approxSize(m)
  const material = `${from}|${date}|${subject}|${size}`
  return `${WEAK_PREFIX}${weakHashHex(material)}`
}

/**
 * Soft dedupe key scoped to mailbox + folder (client UNIQUE analog of
 * server (account_id, folder, stable_id)).
 */
export function mailDedupeKey(
  email: string,
  folder: string | null | undefined,
  m: MailIdentityInput,
): string {
  const e = String(email || '')
    .toLowerCase()
    .trim()
  const f = normalizeFolderForStable(folder || m.folder)
  const sid = computeMailStableId(m)
  return `${e}::${f}::${sid}`
}

/** Same folder normalization as mailCache (inbox | spam | sent). */
export function normalizeFolderForStable(folder?: string | null): string {
  const f = (folder || 'inbox').toLowerCase()
  if (f === 'junk' || f === 'spam' || f === 'junkemail') return 'spam'
  if (f === 'sent' || f === 'sentitems' || f === 'sent mail' || f === '已发送') return 'sent'
  return 'inbox'
}

/** True when stable_id is weak (content fingerprint only). */
export function isWeakStableId(stableId: string): boolean {
  return String(stableId || '').startsWith(WEAK_PREFIX)
}
