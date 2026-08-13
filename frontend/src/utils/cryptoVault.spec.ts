/**
 * Vault crypto round-trips: password / recovery DEK, session wrap, tamper.
 */
import { describe, expect, it } from 'vitest'
import {
  VaultCryptoError,
  createDualUnlock,
  createVaultMeta,
  decryptJson,
  dekRawFromB64,
  dekRawToB64,
  encryptJson,
  generateRecoveryKey,
  hmacSha256Hex,
  normalizeRecoveryKey,
  unlockVaultMeta,
  unlockWithPassword,
  unlockWithRecoveryKey,
  unwrapDekFromSession,
  wrapDekForSession,
} from '@/utils/cryptoVault'

const PASSWORD = 'correct-horse-battery'

describe('cryptoVault round-trip', () => {
  it('encrypts and decrypts JSON under the vault key', async () => {
    const { key } = await createVaultMeta(PASSWORD)
    const pkg = await encryptJson(key, { hello: '世界', n: 1 })
    await expect(decryptJson(key, pkg)).resolves.toEqual({ hello: '世界', n: 1 })
    expect(pkg.iv).toBeTruthy()
    expect(pkg.ct).toBeTruthy()
  })

  it('uses a new IV each encrypt so ciphertexts differ', async () => {
    const { key } = await createVaultMeta(PASSWORD)
    const a = await encryptJson(key, 'same')
    const b = await encryptJson(key, 'same')
    expect(a.iv).not.toBe(b.iv)
    expect(a.ct).not.toBe(b.ct)
  })

  it('rejects a wrong password and a tampered ciphertext', async () => {
    const { meta, key } = await createVaultMeta(PASSWORD)
    await expect(unlockVaultMeta('wrong-password-xx', meta)).rejects.toBeInstanceOf(
      VaultCryptoError,
    )
    const pkg = await encryptJson(key, 'secret')
    const flipped = { ...pkg, ct: pkg.ct.slice(0, -2) + (pkg.ct.endsWith('AA') ? 'BB' : 'AA') }
    await expect(decryptJson(key, flipped)).rejects.toBeInstanceOf(VaultCryptoError)
  })

  it('unlocks the same DEK with password or recovery key', async () => {
    const recovery = generateRecoveryKey()
    const { meta, key, recoveryKeyDisplay, dekRaw } = await createDualUnlock(PASSWORD, recovery)
    expect(normalizeRecoveryKey(recoveryKeyDisplay)).toBe(normalizeRecoveryKey(recovery))

    const viaPw = await unlockWithPassword(PASSWORD, meta)
    const viaRk = await unlockWithRecoveryKey(recoveryKeyDisplay, meta)
    expect(viaPw.dekRaw).not.toBeNull()
    expect(Array.from(viaPw.dekRaw!)).toEqual(Array.from(dekRaw))
    expect(Array.from(viaRk.dekRaw)).toEqual(Array.from(dekRaw))

    const sealed = await encryptJson(key, { otp: '123456' })
    await expect(decryptJson(viaPw.key, sealed)).resolves.toEqual({ otp: '123456' })
    await expect(decryptJson(viaRk.key, sealed)).resolves.toEqual({ otp: '123456' })
  })

  it('rejects a wrong recovery key without unlocking the DEK', async () => {
    const { meta } = await createDualUnlock(PASSWORD, generateRecoveryKey())
    await expect(unlockWithRecoveryKey(generateRecoveryKey(), meta)).rejects.toBeInstanceOf(
      VaultCryptoError,
    )
    await expect(unlockWithPassword('wrong-password-xx', meta)).rejects.toBeInstanceOf(
      VaultCryptoError,
    )
  })

  it('round-trips the tab session wrap', async () => {
    const { dekRaw, key } = await createDualUnlock(PASSWORD, generateRecoveryKey())
    const wrap = await wrapDekForSession(dekRaw)
    const opened = await unwrapDekFromSession(wrap.sessionKeyB64, wrap.package)
    expect(Array.from(opened.dekRaw)).toEqual(Array.from(dekRaw))
    const pkg = await encryptJson(key, 'session-ok')
    await expect(decryptJson(opened.key, pkg)).resolves.toBe('session-ok')
    expect(dekRawFromB64(dekRawToB64(dekRaw))).toEqual(dekRaw)
  })

  it('HMAC is stable for a known secret and message', async () => {
    const secretB64 = btoa('\x01'.repeat(32))
    const a = await hmacSha256Hex(secretB64, 'GET./api/health')
    const b = await hmacSha256Hex(secretB64, 'GET./api/health')
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    const other = await hmacSha256Hex(secretB64, 'POST./api/health')
    expect(other).not.toBe(a)
  })
})
