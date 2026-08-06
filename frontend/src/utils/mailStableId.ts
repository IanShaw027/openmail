/**
 * Stable mail identity for local fetch + cloud delta merge.
 *
 * Priority (docs/19-cloud-sync-incremental.md):
 * 1. Provider native id (message.id when it looks like a provider id)
 * 2. Internet Message-ID / message_id header when present
 * 3. Weak fingerprint: must match backend `mail_store._weak_fingerprint`
 *    → `wh_` + sha256(from|date|subject|size)[:40]
 *    Material is raw trimmed strings (not lowercased) so client and server agree.
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

const WEAK_PREFIX = 'wh_'
/** Legacy client-only weak ids (FNV); still recognized for merge/dedupe. */
const WEAK_PREFIX_LEGACY = 'w:'
const MSGID_PREFIX = 'm:'
const PROVIDER_PREFIX = 'p:'

/** Minimal sync SHA-256 (UTF-8) → hex — matches Python hashlib.sha256. */
function sha256Hex(message: string): string {
  // Pure JS SHA-256 (compact). Same digest as backend for identical UTF-8 bytes.
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
    0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
    0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
    0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
    0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
    0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2,
  ])
  const bytes = new TextEncoder().encode(message)
  const l = bytes.length
  const bitLen = l * 8
  const withOne = l + 1
  let total = withOne + 8
  const pad = (64 - (total % 64)) % 64
  total += pad
  const buf = new Uint8Array(total)
  buf.set(bytes)
  buf[l] = 0x80
  const view = new DataView(buf.buffer)
  // length in bits as big-endian 64-bit (high 32 always 0 for our sizes)
  view.setUint32(total - 4, bitLen >>> 0, false)

  let h0 = 0x6a09e667
  let h1 = 0xbb67ae85
  let h2 = 0x3c6ef372
  let h3 = 0xa54ff53a
  let h4 = 0x510e527f
  let h5 = 0x9b05688c
  let h6 = 0x1f83d9ab
  let h7 = 0x5be0cd19

  const w = new Uint32Array(64)
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n))

  for (let i = 0; i < total; i += 64) {
    for (let j = 0; j < 16; j++) {
      w[j] = view.getUint32(i + j * 4, false)
    }
    for (let j = 16; j < 64; j++) {
      const s0 = rotr(w[j - 15]!, 7) ^ rotr(w[j - 15]!, 18) ^ (w[j - 15]! >>> 3)
      const s1 = rotr(w[j - 2]!, 17) ^ rotr(w[j - 2]!, 19) ^ (w[j - 2]! >>> 10)
      w[j] = (w[j - 16]! + s0 + w[j - 7]! + s1) >>> 0
    }
    let a = h0
    let b = h1
    let c = h2
    let d = h3
    let e = h4
    let f = h5
    let g = h6
    let h = h7
    for (let j = 0; j < 64; j++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
      const ch = (e & f) ^ (~e & g)
      const t1 = (h + S1 + ch + K[j]! + w[j]!) >>> 0
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (S0 + maj) >>> 0
      h = g
      g = f
      f = e
      e = (d + t1) >>> 0
      d = c
      c = b
      b = a
      a = (t1 + t2) >>> 0
    }
    h0 = (h0 + a) >>> 0
    h1 = (h1 + b) >>> 0
    h2 = (h2 + c) >>> 0
    h3 = (h3 + d) >>> 0
    h4 = (h4 + e) >>> 0
    h5 = (h5 + f) >>> 0
    h6 = (h6 + g) >>> 0
    h7 = (h7 + h) >>> 0
  }

  const out = new Uint32Array([h0, h1, h2, h3, h4, h5, h6, h7])
  let hex = ''
  for (let i = 0; i < 8; i++) {
    hex += out[i]!.toString(16).padStart(8, '0')
  }
  return hex
}

function trimStr(v: unknown): string {
  return String(v ?? '').trim()
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
  // Already-tagged stable ids (incl. legacy weak `w:`)
  if (
    s.startsWith(PROVIDER_PREFIX) ||
    s.startsWith(MSGID_PREFIX) ||
    s.startsWith(WEAK_PREFIX) ||
    s.startsWith(WEAK_PREFIX_LEGACY)
  ) {
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

/** Match Python mail_store._as_str for weak fingerprint fields (trim only). */
function pickFromRaw(m: MailIdentityInput): string {
  return trimStr(m.from_address || m.from_addr || m.from || '')
}

function pickDateRaw(m: MailIdentityInput): string {
  if (m.date == null || m.date === '') return ''
  // Server uses str(date).strip() on the raw field; keep string form as-is when string.
  if (typeof m.date === 'number' && Number.isFinite(m.date)) {
    return String(m.date)
  }
  return String(m.date).trim()
}

/**
 * Size segment for weak fingerprint — must match server:
 * empty when size missing; else str(size). Do not use preview length.
 */
function sizeSegment(m: MailIdentityInput): string {
  if (m.size == null || m.size === ('' as unknown)) return ''
  if (!Number.isFinite(Number(m.size))) return ''
  return String(m.size)
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
    // Scope IMAP UIDs with uidvalidity when present (matches mailCache / server)
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

  // 3) Weak fingerprint — identical material + digest prefix as backend
  const from = pickFromRaw(m)
  const date = pickDateRaw(m)
  const subject = trimStr(m.subject || '')
  const size = sizeSegment(m)
  const material = `${from}|${date}|${subject}|${size}`
  return `${WEAK_PREFIX}${sha256Hex(material).slice(0, 40)}`
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
  const s = String(stableId || '')
  return s.startsWith(WEAK_PREFIX) || s.startsWith(WEAK_PREFIX_LEGACY)
}
