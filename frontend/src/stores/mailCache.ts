/** Local mail history for search (no login required). */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { MailMessage } from '@/api/accounts'
import { useVaultStore } from '@/stores/vault'

/**
 * Lightweight client-side OTP re-parse (aligned with backend heuristics).
 * Used to refresh sticky false positives without a network round-trip.
 */
export function extractCodeFromMessage(m: Pick<
  MailMessage,
  'subject' | 'body_preview' | 'body_text' | 'body_html'
>): string | null {
  const subject = m.subject || ''
  const body =
    (m.body_text || '') +
    '\n' +
    (m.body_preview || '') +
    '\n' +
    String(m.body_html || '').replace(/<[^>]+>/g, ' ')
  const blob = `${subject}\n${body}`

  const yearOk = (d: string) => !/^(?:19|20)\d{2}$/.test(d)

  // Keyword-adjacent 4–8 digits
  const nearDigit =
    /(?:验证码|校验码|动态码|确认码|临时验证码|confirmation\s*code|verification\s*code|security\s*code|access\s*code|login\s*code|auth(?:entication)?\s*code|temporary\s+(?:login\s+|verification\s+)?code|one[-\s]?time\s+(?:pass(?:word|code)|code|otp|pin)|(?<![A-Za-z])code(?![A-Za-z])|\botp\b|\bpin\b)[^\d]{0,48}(\d{4,8})/i.exec(
      blob,
    ) ||
    /(\d{4,8})[^\d]{0,24}(?:验证码|校验码|is\s+your\s+code)/i.exec(blob)
  if (nearDigit?.[1] && yearOk(nearDigit[1])) return nearDigit[1]

  // Alphanumeric with a digit (8IX-FGG / M1M-J00)
  const alnum =
    /(?:验证码|校验码|confirmation\s*code|verification\s*code|access\s*code|login\s*code|(?<![A-Za-z])code(?![A-Za-z])|\botp\b)(?:[\s:：#=\-–—]|is|为|：|是){0,24}([A-Za-z0-9]{3,8}(?:-[A-Za-z0-9]{2,8}){0,3})(?![A-Za-z0-9])/i.exec(
      blob,
    )
  if (alnum?.[1] && /\d/.test(alnum[1].replace(/-/g, ''))) {
    const t = alnum[1]
    if (!/^(code|codes|login|token|password)$/i.test(t.replace(/-/g, ''))) return t
  }

  // Subject bare digits only with code-ish subject
  const subjLow = subject.toLowerCase()
  if (
    /code|otp|验证|校验|pin|login|passcode|sign-?in/.test(subjLow) ||
    /验证码|校验码/.test(subject)
  ) {
    const bare = /(?<!\d)(\d{4,8})(?!\d)/.exec(subject)
    if (bare?.[1] && yearOk(bare[1])) return bare[1]
  }

  // Short body 6-digit near code keywords
  if (blob.length < 1200 && /code|otp|验证|校验|login code|passcode|one-?time/i.test(blob)) {
    const m6 = /(?<!\d)(\d{6})(?!\d)/.exec(blob)
    if (m6?.[1] && yearOk(m6[1])) return m6[1]
  }

  return null
}

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
        // Body: keep richer content when the new fetch is a thin list row.
        const body_html = m.body_html || old.body_html
        const body_text = m.body_text || old.body_text
        const body_preview = m.body_preview || old.body_preview
        // Verification code: prefer the new parse when present. If the new
        // message explicitly has no code (null/undefined/''), drop the sticky
        // old value so fixed parsers (and false positives) can clear cache.
        // Only fall back to old when the incoming object omits the field entirely
        // AND we did not re-fetch a full row (thin merge without re-annotate).
        let verification_code: string | null | undefined
        if (Object.prototype.hasOwnProperty.call(m, 'verification_code')) {
          const incoming = m.verification_code
          if (incoming != null && String(incoming).trim() !== '') {
            verification_code = incoming
          } else {
            // Explicit empty/null from server → clear stale false positives
            verification_code = null
          }
        } else {
          verification_code = old.verification_code
        }
        map.set(k, {
          ...old,
          ...withFolder,
          folder,
          uidvalidity: withFolder.uidvalidity ?? old.uidvalidity,
          body_html,
          body_text,
          body_preview,
          verification_code,
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
   * Re-run client OTP heuristics on cached rows (optional folder scope).
   * Clears stale false positives and fills codes when body is present.
   * @returns number of messages whose verification_code changed
   */
  function reparseCodes(email: string, folder?: string | null): number {
    const key = email.toLowerCase()
    const list = byEmail.value[key]
    if (!list?.length) return 0
    const fScope = folder != null && folder !== '' ? normalizeFolder(folder) : null
    let changed = 0
    const next = list.map((m) => {
      if (fScope && normalizeFolder(m.folder) !== fScope) return m
      const parsed = extractCodeFromMessage(m)
      const prev =
        m.verification_code != null && String(m.verification_code).trim() !== ''
          ? String(m.verification_code)
          : null
      const nextCode = parsed
      if (prev === nextCode) return m
      // No body and no subject digits → leave alone (cannot improve)
      const hasText = Boolean(
        (m.body_text && m.body_text.length > 8) ||
          (m.body_html && m.body_html.length > 20) ||
          (m.body_preview && m.body_preview.length > 8) ||
          (m.subject && /code|otp|验证|校验|pin/i.test(m.subject)),
      )
      if (!hasText && nextCode == null && prev != null) {
        // Clear known year-like / pure-letter false positives even without body
        if (
          /^(?:19|20)\d{2}$/.test(prev) ||
          (!/\d/.test(prev) && prev.length >= 4)
        ) {
          changed += 1
          return { ...m, verification_code: null }
        }
        return m
      }
      changed += 1
      return { ...m, verification_code: nextCode }
    })
    if (changed) {
      byEmail.value = { ...byEmail.value, [key]: next }
    }
    return changed
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
    reparseCodes,
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
