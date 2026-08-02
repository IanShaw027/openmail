/** TOTP / OTP Auth helpers (otpauth). */

import * as OTPAuth from 'otpauth'

export type TotpAlgorithm = 'SHA1' | 'SHA256' | 'SHA512'
export type TotpType = 'totp' | 'hotp'

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

export function parseOtpauthUri(uri: string): TotpEntryDraft | null {
  const text = String(uri || '').trim()
  if (!text.toLowerCase().startsWith('otpauth://')) return null
  try {
    const parsed = OTPAuth.URI.parse(text)
    const isHotp = parsed instanceof OTPAuth.HOTP
    const issuer =
      (parsed as OTPAuth.TOTP).issuer ||
      decodeURIComponent((text.match(/issuer=([^&]+)/i)?.[1] || '').replace(/\+/g, ' ')) ||
      ''
    const label = (parsed as OTPAuth.TOTP).label || issuer || '2FA'
    const secret = parsed.secret.base32
    const algorithm = String(parsed.algorithm || 'SHA1').toUpperCase() as TotpAlgorithm
    const digits = Number(parsed.digits) || 6
    let period = 30
    let counter = 0
    if (parsed instanceof OTPAuth.TOTP) {
      period = Number(parsed.period) || 30
    }
    if (parsed instanceof OTPAuth.HOTP) {
      counter = Number(parsed.counter) || 0
    }
    return {
      issuer: issuer || guessIssuerFromLabel(label),
      label: stripIssuerFromLabel(label, issuer),
      secret: normalizeSecret(secret),
      type: isHotp ? 'hotp' : 'totp',
      algorithm: ['SHA1', 'SHA256', 'SHA512'].includes(algorithm) ? algorithm : 'SHA1',
      digits: digits === 8 ? 8 : 6,
      period: Math.max(15, Math.min(120, period)),
      counter: Math.max(0, counter),
    }
  } catch {
    return null
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

/** Accept otpauth URI or raw base32 secret (+ optional issuer/label). */
export function parseSecretOrUri(
  input: string,
  fallback?: { issuer?: string; label?: string },
): TotpEntryDraft | null {
  const text = String(input || '').trim()
  if (!text) return null
  if (text.toLowerCase().startsWith('otpauth://')) {
    return parseOtpauthUri(text)
  }
  // migration URI batch: otpauth-migration:// is not fully supported here
  if (text.toLowerCase().startsWith('otpauth-migration://')) {
    return null
  }
  const secret = normalizeSecret(text)
  if (!isValidBase32Secret(secret)) return null
  return {
    issuer: fallback?.issuer || '',
    label: fallback?.label || 'Account',
    secret,
    type: 'totp',
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
    counter: 0,
  }
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
    issuer: entry.issuer || undefined,
    label: entry.label || 'Account',
    algorithm: entry.algorithm,
    digits: entry.digits,
    period: entry.period,
    secret,
  })
  return totp.toString()
}

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
  const totp = new OTPAuth.TOTP({
    issuer: entry.issuer || undefined,
    label: entry.label || 'Account',
    algorithm: entry.algorithm,
    digits: entry.digits,
    period: entry.period,
    secret,
  })
  return totp.generate({ timestamp: nowMs })
}

/** Seconds remaining in current TOTP window. */
export function remainingSeconds(period: number, nowMs: number = Date.now()): number {
  const p = Math.max(15, period || 30)
  const elapsed = Math.floor(nowMs / 1000) % p
  return p - elapsed
}

/** Common service presets for UI. */
export const SERVICE_PRESETS: Array<{ id: string; name: string; issuer: string }> = [
  { id: 'google', name: 'Google', issuer: 'Google' },
  { id: 'microsoft', name: 'Microsoft', issuer: 'Microsoft' },
  { id: 'github', name: 'GitHub', issuer: 'GitHub' },
  { id: 'apple', name: 'Apple', issuer: 'Apple' },
  { id: 'amazon', name: 'Amazon', issuer: 'Amazon' },
  { id: 'discord', name: 'Discord', issuer: 'Discord' },
  { id: 'twitter', name: 'X / Twitter', issuer: 'Twitter' },
  { id: 'facebook', name: 'Facebook', issuer: 'Facebook' },
  { id: 'dropbox', name: 'Dropbox', issuer: 'Dropbox' },
  { id: 'steam', name: 'Steam', issuer: 'Steam' },
  { id: 'binance', name: 'Binance', issuer: 'Binance' },
  { id: 'other', name: 'Other', issuer: '' },
]
