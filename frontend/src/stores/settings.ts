import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { apiRequest } from '@/api/client'
import { useMailCacheStore } from '@/stores/mailCache'
import { setDisplayTheme, setDisplayTimeZone } from '@/utils/displayPrefs'
import { applyTheme, bindSystemThemeListener, normalizeTheme, type ThemeMode } from '@/utils/theme'
import { DEFAULT_TIMEZONE, normalizeTimeZone } from '@/utils/timezones'
import { clearSyncAck, getSyncAck, setSyncAck } from '@/utils/syncAck'

const KEY = 'openmail.userSettings'
/** Cap map keys so CF temp churn cannot blow localStorage (origin ~5MB shared). */
const MAP_CAP = 400

export interface UserSettings {
  /** Days to keep local mail history */
  retentionDays: number
  /** After first full fetch, only pull last N days */
  lookbackDays: number
  /**
   * email or email::folder -> has completed first full fetch for that scope.
   * Folder-scoped keys prevent inbox completion from suppressing spam/sent full pulls,
   * and keep since cursors from crossing folders.
   */
  firstFullDone: Record<string, boolean>
  /**
   * @deprecated Not used by sinceFor anymore; kept briefly for migration then pruned.
   * Do not grow this map.
   */
  lastFetchAt?: Record<string, string>
  batchConcurrency: number
  licenseToken: string
  /** When true, import modal probes mailboxes; when false, format-only check */
  importPrecheck: boolean
  /**
   * IANA timezone for display (e.g. Asia/Shanghai), or `browser` to follow OS.
   * Default: Asia/Shanghai.
   */
  timeZone: string
  /** UI theme: system | light | dark */
  theme: ThemeMode
}

/** Normalize folder to inbox|spam|sent for fetch-map keys. */
export function fetchMapFolder(folder?: string | null): string {
  const f = (folder || 'inbox').toLowerCase()
  if (f === 'junk' || f === 'spam' || f === 'junkemail') return 'spam'
  if (f === 'sent' || f === 'sentitems' || f === 'sent mail' || f === '已发送') return 'sent'
  return 'inbox'
}

/** Composite key: email::folder (email-only legacy keys still recognized as inbox). */
export function fetchMapKey(email: string, folder?: string | null): string {
  const e = email.toLowerCase().trim()
  if (!e) return ''
  return `${e}::${fetchMapFolder(folder)}`
}

/** Subset of /api/config/public used for client-side quota gates. */
export interface PublicQuota {
  licensed: boolean
  max_local_accounts: number | null
  max_cloud_accounts: number | null
  max_poll_per_hour: number | null
  poll_used_hour?: number
  cloud_used?: number
  mail_retention_days?: number
}

export interface QuotaDefaults {
  max_local_accounts: number
  max_cloud_accounts: number
  max_poll_per_hour: number
}

function defaults(): UserSettings {
  return {
    retentionDays: 90,
    lookbackDays: 3,
    firstFullDone: {},
    batchConcurrency: 10,
    licenseToken: '',
    importPrecheck: true,
    timeZone: DEFAULT_TIMEZONE,
    theme: 'system',
  }
}

/** Keep only the most recently touched keys (insertion order in modern engines ≈ write order). */
function capRecord<T>(map: Record<string, T> | undefined | null, cap = MAP_CAP): Record<string, T> {
  if (!map || typeof map !== 'object') return {}
  const keys = Object.keys(map)
  if (keys.length <= cap) return { ...map }
  const keep = keys.slice(-cap)
  const out: Record<string, T> = {}
  for (const k of keep) out[k] = map[k]!
  return out
}

function sanitize(raw: Partial<UserSettings> | null | undefined): UserSettings {
  const d = defaults()
  if (!raw || typeof raw !== 'object') return d
  return {
    retentionDays: Math.max(1, Number(raw.retentionDays) || d.retentionDays),
    lookbackDays: Math.max(1, Number(raw.lookbackDays) || d.lookbackDays),
    firstFullDone: capRecord(raw.firstFullDone || {}),
    // Drop lastFetchAt from disk — no longer needed and was the main bloat vector
    batchConcurrency: Math.max(1, Math.min(50, Number(raw.batchConcurrency) || d.batchConcurrency)),
    licenseToken: typeof raw.licenseToken === 'string' ? raw.licenseToken : '',
    importPrecheck: raw.importPrecheck !== false,
    timeZone: normalizeTimeZone(raw.timeZone ?? d.timeZone),
    theme: normalizeTheme(raw.theme ?? d.theme),
  }
}

function load(): UserSettings {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return sanitize(JSON.parse(raw) as Partial<UserSettings>)
  } catch {
    /* ignore corrupt / private mode */
  }
  return defaults()
}

/** Payload actually written to localStorage (minimal). */
function persistPayload(v: UserSettings): string {
  const clean = sanitize(v)
  return JSON.stringify({
    retentionDays: clean.retentionDays,
    lookbackDays: clean.lookbackDays,
    firstFullDone: clean.firstFullDone,
    batchConcurrency: clean.batchConcurrency,
    licenseToken: clean.licenseToken,
    importPrecheck: clean.importPrecheck,
    timeZone: clean.timeZone,
    theme: clean.theme,
  })
}

/**
 * Best-effort localStorage write. On quota failure: drop maps, clear other
 * non-critical openmail keys, then retry once with minimal payload.
 */
function safeSetItem(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value)
    return true
  } catch (e) {
    const name = e instanceof DOMException ? e.name : ''
    const quota =
      name === 'QuotaExceededError' ||
      name === 'NS_ERROR_DOM_QUOTA_REACHED' ||
      (e as { code?: number })?.code === 22
    if (!quota) {
      console.warn('[openmail] settings persist failed', e)
      return false
    }
  }

  // 1) Retry with empty firstFullDone
  try {
    const minimal = JSON.parse(value) as UserSettings
    minimal.firstFullDone = {}
    localStorage.setItem(key, JSON.stringify(minimal))
    console.warn('[openmail] settings: pruned firstFullDone after storage quota')
    return true
  } catch {
    /* continue */
  }

  // 2) Free space: drop legacy plaintext caches (vault holds secrets now)
  try {
    const drop = [
      'openmail.mailCache.v1',
      'openmail.localAccounts',
      'openmail.twofa',
      'openmail.noteTemplates',
    ]
    for (const k of drop) {
      try {
        localStorage.removeItem(k)
      } catch {
        /* ignore */
      }
    }
    const minimal: UserSettings = {
      retentionDays: 90,
      lookbackDays: 3,
      firstFullDone: {},
      batchConcurrency: 10,
      licenseToken: '',
      importPrecheck: true,
      timeZone: DEFAULT_TIMEZONE,
      theme: 'system',
    }
    // preserve license / display prefs if present in original payload
    try {
      const parsed = JSON.parse(value) as Partial<UserSettings>
      if (parsed.licenseToken) minimal.licenseToken = parsed.licenseToken
      if (parsed.retentionDays) minimal.retentionDays = parsed.retentionDays
      if (parsed.lookbackDays) minimal.lookbackDays = parsed.lookbackDays
      if (parsed.timeZone) minimal.timeZone = normalizeTimeZone(parsed.timeZone)
      if (parsed.theme) minimal.theme = normalizeTheme(parsed.theme)
    } catch {
      /* ignore */
    }
    localStorage.setItem(key, JSON.stringify(minimal))
    console.warn('[openmail] settings: cleared legacy caches after storage quota')
    return true
  } catch (e2) {
    console.warn('[openmail] settings: could not recover storage quota', e2)
    return false
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const s = ref<UserSettings>(load())
  const quota = ref<PublicQuota | null>(null)
  const quotaDefaults = ref<QuotaDefaults | null>(null)
  const publicConfigLoaded = ref(false)
  let persistTimer: ReturnType<typeof setTimeout> | null = null

  function persistNow() {
    // Keep in-memory maps capped so markFetched cannot grow forever
    s.value.firstFullDone = capRecord(s.value.firstFullDone)
    if (s.value.lastFetchAt) {
      // Strip deprecated field from live state after first sanitize
      delete s.value.lastFetchAt
    }
    safeSetItem(KEY, persistPayload(s.value))
  }

  function schedulePersist() {
    if (persistTimer) clearTimeout(persistTimer)
    persistTimer = setTimeout(() => {
      persistTimer = null
      persistNow()
    }, 80)
  }

  /** Cancel debounce and write settings immediately (pagehide). */
  function flushPersist() {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = null
    }
    persistNow()
  }

  watch(s, () => schedulePersist(), { deep: true })

  // Theme + timezone: push to runtime helpers / CSS on change and at init
  watch(
    () => s.value.theme,
    (mode) => {
      setDisplayTheme(mode)
      applyTheme(mode)
    },
    { immediate: true },
  )
  watch(
    () => s.value.timeZone,
    (tz) => setDisplayTimeZone(tz),
    { immediate: true },
  )
  bindSystemThemeListener(() => s.value.theme)

  // Retention is applied only through applyRetentionNow(). Pruning must never be
  // a side effect of assigning retentionDays: an input bound straight to this
  // value passes through every intermediate number the user types, so editing
  // 90 into 10 would delete everything older than 1 day the moment "1" landed.

  function markFetched(email: string, full: boolean, folder?: string | null) {
    const k = fetchMapKey(email, folder)
    if (!k) return
    // lastFetchAt intentionally not stored (unused by sinceFor; bloat risk)
    if (full) {
      // Touch key so capRecord keeps recently used scopes
      const next = { ...s.value.firstFullDone }
      delete next[k]
      next[k] = true
      // Drop legacy email-only key for this mailbox so it does not shadow folder scopes
      const e = email.toLowerCase().trim()
      if (e && e in next) delete next[e]
      s.value.firstFullDone = capRecord(next)
    }
  }

  /**
   * Drop firstFullDone keys not belonging to known emails.
   * Keys are email or email::folder.
   */
  function pruneFetchMaps(knownEmails: string[]) {
    const allow = new Set(knownEmails.map((e) => e.toLowerCase().trim()).filter(Boolean))
    if (!allow.size) {
      // Still cap if empty list (don't wipe everything on empty store init)
      s.value.firstFullDone = capRecord(s.value.firstFullDone)
      return
    }
    const next: Record<string, boolean> = {}
    for (const [k, v] of Object.entries(s.value.firstFullDone || {})) {
      if (!v) continue
      const emailPart = k.includes('::') ? k.split('::')[0]! : k
      if (allow.has(emailPart)) next[k] = true
    }
    s.value.firstFullDone = capRecord(next)
    if (s.value.lastFetchAt) delete s.value.lastFetchAt
  }

  function needsFullFetch(email: string, folder?: string | null): boolean {
    const map = s.value.firstFullDone || {}
    const k = fetchMapKey(email, folder)
    if (k && map[k]) return false
    // Legacy: email-only key only counts for inbox (old clients marked whole mailbox done)
    const e = email.toLowerCase().trim()
    if (fetchMapFolder(folder) === 'inbox' && map[e]) return false
    return true
  }

  /**
   * Incremental since (UTC ISO) for silent/background poll.
   *
   * Prefer **newest cached message date in this folder only**. Never use
   * lastFetchAt (wall-clock) or cross-folder newest (sent would skip inbox).
   *
   * Falls back to lookbackDays when cache has no parseable dates.
   * Full first fetch returns undefined (caller should use forceRecent / full).
   */
  function sinceFor(email: string, folder?: string | null): string | undefined {
    if (needsFullFetch(email, folder)) return undefined
    const e = email.toLowerCase()
    const f = fetchMapFolder(folder)
    let mailMs: number | null = null
    try {
      const cachedIso = useMailCacheStore().newestUtcIso(e, f)
      if (cachedIso) {
        const t = Date.parse(cachedIso)
        if (Number.isFinite(t)) mailMs = t
      }
    } catch {
      /* pinia not ready */
    }
    const days = Math.max(1, s.value.lookbackDays || 3)
    const lookbackMs = Date.now() - days * 86_400_000
    let sinceMs: number
    if (mailMs != null) {
      sinceMs = Math.min(mailMs - 120_000, Date.now())
      if (sinceMs < lookbackMs) sinceMs = lookbackMs
    } else {
      sinceMs = lookbackMs
    }
    return new Date(sinceMs).toISOString()
  }

  function applyRetentionNow() {
    try {
      useMailCacheStore().pruneAll(s.value.retentionDays)
    } catch {
      /* ignore */
    }
  }

  async function loadPublicConfig(): Promise<PublicQuota | null> {
    try {
      const data = await apiRequest<{
        quota?: Partial<PublicQuota>
        licensed?: boolean
        quota_defaults?: Partial<QuotaDefaults>
        device_admission?: string
      }>('/api/config/public')
      const snap: PublicQuota = {
        licensed: Boolean(data.licensed ?? data.quota?.licensed),
        max_local_accounts:
          data.quota?.max_local_accounts === undefined
            ? null
            : (data.quota.max_local_accounts as number | null),
        max_cloud_accounts:
          data.quota?.max_cloud_accounts === undefined
            ? null
            : (data.quota.max_cloud_accounts as number | null),
        max_poll_per_hour:
          data.quota?.max_poll_per_hour === undefined
            ? null
            : (data.quota.max_poll_per_hour as number | null),
        poll_used_hour: data.quota?.poll_used_hour,
        cloud_used: data.quota?.cloud_used,
        mail_retention_days: data.quota?.mail_retention_days,
      }
      quota.value = snap
      const d = data.quota_defaults
      if (d && typeof d.max_local_accounts === 'number') {
        quotaDefaults.value = {
          max_local_accounts: d.max_local_accounts,
          max_cloud_accounts: d.max_cloud_accounts ?? 0,
          max_poll_per_hour: d.max_poll_per_hour ?? 0,
        }
      }
      publicConfigLoaded.value = true
      return snap
    } catch {
      publicConfigLoaded.value = true
      return quota.value
    }
  }

  /**
   * How many new local accounts can still be added under the unlicensed cap.
   * null = unlimited (licensed or unknown / no cap).
   */
  function remainingLocalSlots(currentLocalCount: number): number | null {
    const q = quota.value
    if (!q || q.licensed) return null
    const max = q.max_local_accounts
    if (max == null || max < 0) return null
    return Math.max(0, max - currentLocalCount)
  }

  function remainingCloudSlots(currentCloudCount: number): number | null {
    const q = quota.value
    if (!q || q.licensed) return null
    const max = q.max_cloud_accounts
    if (max == null || max < 0) return null
    return Math.max(0, max - currentCloudCount)
  }

  // One-shot sanitize of any bloated legacy payload already in memory
  persistNow()

  return {
    s,
    quota,
    quotaDefaults,
    publicConfigLoaded,
    markFetched,
    pruneFetchMaps,
    needsFullFetch,
    sinceFor,
    applyRetentionNow,
    loadPublicConfig,
    remainingLocalSlots,
    remainingCloudSlots,
    /** Cloud delta water-mark (localStorage openmail.syncAck.v1). */
    getSyncAck,
    setSyncAck,
    clearSyncAck,
    persistNow,
    flushPersist,
  }
})
