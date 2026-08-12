/** Local mail history for search (no login required). */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { MailMessage } from '@/api/accounts'
import { useVaultStore } from '@/stores/vault'
import { useSettingsStore } from '@/stores/settings'
import {
  computeMailStableId,
  isWeakStableId,
  type MailIdentityInput,
} from '@/utils/mailStableId'

/**
 * Delta row from GET /api/sync/delta (or equivalent).
 * Merge by (email, folder, stable_id); LWW updated_at; prefer body if other empty.
 */
export type DeltaMailItem = {
  email: string
  folder?: string | null
  stable_id?: string | null
  id?: string | null
  subject?: string | null
  from?: string | null
  from_addr?: string | null
  to?: string | string[] | null
  to_addrs?: string[] | null
  date?: string | null
  preview?: string | null
  body_preview?: string | null
  verification_code?: string | null
  body_text?: string | null
  body_html?: string | null
  deleted?: boolean | null
  updated_at?: string | null
  message_id?: string | null
  provider_id?: string | null
  uidvalidity?: number | null
}

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
/**
 * Max messages kept **per folder** (inbox | spam | sent) for one mailbox.
 * Previously a single 200-cap across all folders meant load-more on inbox
 * could push out spam/sent (and My Mails looked almost empty).
 */
/** Per-folder cap: enough history without blowing ~5MB vault localStorage. */
const PER_FOLDER_CAP = 300
/** Absolute safety cap per mailbox address (sum of folders). */
const PER_MAILBOX_CAP = 900

/**
 * Persist-time body limits (chars). Server also slims on fetch; this covers
 * already-cached fat marketing HTML so vault localStorage does not quota-blow.
 */
const PERSIST_HTML_SOFT = 12_000
const PERSIST_HTML_HARD = 48_000
const PERSIST_TEXT_HARD = 16_000
const PERSIST_PREVIEW_HARD = 280

type CacheMap = Record<string, MailMessage[]>

function stripHtmlToText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Client-side body slim aligned with server mail_slim (for legacy fat cache). */
export function slimMessageForPersist(m: MailMessage, aggressive = false): MailMessage {
  let html = m.body_html || ''
  let text = m.body_text || ''
  let preview = m.body_preview || ''

  if (html) {
    html = html
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .replace(/<svg[\s\S]*?<\/svg>/gi, '')
      .replace(/<noscript[\s\S]*?<\/noscript>/gi, '')
      // data: URIs (base64 images) — main quota killer
      .replace(
        /(?:src|href|background|data-src|poster)\s*=\s*(['"])\s*data:[^'"]{200,}\1/gi,
        'src="about:blank"',
      )
      .replace(/url\(\s*['"]?data:[^)]{200,}\)/gi, 'none')
      .replace(/<img\b[^>]*(?:width\s*=\s*['"]?1['"]?|height\s*=\s*['"]?1['"]?)[^>]*\/?>/gi, '')

    if (html.length > PERSIST_HTML_SOFT || aggressive) {
      html = html
        .replace(/\sstyle\s*=\s*(['"]).*?\1/gi, '')
        .replace(/\s(?:class|id)\s*=\s*(['"]).*?\1/gi, '')
        .replace(/(?:src|href)\s*=\s*(['"])[^'"]{800,}\1/gi, 'href="#"')
    }
    if (html.length > PERSIST_HTML_HARD) {
      html = html.slice(0, PERSIST_HTML_HARD) + '\n<!-- openmail:truncated -->'
    }
  }

  if (aggressive) {
    // Drop HTML entirely; keep plain text only
    if (!text && html) text = stripHtmlToText(html)
    html = ''
  }

  if (text.length > PERSIST_TEXT_HARD) {
    text = text.slice(0, PERSIST_TEXT_HARD) + '…'
  }
  if (!text && html) {
    text = stripHtmlToText(html).slice(0, PERSIST_TEXT_HARD)
  }
  if (!preview) {
    preview = (text || stripHtmlToText(html || '')).replace(/\s+/g, ' ').trim()
  }
  if (preview.length > PERSIST_PREVIEW_HARD) {
    preview = preview.slice(0, PERSIST_PREVIEW_HARD - 1) + '…'
  }

  return {
    ...m,
    body_html: html || undefined,
    body_text: text || undefined,
    body_preview: preview || undefined,
  }
}

function load(): CacheMap {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as CacheMap
  } catch {
    /* ignore */
  }
  return {}
}

/**
 * Best-effort parse of message.date → epoch ms.
 * Accepts ISO, RFC2822, mail.com UI strings ("Tuesday, August 04, 2026 at 10:56 AM"),
 * epoch ms/seconds numbers. null if unparseable (sort treats as oldest).
 */
export function parseMessageDateMs(date?: string | number | null): number | null {
  if (date == null || date === '') return null
  if (typeof date === 'number') {
    if (!Number.isFinite(date)) return null
    // Heuristic: seconds vs milliseconds
    const ms = date < 1e12 ? date * 1000 : date
    return Number.isFinite(ms) ? ms : null
  }
  if (typeof date !== 'string') return null
  let s = date.trim()
  if (!s) return null

  // mail.com lightmailer: "Tuesday, August 04, 2026 at 10:56 AM"
  // Date.parse rejects the bare "at"; strip it.
  s = s.replace(/\s+at\s+/gi, ' ')
  // Collapse whitespace
  s = s.replace(/\s+/g, ' ').trim()

  let t = Date.parse(s)
  if (Number.isFinite(t)) return t
  // Some providers strip the comma: "3 Aug 2026 14:30:00 +0000"
  t = Date.parse(s.replace(/^(\w{3})\s+(\d)/, '$1, $2'))
  if (Number.isFinite(t)) return t
  // "August 04, 2026 10:56 AM" without weekday
  const noWeekday = s.replace(
    /^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+/i,
    '',
  )
  if (noWeekday !== s) {
    t = Date.parse(noWeekday)
    if (Number.isFinite(t)) return t
  }
  return null
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

/**
 * Sort helper: **newest first** (desc by date).
 * Undated messages sort as oldest so they land at the bottom / drop under caps.
 * Exported so UI can re-assert order after load-more merges.
 */
export function compareMailDateDesc(
  a: Pick<MailMessage, 'id' | 'date'>,
  b: Pick<MailMessage, 'id' | 'date'>,
): number {
  const da = parseMessageDateMs(a.date)
  const db = parseMessageDateMs(b.date)
  // null date → treat as very old (bottom of newest-first list)
  const va = da ?? Number.NEGATIVE_INFINITY
  const vb = db ?? Number.NEGATIVE_INFINITY
  if (vb !== va) return vb - va
  // Stable: higher numeric id (IMAP UID) first when same timestamp
  const na = Number(a.id)
  const nb = Number(b.id)
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return nb - na
  return String(b.id).localeCompare(String(a.id))
}

/**
 * Keep only the newest `keepCount` messages (by date). Used under storage quota:
 * drop a little of the oldest mail instead of wiping random rows.
 */
export function keepNewestByDate(list: MailMessage[], keepCount: number): MailMessage[] {
  const n = Math.max(0, Math.floor(keepCount))
  if (n <= 0) return []
  if (list.length <= n) return list
  return [...list].sort(compareMailDateDesc).slice(0, n)
}

/** Best-effort read of user retention days (settings store). */
function readRetentionDays(): number {
  try {
    const d = Number(useSettingsStore().s?.retentionDays)
    if (Number.isFinite(d) && d > 0) return Math.min(365, Math.max(1, d))
  } catch {
    /* pinia not ready */
  }
  return 90
}

export const useMailCacheStore = defineStore('mailCache', () => {
  const byEmail = ref<CacheMap>({})
  const vaultHydrated = ref(false)
  let persistTimer: ReturnType<typeof setTimeout> | null = null
  /** Skip watch→persist while we rewrite memory after a quota slim. */
  let suppressPersistWatch = false

  /**
   * Cap each folder to folderCap (newest first by date), then whole mailbox.
   * Always date-ordered: older mail is what gets cut when over cap.
   */
  function capMailboxList(
    list: MailMessage[],
    folderCap = PER_FOLDER_CAP,
    mailboxCap = PER_MAILBOX_CAP,
  ): MailMessage[] {
    const byFolder = new Map<string, MailMessage[]>()
    for (const m of list) {
      const f = normalizeFolder(m.folder || 'inbox')
      const arr = byFolder.get(f) || []
      arr.push(m)
      byFolder.set(f, arr)
    }
    const out: MailMessage[] = []
    for (const [, arr] of byFolder) {
      out.push(...[...arr].sort(compareMailDateDesc).slice(0, folderCap))
    }
    return out.sort(compareMailDateDesc).slice(0, mailboxCap)
  }

  /**
   * Prepare one mailbox list for persist:
   * 1) drop older than maxAgeDays (date-based retention)
   * 2) per-folder / mailbox count caps (newest kept)
   * 3) optional body slim
   */
  function prepareListForPersist(
    list: MailMessage[],
    opts: {
      aggressive?: boolean
      folderCap?: number
      mailboxCap?: number
      /** Soft age window in days; null = use settings retention */
      maxAgeDays?: number | null
    } = {},
  ): MailMessage[] {
    const retention =
      opts.maxAgeDays === null
        ? 0
        : opts.maxAgeDays != null && opts.maxAgeDays > 0
          ? opts.maxAgeDays
          : readRetentionDays()
    let rows = list
    if (retention > 0) {
      rows = pruneByRetention(rows, retention)
    }
    rows = capMailboxList(
      rows,
      opts.folderCap ?? PER_FOLDER_CAP,
      opts.mailboxCap ?? PER_MAILBOX_CAP,
    )
    return rows.map((m) => slimMessageForPersist(m, Boolean(opts.aggressive)))
  }

  function buildPersistMap(opts: {
    aggressive?: boolean
    folderCap?: number
    mailboxCap?: number
    maxAgeDays?: number | null
  } = {}): CacheMap {
    const out: CacheMap = {}
    for (const [k, list] of Object.entries(byEmail.value)) {
      out[k] = prepareListForPersist(list, opts)
    }
    return out
  }

  function isQuotaError(e: unknown): boolean {
    if (!e || typeof e !== 'object') return false
    const name = (e as { name?: string }).name || ''
    return name === 'QuotaExceededError' || name === 'NS_ERROR_DOM_QUOTA_REACHED'
  }

  async function persistEncrypted() {
    const vault = useVaultStore()
    if (vault.status !== 'unlocked') return
    const baseDays = readRetentionDays()
    /**
     * Tiered writes under quota (always prefer deleting **older by date**):
     * 1) retention + body slim
     * 2) drop HTML
     * 3) shorten retention window (keep recent N days only)
     * 4) fewer rows per folder (still newest-first)
     */
    const tiers: Array<{
      aggressive: boolean
      folderCap: number
      mailboxCap: number
      maxAgeDays: number | null
    }> = [
      {
        aggressive: false,
        folderCap: PER_FOLDER_CAP,
        mailboxCap: PER_MAILBOX_CAP,
        maxAgeDays: baseDays,
      },
      {
        aggressive: true,
        folderCap: PER_FOLDER_CAP,
        mailboxCap: PER_MAILBOX_CAP,
        maxAgeDays: baseDays,
      },
      // Keep only last 30 / 14 / 7 days of mail (date-based), then shrink counts
      {
        aggressive: true,
        folderCap: PER_FOLDER_CAP,
        mailboxCap: PER_MAILBOX_CAP,
        maxAgeDays: Math.min(baseDays, 30),
      },
      {
        aggressive: true,
        folderCap: 120,
        mailboxCap: 360,
        maxAgeDays: Math.min(baseDays, 14),
      },
      {
        aggressive: true,
        folderCap: 50,
        mailboxCap: 150,
        maxAgeDays: Math.min(baseDays, 7),
      },
      {
        aggressive: true,
        folderCap: 25,
        mailboxCap: 75,
        maxAgeDays: Math.min(baseDays, 3),
      },
    ]
    let lastErr: unknown
    for (const tier of tiers) {
      try {
        const out = buildPersistMap(tier)
        await vault.saveMailCache(out as Record<string, unknown>)
        try {
          localStorage.removeItem(KEY)
        } catch {
          /* ignore */
        }
        // After a degraded write, apply same date/body policy to memory
        const degraded =
          tier.aggressive ||
          tier.folderCap < PER_FOLDER_CAP ||
          (tier.maxAgeDays != null && tier.maxAgeDays < baseDays)
        if (degraded) {
          suppressPersistWatch = true
          try {
            const next: CacheMap = {}
            for (const [k, list] of Object.entries(byEmail.value)) {
              next[k] = prepareListForPersist(list, tier)
            }
            byEmail.value = next
          } finally {
            queueMicrotask(() => {
              suppressPersistWatch = false
            })
          }
        }
        return
      } catch (e) {
        lastErr = e
        if (!isQuotaError(e)) throw e
        console.warn('[openmail] mail cache quota; drop older mail / slim bodies', {
          maxAgeDays: tier.maxAgeDays,
          folderCap: tier.folderCap,
          aggressive: tier.aggressive,
        })
      }
    }
    throw lastErr
  }

  function persistInBackground(): void {
    void flushPersist().catch((error) => {
      if (isQuotaError(error)) {
        console.warn(
          '[openmail] mail cache still exceeds storage after slimming — free site data or reduce retention',
        )
        return
      }
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
      if (!vaultHydrated.value || suppressPersistWatch) return
      if (persistTimer) clearTimeout(persistTimer)
      // Short debounce; pagehide also flushes so refresh cannot lose mail
      persistTimer = setTimeout(() => {
        persistTimer = null
        void persistEncrypted().catch((error) => {
          if (isQuotaError(error)) {
            console.warn(
              '[openmail] mail cache still exceeds storage after slimming — free site data or reduce retention',
            )
            return
          }
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
    const scoped = !folder
      ? all
      : all.filter((m) => normalizeFolder(m.folder || 'inbox') === normalizeFolder(folder))
    // Always return deduped + newest-first so UI never shows duplicates
    return dedupeAndSortMessages(scoped)
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

  function messageRichness(m: MailMessage): number {
    return (
      (m.body_html?.length || 0) +
      (m.body_text?.length || 0) +
      (m.body_preview?.length || 0) +
      (m.verification_code ? 50 : 0)
    )
  }

  /**
   * Soft content fingerprint — only for clearly-identical list/detail pairs.
   * Requires non-empty subject (≥3 chars) + from + same second. Empty subjects
   * or bulk same-minute newsletters must NOT collapse (that emptied My Mails).
   */
  function contentFingerprint(
    m: Pick<MailMessage, 'id' | 'subject' | 'from' | 'from_address' | 'date' | 'folder'>,
  ): string | null {
    const subj = String(m.subject || '')
      .trim()
      .toLowerCase()
      .replace(/\s+/g, ' ')
    if (subj.length < 3) return null
    const from = String(m.from_address || m.from || '')
      .trim()
      .toLowerCase()
    if (!from) return null
    const dayMs = parseMessageDateMs(m.date)
    if (dayMs == null) return null
    // Same second — tighter than minute to avoid merging real different mails
    const bucket = String(Math.floor(dayMs / 1000))
    const f = normalizeFolder(m.folder)
    return `${f}|${subj}|${from}|${bucket}`
  }

  /** Folder-scoped stable_id key for soft merge (aligns with server UNIQUE). */
  function stableDedupeKey(m: MailMessage): string {
    const f = normalizeFolder(m.folder || 'inbox')
    return `${f}::${computeMailStableId(m as MailIdentityInput)}`
  }

  /**
   * Collapse duplicates by cache key; soft merge by strong content fingerprint,
   * then by stable_id when fingerprint is weak/null (same rules as cloud delta).
   * Newest-first sort.
   */
  function dedupeAndSortMessages(list: MailMessage[]): MailMessage[] {
    const byKey = new Map<string, MailMessage>()
    for (const m of list) {
      if (!m?.id) continue
      const folder = normalizeFolder(m.folder || 'inbox')
      const withFolder: MailMessage = { ...m, folder }
      const k = messageCacheKey(withFolder)
      if (!k) continue
      // Also absorb legacy folder::id when uv-keyed entry arrives
      const legacyKey = `${folder}::${String(m.id).trim()}`
      if (legacyKey !== k && byKey.has(legacyKey) && !byKey.has(k)) {
        const old = byKey.get(legacyKey)!
        byKey.delete(legacyKey)
        byKey.set(k, preferRicherMessage(old, withFolder))
        continue
      }
      const prev = byKey.get(k)
      if (!prev) {
        byKey.set(k, withFolder)
        continue
      }
      byKey.set(k, preferRicherMessage(prev, withFolder))
    }

    // Soft pass 1: only merge when content fingerprint is strong (non-null)
    const byFp = new Map<string, MailMessage>()
    const noFp: MailMessage[] = []
    for (const m of byKey.values()) {
      const fp = contentFingerprint(m)
      if (!fp) {
        noFp.push(m)
        continue
      }
      const prev = byFp.get(fp)
      if (!prev) {
        byFp.set(fp, m)
        continue
      }
      byFp.set(fp, preferRicherMessage(prev, m))
    }

    // Soft pass 2: stable_id merge (provider/message-id strong; weak when no better key).
    // Prefer stable_id when content fingerprint was weak so local fetch + cloud delta
    // share the same identity rules without collapsing unrelated bulk mail.
    const bySid = new Map<string, MailMessage>()
    const leftover: MailMessage[] = []
    for (const m of [...byFp.values(), ...noFp]) {
      const sid = computeMailStableId(m as MailIdentityInput)
      // Skip ultra-weak empty fingerprints (no from/date/subject/size material)
      if (!sid || (isWeakStableId(sid) && !contentFingerprint(m) && approxIdentityEmpty(m))) {
        leftover.push(m)
        continue
      }
      const k = `${normalizeFolder(m.folder || 'inbox')}::${sid}`
      const prev = bySid.get(k)
      if (!prev) {
        bySid.set(k, m)
        continue
      }
      bySid.set(k, preferRicherMessage(prev, m))
    }

    // Always newest-first so list + load-more (older appends visually at bottom) stay consistent
    return [...bySid.values(), ...leftover].sort(compareMailDateDesc)
  }

  /** True when message has no useful identity material for weak stable_id. */
  function approxIdentityEmpty(
    m: Pick<MailMessage, 'subject' | 'from' | 'from_address' | 'date' | 'body_preview'>,
  ): boolean {
    const from = String(m.from_address || m.from || '').trim()
    const subj = String(m.subject || '').trim()
    const date = m.date == null || m.date === '' ? '' : String(m.date).trim()
    const prev = String(m.body_preview || '').trim()
    return !from && !subj && !date && !prev
  }

  function preferRicherMessage(a: MailMessage, b: MailMessage): MailMessage {
    const pickNew = messageRichness(b) >= messageRichness(a)
    const base = pickNew ? b : a
    const other = pickNew ? a : b
    const body_html = base.body_html || other.body_html
    const body_text = base.body_text || other.body_text
    const body_preview = base.body_preview || other.body_preview
    let verification_code: string | null | undefined
    if (Object.prototype.hasOwnProperty.call(base, 'verification_code')) {
      const incoming = base.verification_code
      if (incoming != null && String(incoming).trim() !== '') verification_code = incoming
      else if (Object.prototype.hasOwnProperty.call(other, 'verification_code')) {
        const o = other.verification_code
        verification_code =
          o != null && String(o).trim() !== '' ? o : null
      } else {
        verification_code = null
      }
    } else {
      verification_code = other.verification_code
    }
    // Prefer a parseable date so sort never falls back to id order after merge
    const dateA = parseMessageDateMs(a.date)
    const dateB = parseMessageDateMs(b.date)
    let date = base.date
    if (dateA != null && dateB != null) {
      // Keep the one that matches the newer of the two timestamps' source string
      date = dateA >= dateB ? a.date : b.date
    } else if (dateA != null) {
      date = a.date
    } else if (dateB != null) {
      date = b.date
    }
    return {
      ...other,
      ...base,
      folder: normalizeFolder(base.folder || other.folder || 'inbox'),
      uidvalidity: base.uidvalidity ?? other.uidvalidity,
      date: date ?? base.date ?? other.date,
      body_html,
      body_text,
      body_preview,
      verification_code,
    }
  }

  /**
   * Find an existing map entry by cache key or stable_id (same folder).
   * Used when soft identity matches but provider ids differ across paths.
   */
  function findByStableId(
    map: Map<string, MailMessage>,
    m: MailMessage,
  ): { key: string; msg: MailMessage } | undefined {
    const want = stableDedupeKey(m)
    for (const [k, existing] of map) {
      if (stableDedupeKey(existing) === want) return { key: k, msg: existing }
    }
    return undefined
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
      let old = map.get(k) || (uidvalidity != null ? map.get(legacyKey) : undefined)
      // Soft match by stable_id when hard key misses (cloud / re-fetch id drift)
      if (!old) {
        const hit = findByStableId(map, withFolder)
        if (hit) {
          old = hit.msg
          map.delete(hit.key)
        }
      }
      if (old && legacyKey !== k) map.delete(legacyKey)
      if (old) {
        map.set(k, preferRicherMessage(old, withFolder))
      } else {
        map.set(k, withFolder)
      }
    }
    let merged = dedupeAndSortMessages([...map.values()])
    if (retentionDays != null && retentionDays > 0) {
      merged = pruneByRetention(merged, retentionDays)
    }
    byEmail.value = { ...byEmail.value, [key]: capMailboxList(merged) }
  }

  function parseUpdatedAtMs(v?: string | null): number | null {
    if (v == null || v === '') return null
    const t = Date.parse(String(v))
    return Number.isFinite(t) ? t : null
  }

  /** Map delta DTO → MailMessage for cache storage. */
  function deltaItemToMessage(item: DeltaMailItem, folder: string): MailMessage {
    const sid = String(item.stable_id || '').trim()
    const providerRaw = String(item.provider_id || '').trim()
    let id = String(item.id || '').trim()
    if (!id && sid.startsWith('p:')) id = sid.slice(2)
    if (!id && providerRaw) id = providerRaw
    if (!id && sid) id = sid
    if (!id) id = computeMailStableId(item as MailIdentityInput)

    const from = item.from_addr || item.from || undefined
    const toArr = item.to_addrs || (Array.isArray(item.to) ? item.to : null)
    const to =
      toArr && toArr.length
        ? toArr.join(', ')
        : typeof item.to === 'string'
          ? item.to
          : undefined
    const preview = item.preview || item.body_preview || undefined

    return {
      id,
      subject: item.subject || undefined,
      from,
      from_address: item.from_addr || item.from || undefined,
      to,
      date: item.date ?? null,
      body_preview: preview,
      body_text: item.body_text || undefined,
      body_html: item.body_html || undefined,
      folder,
      verification_code:
        item.verification_code != null && String(item.verification_code).trim() !== ''
          ? String(item.verification_code)
          : item.verification_code === null
            ? null
            : undefined,
      ...(item.uidvalidity != null && Number.isFinite(Number(item.uidvalidity))
        ? { uidvalidity: Number(item.uidvalidity) }
        : {}),
    }
  }

  /**
   * Snapshot fields that affect UI/persist — used to skip no-op vault writes.
   */
  function messageSnapshot(m: MailMessage): string {
    return JSON.stringify({
      id: m.id,
      subject: m.subject || '',
      from: m.from || m.from_address || '',
      to: m.to || '',
      date: m.date || '',
      folder: normalizeFolder(m.folder),
      body_preview: m.body_preview || '',
      body_text: m.body_text || '',
      body_html: m.body_html || '',
      verification_code: m.verification_code ?? null,
      uidvalidity: m.uidvalidity ?? null,
    })
  }

  /**
   * LWW merge of two messages when both carry updated_at; otherwise prefer richer.
   * Always fill empty body fields from the other side.
   */
  function mergeDeltaPair(
    local: MailMessage,
    remote: MailMessage,
    localUpdatedAt?: string | null,
    remoteUpdatedAt?: string | null,
  ): MailMessage {
    const lu = parseUpdatedAtMs(localUpdatedAt)
    const ru = parseUpdatedAtMs(remoteUpdatedAt)
    let base: MailMessage
    let other: MailMessage
    if (lu != null && ru != null) {
      // LWW by updated_at
      if (ru >= lu) {
        base = remote
        other = local
      } else {
        base = local
        other = remote
      }
    } else if (ru != null && lu == null) {
      base = remote
      other = local
    } else {
      // No clocks: prefer richer (existing local-fetch semantics)
      return preferRicherMessage(local, remote)
    }
    // Prefer body if the other side is empty
    return {
      ...other,
      ...base,
      folder: normalizeFolder(base.folder || other.folder || 'inbox'),
      uidvalidity: base.uidvalidity ?? other.uidvalidity,
      body_html: base.body_html || other.body_html,
      body_text: base.body_text || other.body_text,
      body_preview: base.body_preview || other.body_preview,
      verification_code: (() => {
        const b = base.verification_code
        if (b != null && String(b).trim() !== '') return b
        const o = other.verification_code
        if (o != null && String(o).trim() !== '') return o
        if (Object.prototype.hasOwnProperty.call(base, 'verification_code')) {
          return base.verification_code ?? null
        }
        return other.verification_code
      })(),
      subject: base.subject || other.subject,
      from: base.from || other.from,
      from_address: base.from_address || other.from_address,
      to: base.to || other.to,
      date: base.date || other.date,
    }
  }

  /**
   * Merge cloud (or future remote) delta rows into mailCache.
   * - Upsert by (email, folder, stable_id) or existing cache key
   * - LWW by updated_at when present
   * - Prefer body when the other side is empty
   * - deleted → remove from cache
   * - Skip write if nothing changed (avoid vault churn)
   *
   * @returns number of mailboxes that changed
   */
  function mergeDeltaMails(items: DeltaMailItem[]): number {
    if (!items?.length) return 0

    // Track updated_at per cache key so LWW works across delta pages
    const updatedAtByKey = new Map<string, string>()

    // Group by email for fewer byEmail copies
    const byMailbox = new Map<string, DeltaMailItem[]>()
    for (const item of items) {
      const email = String(item.email || '')
        .toLowerCase()
        .trim()
      if (!email) continue
      const arr = byMailbox.get(email) || []
      arr.push(item)
      byMailbox.set(email, arr)
    }

    let mailboxesChanged = 0
    const nextRoot: CacheMap = { ...byEmail.value }

    for (const [email, batch] of byMailbox) {
      const prev = nextRoot[email] || []
      const map = new Map<string, MailMessage>()
      // secondary index: folder::stable_id → cache key
      const sidIndex = new Map<string, string>()

      for (const m of prev) {
        const k = messageCacheKey(m)
        if (!k) continue
        map.set(k, m)
        sidIndex.set(stableDedupeKey(m), k)
      }

      let mailboxDirty = false

      for (const item of batch) {
        const folder = normalizeFolder(item.folder || 'inbox')
        const identity: MailIdentityInput = {
          id: item.id,
          provider_id: item.provider_id,
          message_id: item.message_id,
          subject: item.subject,
          from: item.from,
          from_addr: item.from_addr,
          date: item.date,
          preview: item.preview || item.body_preview,
          body_text: item.body_text,
          body_html: item.body_html,
          folder,
          uidvalidity: item.uidvalidity,
        }
        const sid =
          String(item.stable_id || '').trim() || computeMailStableId(identity)
        const sidKey = `${folder}::${sid}`

        if (item.deleted) {
          // Remove by stable_id index or by id cache key
          const hitKey = sidIndex.get(sidKey)
          if (hitKey && map.has(hitKey)) {
            map.delete(hitKey)
            sidIndex.delete(sidKey)
            mailboxDirty = true
          } else {
            // Fallback: match any row with same stable_id
            for (const [k, m] of [...map.entries()]) {
              if (stableDedupeKey(m) === sidKey) {
                map.delete(k)
                sidIndex.delete(sidKey)
                mailboxDirty = true
              }
            }
          }
          continue
        }

        const remote = deltaItemToMessage({ ...item, stable_id: sid }, folder)
        // Ensure id present for cache key
        if (!remote.id) remote.id = sid

        const cacheKey = messageCacheKey(remote)
        if (!cacheKey) continue

        const existingKey: string | undefined = map.has(cacheKey)
          ? cacheKey
          : sidIndex.get(sidKey)
        const existing = existingKey ? map.get(existingKey) : undefined

        if (!existing || !existingKey) {
          map.set(cacheKey, remote)
          sidIndex.set(sidKey, cacheKey)
          if (item.updated_at) updatedAtByKey.set(`${email}::${cacheKey}`, item.updated_at)
          mailboxDirty = true
          continue
        }

        const localUa = updatedAtByKey.get(`${email}::${existingKey}`)
        const merged = mergeDeltaPair(existing, remote, localUa, item.updated_at)
        // Re-key if cache key changed (e.g. uv upgrade)
        if (existingKey !== cacheKey) {
          map.delete(existingKey)
        }
        const before = messageSnapshot(existing)
        const after = messageSnapshot(merged)
        if (before !== after) {
          map.set(cacheKey, merged)
          sidIndex.set(sidKey, cacheKey)
          if (item.updated_at) updatedAtByKey.set(`${email}::${cacheKey}`, item.updated_at)
          mailboxDirty = true
        }
      }

      if (!mailboxDirty) continue

      const mergedList = capMailboxList(dedupeAndSortMessages([...map.values()]))
      // Final no-op guard vs previous list
      const prevSnap = prev.map(messageSnapshot).join('\n')
      const nextSnap = mergedList.map(messageSnapshot).join('\n')
      if (prevSnap === nextSnap) continue

      if (!mergedList.length) {
        delete nextRoot[email]
      } else {
        nextRoot[email] = mergedList
      }
      mailboxesChanged += 1
    }

    if (mailboxesChanged > 0) {
      byEmail.value = nextRoot
    }
    return mailboxesChanged
  }

  /** Alias for delta merge (docs / other agents). */
  function mergeRemoteMail(items: DeltaMailItem[]): number {
    return mergeDeltaMails(items)
  }

  /** Alias: merge mail items from remote into local cache. */
  function mergeMailItems(items: DeltaMailItem[]): number {
    return mergeDeltaMails(items)
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

  /**
   * Atomically replace one folder's messages (drop old folder rows, write `messages`).
   * Used by 清空重拉 so merge cannot leave stale mails after a short/partial page.
   * Other folders on the same mailbox are preserved.
   */
  function replaceFolder(
    email: string,
    folder: string,
    messages: MailMessage[],
    retentionDays?: number,
  ) {
    const key = email.toLowerCase()
    const f = normalizeFolder(folder)
    const prev = byEmail.value[key] || []
    const kept = prev.filter((m) => normalizeFolder(m.folder || 'inbox') !== f)
    const map = new Map<string, MailMessage>()
    for (const m of messages) {
      if (!m?.id) continue
      // Force target folder so mis-tagged rows cannot escape the replace scope
      const withFolder: MailMessage = {
        ...m,
        folder: f,
        ...(m.uidvalidity != null && Number.isFinite(Number(m.uidvalidity))
          ? { uidvalidity: Number(m.uidvalidity) }
          : {}),
      }
      const k = messageCacheKey(withFolder)
      if (!k) continue
      const prevM = map.get(k)
      map.set(k, prevM ? preferRicherMessage(prevM, withFolder) : withFolder)
    }
    // Dedupe + newest-first for the whole mailbox (kept other folders + new page)
    let nextList = dedupeAndSortMessages([...kept, ...map.values()])
    if (retentionDays != null && retentionDays > 0) {
      nextList = pruneByRetention(nextList, retentionDays)
    }
    if (!nextList.length) {
      const next = { ...byEmail.value }
      delete next[key]
      byEmail.value = next
    } else {
      byEmail.value = {
        ...byEmail.value,
        [key]: capMailboxList(nextList),
      }
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
      const pruned = capMailboxList(pruneByRetention(list, days))
      next[k] = pruned
      if (pruned.length !== list.length) changed = true
    }
    if (changed) byEmail.value = next
  }

  /** Messages `pruneAll(days)` would remove, caps included. */
  function _prunedCount(days: number): number {
    let n = 0
    for (const list of Object.values(byEmail.value)) {
      n += list.length - capMailboxList(pruneByRetention(list, days)).length
    }
    return n
  }

  /**
   * How many extra messages moving retention to `retentionDays` would delete,
   * or null when the count is unknowable because the cache is still encrypted.
   *
   * Returning 0 in that state would be a lie that skips the confirmation: the
   * in-memory map is empty only because it is still encrypted, and the mail
   * gets deleted for real as soon as the vault hydrates and retention applies.
   *
   * The number is a difference against the current window, not a raw prune
   * count, because `pruneAll` also enforces the per-folder and per-mailbox
   * caps. Counting those made a harmless save on an over-cap mailbox announce
   * hundreds of doomed messages that the window had nothing to do with.
   */
  function countPrunedBy(retentionDays: number, currentDays?: number): number | null {
    if (!vaultHydrated.value) return null
    const days = Math.max(0, Number(retentionDays) || 0)
    if (!days) return 0
    const next = _prunedCount(days)
    const current = Math.max(0, Number(currentDays) || 0)
    if (!current) return next
    return Math.max(0, next - _prunedCount(current))
  }

  /** Total cached messages (optional folder / email filter). For UI counts. */
  function totalCount(opts?: { email?: string; folder?: string }): number {
    let n = 0
    const emailKey = opts?.email?.toLowerCase()
    const f = opts?.folder ? normalizeFolder(opts.folder) : null
    for (const [email, list] of Object.entries(byEmail.value)) {
      if (emailKey && email !== emailKey) continue
      for (const m of list) {
        if (f && normalizeFolder(m.folder || 'inbox') !== f) continue
        n += 1
      }
    }
    return n
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
    // Dedupe within each accountEmail then global newest-first
    const byAccount = new Map<string, MailMessage[]>()
    for (const m of out) {
      const e = m.accountEmail
      const list = byAccount.get(e) || []
      list.push(m)
      byAccount.set(e, list)
    }
    const flat: Array<MailMessage & { accountEmail: string }> = []
    for (const [email, list] of byAccount) {
      for (const m of dedupeAndSortMessages(list)) {
        flat.push({ ...m, accountEmail: email })
      }
    }
    return flat.sort(compareMailDateDesc)
  }

  function replaceAll(map: CacheMap, retentionDays?: number) {
    const next: CacheMap = {}
    for (const [k, list] of Object.entries(map)) {
      let rows = list as MailMessage[]
      if (retentionDays != null && retentionDays > 0) {
        rows = pruneByRetention(rows, retentionDays)
      }
      next[k] = capMailboxList(dedupeAndSortMessages(rows))
    }
    byEmail.value = next
  }

  return {
    byEmail,
    vaultHydrated,
    listFor,
    normalizeFolder,
    merge,
    mergeDeltaMails,
    mergeRemoteMail,
    mergeMailItems,
    reparseCodes,
    newestUtcIso,
    oldestUtcIso,
    clearMailbox,
    clearMailboxFolder,
    replaceFolder,
    pruneAll,
    countPrunedBy,
    totalCount,
    search,
    replaceAll,
    hydrateFromVault,
    clearSecrets,
    flushPersist,
  }
})
