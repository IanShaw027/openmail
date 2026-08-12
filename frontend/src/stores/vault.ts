/**
 * Local vault: master password → AES key in memory only.
 * Dual vaults can resume unlock within the same browser tab session
 * (sessionStorage wrap of DEK). Explicit lock / idle timeout clear it.
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  type CipherPackage,
  type VaultMeta,
  type VaultMetaV2,
  VaultCryptoError,
  createDualUnlock,
  decryptJson,
  encryptJson,
  generateDeviceSecret,
  generateRecoveryKey,
  isWebCryptoAvailable,
  devicePublicIdFromSecretB64,
  unlockWithPassword,
  unlockWithRecoveryKey,
  unlockVaultMeta,
  wrapDekForSession,
  unwrapDekFromSession,
} from '@/utils/cryptoVault'
import { factoryResetAndReload } from '@/utils/clearLocalEnvironment'
import { setVaultDeviceIdentity } from '@/utils/device'
import { apiRequest } from '@/api/client'
import { useAccountsStore } from '@/stores/accounts'
import { useTwoFaStore } from '@/stores/twofa'
import { useMailCacheStore } from '@/stores/mailCache'

const META_KEY = 'openmail.vault.meta.v1'
const ACCOUNTS_ENC_KEY = 'openmail.accounts.enc.v1'
const TWOFA_ENC_KEY = 'openmail.twofa.enc.v1'
const MAILCACHE_ENC_KEY = 'openmail.mailCache.enc.v1'
const DEVICE_SECRET_ENC_KEY = 'openmail.deviceSecret.enc.v1'
/** Recovery key display sealed inside vault (only while unlocked). */
const RECOVERY_ENC_KEY = 'openmail.vault.recovery.enc.v1'
const LEGACY_ACCOUNTS = 'openmail.accounts.local'
const LEGACY_TWOFA = 'openmail.twofa.v1'
const LEGACY_MAIL = 'openmail.mailCache.v1'
const LOCK_MINUTES_KEY = 'openmail.vault.lockMinutes'
/** sessionStorage: { sk, pkg } for this-tab resume */
const SESSION_WRAP_KEY = 'openmail.vault.session.v1'

/**
 * Default auto-lock after idle (minutes).
 * A non-zero default limits exposure if a device is left unattended while the
 * vault (and its decrypted secrets) is unlocked. Users may set 0 = never in
 * Settings, which is still honored.
 */
const DEFAULT_LOCK_MINUTES = 30

export type VaultStatus = 'unavailable' | 'setup' | 'locked' | 'unlocked'

function loadMeta(): VaultMeta | null {
  try {
    const raw = localStorage.getItem(META_KEY)
    if (!raw) return null
    return JSON.parse(raw) as VaultMeta
  } catch {
    return null
  }
}

function saveMeta(meta: VaultMeta) {
  localStorage.setItem(META_KEY, JSON.stringify(meta))
}

function clearSessionWrap() {
  try {
    sessionStorage.removeItem(SESSION_WRAP_KEY)
  } catch {
    /* ignore */
  }
}

export const useVaultStore = defineStore('vault', () => {
  const cryptoOk = isWebCryptoAvailable()
  const meta = ref<VaultMeta | null>(cryptoOk ? loadMeta() : null)
  /** AES key — memory only, never persisted as raw plaintext long-term */
  let vaultKey: CryptoKey | null = null
  const unlocked = ref(false)
  const lastActivityAt = ref(Date.now())
  const lockMinutes = ref(
    (() => {
      const raw = localStorage.getItem(LOCK_MINUTES_KEY)
      if (raw === null || raw === '') {
        return DEFAULT_LOCK_MINUTES
      }
      const n = Number(raw)
      return Number.isFinite(n) && n >= 0 ? n : DEFAULT_LOCK_MINUTES
    })(),
  )
  const busy = ref(false)
  const lastError = ref<string | null>(null)
  /** Shown once after create / enable recovery — user must save offline */
  const pendingRecoveryKey = ref<string | null>(null)
  /** Loaded from vault after unlock (for Settings "view recovery") */
  const savedRecoveryKey = ref<string | null>(null)
  const resuming = ref(false)
  const hasRecovery = computed(() => {
    const m = meta.value as VaultMetaV2 | null
    return Boolean(m && (m as VaultMetaV2).dual && (m as VaultMetaV2).recovery)
  })

  const deviceSecret = ref<string | null>(null)
  const devicePublicId = ref<string | null>(null)

  let idleTimer: ReturnType<typeof setInterval> | null = null

  const status = computed<VaultStatus>(() => {
    if (!cryptoOk) return 'unavailable'
    if (!meta.value) return 'setup'
    if (!unlocked.value) return 'locked'
    return 'unlocked'
  })

  const needsGate = computed(() => status.value === 'setup' || status.value === 'locked')

  function touch() {
    lastActivityAt.value = Date.now()
  }

  function setLockMinutes(m: number) {
    lockMinutes.value = Math.max(0, Math.min(24 * 60, Math.floor(m)))
    localStorage.setItem(LOCK_MINUTES_KEY, String(lockMinutes.value))
    try {
      localStorage.setItem('openmail.vault.lockCustom', '1')
    } catch {
      /* ignore */
    }
  }

  function startIdleWatch() {
    if (idleTimer) return
    idleTimer = setInterval(() => {
      if (!unlocked.value || lockMinutes.value <= 0) return
      const idleMs = Date.now() - lastActivityAt.value
      if (idleMs >= lockMinutes.value * 60_000) {
        lock()
      }
    }, 15_000)
  }

  function stopIdleWatch() {
    if (idleTimer) {
      clearInterval(idleTimer)
      idleTimer = null
    }
  }

  async function persistSessionWrap(raw: Uint8Array | null) {
    if (!raw || raw.length < 16) {
      clearSessionWrap()
      return
    }
    try {
      const { sessionKeyB64, package: pkg } = await wrapDekForSession(raw)
      sessionStorage.setItem(
        SESSION_WRAP_KEY,
        JSON.stringify({ sk: sessionKeyB64, pkg }),
      )
    } catch {
      clearSessionWrap()
    }
  }

  /**
   * Serialize vault writes so concurrent encrypt+setItem cannot finish out of order
   * (e.g. accounts flush + mail flush racing, or double flush on pagehide).
   * Each storage key has its own chain so accounts/mail/2FA can still encrypt in parallel.
   */
  const writeChains = new Map<string, Promise<void>>()

  async function persistBlob(storageKey: string, value: unknown): Promise<void> {
    if (!vaultKey) throw new VaultCryptoError('locked')
    const prev = writeChains.get(storageKey) || Promise.resolve()
    const next = prev
      .catch(() => {
        /* prior write failed; still attempt this one */
      })
      .then(async () => {
        if (!vaultKey) throw new VaultCryptoError('locked')
        // Snapshot JSON now so a later mutation cannot change mid-encrypt payload
        const snapshot = JSON.parse(JSON.stringify(value)) as unknown
        const pkg = await encryptJson(vaultKey, snapshot)
        try {
          localStorage.setItem(storageKey, JSON.stringify(pkg))
        } catch (e) {
          const name = e instanceof DOMException ? e.name : ''
          if (name === 'QuotaExceededError' || name === 'NS_ERROR_DOM_QUOTA_REACHED') {
            console.warn(
              '[openmail] vault persist quota exceeded for',
              storageKey,
              '— prune mail cache or free site data',
            )
          } else {
            console.warn('[openmail] vault persist failed', storageKey, e)
          }
          throw e
        }
      })
    writeChains.set(storageKey, next)
    await next
  }

  async function loadBlob<T>(storageKey: string, fallback: T): Promise<T> {
    if (!vaultKey) return fallback
    // Wait for any in-flight write to this key so we don't read a half-updated package
    const pending = writeChains.get(storageKey)
    if (pending) {
      try {
        await pending
      } catch {
        /* ignore */
      }
    }
    const raw = localStorage.getItem(storageKey)
    if (!raw) return fallback
    try {
      const pkg = JSON.parse(raw) as CipherPackage
      return await decryptJson<T>(vaultKey, pkg)
    } catch (e) {
      console.error('[openmail] encrypted vault blob is corrupt', storageKey, e)
      throw new VaultCryptoError('corrupt_data')
    }
  }

  async function loadSavedRecovery(): Promise<void> {
    savedRecoveryKey.value = null
    if (!vaultKey || !hasRecovery.value) return
    try {
      const rk = await loadBlob<string | null>(RECOVERY_ENC_KEY, null)
      if (rk && typeof rk === 'string' && rk.length > 8) {
        savedRecoveryKey.value = rk
      }
    } catch {
      savedRecoveryKey.value = null
    }
  }

  async function ensureDeviceSecret(): Promise<void> {
    if (!vaultKey) return
    let secret = await loadBlob<string | null>(DEVICE_SECRET_ENC_KEY, null)
    if (!secret || secret.length < 16) {
      secret = generateDeviceSecret()
      await persistBlob(DEVICE_SECRET_ENC_KEY, secret)
    }
    deviceSecret.value = secret
    devicePublicId.value = await devicePublicIdFromSecretB64(secret)
    try {
      setVaultDeviceIdentity(devicePublicId.value, secret)
    } catch {
      /* ignore */
    }
  }

  async function migrateLegacyPlaintext(): Promise<{
    accounts: unknown[]
    twofa: unknown[]
    mailCache: Record<string, unknown>
  }> {
    let accounts: unknown[] = []
    let twofa: unknown[] = []
    let mailCache: Record<string, unknown> = {}

    accounts = await loadBlob(ACCOUNTS_ENC_KEY, [])
    twofa = await loadBlob(TWOFA_ENC_KEY, [])
    mailCache = await loadBlob(MAILCACHE_ENC_KEY, {})

    if ((!Array.isArray(accounts) || !accounts.length) && localStorage.getItem(LEGACY_ACCOUNTS)) {
      try {
        const raw = JSON.parse(localStorage.getItem(LEGACY_ACCOUNTS) || '[]')
        if (Array.isArray(raw) && raw.length) {
          accounts = raw
          await persistBlob(ACCOUNTS_ENC_KEY, accounts)
          localStorage.removeItem(LEGACY_ACCOUNTS)
        }
      } catch {
        /* ignore */
      }
    }
    if ((!Array.isArray(twofa) || !twofa.length) && localStorage.getItem(LEGACY_TWOFA)) {
      try {
        const raw = JSON.parse(localStorage.getItem(LEGACY_TWOFA) || '[]')
        if (Array.isArray(raw) && raw.length) {
          twofa = raw
          await persistBlob(TWOFA_ENC_KEY, twofa)
          localStorage.removeItem(LEGACY_TWOFA)
        }
      } catch {
        /* ignore */
      }
    }
    if ((!mailCache || !Object.keys(mailCache).length) && localStorage.getItem(LEGACY_MAIL)) {
      try {
        const raw = JSON.parse(localStorage.getItem(LEGACY_MAIL) || '{}')
        if (raw && typeof raw === 'object') {
          mailCache = raw as Record<string, unknown>
          await persistBlob(MAILCACHE_ENC_KEY, mailCache)
          localStorage.removeItem(LEGACY_MAIL)
        }
      } catch {
        /* ignore */
      }
    }

    await persistBlob(ACCOUNTS_ENC_KEY, accounts)
    await persistBlob(TWOFA_ENC_KEY, twofa)
    await persistBlob(MAILCACHE_ENC_KEY, mailCache)

    return { accounts, twofa, mailCache }
  }

  async function afterUnlock(raw: Uint8Array | null): Promise<void> {
    unlocked.value = true
    touch()
    startIdleWatch()
    await persistSessionWrap(raw)
    await migrateLegacyPlaintext()
    await ensureDeviceSecret()
    await loadSavedRecovery()
    await registerDeviceWithServer()
  }

  /**
   * Resume unlock from sessionStorage (same tab session).
   * Call once on app boot before showing VaultGate.
   */
  async function tryResumeSession(): Promise<boolean> {
    if (!cryptoOk || unlocked.value) return unlocked.value
    const m = meta.value || loadMeta()
    if (!m) return false
    resuming.value = true
    lastError.value = null
    try {
      const raw = sessionStorage.getItem(SESSION_WRAP_KEY)
      if (!raw) return false
      const parsed = JSON.parse(raw) as { sk?: string; pkg?: CipherPackage }
      if (!parsed?.sk || !parsed?.pkg) {
        clearSessionWrap()
        return false
      }
      const { key, dekRaw } = await unwrapDekFromSession(parsed.sk, parsed.pkg)
      try {
        const marker = await decryptJson<string>(key, (m as VaultMeta).check)
        if (marker !== 'openmail-vault-ok') throw new Error('bad')
      } catch {
        clearSessionWrap()
        return false
      }
      vaultKey = key
      meta.value = m
      await afterUnlock(dekRaw)
      return true
    } catch {
      clearSessionWrap()
      vaultKey = null
      unlocked.value = false
      return false
    } finally {
      resuming.value = false
    }
  }

  async function createVault(password: string): Promise<string> {
    if (!cryptoOk) throw new VaultCryptoError('crypto_unavailable')
    if (password.length < 8) throw new VaultCryptoError('password_too_short')
    busy.value = true
    lastError.value = null
    try {
      const recoveryRaw = generateRecoveryKey()
      const { meta: m, key, recoveryKeyDisplay, dekRaw } = await createDualUnlock(
        password,
        recoveryRaw,
      )
      saveMeta(m)
      meta.value = m
      vaultKey = key
      pendingRecoveryKey.value = recoveryKeyDisplay
      await afterUnlock(dekRaw)
      // Persist recovery inside vault so Settings can show it later
      await persistBlob(RECOVERY_ENC_KEY, recoveryKeyDisplay)
      savedRecoveryKey.value = recoveryKeyDisplay
      return recoveryKeyDisplay
    } catch (e) {
      lastError.value = e instanceof Error ? e.message : 'create_failed'
      throw e
    } finally {
      busy.value = false
    }
  }

  function dismissRecoveryKey() {
    pendingRecoveryKey.value = null
  }

  async function registerDeviceWithServer(): Promise<void> {
    if (!deviceSecret.value || !devicePublicId.value) return
    try {
      const public_id = `vk_${devicePublicId.value.slice(0, 40)}`
      const res = await apiRequest<{ ok?: boolean; public_id?: string }>('/api/device/register', {
        method: 'POST',
        body: {
          public_id,
          secret_b64: deviceSecret.value,
        },
        timeoutMs: 15_000,
      })
      if (res?.public_id && res.public_id.startsWith('vk_')) {
        const hex = res.public_id.slice(3)
        if (hex.length >= 32) {
          devicePublicId.value = hex.length >= 64 ? hex : devicePublicId.value
          setVaultDeviceIdentity(devicePublicId.value, deviceSecret.value)
        }
      }
    } catch (e) {
      console.warn('device register failed', e)
      // Cloud rows are owned by this identity. A transient registration
      // failure must never rotate the persisted secret and orphan those rows.
    }
  }

  async function unlock(password: string): Promise<void> {
    if (!cryptoOk) throw new VaultCryptoError('crypto_unavailable')
    const m = meta.value || loadMeta()
    if (!m) throw new VaultCryptoError('no_vault')
    busy.value = true
    lastError.value = null
    try {
      const { key, dekRaw } = await unlockWithPassword(password, m as VaultMetaV2)
      vaultKey = key
      meta.value = m
      await afterUnlock(dekRaw)
    } catch (e) {
      vaultKey = null
      unlocked.value = false
      if (e instanceof VaultCryptoError && e.message === 'corrupt_data') {
        lastError.value = 'corrupt_data'
        throw e
      }
      lastError.value = 'bad_password'
      throw new VaultCryptoError('bad_password')
    } finally {
      busy.value = false
    }
  }

  async function unlockWithRecovery(recoveryKey: string): Promise<void> {
    if (!cryptoOk) throw new VaultCryptoError('crypto_unavailable')
    const m = (meta.value || loadMeta()) as VaultMetaV2 | null
    if (!m) throw new VaultCryptoError('no_vault')
    busy.value = true
    lastError.value = null
    try {
      const { key, dekRaw } = await unlockWithRecoveryKey(recoveryKey, m)
      vaultKey = key
      meta.value = m
      await afterUnlock(dekRaw)
      // Ensure recovery key is stored for later viewing
      const display = (recoveryKey.replace(/[^0-9a-fA-F]/g, '').match(/.{1,8}/g) || [])
        .join('-')
        .toUpperCase()
      if (display.length >= 16) {
        await persistBlob(RECOVERY_ENC_KEY, display)
        savedRecoveryKey.value = display
      }
    } catch (e) {
      vaultKey = null
      unlocked.value = false
      if (e instanceof VaultCryptoError && e.message === 'corrupt_data') {
        lastError.value = 'corrupt_data'
        throw e
      }
      lastError.value = 'bad_recovery'
      throw new VaultCryptoError('bad_recovery')
    } finally {
      busy.value = false
    }
  }

  async function enableRecovery(password: string): Promise<string> {
    if (!vaultKey || !unlocked.value) throw new VaultCryptoError('locked')
    const m = meta.value as VaultMetaV2 | null
    if (!m) throw new VaultCryptoError('no_vault')
    if (m.dual) throw new VaultCryptoError('already_has_recovery')

    const accounts = await loadAccounts()
    const twofa = await loadTwoFa()
    const mailCache = await loadMailCache()
    let deviceSec: string | null = null
    try {
      deviceSec = await loadBlob<string | null>(DEVICE_SECRET_ENC_KEY, null)
    } catch {
      deviceSec = deviceSecret.value
    }

    await unlockVaultMeta(password, m)

    const recoveryRaw = generateRecoveryKey()
    const { meta: newMeta, key, recoveryKeyDisplay, dekRaw } = await createDualUnlock(
      password,
      recoveryRaw,
    )
    vaultKey = key
    saveMeta(newMeta)
    meta.value = newMeta
    pendingRecoveryKey.value = recoveryKeyDisplay
    await persistSessionWrap(dekRaw)

    await persistBlob(ACCOUNTS_ENC_KEY, accounts)
    await persistBlob(TWOFA_ENC_KEY, twofa)
    await persistBlob(MAILCACHE_ENC_KEY, mailCache)
    if (deviceSec) await persistBlob(DEVICE_SECRET_ENC_KEY, deviceSec)
    await persistBlob(RECOVERY_ENC_KEY, recoveryKeyDisplay)
    savedRecoveryKey.value = recoveryKeyDisplay

    return recoveryKeyDisplay
  }

  function lock() {
    vaultKey = null
    unlocked.value = false
    deviceSecret.value = null
    devicePublicId.value = null
    pendingRecoveryKey.value = null
    savedRecoveryKey.value = null
    clearSessionWrap()
    try {
      setVaultDeviceIdentity(null, null)
    } catch {
      /* ignore */
    }
    try {
      useAccountsStore().clearLocalSecrets()
      useTwoFaStore().clearSecrets()
      useMailCacheStore().clearSecrets()
    } catch {
      /* ignore */
    }
    stopIdleWatch()
  }

  async function saveAccounts(list: unknown[]): Promise<void> {
    touch()
    await persistBlob(ACCOUNTS_ENC_KEY, list)
    try {
      localStorage.removeItem(LEGACY_ACCOUNTS)
    } catch {
      /* ignore */
    }
  }

  async function loadAccounts(): Promise<unknown[]> {
    touch()
    const data = await loadBlob<unknown[]>(ACCOUNTS_ENC_KEY, [])
    return Array.isArray(data) ? data : []
  }

  async function saveTwoFa(list: unknown[]): Promise<void> {
    touch()
    await persistBlob(TWOFA_ENC_KEY, list)
    try {
      localStorage.removeItem(LEGACY_TWOFA)
    } catch {
      /* ignore */
    }
  }

  async function loadTwoFa(): Promise<unknown[]> {
    touch()
    const data = await loadBlob<unknown[]>(TWOFA_ENC_KEY, [])
    return Array.isArray(data) ? data : []
  }

  async function saveMailCache(map: Record<string, unknown>): Promise<void> {
    touch()
    await persistBlob(MAILCACHE_ENC_KEY, map)
    try {
      localStorage.removeItem(LEGACY_MAIL)
    } catch {
      /* ignore */
    }
  }

  async function loadMailCache(): Promise<Record<string, unknown>> {
    touch()
    const data = await loadBlob<Record<string, unknown>>(MAILCACHE_ENC_KEY, {})
    return data && typeof data === 'object' ? data : {}
  }

  async function sealForCloud(payload: unknown): Promise<string> {
    if (!vaultKey) throw new VaultCryptoError('locked')
    const pkg = await encryptJson(vaultKey, payload)
    return btoa(JSON.stringify(pkg))
  }

  async function openFromCloud<T = unknown>(sealed: string): Promise<T> {
    if (!vaultKey) throw new VaultCryptoError('locked')
    try {
      const pkg = JSON.parse(atob(sealed)) as CipherPackage
      return await decryptJson<T>(vaultKey, pkg)
    } catch {
      throw new VaultCryptoError('cloud_decrypt_failed')
    }
  }

  function getVaultKey(): CryptoKey | null {
    return vaultKey
  }

  /**
   * Wipe all browser OpenMail data and reload → fresh setup.
   * Works even when locked / password forgotten (no crypto needed).
   */
  function factoryResetLocal(): void {
    stopIdleWatch()
    vaultKey = null
    unlocked.value = false
    deviceSecret.value = null
    devicePublicId.value = null
    pendingRecoveryKey.value = null
    savedRecoveryKey.value = null
    meta.value = null
    try {
      setVaultDeviceIdentity(null, null)
    } catch {
      /* ignore */
    }
    factoryResetAndReload()
  }

  return {
    cryptoOk,
    meta,
    unlocked,
    status,
    needsGate,
    busy,
    lastError,
    lockMinutes,
    deviceSecret,
    devicePublicId,
    pendingRecoveryKey,
    savedRecoveryKey,
    hasRecovery,
    resuming,
    touch,
    setLockMinutes,
    createVault,
    unlock,
    unlockWithRecovery,
    enableRecovery,
    dismissRecoveryKey,
    lock,
    tryResumeSession,
    factoryResetLocal,
    saveAccounts,
    loadAccounts,
    saveTwoFa,
    loadTwoFa,
    saveMailCache,
    loadMailCache,
    sealForCloud,
    openFromCloud,
    getVaultKey,
    ensureDeviceSecret,
  }
})
