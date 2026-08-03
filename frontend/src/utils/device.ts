/**
 * Device identity + license headers.
 *
 * Prefer vault-derived cryptographic public id (SHA-256 of device secret).
 * Fallback: stable random id in localStorage (not a spoofable fingerprint).
 */

import { hmacSha256Hex, sha256Hex } from '@/utils/cryptoVault'

const DEVICE_KEY = 'openmail.deviceId'
const LICENSE_KEY = 'openmail.licenseToken'

/** Set by vault store on unlock (avoids circular pinia import in getDeviceId). */
let vaultPublicId: string | null = null
let vaultSecretB64: string | null = null

export function setVaultDeviceIdentity(publicId: string | null, secretB64: string | null) {
  vaultPublicId = publicId
  vaultSecretB64 = secretB64
}

function randomDeviceId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
  return `dev_${hex}`
}

/** Stable public device id for quotas (not a secret). */
export function getDeviceId(): string {
  if (vaultPublicId && vaultPublicId.length >= 32) {
    return `vk_${vaultPublicId.slice(0, 40)}`
  }
  try {
    const existing = localStorage.getItem(DEVICE_KEY)
    if (existing && existing.length >= 16) return existing
  } catch {
    /* ignore */
  }
  const id = randomDeviceId()
  try {
    localStorage.setItem(DEVICE_KEY, id)
  } catch {
    /* ignore */
  }
  return id
}

export function getLicenseToken(): string {
  try {
    return localStorage.getItem(LICENSE_KEY) || ''
  } catch {
    return ''
  }
}

export function setLicenseToken(token: string) {
  try {
    if (token.trim()) localStorage.setItem(LICENSE_KEY, token.trim())
    else localStorage.removeItem(LICENSE_KEY)
  } catch {
    /* ignore */
  }
}

/** Sync headers (quota / most calls). */
export function deviceHeaders(): Record<string, string> {
  const h: Record<string, string> = {
    'X-Device-Id': getDeviceId(),
  }
  const lic = getLicenseToken()
  if (lic) h['X-License-Token'] = lic
  return h
}

/**
 * Async headers with HMAC proof when vault unlocked.
 * Message: `${ts}.${method}.${path}.${body_sha256_hex}`
 * Always sets X-Device-Body-Sha256 (sha256 of empty string when no body).
 *
 * @param bodyText Raw request body string as sent on the wire (e.g. JSON.stringify(body)).
 */
export async function deviceHeadersAsync(
  method: string,
  path: string,
  bodyText: string = '',
): Promise<Record<string, string>> {
  const h = deviceHeaders()
  if (!vaultSecretB64 || !vaultPublicId) return h
  try {
    const ts = String(Math.floor(Date.now() / 1000))
    const pathOnly = path
    const bodyHash = await sha256Hex(bodyText)
    const msg = `${ts}.${method.toUpperCase()}.${pathOnly}.${bodyHash}`
    const sig = await hmacSha256Hex(vaultSecretB64, msg)
    h['X-Device-Id'] = `vk_${vaultPublicId.slice(0, 40)}`
    h['X-Device-Ts'] = ts
    h['X-Device-Sign'] = sig
    h['X-Device-Body-Sha256'] = bodyHash
  } catch {
    /* ignore */
  }
  return h
}
