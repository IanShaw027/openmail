/** Local-first 2FA (TOTP/HOTP) store. */

import { defineStore } from 'pinia'
import { useVaultStore } from '@/stores/vault'
import { computed, ref, watch } from 'vue'
import {
  type TotpAlgorithm,
  type TotpEntryDraft,
  type TotpType,
  buildOtpauthUri,
  generateCode,
  normalizeSecret,
  parseOtpauthUri,
  parseSecretOrUri,
  remainingSeconds,
} from '@/utils/totp'

const STORAGE_KEY = 'openmail.twofa.v1'
/** Prefer encrypted vault; plaintext key only for legacy migration. */

export interface TwoFaEntry {
  id: string
  /** Service / issuer display name */
  issuer: string
  /** Account label (email or username) */
  label: string
  /** Base32 secret */
  secret: string
  type: TotpType
  algorithm: TotpAlgorithm
  digits: number
  period: number
  counter: number
  /** Optional logo key or data URL */
  logo?: string
  /** Bound MailAccount.id */
  accountId?: string
  /** Bound mailbox email (for display / search) */
  accountEmail?: string
  /** Manual sort position (lower = earlier). Persisted with vault. */
  sortOrder: number
  createdAt: number
  updatedAt: number
}

function uid(): string {
  return `2fa_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function load(): TwoFaEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const list = JSON.parse(raw) as TwoFaEntry[]
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

function draftToEntry(d: TotpEntryDraft, partial?: Partial<TwoFaEntry>): TwoFaEntry {
  const now = Date.now()
  return {
    id: partial?.id || uid(),
    issuer: d.issuer || partial?.issuer || '',
    label: d.label || partial?.label || 'Account',
    secret: normalizeSecret(d.secret),
    type: d.type,
    algorithm: d.algorithm,
    digits: d.digits,
    period: d.period,
    counter: d.counter,
    logo: partial?.logo,
    accountId: partial?.accountId,
    accountEmail: partial?.accountEmail,
    sortOrder: typeof partial?.sortOrder === 'number' ? partial.sortOrder : now,
    createdAt: partial?.createdAt || now,
    updatedAt: now,
  }
}

/** Ensure every entry has a stable sortOrder (legacy vault rows may omit it). */
function normalizeEntries(list: TwoFaEntry[]): TwoFaEntry[] {
  const withOrder = list.map((e, i) => {
    const so = (e as TwoFaEntry & { sortOrder?: number }).sortOrder
    if (typeof so === 'number' && Number.isFinite(so)) return e as TwoFaEntry
    // Prefer createdAt then index so old issuer-sorted feel is not random
    return {
      ...e,
      sortOrder: typeof e.createdAt === 'number' ? e.createdAt + i : i,
    }
  })
  return withOrder
}

export const useTwoFaStore = defineStore('twofa', () => {
  const entries = ref<TwoFaEntry[]>([])
  const nowTick = ref(Date.now())
  let timer: ReturnType<typeof setInterval> | null = null
  /** Ref-count so Console + 2FA page can share one ticker */
  let tickerRefs = 0
  const vaultHydrated = ref(false)
  let persistTimer: ReturnType<typeof setTimeout> | null = null

  async function persistEncrypted() {
    const vault = useVaultStore()
    if (vault.status !== 'unlocked') return
    await vault.saveTwoFa(entries.value)
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
  }

  function persistInBackground(): void {
    void flushPersist().catch((error) => {
      console.warn('[openmail] 2FA vault persist failed', error)
    })
  }

  /** Cancel debounce and write vault immediately (pagehide). */
  async function flushPersist(): Promise<void> {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = null
    }
    if (!vaultHydrated.value) return
    await persistEncrypted()
  }

  watch(
    entries,
    () => {
      if (!vaultHydrated.value) return
      if (persistTimer) clearTimeout(persistTimer)
      persistTimer = setTimeout(() => {
        persistTimer = null
        void persistEncrypted().catch((error) => {
          console.warn('[openmail] 2FA vault persist failed', error)
        })
      }, 80)
    },
    { deep: true },
  )

  async function hydrateFromVault(): Promise<void> {
    try {
      const vault = useVaultStore()
      if (vault.status !== 'unlocked') {
        entries.value = []
        vaultHydrated.value = false
        return
      }
      const raw = await vault.loadTwoFa()
      if (Array.isArray(raw) && raw.length) {
        entries.value = normalizeEntries(raw as TwoFaEntry[])
      } else {
        const legacy = normalizeEntries(load())
        entries.value = legacy
        if (legacy.length) await vault.saveTwoFa(legacy)
        try {
          localStorage.removeItem(STORAGE_KEY)
        } catch {
          /* ignore */
        }
      }
      vaultHydrated.value = true
    } catch {
      entries.value = []
      vaultHydrated.value = false
    }
  }

  function clearSecrets() {
    entries.value = []
    vaultHydrated.value = false
  }

  function startTicker() {
    tickerRefs += 1
    if (timer) return
    timer = setInterval(() => {
      nowTick.value = Date.now()
    }, 1000)
  }

  function stopTicker() {
    tickerRefs = Math.max(0, tickerRefs - 1)
    if (tickerRefs > 0) return
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  const sorted = computed(() =>
    [...entries.value].sort((a, b) => {
      const oa = a.sortOrder ?? a.createdAt ?? 0
      const ob = b.sortOrder ?? b.createdAt ?? 0
      if (oa !== ob) return oa - ob
      const ia = (a.issuer || a.label).toLowerCase()
      const ib = (b.issuer || b.label).toLowerCase()
      return ia.localeCompare(ib)
    }),
  )

  function nextSortOrder(): number {
    let max = 0
    for (const e of entries.value) {
      const o = e.sortOrder ?? e.createdAt ?? 0
      if (o > max) max = o
    }
    return max + 1
  }

  function addFromDraft(d: TotpEntryDraft, extra?: Partial<TwoFaEntry>): TwoFaEntry {
    const entry = draftToEntry(d, {
      ...extra,
      sortOrder: extra?.sortOrder ?? nextSortOrder(),
    })
    entries.value = [...entries.value, entry]
    persistInBackground()
    return entry
  }

  /**
   * Reorder within the current view: move `fromId` to `toId`'s slot among `viewIds`.
   * Non-view entries keep their relative positions. Persists via entries watch → vault.
   */
  function reorder(fromId: string, toId: string, viewIds?: string[]) {
    if (fromId === toId) return
    const full = sorted.value.map((e) => e.id)
    const view =
      viewIds && viewIds.length
        ? viewIds.filter((id) => full.includes(id))
        : full
    const from = view.indexOf(fromId)
    const to = view.indexOf(toId)
    if (from < 0 || to < 0) return
    const nextView = [...view]
    const [item] = nextView.splice(from, 1)
    if (!item) return
    nextView.splice(to, 0, item)

    // Merge: walk full order; when hitting a view id, take next from nextView
    let vi = 0
    const merged: string[] = []
    const viewSet = new Set(view)
    for (const id of full) {
      if (viewSet.has(id)) {
        const nid = nextView[vi++]
        if (nid) merged.push(nid)
      } else {
        merged.push(id)
      }
    }
    while (vi < nextView.length) {
      const nid = nextView[vi++]
      if (nid) merged.push(nid)
    }

    const map = new Map(entries.value.map((e) => [e.id, e]))
    const now = Date.now()
    entries.value = merged
      .map((id, i) => {
        const e = map.get(id)
        if (!e) return null
        return {
          ...e,
          sortOrder: i,
          updatedAt: e.id === fromId ? now : e.updatedAt,
        }
      })
      .filter(Boolean) as TwoFaEntry[]
    persistInBackground()
  }

  function addFromUri(uri: string, extra?: Partial<TwoFaEntry>): TwoFaEntry | null {
    const d = parseOtpauthUri(uri) || parseSecretOrUri(uri)
    if (!d) return null
    return addFromDraft(d, extra)
  }

  /** Import multiple otpauth:// lines (or mixed secrets). */
  function importText(text: string): { ok: number; fail: number } {
    let ok = 0
    let fail = 0
    for (const line of text.split(/\r?\n/)) {
      const t = line.trim()
      if (!t || t.startsWith('#')) continue
      const d = parseOtpauthUri(t) || parseSecretOrUri(t)
      if (!d) {
        fail += 1
        continue
      }
      // defer vault write to one flush at end
      const entry = draftToEntry(d, { sortOrder: nextSortOrder() })
      entries.value = [...entries.value, entry]
      ok += 1
    }
    if (ok) persistInBackground()
    return { ok, fail }
  }

  function update(id: string, patch: Partial<TwoFaEntry>) {
    const i = entries.value.findIndex((e) => e.id === id)
    if (i < 0) return
    const next = { ...entries.value[i]!, ...patch, id, updatedAt: Date.now() }
    if (patch.secret) next.secret = normalizeSecret(patch.secret)
    const list = [...entries.value]
    list[i] = next
    entries.value = list
    persistInBackground()
  }

  function remove(id: string) {
    entries.value = entries.value.filter((e) => e.id !== id)
    persistInBackground()
  }

  function bindAccount(id: string, accountId: string | undefined, accountEmail?: string) {
    update(id, {
      accountId: accountId || undefined,
      accountEmail: accountEmail || undefined,
    })
  }

  function unbindAccount(id: string) {
    update(id, { accountId: undefined, accountEmail: undefined })
  }

  function findByAccountId(accountId: string): TwoFaEntry | undefined {
    return entries.value.find((e) => e.accountId === accountId)
  }

  function findByAccountEmail(email: string): TwoFaEntry | undefined {
    const e = email.toLowerCase()
    return entries.value.find(
      (x) => x.accountEmail?.toLowerCase() === e || x.label.toLowerCase() === e,
    )
  }

  function codeFor(entry: TwoFaEntry): string {
    try {
      return generateCode(entry, nowTick.value)
    } catch {
      return '······'
    }
  }

  function remainingFor(entry: TwoFaEntry): number {
    if (entry.type === 'hotp') return 0
    return remainingSeconds(entry.period, nowTick.value)
  }

  function exportText(): string {
    return sorted.value.map((e) => buildOtpauthUri(e)).join('\n')
  }

  function exportJson(): string {
    return JSON.stringify(
      {
        v: 1,
        exportedAt: Date.now(),
        entries: sorted.value.map((e) => ({
          issuer: e.issuer,
          label: e.label,
          secret: e.secret,
          type: e.type,
          algorithm: e.algorithm,
          digits: e.digits,
          period: e.period,
          counter: e.counter,
          logo: e.logo,
          accountEmail: e.accountEmail,
          sortOrder: e.sortOrder,
          uri: buildOtpauthUri(e),
        })),
      },
      null,
      2,
    )
  }

  function replaceAll(list: TwoFaEntry[]) {
    entries.value = normalizeEntries(list)
    persistInBackground()
  }

  return {
    entries,
    sorted,
    nowTick,
    vaultHydrated,
    startTicker,
    stopTicker,
    addFromDraft,
    addFromUri,
    importText,
    update,
    remove,
    reorder,
    bindAccount,
    unbindAccount,
    findByAccountId,
    findByAccountEmail,
    codeFor,
    remainingFor,
    exportText,
    exportJson,
    replaceAll,
    hydrateFromVault,
    clearSecrets,
    flushPersist,
  }
})
