/** Local mail history for search (no login required). */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { MailMessage } from '@/api/accounts'
import { useVaultStore } from '@/stores/vault'

const KEY = 'openmail.mailCache.v1'
const PER_MAILBOX_CAP = 200

type CacheMap = Record<string, MailMessage[]>

function load(): CacheMap {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as CacheMap
  } catch {
    /* ignore */
  }
  return {}
}

/** Best-effort parse of message.date; null if unparseable. */
export function parseMessageDateMs(date?: string | null): number | null {
  if (!date || typeof date !== 'string') return null
  const t = Date.parse(date)
  return Number.isFinite(t) ? t : null
}

/**
 * Drop messages older than retentionDays.
 * Unparseable dates are kept (cannot prove they are stale).
 */
export function pruneByRetention(
  list: MailMessage[],
  retentionDays: number,
  nowMs: number = Date.now(),
): MailMessage[] {
  const days = Math.max(0, Number(retentionDays) || 0)
  if (!days) return list
  const cutoff = nowMs - days * 86_400_000
  return list.filter((m) => {
    const t = parseMessageDateMs(m.date)
    if (t === null) return true
    return t >= cutoff
  })
}

export const useMailCacheStore = defineStore('mailCache', () => {
  const byEmail = ref<CacheMap>({})
  const vaultHydrated = ref(false)
  let persistTimer: ReturnType<typeof setTimeout> | null = null

  async function persistEncrypted() {
    const vault = useVaultStore()
    if (vault.status !== 'unlocked') return
    const out: CacheMap = {}
    for (const [k, list] of Object.entries(byEmail.value)) {
      out[k] = list.slice(0, PER_MAILBOX_CAP)
    }
    await vault.saveMailCache(out as Record<string, unknown>)
    try {
      localStorage.removeItem(KEY)
    } catch {
      /* ignore */
    }
  }

  function persistInBackground(): void {
    void flushPersist().catch((error) => {
      console.warn('[openmail] mail cache persist failed', error)
    })
  }

  /** Cancel debounce and write vault immediately (pagehide / after fetch). */
  async function flushPersist(): Promise<void> {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = null
    }
    if (!vaultHydrated.value) return
    await persistEncrypted()
  }

  watch(
    byEmail,
    () => {
      if (!vaultHydrated.value) return
      if (persistTimer) clearTimeout(persistTimer)
      // Short debounce; pagehide also flushes so refresh cannot lose mail
      persistTimer = setTimeout(() => {
        persistTimer = null
        void persistEncrypted().catch((error) => {
          console.warn('[openmail] mail cache persist failed', error)
        })
      }, 80)
    },
    { deep: true },
  )

  async function hydrateFromVault(): Promise<void> {
    try {
      const vault = useVaultStore()
      if (vault.status !== 'unlocked') {
        byEmail.value = {}
        vaultHydrated.value = false
        return
      }
      const raw = await vault.loadMailCache()
      if (raw && typeof raw === 'object' && Object.keys(raw).length) {
        byEmail.value = raw as CacheMap
      } else {
        const legacy = load()
        byEmail.value = legacy
        if (Object.keys(legacy).length) await vault.saveMailCache(legacy as Record<string, unknown>)
        try {
          localStorage.removeItem(KEY)
        } catch {
          /* ignore */
        }
      }
      vaultHydrated.value = true
    } catch {
      byEmail.value = {}
      vaultHydrated.value = false
    }
  }

  function clearSecrets() {
    byEmail.value = {}
    vaultHydrated.value = false
  }

  function listFor(email: string, folder?: string): MailMessage[] {
    const all = byEmail.value[email.toLowerCase()] || []
    if (!folder) return all
    const f = normalizeFolder(folder)
    return all.filter((m) => normalizeFolder(m.folder || 'inbox') === f)
  }

  /** Normalize provider folder labels to inbox | spam | sent. */
  function normalizeFolder(folder?: string | null): string {
    const f = (folder || 'inbox').toLowerCase()
    if (f === 'junk' || f === 'spam' || f === 'junkemail') return 'spam'
    if (f === 'sent' || f === 'sentitems' || f === 'sent mail' || f === '已发送') return 'sent'
    return 'inbox'
  }

  /**
   * Dedup key: IMAP UIDs are only unique within (mailbox, UIDVALIDITY).
   * Always scope by normalized folder; include uidvalidity when present so a
   * rebuilt mailbox does not merge new UIDs into old cached rows.
   */
  function messageCacheKey(
    m: Pick<MailMessage, 'id' | 'folder' | 'uidvalidity'>,
  ): string {
    const id = String(m.id || '').trim()
    if (!id) return ''
    const f = normalizeFolder(m.folder)
    const uv =
      m.uidvalidity != null && Number.isFinite(Number(m.uidvalidity))
        ? String(Number(m.uidvalidity))
        : ''
    return uv ? `${f}::v${uv}::${id}` : `${f}::${id}`
  }

  function merge(email: string, messages: MailMessage[], retentionDays?: number) {
    const key = email.toLowerCase()
    const prev = byEmail.value[key] || []
    const map = new Map<string, MailMessage>()
    for (const m of prev) {
      const k = messageCacheKey(m)
      if (k) map.set(k, m)
    }
    // Folders for which this merge batch carries UIDVALIDITY — drop legacy
    // folder::id keys that lack uv so upgraded IMAP caches do not duplicate.
    const foldersWithUv = new Set<string>()
    for (const m of messages) {
      if (
        m?.id &&
        m.uidvalidity != null &&
        Number.isFinite(Number(m.uidvalidity))
      ) {
        foldersWithUv.add(normalizeFolder(m.folder || 'inbox'))
      }
    }
    if (foldersWithUv.size > 0) {
      for (const [k, m] of [...map.entries()]) {
        const f = normalizeFolder(m.folder)
        if (!foldersWithUv.has(f)) continue
        const hasUv =
          m.uidvalidity != null && Number.isFinite(Number(m.uidvalidity))
        if (!hasUv) map.delete(k)
      }
    }
    for (const m of messages) {
      if (!m?.id) continue
      // Prefer richer body when merging; keep folder tag for inbox/spam/sent tabs
      const folder = normalizeFolder(m.folder || 'inbox')
      const uidvalidity =
        m.uidvalidity != null && Number.isFinite(Number(m.uidvalidity))
          ? Number(m.uidvalidity)
          : undefined
      const withFolder = {
        ...m,
        folder,
        ...(uidvalidity != null ? { uidvalidity } : {}),
      }
      const k = messageCacheKey(withFolder)
      if (!k) continue
      // Also absorb a legacy same-folder same-id entry when re-keying with uv
      const legacyKey = `${folder}::${String(m.id).trim()}`
      const old = map.get(k) || (uidvalidity != null ? map.get(legacyKey) : undefined)
      if (old && legacyKey !== k) map.delete(legacyKey)
      if (old) {
        map.set(k, {
          ...old,
          ...withFolder,
          folder,
          uidvalidity: withFolder.uidvalidity ?? old.uidvalidity,
          body_html: m.body_html || old.body_html,
          body_text: m.body_text || old.body_text,
          body_preview: m.body_preview || old.body_preview,
          verification_code: m.verification_code || old.verification_code,
        })
      } else {
        map.set(k, withFolder)
      }
    }
    let merged = [...map.values()].sort((a, b) => {
      const da = parseMessageDateMs(a.date) ?? 0
      const db = parseMessageDateMs(b.date) ?? 0
      return db - da
    })
    if (retentionDays != null && retentionDays > 0) {
      merged = pruneByRetention(merged, retentionDays)
    }
    byEmail.value = { ...byEmail.value, [key]: merged.slice(0, PER_MAILBOX_CAP) }
  }

  /**
   * Newest cached message date as UTC ISO, for incremental fetch.
   * When `folder` is set, only that folder is considered (prevents sent dates
   * from advancing the inbox since cursor).
   */
  function newestUtcIso(email: string, folder?: string): string | undefined {
    const list = listFor(email, folder)
    let best: number | null = null
    for (const m of list) {
      const t = parseMessageDateMs(m.date)
      if (t == null) continue
      if (best === null || t > best) best = t
    }
    return best == null ? undefined : new Date(best).toISOString()
  }

  /** Oldest cached message date as UTC ISO (optionally folder-scoped), for load-older. */
  function oldestUtcIso(email: string, folder?: string): string | undefined {
    const list = listFor(email, folder)
    let best: number | null = null
    for (const m of list) {
      const t = parseMessageDateMs(m.date)
      if (t == null) continue
      if (best === null || t < best) best = t
    }
    return best == null ? undefined : new Date(best).toISOString()
  }

  /** Replace mailbox cache (used by full wipe). */
  function clearMailbox(email: string) {
    const key = email.toLowerCase()
    if (!byEmail.value[key]?.length) return
    const next = { ...byEmail.value }
    delete next[key]
    byEmail.value = next
    persistInBackground()
  }

  /**
   * Clear one folder only (inbox | spam | sent). Other folders stay cached.
   * Used by Clear & refetch so switching tabs still has history.
   */
  function clearMailboxFolder(email: string, folder: string) {
    const key = email.toLowerCase()
    const list = byEmail.value[key]
    if (!list?.length) return
    const f = normalizeFolder(folder)
    const kept = list.filter((m) => normalizeFolder(m.folder || 'inbox') !== f)
    if (kept.length === list.length) return
    if (!kept.length) {
      const next = { ...byEmail.value }
      delete next[key]
      byEmail.value = next
    } else {
      byEmail.value = { ...byEmail.value, [key]: kept }
    }
    persistInBackground()
  }

  /** Prune all mailboxes to retention window (call on settings load / change). */
  function pruneAll(retentionDays: number) {
    const days = Math.max(0, Number(retentionDays) || 0)
    if (!days) return
    const next: CacheMap = {}
    let changed = false
    for (const [k, list] of Object.entries(byEmail.value)) {
      const pruned = pruneByRetention(list, days).slice(0, PER_MAILBOX_CAP)
      next[k] = pruned
      if (pruned.length !== list.length) changed = true
    }
    if (changed) byEmail.value = next
  }

  function search(opts: {
    q?: string
    from?: string
    subject?: string
    hasCode?: boolean
    email?: string
    /** Only messages for these mailbox addresses (lowercase) */
    emails?: string[]
    /** inbox | spam | sent — omit for all folders */
    folder?: string
  }): Array<MailMessage & { accountEmail: string }> {
    const q = (opts.q || '').toLowerCase()
    const from = (opts.from || '').toLowerCase()
    const subject = (opts.subject || '').toLowerCase()
    const folderFilter = opts.folder ? normalizeFolder(opts.folder) : null
    const emailSet =
      opts.emails && opts.emails.length
        ? new Set(opts.emails.map((e) => e.toLowerCase()))
        : null
    const out: Array<MailMessage & { accountEmail: string }> = []
    for (const [email, list] of Object.entries(byEmail.value)) {
      if (opts.email && email !== opts.email.toLowerCase()) continue
      if (emailSet && !emailSet.has(email)) continue
      for (const m of list) {
        if (folderFilter && normalizeFolder(m.folder || 'inbox') !== folderFilter) continue
        if (opts.hasCode && !m.verification_code) continue
        if (from && !(m.from || m.from_address || '').toLowerCase().includes(from)) continue
        if (subject && !(m.subject || '').toLowerCase().includes(subject)) continue
        if (q) {
          const blob = [
            m.subject,
            m.from,
            m.from_address,
            m.body_preview,
            m.body_text,
            m.verification_code,
          ]
            .join(' ')
            .toLowerCase()
          if (!blob.includes(q)) continue
        }
        out.push({ ...m, accountEmail: email })
      }
    }
    return out.sort((a, b) => {
      const da = parseMessageDateMs(a.date) ?? 0
      const db = parseMessageDateMs(b.date) ?? 0
      return db - da
    })
  }

  function replaceAll(map: CacheMap, retentionDays?: number) {
    if (retentionDays != null && retentionDays > 0) {
      const next: CacheMap = {}
      for (const [k, list] of Object.entries(map)) {
        next[k] = pruneByRetention(list, retentionDays).slice(0, PER_MAILBOX_CAP)
      }
      byEmail.value = next
      return
    }
    byEmail.value = map
  }

  return {
    byEmail,
    vaultHydrated,
    listFor,
    normalizeFolder,
    merge,
    newestUtcIso,
    oldestUtcIso,
    clearMailbox,
    clearMailboxFolder,
    pruneAll,
    search,
    replaceAll,
    hydrateFromVault,
    clearSecrets,
    flushPersist,
  }
})
