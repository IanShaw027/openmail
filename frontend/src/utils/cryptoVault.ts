/**
 * Client-side vault crypto (Web Crypto API).
 * PBKDF2-SHA-256 → AES-256-GCM. Keys never leave the browser.
 *
 * Package format (JSON, base64 fields):
 * { v:1, salt, iv, ct }  — encrypt arbitrary JSON
 * Vault meta: { v:1, salt, kdf: 'PBKDF2', iter, check: <encrypt("ok")> }
 */

const TEXT = new TextEncoder()
const TEXT_DEC = new TextDecoder()

/** OWASP-oriented default; tuned for mobile UX (~0.3–1s on mid devices). */
export const PBKDF2_ITERATIONS = 310_000
export const SALT_LEN = 16
export const IV_LEN = 12

export class VaultCryptoError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VaultCryptoError'
  }
}

function b64encode(buf: ArrayBuffer | Uint8Array): string {
  const u8 = buf instanceof Uint8Array ? buf : new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]!)
  return btoa(s)
}

function b64decode(s: string): Uint8Array {
  // Accept std / url-safe base64, optional padding
  let t = s.replace(/-/g, '+').replace(/_/g, '/')
  const pad = t.length % 4
  if (pad) t += '='.repeat(4 - pad)
  const bin = atob(t)
  const u8 = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i)
  return u8
}

/** Export for device id derivation (hash raw secret bytes, not the b64 string). */
export function b64decodeToBytes(s: string): Uint8Array {
  return b64decode(s)
}

function randomBytes(n: number): Uint8Array {
  return crypto.getRandomValues(new Uint8Array(n))
}

async function importPasswordKey(password: string): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', TEXT.encode(password), 'PBKDF2', false, ['deriveKey'])
}

export async function deriveVaultKey(
  password: string,
  salt: Uint8Array,
  iterations: number = PBKDF2_ITERATIONS,
): Promise<CryptoKey> {
  const base = await importPasswordKey(password)
  return crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: salt as BufferSource,
      iterations,
      hash: 'SHA-256',
    },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

export interface CipherPackage {
  v: 1
  salt?: string
  iv: string
  ct: string
  iter?: number
}

/** Encrypt JSON-serializable value with an AES-GCM key. */
export async function encryptJson(key: CryptoKey, value: unknown): Promise<CipherPackage> {
  const iv = randomBytes(IV_LEN)
  const plain = TEXT.encode(JSON.stringify(value))
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: iv as BufferSource }, key, plain)
  return {
    v: 1,
    iv: b64encode(iv),
    ct: b64encode(ct),
  }
}

/** Decrypt package; throws VaultCryptoError on wrong key / tamper. */
export async function decryptJson<T = unknown>(key: CryptoKey, pkg: CipherPackage): Promise<T> {
  try {
    const iv = b64decode(pkg.iv)
    const ct = b64decode(pkg.ct)
    const plain = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      key,
      ct as BufferSource,
    )
    return JSON.parse(TEXT_DEC.decode(plain)) as T
  } catch {
    throw new VaultCryptoError('decrypt_failed')
  }
}

export interface VaultMeta {
  v: 1
  salt: string
  kdf: 'PBKDF2'
  iter: number
  /** AES-GCM encrypt of fixed string "openmail-vault-ok" for password verify */
  check: CipherPackage
  createdAt: number
}

export async function createVaultMeta(password: string): Promise<{ meta: VaultMeta; key: CryptoKey }> {
  const salt = randomBytes(SALT_LEN)
  const iter = PBKDF2_ITERATIONS
  const key = await deriveVaultKey(password, salt, iter)
  const check = await encryptJson(key, 'openmail-vault-ok')
  const meta: VaultMeta = {
    v: 1,
    salt: b64encode(salt),
    kdf: 'PBKDF2',
    iter,
    check,
    createdAt: Date.now(),
  }
  return { meta, key }
}

export async function unlockVaultMeta(
  password: string,
  meta: VaultMeta,
): Promise<CryptoKey> {
  const salt = b64decode(meta.salt)
  const iter = meta.iter || PBKDF2_ITERATIONS
  const key = await deriveVaultKey(password, salt, iter)
  const marker = await decryptJson<string>(key, meta.check)
  if (marker !== 'openmail-vault-ok') {
    throw new VaultCryptoError('bad_password')
  }
  return key
}

/** SHA-256 hex of raw bytes / UTF-8 string. */
export async function sha256Hex(data: string | Uint8Array): Promise<string> {
  const buf = typeof data === 'string' ? TEXT.encode(data) : data
  const dig = await crypto.subtle.digest('SHA-256', buf as BufferSource)
  return Array.from(new Uint8Array(dig))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Device public id material: SHA-256 of the raw secret bytes (decoded from base64).
 * Must match server: sha256(base64_decode(secret_b64)).
 */
export async function devicePublicIdFromSecretB64(secretB64: string): Promise<string> {
  const raw = b64decode(secretB64)
  return sha256Hex(raw)
}

/** HMAC-SHA-256 hex. */
export async function hmacSha256Hex(secretB64: string, message: string): Promise<string> {
  const raw = b64decode(secretB64)
  const key = await crypto.subtle.importKey(
    'raw',
    raw as BufferSource,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const sig = await crypto.subtle.sign('HMAC', key, TEXT.encode(message))
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export function generateDeviceSecret(): string {
  return b64encode(randomBytes(32))
}

export function isWebCryptoAvailable(): boolean {
  return typeof crypto !== 'undefined' && !!crypto.subtle
}

/** Recovery key: 32 random bytes as groups of base32-ish hex (easy to copy). */
export function generateRecoveryKey(): string {
  const bytes = randomBytes(24)
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
  // 6 groups of 8 hex chars
  return (hex.match(/.{1,8}/g) || [hex]).join('-').toUpperCase()
}

export function normalizeRecoveryKey(input: string): string {
  return input.replace(/[^0-9a-fA-F]/g, '').toLowerCase()
}

/**
 * Wrap vault key material so either password OR recovery key can unlock.
 * We store check packages under both derived keys.
 */
export interface VaultMetaV2 extends VaultMeta {
  recovery?: {
    salt: string
    iter: number
    check: CipherPackage
    /** AES-GCM wrap of password-derived key export is not possible (non-extractable).
     * Instead store a random DEK encrypted under password key and recovery key. */
    dek_pw: CipherPackage
    dek_rk: CipherPackage
  }
  /** Present when dual-unlock DEK scheme is active */
  dual?: boolean
}

/** Create random DEK and encrypt under password-derived key. */
export async function createDualUnlock(
  password: string,
  recoveryKeyRaw: string,
): Promise<{
  meta: VaultMetaV2
  key: CryptoKey
  recoveryKeyDisplay: string
  dekRaw: Uint8Array
}> {
  const recoveryNorm = normalizeRecoveryKey(recoveryKeyRaw)
  if (recoveryNorm.length < 32) throw new VaultCryptoError('recovery_too_short')

  const saltPw = randomBytes(SALT_LEN)
  const saltRk = randomBytes(SALT_LEN)
  const iter = PBKDF2_ITERATIONS
  const pwKey = await deriveVaultKey(password, saltPw, iter)
  const rkKey = await deriveVaultKey(recoveryNorm, saltRk, iter)

  // Random DEK
  const dekRaw = randomBytes(32)
  const dek = await crypto.subtle.importKey(
    'raw',
    dekRaw as BufferSource,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )

  const wrap = async (wrappingKey: CryptoKey) => {
    const iv = randomBytes(IV_LEN)
    const ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      wrappingKey,
      dekRaw as BufferSource,
    )
    return { v: 1 as const, iv: b64encode(iv), ct: b64encode(ct) }
  }

  const dek_pw = await wrap(pwKey)
  const dek_rk = await wrap(rkKey)
  const check = await encryptJson(dek, 'openmail-vault-ok')
  const rkCheck = await encryptJson(rkKey, 'openmail-recovery-ok')

  const meta: VaultMetaV2 = {
    v: 1,
    salt: b64encode(saltPw),
    kdf: 'PBKDF2',
    iter,
    check,
    createdAt: Date.now(),
    dual: true,
    recovery: {
      salt: b64encode(saltRk),
      iter,
      check: rkCheck,
      dek_pw,
      dek_rk,
    },
  }

  // display form
  const display = (recoveryNorm.match(/.{1,8}/g) || [recoveryNorm]).join('-').toUpperCase()
  return { meta, key: dek, recoveryKeyDisplay: display, dekRaw }
}

async function unwrapDek(
  wrappingKey: CryptoKey,
  pkg: CipherPackage,
): Promise<{ key: CryptoKey; raw: Uint8Array }> {
  try {
    const iv = b64decode(pkg.iv)
    const ct = b64decode(pkg.ct)
    const rawBuf = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      wrappingKey,
      ct as BufferSource,
    )
    const raw = new Uint8Array(rawBuf)
    const key = await crypto.subtle.importKey(
      'raw',
      raw as BufferSource,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )
    return { key, raw }
  } catch {
    throw new VaultCryptoError('unwrap_failed')
  }
}

export async function importDekFromRaw(raw: Uint8Array): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    raw as BufferSource,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

/** Export DEK raw bytes for session wrap (caller must clear). */
export function dekRawToB64(raw: Uint8Array): string {
  return b64encode(raw)
}

export function dekRawFromB64(s: string): Uint8Array {
  return b64decode(s)
}

/**
 * Wrap DEK raw for tab/session resume (sessionStorage).
 * Uses a random session key that only lives in sessionStorage.
 */
export async function wrapDekForSession(dekRaw: Uint8Array): Promise<{
  sessionKeyB64: string
  package: CipherPackage
}> {
  const sessionKeyRaw = randomBytes(32)
  const sessionKey = await crypto.subtle.importKey(
    'raw',
    sessionKeyRaw as BufferSource,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
  const iv = randomBytes(IV_LEN)
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: iv as BufferSource },
    sessionKey,
    dekRaw as BufferSource,
  )
  return {
    sessionKeyB64: b64encode(sessionKeyRaw),
    package: { v: 1, iv: b64encode(iv), ct: b64encode(ct) },
  }
}

export async function unwrapDekFromSession(
  sessionKeyB64: string,
  pkg: CipherPackage,
): Promise<{ key: CryptoKey; dekRaw: Uint8Array }> {
  const sessionKeyRaw = b64decode(sessionKeyB64)
  const sessionKey = await crypto.subtle.importKey(
    'raw',
    sessionKeyRaw as BufferSource,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
  const { key, raw } = await unwrapDek(sessionKey, pkg)
  return { key, dekRaw: raw }
}

/** Unlock with password (supports dual DEK meta and legacy single-key meta). */
export async function unlockWithPassword(
  password: string,
  meta: VaultMeta | VaultMetaV2,
): Promise<{ key: CryptoKey; dekRaw: Uint8Array | null }> {
  const m = meta as VaultMetaV2
  if (m.dual && m.recovery?.dek_pw) {
    const salt = b64decode(m.salt)
    const iter = m.iter || PBKDF2_ITERATIONS
    const pwKey = await deriveVaultKey(password, salt, iter)
    const { key: dek, raw } = await unwrapDek(pwKey, m.recovery.dek_pw)
    const marker = await decryptJson<string>(dek, m.check)
    if (marker !== 'openmail-vault-ok') throw new VaultCryptoError('bad_password')
    return { key: dek, dekRaw: raw }
  }
  // Legacy password-only vault: key is non-extractable → no tab session resume.
  const key = await unlockVaultMeta(password, meta)
  return { key, dekRaw: null }
}

/** Unlock with recovery key (dual meta only). */
export async function unlockWithRecoveryKey(
  recoveryKeyRaw: string,
  meta: VaultMetaV2,
): Promise<{ key: CryptoKey; dekRaw: Uint8Array }> {
  if (!meta.dual || !meta.recovery?.dek_rk) {
    throw new VaultCryptoError('no_recovery')
  }
  const recoveryNorm = normalizeRecoveryKey(recoveryKeyRaw)
  const salt = b64decode(meta.recovery.salt)
  const iter = meta.recovery.iter || PBKDF2_ITERATIONS
  const rkKey = await deriveVaultKey(recoveryNorm, salt, iter)
  // verify recovery check
  try {
    const mk = await decryptJson<string>(rkKey, meta.recovery.check)
    if (mk !== 'openmail-recovery-ok') throw new Error('bad')
  } catch {
    throw new VaultCryptoError('bad_recovery')
  }
  const { key: dek, raw } = await unwrapDek(rkKey, meta.recovery.dek_rk)
  const marker = await decryptJson<string>(dek, meta.check)
  if (marker !== 'openmail-vault-ok') throw new VaultCryptoError('bad_recovery')
  return { key: dek, dekRaw: raw }
}

/**
 * Upgrade legacy password-only vault to dual-unlock by re-encrypting all data
 * under a new DEK. Caller must pass plaintext snapshots to re-seal.
 */
export async function upgradeToDualUnlock(
  password: string,
  legacyMeta: VaultMeta,
  recoveryKeyRaw: string,
): Promise<{ meta: VaultMetaV2; key: CryptoKey; recoveryKeyDisplay: string }> {
  // Verify password still works
  await unlockVaultMeta(password, legacyMeta)
  return createDualUnlock(password, recoveryKeyRaw)
}
