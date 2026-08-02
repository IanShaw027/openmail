import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { apiRequest } from '@/api/client'
import { useMailCacheStore } from '@/stores/mailCache'

const KEY = 'openmail.userSettings'

export interface UserSettings {
  /** Days to keep local mail history */
  retentionDays: number
  /** After first full fetch, only pull last N days */
  lookbackDays: number
  /** email -> has completed first full fetch */
  firstFullDone: Record<string, boolean>
  /** email -> last successful fetch ISO time */
  lastFetchAt: Record<string, string>
  batchConcurrency: number
  licenseToken: string
  /** When true, import modal probes mailboxes; when false, format-only check */
  importPrecheck: boolean
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

function load(): UserSettings {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...defaults(), ...JSON.parse(raw) }
  } catch {
    /* ignore */
  }
  return defaults()
}

function defaults(): UserSettings {
  return {
    retentionDays: 90,
    lookbackDays: 3,
    firstFullDone: {},
    lastFetchAt: {},
    batchConcurrency: 10,
    licenseToken: '',
    importPrecheck: true,
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const s = ref<UserSettings>(load())
  const quota = ref<PublicQuota | null>(null)
  const publicConfigLoaded = ref(false)

  watch(
    s,
    (v) => {
      localStorage.setItem(KEY, JSON.stringify(v))
    },
    { deep: true },
  )

  // Prune local mail cache when retention window changes
  watch(
    () => s.value.retentionDays,
    (days) => {
      try {
        useMailCacheStore().pruneAll(days)
      } catch {
        /* pinia may not be ready in rare edge cases */
      }
    },
  )

  function markFetched(email: string, full: boolean) {
    const e = email.toLowerCase()
    // Always store UTC ISO
    s.value.lastFetchAt[e] = new Date().toISOString()
    if (full) s.value.firstFullDone[e] = true
  }

  function needsFullFetch(email: string): boolean {
    return !s.value.firstFullDone[email.toLowerCase()]
  }

  /**
   * Incremental since (UTC ISO): prefer newest cached message time, else lastFetchAt,
   * else lookback window. Full first fetch returns undefined.
   */
  function sinceFor(email: string): string | undefined {
    if (needsFullFetch(email)) return undefined
    const e = email.toLowerCase()
    let bestMs: number | null = null
    try {
      const cachedIso = useMailCacheStore().newestUtcIso(e)
      if (cachedIso) {
        const t = Date.parse(cachedIso)
        if (Number.isFinite(t)) bestMs = t
      }
    } catch {
      /* pinia not ready */
    }
    const last = s.value.lastFetchAt[e]
    if (last) {
      const t = Date.parse(last)
      if (Number.isFinite(t) && (bestMs === null || t > bestMs)) bestMs = t
    }
    if (bestMs != null) {
      // slight overlap (60s) so boundary mails are not missed
      return new Date(bestMs - 60_000).toISOString()
    }
    const days = s.value.lookbackDays || 3
    const d = new Date()
    d.setUTCDate(d.getUTCDate() - days)
    return d.toISOString()
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

  return {
    s,
    quota,
    publicConfigLoaded,
    markFetched,
    needsFullFetch,
    sinceFor,
    applyRetentionNow,
    loadPublicConfig,
    remainingLocalSlots,
    remainingCloudSlots,
  }
})
