import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiError } from '@/api/client'
import {
  pullSyncDelta,
  type SyncDeltaAccount,
  type SyncDeltaMail,
} from '@/api/sync'
import { useAccountsStore } from '@/stores/accounts'
import { useMailCacheStore, type DeltaMailItem } from '@/stores/mailCache'
import { formatSyncAck, getSyncAck, parseSyncAck, setSyncAck } from '@/utils/syncAck'

const MAX_PAGES = 20
const DEFAULT_LIMIT = 200
const POLL_MS = 3 * 60 * 1000

function isSoftFail(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 410 || e.status === 501)
}

function mapMailToDelta(m: SyncDeltaMail): DeltaMailItem | null {
  const email = String(m.email || '')
    .toLowerCase()
    .trim()
  if (!email) return null
  return {
    email,
    folder: m.folder,
    stable_id: m.stable_id,
    id: m.id,
    subject: m.subject,
    from: m.from,
    from_addr: m.from_addr,
    to: m.to,
    to_addrs: m.to_addrs,
    date: m.date,
    preview: m.preview,
    body_preview: m.body_preview,
    verification_code: m.verification_code,
    body_text: m.body_text,
    body_html: m.body_html,
    deleted: m.deleted,
    updated_at: m.updated_at,
    message_id: m.message_id,
    provider_id: m.provider_id,
    uidvalidity: m.uidvalidity,
  }
}

function patchAccountsFromDelta(rows: SyncDeltaAccount[] | undefined): void {
  if (!rows?.length) return
  try {
    const accounts = useAccountsStore()
    for (const row of rows) {
      const code =
        row.latest_verification_code != null && String(row.latest_verification_code).trim() !== ''
          ? String(row.latest_verification_code).trim()
          : null
      if (!code) continue

      const sid = row.id != null ? String(row.id) : ''
      const emailKey = row.email ? String(row.email).toLowerCase().trim() : ''

      const match = (a: { serverId?: string; id: string; email: string }) => {
        if (sid && (a.serverId === sid || a.id === sid)) return true
        if (emailKey && a.email.toLowerCase() === emailKey) return true
        return false
      }

      for (let i = 0; i < accounts.localAccounts.length; i++) {
        const a = accounts.localAccounts[i]!
        if (!match(a)) continue
        if (a.latestCode === code) continue
        accounts.localAccounts[i] = {
          ...a,
          latestCode: code,
          updatedAt: Date.now(),
        }
      }
      for (let i = 0; i < accounts.serverAccounts.length; i++) {
        const a = accounts.serverAccounts[i]!
        if (!match(a)) continue
        if (a.latestCode === code) continue
        accounts.serverAccounts[i] = {
          ...a,
          latestCode: code,
          updatedAt: Date.now(),
        }
      }
    }
  } catch (e) {
    console.warn('[openmail] patch accounts from delta failed', e)
  }
}

export const useCloudSyncStore = defineStore('cloudSync', () => {
  const pulling = ref(false)
  const lastError = ref<string | null>(null)
  const lastPullAt = ref<number | null>(null)
  const lastMerged = ref(0)

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let pullChain: Promise<{ merged: number; done: boolean }> | null = null

  /**
   * Pull cloud mail delta pages, merge into mailCache, then ack after persist.
   * Soft-fails (404/410/501) with console.warn only — backend may not ship yet.
   */
  async function pullCloudMailDelta(): Promise<{ merged: number; done: boolean }> {
    // Serialize concurrent unlock + interval pulls
    if (pullChain) return pullChain

    pullChain = (async () => {
      pulling.value = true
      lastError.value = null
      let totalMerged = 0
      let done = false

      try {
        const mailCache = useMailCacheStore()
        // Ack is "updated_at\tid" keyset — never wall-clock server_time alone
        const ack0 = parseSyncAck(getSyncAck())
        let since: string | null = ack0.since
        let sinceId: string | null = ack0.sinceId
        /** Last consumed mail row keyset (for durable ack). */
        let lastMailSince: string | null = since
        let lastMailId: string | null = sinceId

        for (let page = 0; page < MAX_PAGES; page++) {
          let res
          try {
            res = await pullSyncDelta({
              since,
              sinceId,
              limit: DEFAULT_LIMIT,
              includeBody: true,
            })
          } catch (e) {
            if (isSoftFail(e)) {
              console.warn('[openmail] cloud sync delta unavailable', e)
              lastError.value = e instanceof ApiError ? `HTTP ${e.status}` : 'unavailable'
              return { merged: totalMerged, done: false }
            }
            throw e
          }

          const mails = res.mails || []
          const items: DeltaMailItem[] = []
          for (const m of mails) {
            const mapped = mapMailToDelta(m)
            if (mapped) items.push(mapped)
          }

          if (items.length) {
            totalMerged += mailCache.mergeDeltaMails(items)
          }
          patchAccountsFromDelta(res.accounts)

          // Advance durable cursor from last mail on this page (keyset)
          const last = mails[mails.length - 1]
          if (last?.updated_at) {
            lastMailSince = String(last.updated_at)
            lastMailId =
              res.next_since_id != null && String(res.next_since_id)
                ? String(res.next_since_id)
                : last.id != null
                  ? String(last.id)
                  : null
            since = lastMailSince
            sinceId = lastMailId
          }

          if (!res.has_more) {
            done = true
            break
          }

          if (!last?.updated_at) {
            console.warn('[openmail] delta has_more but no mail cursor; stopping')
            break
          }
        }

        const cursorMoved =
          lastMailSince != null &&
          (lastMailSince !== ack0.since || lastMailId !== ack0.sinceId)

        if (cursorMoved && lastMailSince != null) {
          try {
            if (totalMerged > 0) {
              await mailCache.flushPersist()
            }
            setSyncAck(formatSyncAck(lastMailSince, lastMailId))
          } catch (e) {
            console.warn('[openmail] mail cache flush after delta failed', e)
          }
        }

        lastMerged.value = totalMerged
        lastPullAt.value = Date.now()
        return { merged: totalMerged, done }
      } catch (e) {
        lastError.value = e instanceof Error ? e.message : String(e)
        console.warn('[openmail] pullCloudMailDelta failed', e)
        return { merged: totalMerged, done: false }
      } finally {
        pulling.value = false
        pullChain = null
      }
    })()

    return pullChain
  }

  function startCloudDeltaPolling(): void {
    stopCloudDeltaPolling()
    pollTimer = setInterval(() => {
      void pullCloudMailDelta()
    }, POLL_MS)
  }

  function stopCloudDeltaPolling(): void {
    if (pollTimer != null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    pulling,
    lastError,
    lastPullAt,
    lastMerged,
    pullCloudMailDelta,
    startCloudDeltaPolling,
    stopCloudDeltaPolling,
  }
})
