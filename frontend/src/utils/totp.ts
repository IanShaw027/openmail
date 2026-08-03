/** TOTP / HOTP / Steam Guard helpers (otpauth). */

import * as OTPAuth from 'otpauth'

export type TotpAlgorithm =
  | 'SHA1'
  | 'SHA224'
  | 'SHA256'
  | 'SHA384'
  | 'SHA512'
  | 'SHA3-224'
  | 'SHA3-256'
  | 'SHA3-384'
  | 'SHA3-512'

export type TotpType = 'totp' | 'hotp' | 'steam'

export const TOTP_ALGORITHMS: TotpAlgorithm[] = [
  'SHA1',
  'SHA224',
  'SHA256',
  'SHA384',
  'SHA512',
  'SHA3-224',
  'SHA3-256',
  'SHA3-384',
  'SHA3-512',
]

export const TOTP_TYPES: TotpType[] = ['totp', 'steam', 'hotp']

export interface TotpEntryDraft {
  issuer: string
  label: string
  secret: string
  type: TotpType
  algorithm: TotpAlgorithm
  digits: number
  period: number
  counter: number
}

const BASE32_RE = /^[A-Z2-7]+=*$/i
const STEAM_ALPHABET = '23456789BCDFGHJKMNPQRTVWXY'

export function normalizeSecret(raw: string): string {
  return String(raw || '')
    .replace(/\s+/g, '')
    .replace(/-/g, '')
    .toUpperCase()
}

export function isValidBase32Secret(raw: string): boolean {
  const s = normalizeSecret(raw)
  if (s.length < 8) return false
  return BASE32_RE.test(s)
}

export function normalizeAlgorithm(raw?: string | null): TotpAlgorithm {
  const a = String(raw || 'SHA1')
    .trim()
    .toUpperCase()
    .replace(/_/g, '-')
  // Common aliases
  if (a === 'SHA3_224' || a === 'SHA3224') return 'SHA3-224'
  if (a === 'SHA3_256' || a === 'SHA3256') return 'SHA3-256'
  if (a === 'SHA3_384' || a === 'SHA3384') return 'SHA3-384'
  if (a === 'SHA3_512' || a === 'SHA3512') return 'SHA3-512'
  if ((TOTP_ALGORITHMS as string[]).includes(a)) return a as TotpAlgorithm
  return 'SHA1'
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s.replace(/\+/g, ' '))
  } catch {
    return s.replace(/\+/g, ' ')
  }
}

function guessIssuerFromLabel(label: string): string {
  if (label.includes(':')) return label.split(':')[0]!.trim()
  return ''
}

function stripIssuerFromLabel(label: string, issuer: string): string {
  if (issuer && label.toLowerCase().startsWith(issuer.toLowerCase() + ':')) {
    return label.slice(issuer.length + 1).trim() || label
  }
  if (label.includes(':')) {
    return label.split(':').slice(1).join(':').trim() || label
  }
  return label
}

/** Manual fallback when OTPAuth.URI.parse rejects edge-case QR payloads. */
function parseOtpauthUriManual(text: string): TotpEntryDraft | null {
  const m = text.match(/^otpauth:\/\/(totp|hotp|steam)\/([^?]*)\?(.*)$/i)
  if (!m) return null
  const kind = (m[1] || 'totp').toLowerCase()
  const type: TotpType = kind === 'hotp' ? 'hotp' : kind === 'steam' ? 'steam' : 'totp'
  const path = safeDecode(m[2] || '')
  const qs = new URLSearchParams(m[3] || '')
  const secret = normalizeSecret(qs.get('secret') || '')
  if (!isValidBase32Secret(secret)) return null
  const issuerQ = safeDecode(qs.get('issuer') || '')
  const algorithm = normalizeAlgorithm(qs.get('algorithm') || 'SHA1')
  let digits = Number(qs.get('digits') || (type === 'steam' ? 5 : 6))
  if (type === 'steam') digits = 5
  else if (digits !== 8 && digits !== 7 && digits !== 6) digits = 6
  const period = Math.max(15, Math.min(120, Number(qs.get('period') || 30)))
  const counter = Math.max(0, Number(qs.get('counter') || 0))
  const issuer = issuerQ || guessIssuerFromLabel(path) || (type === 'steam' ? 'Steam' : '')
  const label = stripIssuerFromLabel(path, issuer) || path || 'Account'
  return {
    issuer,
    label,
    secret,
    type,
    algorithm: type === 'steam' ? 'SHA1' : algorithm,
    digits,
    period,
    counter,
  }
}

export function parseOtpauthUri(uri: string): TotpEntryDraft | null {
  let text = String(uri || '').trim()
  text = text.replace(/^\uFEFF/, '').replace(/^["']|["']$/g, '').trim()
  const embedded = text.match(/otpauth:\/\/[^\s"'<>]+/i)
  if (embedded && !text.toLowerCase().startsWith('otpauth://')) {
    text = embedded[0]!
  }
  if (!text.toLowerCase().startsWith('otpauth://')) return null

  // Steam Guard special scheme sometimes uses otpauth://totp/Steam:... with digits=5
  try {
    const parsed = OTPAuth.URI.parse(text)
    const isHotp = parsed instanceof OTPAuth.HOTP
    const issuer =
      (parsed as OTPAuth.TOTP).issuer ||
      safeDecode(text.match(/[?&]issuer=([^&]+)/i)?.[1] || '') ||
      ''
    const label = (parsed as OTPAuth.TOTP).label || issuer || '2FA'
    const secret = parsed.secret.base32
    const algorithm = normalizeAlgorithm(String(parsed.algorithm || 'SHA1'))
    let digits = Number(parsed.digits) || 6
    let period = 30
    let counter = 0
    if (parsed instanceof OTPAuth.TOTP) {
      period = Number(parsed.period) || 30
    }
    if (parsed instanceof OTPAuth.HOTP) {
      counter = Number(parsed.counter) || 0
    }
    const issuerNorm = issuer || guessIssuerFromLabel(label)
    const isSteam =
      issuerNorm.toLowerCase() === 'steam' ||
      label.toLowerCase().startsWith('steam') ||
      digits === 5
    return {
      issuer: isSteam ? issuerNorm || 'Steam' : issuerNorm,
      label: stripIssuerFromLabel(label, issuer),
      secret: normalizeSecret(secret),
      type: isHotp ? 'hotp' : isSteam ? 'steam' : 'totp',
      algorithm: isSteam ? 'SHA1' : algorithm,
      digits: isSteam ? 5 : digits === 8 ? 8 : digits === 7 ? 7 : 6,
      period: Math.max(15, Math.min(120, period)),
      counter: Math.max(0, counter),
    }
  } catch {
    return parseOtpauthUriManual(text)
  }
}

/** Accept otpauth URI or raw base32 secret (+ optional issuer/label). */
export function parseSecretOrUri(
  input: string,
  fallback?: { issuer?: string; label?: string; type?: TotpType },
): TotpEntryDraft | null {
  const text = String(input || '').trim()
  if (!text) return null
  if (text.toLowerCase().startsWith('otpauth://')) {
    return parseOtpauthUri(text)
  }
  if (text.toLowerCase().startsWith('otpauth-migration://')) {
    return null
  }
  const secret = normalizeSecret(text)
  if (!isValidBase32Secret(secret)) return null
  const type = fallback?.type || 'totp'
  return {
    issuer: fallback?.issuer || (type === 'steam' ? 'Steam' : ''),
    label: fallback?.label || 'Account',
    secret,
    type,
    algorithm: 'SHA1',
    digits: type === 'steam' ? 5 : 6,
    period: 30,
    counter: 0,
  }
}

function steamGuardCode(hmacFull: Uint8Array): string {
  // Steam uses last byte of HMAC as offset into first 4 bytes, then maps to custom alphabet (5 chars)
  const offset = hmacFull[hmacFull.length - 1]! & 0x0f
  let full =
    ((hmacFull[offset]! & 0x7f) << 24) |
    ((hmacFull[offset + 1]! & 0xff) << 16) |
    ((hmacFull[offset + 2]! & 0xff) << 8) |
    (hmacFull[offset + 3]! & 0xff)
  let out = ''
  for (let i = 0; i < 5; i++) {
    out += STEAM_ALPHABET[full % STEAM_ALPHABET.length]
    full = Math.floor(full / STEAM_ALPHABET.length)
  }
  return out
}

/**
 * Generate Steam Guard style code via HMAC-SHA1 of time step (same as TOTP core).
 * Uses Web Crypto when available; falls back to otpauth 5-digit mapping approximation.
 */
async function generateSteamCodeAsync(secretB32: string, nowMs: number, period: number): Promise<string> {
  try {
    const secret = OTPAuth.Secret.fromBase32(normalizeSecret(secretB32))
    const step = Math.floor(nowMs / 1000 / Math.max(15, period || 30))
    // Use otpauth internal by generating with digits that we won't display —
    // better: use SubtleCrypto
    if (globalThis.crypto?.subtle) {
      const keyBytes = new Uint8Array(secret.bytes)
      const key = await crypto.subtle.importKey(
        'raw',
        keyBytes,
        { name: 'HMAC', hash: 'SHA-1' },
        false,
        ['sign'],
      )
      const buf = new ArrayBuffer(8)
      const view = new DataView(buf)
      // big-endian uint64 time step
      const high = Math.floor(step / 0x100000000)
      const low = step >>> 0
      view.setUint32(0, high)
      view.setUint32(4, low)
      const sig = new Uint8Array(await crypto.subtle.sign('HMAC', key, buf))
      return steamGuardCode(sig)
    }
  } catch {
    /* fall through */
  }
  // Fallback: 5-digit TOTP (not perfect Steam alphabet, but usable offline)
  const totp = new OTPAuth.TOTP({
    algorithm: 'SHA1',
    digits: 5,
    period: Math.max(15, period || 30),
    secret: OTPAuth.Secret.fromBase32(normalizeSecret(secretB32)),
  })
  return totp.generate({ timestamp: nowMs })
}

export function buildOtpauthUri(entry: TotpEntryDraft): string {
  const secret = OTPAuth.Secret.fromBase32(normalizeSecret(entry.secret))
  if (entry.type === 'hotp') {
    const hotp = new OTPAuth.HOTP({
      issuer: entry.issuer || undefined,
      label: entry.label || 'Account',
      algorithm: entry.algorithm,
      digits: entry.digits,
      counter: entry.counter,
      secret,
    })
    return hotp.toString()
  }
  const totp = new OTPAuth.TOTP({
    issuer: entry.type === 'steam' ? entry.issuer || 'Steam' : entry.issuer || undefined,
    label: entry.label || 'Account',
    algorithm: entry.type === 'steam' ? 'SHA1' : entry.algorithm,
    digits: entry.type === 'steam' ? 5 : entry.digits,
    period: entry.period,
    secret,
  })
  return totp.toString()
}

/** Sync code generation (Steam uses best-effort sync path). */
export function generateCode(entry: TotpEntryDraft, nowMs: number = Date.now()): string {
  const secret = OTPAuth.Secret.fromBase32(normalizeSecret(entry.secret))
  if (entry.type === 'hotp') {
    const hotp = new OTPAuth.HOTP({
      issuer: entry.issuer || undefined,
      label: entry.label || 'Account',
      algorithm: entry.algorithm,
      digits: entry.digits,
      counter: entry.counter,
      secret,
    })
    return hotp.generate()
  }
  if (entry.type === 'steam') {
    try {
      return generateSteamCodeSync(secret.bytes, nowMs, entry.period)
    } catch {
      return '-----'
    }
  }
  const totp = new OTPAuth.TOTP({
    issuer: entry.issuer || undefined,
    label: entry.label || 'Account',
    algorithm: entry.algorithm,
    digits: entry.digits === 8 ? 8 : entry.digits === 7 ? 7 : 6,
    period: entry.period,
    secret,
  })
  return totp.generate({ timestamp: nowMs })
}

/** Minimal pure-JS SHA-1 + HMAC for Steam (sync, works in browser without async). */
function generateSteamCodeSync(key: Uint8Array, nowMs: number, period: number): string {
  const step = Math.floor(nowMs / 1000 / Math.max(15, period || 30))
  const msg = new Uint8Array(8)
  const view = new DataView(msg.buffer)
  view.setUint32(0, Math.floor(step / 0x100000000))
  view.setUint32(4, step >>> 0)
  const mac = hmacSha1(key, msg)
  return steamGuardCode(mac)
}

function sha1(data: Uint8Array): Uint8Array {
  // Compact SHA-1 implementation
  const ml = data.length
  const withOne = new Uint8Array(((ml + 9 + 63) >> 6) << 6)
  withOne.set(data)
  withOne[ml] = 0x80
  const bitLen = ml * 8
  const view = new DataView(withOne.buffer)
  view.setUint32(withOne.length - 4, bitLen >>> 0)
  view.setUint32(withOne.length - 8, Math.floor(bitLen / 0x100000000))

  let h0 = 0x67452301
  let h1 = 0xefcdab89
  let h2 = 0x98badcfe
  let h3 = 0x10325476
  let h4 = 0xc3d2e1f0
  const w = new Uint32Array(80)

  for (let i = 0; i < withOne.length; i += 64) {
    for (let j = 0; j < 16; j++) w[j] = view.getUint32(i + j * 4)
    for (let j = 16; j < 80; j++) {
      const x = w[j - 3]! ^ w[j - 8]! ^ w[j - 14]! ^ w[j - 16]!
      w[j] = (x << 1) | (x >>> 31)
    }
    let a = h0
    let b = h1
    let c = h2
    let d = h3
    let e = h4
    for (let j = 0; j < 80; j++) {
      let f: number
      let k: number
      if (j < 20) {
        f = (b & c) | (~b & d)
        k = 0x5a827999
      } else if (j < 40) {
        f = b ^ c ^ d
        k = 0x6ed9eba1
      } else if (j < 60) {
        f = (b & c) | (b & d) | (c & d)
        k = 0x8f1bbcdc
      } else {
        f = b ^ c ^ d
        k = 0xca62c1d6
      }
      const temp = (((a << 5) | (a >>> 27)) + f + e + k + w[j]!) >>> 0
      e = d
      d = c
      c = ((b << 30) | (b >>> 2)) >>> 0
      b = a
      a = temp
    }
    h0 = (h0 + a) >>> 0
    h1 = (h1 + b) >>> 0
    h2 = (h2 + c) >>> 0
    h3 = (h3 + d) >>> 0
    h4 = (h4 + e) >>> 0
  }
  const out = new Uint8Array(20)
  const ov = new DataView(out.buffer)
  ov.setUint32(0, h0)
  ov.setUint32(4, h1)
  ov.setUint32(8, h2)
  ov.setUint32(12, h3)
  ov.setUint32(16, h4)
  return out
}

function hmacSha1(key: Uint8Array, msg: Uint8Array): Uint8Array {
  let k = key
  if (k.length > 64) k = sha1(k)
  const kk = new Uint8Array(64)
  kk.set(k)
  const o = new Uint8Array(64)
  const i = new Uint8Array(64)
  for (let n = 0; n < 64; n++) {
    o[n] = kk[n]! ^ 0x5c
    i[n] = kk[n]! ^ 0x36
  }
  const inner = new Uint8Array(64 + msg.length)
  inner.set(i)
  inner.set(msg, 64)
  const innerHash = sha1(inner)
  const outer = new Uint8Array(64 + 20)
  outer.set(o)
  outer.set(innerHash, 64)
  return sha1(outer)
}

/** Seconds remaining in current TOTP window. */
export function remainingSeconds(period: number, nowMs: number = Date.now()): number {
  const p = Math.max(15, period || 30)
  const elapsed = Math.floor(nowMs / 1000) % p
  return p - elapsed
}

/** Service / brand presets — "other" requires custom name. Icons from brandLogos. */
export const SERVICE_PRESETS: Array<{ id: string; name: string; issuer: string }> = [
  { id: 'google', name: 'Google', issuer: 'Google' },
  { id: 'microsoft', name: 'Microsoft', issuer: 'Microsoft' },
  { id: 'github', name: 'GitHub', issuer: 'GitHub' },
  { id: 'apple', name: 'Apple', issuer: 'Apple' },
  { id: 'amazon', name: 'Amazon', issuer: 'Amazon' },
  { id: 'openai', name: 'OpenAI / ChatGPT', issuer: 'OpenAI' },
  { id: 'claude', name: 'Claude', issuer: 'Anthropic' },
  { id: 'discord', name: 'Discord', issuer: 'Discord' },
  { id: 'twitter', name: 'X / Twitter', issuer: 'Twitter' },
  { id: 'facebook', name: 'Facebook', issuer: 'Facebook' },
  { id: 'instagram', name: 'Instagram', issuer: 'Instagram' },
  { id: 'telegram', name: 'Telegram', issuer: 'Telegram' },
  { id: 'whatsapp', name: 'WhatsApp', issuer: 'WhatsApp' },
  { id: 'slack', name: 'Slack', issuer: 'Slack' },
  { id: 'notion', name: 'Notion', issuer: 'Notion' },
  { id: 'dropbox', name: 'Dropbox', issuer: 'Dropbox' },
  { id: 'steam', name: 'Steam', issuer: 'Steam' },
  { id: 'epicgames', name: 'Epic Games', issuer: 'Epic Games' },
  { id: 'binance', name: 'Binance', issuer: 'Binance' },
  { id: 'coinbase', name: 'Coinbase', issuer: 'Coinbase' },
  { id: 'stripe', name: 'Stripe', issuer: 'Stripe' },
  { id: 'paypal', name: 'PayPal', issuer: 'PayPal' },
  { id: 'aws', name: 'AWS', issuer: 'Amazon Web Services' },
  { id: 'cloudflare', name: 'Cloudflare', issuer: 'Cloudflare' },
  { id: 'docker', name: 'Docker', issuer: 'Docker' },
  { id: 'gitlab', name: 'GitLab', issuer: 'GitLab' },
  { id: 'bitwarden', name: 'Bitwarden', issuer: 'Bitwarden' },
  { id: '1password', name: '1Password', issuer: '1Password' },
  { id: 'linkedin', name: 'LinkedIn', issuer: 'LinkedIn' },
  { id: 'reddit', name: 'Reddit', issuer: 'Reddit' },
  { id: 'twitch', name: 'Twitch', issuer: 'Twitch' },
  { id: 'youtube', name: 'YouTube', issuer: 'YouTube' },
  { id: 'spotify', name: 'Spotify', issuer: 'Spotify' },
  { id: 'netflix', name: 'Netflix', issuer: 'Netflix' },
  { id: 'figma', name: 'Figma', issuer: 'Figma' },
  { id: 'adobe', name: 'Adobe', issuer: 'Adobe' },
  { id: 'shopify', name: 'Shopify', issuer: 'Shopify' },
  { id: 'vercel', name: 'Vercel', issuer: 'Vercel' },
  { id: 'other', name: 'Other', issuer: '' },
]

// silence unused async helper warning by exporting for potential future use
export { generateSteamCodeAsync }
