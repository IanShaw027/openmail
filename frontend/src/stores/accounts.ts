import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import type { ImportResult, MailAccount } from '@/types/account'
import { createAccountFromParsed, parseImportText } from '@/utils/importParse'
import {
  createServerAccount,
  deleteServerAccount,
  listServerAccounts,
  toCreateBody,
  updateServerAccount,
} from '@/api/accounts'
import { ApiError } from '@/api/client'
import { i18n } from '@/i18n'
import { useVaultStore } from '@/stores/vault'
import {
  LOCAL_ACCOUNTS_KEY,
  loadAccountsPlain,
  mapServerToLocal,
  normalizeLocalAccounts,
} from './accounts/mappers'
import {
  fillMissingAccountUiMeta,
  patchAccountUiMeta,
  removeAccountUiMetaIfUnused,
} from '@/utils/accountUiMeta'
import { exportCredentialsTxt, rebuildRawLine } from '@/utils/exportImport'
import { useMailCacheStore } from '@/stores/mailCache'

function tt(key: string, params?: Record<string, unknown>): string {
  // vue-i18n Composer typings are strict about message keys; runtime keys are fine.
  return String((i18n.global as { t: (k: string, p?: Record<string, unknown>) => unknown }).t(key, params))
}

type ServerAccountPatch = Parameters<typeof updateServerAccount>[1]

/** Payload sealed into cloud credential envelope (must match openFromCloud consumers). */
function sealPayloadFromAccount(acc: MailAccount): Record<string, unknown> {
  return {
    email: acc.email,
    type: acc.type,
    password: acc.password,
    refreshToken: acc.refreshToken,
    clientId: acc.clientId,
    apiUrl: acc.apiUrl,
    apiKey: acc.apiKey,
    apiAuthStyle: acc.apiAuthStyle,
    imapHost: acc.imapHost,
    imapPort: acc.imapPort,
    smtpHost: acc.smtpHost,
    smtpPort: acc.smtpPort,
    authCode: acc.authCode,
    sessionCookies: acc.sessionCookies,
    sessionMeta: acc.sessionMeta,
  }
}

/** Drop mail cache for emails no longer present on local or cloud lists. */
function clearMailCacheIfUnused(emails: string[], stillHave: string[]) {
  const still = new Set(stillHave.map((e) => e.toLowerCase()))
  try {
    const mailCache = useMailCacheStore()
    for (const email of emails) {
      if (!still.has(email.toLowerCase())) {
        mailCache.clearMailbox(email)
      }
    }
    void mailCache.flushPersist()
  } catch {
    /* mail cache store may be unavailable during early boot */
  }
}

export const useAccountsStore = defineStore('accounts', () => {
  /** Local-only rows — secrets live in encrypted vault when unlocked */
  const localAccounts = ref<MailAccount[]>([])
  /** Device cloud rows (server); secrets not returned by API */
  const serverAccounts = ref<MailAccount[]>([])
  const cloudLoading = ref(false)
  const vaultHydrated = ref(false)
  /** Last dual-write / cloud sync failure message (UI may watch). */
  const lastCloudSyncError = ref<string | null>(null)
  let persistTimer: ReturnType<typeof setTimeout> | null = null

  const selectedId = ref<string | null>(null)
  const selectedIds = ref<Set<string>>(new Set())
  const filterQuery = ref('')
  const filterStatus = ref<'all' | 'ok' | 'error' | 'unknown'>('all')
  const filterStorage = ref<'all' | 'local' | 'server' | 'linked'>('all')
  const filterBrand = ref<string>('all')
  const filterGroup = ref<string>(
    typeof localStorage !== 'undefined'
      ? localStorage.getItem('openmail.activeGroup') || 'all'
      : 'all',
  )
  const importGroupId = ref<string>(
    typeof localStorage !== 'undefined'
      ? localStorage.getItem('openmail.importGroup') || 'default'
      : 'default',
  )
  const lastImport = ref<ImportResult | null>(null)

  watch(filterGroup, (v) => {
    try {
      localStorage.setItem('openmail.activeGroup', v)
    } catch {
      /* ignore */
    }
  })
  watch(importGroupId, (v) => {
    try {
      localStorage.setItem('openmail.importGroup', v)
    } catch {
      /* ignore */
    }
  })

  async function persistLocalEncrypted() {
    const vault = useVaultStore()
    if (vault.status !== 'unlocked') return
    await vault.saveAccounts(localAccounts.value)
    // wipe legacy plaintext if still present
    try {
      localStorage.removeItem(LOCAL_ACCOUNTS_KEY)
    } catch {
      /* ignore */
    }
  }

  function persistInBackground(): void {
    void flushPersist().catch((error) => {
      console.warn('[openmail] account vault persist failed', error)
    })
  }

  /** Cancel debounce and write vault immediately (pagehide / critical patches). */
  async function flushPersist(): Promise<void> {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = null
    }
    if (!vaultHydrated.value) return
    await persistLocalEncrypted()
  }

  watch(
    localAccounts,
    () => {
      if (!vaultHydrated.value) return
      if (persistTimer) clearTimeout(persistTimer)
      // Short debounce for bulk edits; always flush on pagehide as well
      persistTimer = setTimeout(() => {
        persistTimer = null
        void persistLocalEncrypted().catch((error) => {
          console.warn('[openmail] account vault persist failed', error)
        })
      }, 80)
    },
    { deep: true },
  )

  /**
   * Align rawLine with current secrets (import lines stay stale after edits).
   * Returns number of rows changed.
   */
  function rebuildAllRawLines(): number {
    let n = 0
    localAccounts.value = localAccounts.value.map((a) => {
      const next = rebuildRawLine(a)
      if (next && next !== a.rawLine) {
        n += 1
        return { ...a, rawLine: next, updatedAt: Date.now() }
      }
      return a
    })
    return n
  }

  /** Call after vault unlock: load encrypted accounts (or migrate legacy plaintext). */
  async function hydrateFromVault(): Promise<void> {
    try {
      const vault = useVaultStore()
      if (vault.status !== 'unlocked') {
        localAccounts.value = []
        vaultHydrated.value = false
        return
      }
      const raw = await vault.loadAccounts()
      if (Array.isArray(raw) && raw.length) {
        localAccounts.value = normalizeLocalAccounts(raw as MailAccount[]).map(
          fillMissingAccountUiMeta,
        )
      } else {
        // one-shot legacy
        const legacy = loadAccountsPlain().map(fillMissingAccountUiMeta)
        localAccounts.value = legacy
        if (legacy.length) await vault.saveAccounts(legacy)
        try {
          localStorage.removeItem(LOCAL_ACCOUNTS_KEY)
        } catch {
          /* ignore */
        }
      }
      // One-shot migration: fix rawLine that still holds pre-edit passwords
      const fixed = rebuildAllRawLines()
      vaultHydrated.value = true
      if (fixed > 0) await persistLocalEncrypted()
    } catch {
      localAccounts.value = []
      vaultHydrated.value = false
    }
  }

  function clearLocalSecrets() {
    localAccounts.value = []
    vaultHydrated.value = false
  }

  const accounts = computed(() => {
    // Prefer local row if same email exists both places
    const localEmails = new Set(localAccounts.value.map((a) => a.email.toLowerCase()))
    const cloudOnly = serverAccounts.value.filter(
      (a) => !localEmails.has(a.email.toLowerCase()),
    )
    return [...localAccounts.value, ...cloudOnly]
  })

  /** Flat list for UI: API source rows first, then their mailbox children, then others. */
  const accountsFlat = computed(() => {
    const all = accounts.value
    const childrenByParent = new Map<string, MailAccount[]>()
    const roots: MailAccount[] = []
    for (const a of all) {
      if (a.parentApiId) {
        const list = childrenByParent.get(a.parentApiId) || []
        list.push(a)
        childrenByParent.set(a.parentApiId, list)
      } else {
        roots.push(a)
      }
    }
    const out: MailAccount[] = []
    for (const r of roots) {
      out.push(r)
      const kids = childrenByParent.get(r.id) || []
      kids.sort((a, b) => a.email.localeCompare(b.email))
      out.push(...kids)
    }
    // orphans
    for (const [pid, kids] of childrenByParent) {
      if (!roots.some((r) => r.id === pid)) out.push(...kids)
    }
    return out
  })

  const selected = computed(
    () => accounts.value.find((a) => a.id === selectedId.value) ?? null,
  )

  const stats = computed(() => {
    // Count concrete mailboxes + non-api rows (not API source shells)
    const countable = accounts.value.filter((a) => !a.isApiSource)
    const all = countable.length
    const ready = countable.filter((a) => a.status === 'ok').length
    const cached = countable.filter((a) => Boolean(a.latestCode)).length
    const error = countable.filter((a) => a.status === 'error').length
    const unknown = countable.filter((a) => a.status === 'unknown').length
    const local = localAccounts.value.filter((a) => !a.isApiSource).length
    const cloud = serverAccounts.value.filter((a) => !a.isApiSource).length
    return { all, ready, cached, error, unknown, local, cloud }
  })

  /**
   * Sync child mailbox rows under an HttpApi source after fetch discovers addresses.
   */
  function syncApiMailboxes(sourceId: string, mailboxes: string[]) {
    const src =
      localAccounts.value.find((a) => a.id === sourceId) ||
      serverAccounts.value.find((a) => a.id === sourceId)
    if (!src || src.type !== 'http_api' || !src.apiUrl) return
    const unique = [
      ...new Set(
        mailboxes.map((e) => e.trim().toLowerCase()).filter((e) => e.includes('@')),
      ),
    ]
    const childBrand =
      src.brand === 'cf_temp' ||
      String(src.apiUrl || '').toLowerCase().includes('workers.dev') ||
      String(src.email || '').toLowerCase().startsWith('api@')
        ? 'cf_temp'
        : src.brand === 'duckmail' || String(src.apiUrl || '').toLowerCase().includes('duck')
          ? 'duckmail'
          : 'http_api'
    const li = localAccounts.value.findIndex((a) => a.id === sourceId)
    if (li >= 0) {
      localAccounts.value[li] = {
        ...localAccounts.value[li]!,
        isApiSource: true,
        brand: childBrand === 'cf_temp' ? 'cf_temp' : localAccounts.value[li]!.brand || childBrand,
        apiMailboxes: unique,
        updatedAt: Date.now(),
      }
    }
    const existingKids = localAccounts.value.filter((a) => a.parentApiId === sourceId)
    const have = new Set(existingKids.map((a) => a.email.toLowerCase()))
    const now = Date.now()
    // Re-attach orphan top-level rows that match a discovered temp address
    for (const email of unique) {
      if (have.has(email)) continue
      const orphanIdx = localAccounts.value.findIndex(
        (a) =>
          a.email.toLowerCase() === email &&
          !a.parentApiId &&
          !a.isApiSource &&
          a.type === 'http_api' &&
          a.id !== sourceId,
      )
      if (orphanIdx >= 0) {
        const o = localAccounts.value[orphanIdx]!
        localAccounts.value[orphanIdx] = {
          ...o,
          parentApiId: sourceId,
          isApiSource: false,
          brand: childBrand,
          apiUrl: src.apiUrl,
          apiKey: src.apiKey || src.password || o.apiKey || o.password,
          password: src.apiKey || src.password || o.password,
          apiAuthStyle: src.apiAuthStyle || o.apiAuthStyle || 'auto',
          groupId: src.groupId || o.groupId || 'default',
          note: o.note || tt('console.apiTempMailboxNote'),
          updatedAt: now,
        }
        have.add(email)
        continue
      }
      localAccounts.value.push({
        id: `mb_${sourceId}_${email.replace(/[^a-z0-9]/gi, '_').slice(0, 48)}`,
        email,
        type: 'http_api',
        brand: childBrand,
        storage: 'local',
        status: 'unknown',
        apiUrl: src.apiUrl,
        apiKey: src.apiKey || src.password,
        apiAuthStyle: src.apiAuthStyle || 'auto',
        parentApiId: sourceId,
        password: src.apiKey || src.password,
        proxy: src.proxy,
        groupId: src.groupId || 'default',
        tags: [],
        rawLine: src.apiKey
          ? `${email}----${src.apiKey}----${src.apiUrl}`
          : `${email}----${src.apiUrl}`,
        note: tt('console.apiTempMailboxNote'),
        createdAt: now,
        updatedAt: now,
      })
      have.add(email)
    }
    persistInBackground()
  }

  const filtered = computed(() => {
    const q = filterQuery.value.trim().toLowerCase()
    return accountsFlat.value.filter((a) => {
      if (filterStatus.value !== 'all' && a.status !== filterStatus.value) return false
      if (filterStorage.value === 'local' && a.storage !== 'local') return false
      if (filterStorage.value === 'server' && a.storage !== 'server') return false
      if (filterBrand.value !== 'all' && (a.brand || 'other') !== filterBrand.value) return false
      if (filterGroup.value !== 'all' && (a.groupId || 'default') !== filterGroup.value) return false
      if (q) {
        const hay = [
          a.email,
          a.note || '',
          a.lastError || '',
          a.latestCode || '',
          a.apiUrl || '',
          ...(a.apiMailboxes || []),
          ...(a.tags || []),
        ]
          .join(' ')
          .toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  })

  function select(id: string | null) {
    selectedId.value = id
  }

  function toggleSelect(id: string) {
    const next = new Set(selectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedIds.value = next
  }

  function selectPage(ids: string[]) {
    selectedIds.value = new Set(ids)
  }

  function deselectAll() {
    selectedIds.value = new Set()
  }

  async function loadServerAccounts(): Promise<void> {
    cloudLoading.value = true
    try {
      const rows = await listServerAccounts()
      const prevByServerId = new Map(
        serverAccounts.value
          .filter((a) => a.serverId)
          .map((a) => [a.serverId!, a] as const),
      )
      // Prefer local vault secrets when the same email exists both places
      const secretByEmail = new Map(
        localAccounts.value.map((a) => [a.email.toLowerCase(), a] as const),
      )
      const prevByEmail = new Map(
        serverAccounts.value.map((a) => [a.email.toLowerCase(), a] as const),
      )
      serverAccounts.value = rows.map((r) => {
        const emailKey = r.email.toLowerCase()
        const prev =
          prevByServerId.get(r.id) ||
          secretByEmail.get(emailKey) ||
          prevByEmail.get(emailKey)
        return mapServerToLocal(r, prev)
      })
      // Link local rows to cloud ids when email matches (keeps secrets in vault).
      // Heal missing local metadata from cloud, but NEVER resurrect a cleared note:
      // empty local note after dual-write means user cleared purpose tags intentionally.
      let healed = false
      for (const srv of serverAccounts.value) {
        if (!srv.serverId) continue
        const li = localAccounts.value.findIndex(
          (a) => a.email.toLowerCase() === srv.email.toLowerCase(),
        )
        if (li < 0) continue
        const loc = localAccounts.value[li]!
        // Only fill note when local has never set one (undefined/null), not when ''.
        // Prefer != null so cloud empty string is preserved (cleared on server).
        const nextNote =
          loc.note === undefined || loc.note === null
            ? srv.note != null
              ? srv.note
              : undefined
            : loc.note
        const nextProxy =
          loc.proxy === undefined || loc.proxy === null
            ? srv.proxy != null
              ? srv.proxy
              : undefined
            : loc.proxy
        const nextCode = loc.latestCode || srv.latestCode
        const linkChanged =
          loc.serverId !== srv.serverId || loc.clientSealed !== srv.clientSealed
        const metaChanged =
          nextNote !== loc.note || nextProxy !== loc.proxy || nextCode !== loc.latestCode
        if (linkChanged || metaChanged) {
          healed = true
          localAccounts.value[li] = {
            ...loc,
            serverId: srv.serverId,
            clientSealed: srv.clientSealed,
            note: nextNote,
            proxy: nextProxy,
            latestCode: nextCode,
            // Prefer cloud status when local never fetched / still unknown
            status:
              loc.status === 'unknown' && srv.status && srv.status !== 'unknown'
                ? srv.status
                : loc.status,
            lastError: loc.lastError || srv.lastError,
            syncEnabled: loc.syncEnabled ?? srv.syncEnabled,
            updatedAt: Date.now(),
          }
        }
      }
      // serverId / healed note must hit vault before a quick refresh
      if (healed) await flushPersist()
      await retryPendingCloudWrites()
    } catch (e) {
      // Missing device header or API down — keep previous
      if (!(e instanceof ApiError && e.status === 400)) {
        console.warn('loadServerAccounts failed', e)
      }
    } finally {
      cloudLoading.value = false
    }
  }

  function importText(text: string): ImportResult {
    const { accounts: parsed, invalid, lines, warnings } = parseImportText(text)
    const lineMessages: string[] = []
    for (const line of lines) {
      if (line.message) {
        lineMessages.push(
          `${line.ok ? '✓' : '✗'} ${line.message}${line.raw ? ` ← ${line.raw.slice(0, 48)}${line.raw.length > 48 ? '…' : ''}` : ''}`,
        )
      }
    }
    return importPartials(parsed, {
      skipOkExisting: false,
      groupId: importGroupId.value || 'default',
      invalid,
      warnings,
      lineMessages,
    })
  }

  /**
   * Confirm-import after preview modal (local browser storage).
   */
  function importPartials(
    partials: Array<
      Omit<
        MailAccount,
        'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'
      >
    >,
    opts: {
      skipOkExisting?: boolean
      groupId?: string
      invalid?: number
      warnings?: string[]
      lineMessages?: string[]
      statusByEmail?: Record<string, { status: MailAccount['status']; lastError?: string }>
      maxLocal?: number
    } = {},
  ): ImportResult {
    let created = 0
    let updated = 0
    let skipped = 0
    let quotaBlocked = 0
    let localCount = localAccounts.value.length
    const hasCap = opts.maxLocal != null && Number.isFinite(opts.maxLocal) && opts.maxLocal >= 0
    const maxLocal = hasCap ? (opts.maxLocal as number) : Number.POSITIVE_INFINITY
    const gid = opts.groupId || importGroupId.value || 'default'
    const skipOk = opts.skipOkExisting !== false

    for (const partial of partials) {
      const emailKey = partial.email.toLowerCase()
      const existingIdx = localAccounts.value.findIndex(
        (a) => a.email.toLowerCase() === emailKey,
      )
      const checked = opts.statusByEmail?.[emailKey]

      if (existingIdx >= 0) {
        const prev = localAccounts.value[existingIdx]!
        if (skipOk && prev.status === 'ok' && checked?.status !== 'error') {
          skipped += 1
          continue
        }
        if (skipOk && prev.status === 'ok' && !checked) {
          skipped += 1
          continue
        }
        // Prefer partial secrets; rebuild rawLine from merged fields so export
        // never keeps a pre-edit import line after password change.
        const mergedPartial = { ...prev, ...partial }
        localAccounts.value[existingIdx] = {
          ...prev,
          ...partial,
          id: prev.id,
          storage: 'local',
          status: checked?.status ?? 'unknown',
          lastError: checked?.lastError,
          tags: prev.tags,
          groupId: partial.groupId || prev.groupId || gid,
          latestCode: prev.latestCode,
          codeApiUrl: prev.codeApiUrl,
          serverId: prev.serverId,
          createdAt: prev.createdAt,
          updatedAt: Date.now(),
          rawLine: partial.rawLine || prev.rawLine || mergedPartial.email,
        }
        updated += 1
      } else {
        if (localCount >= maxLocal) {
          quotaBlocked += 1
          continue
        }
        const acc = createAccountFromParsed(partial, { groupId: gid })
        if (checked) {
          acc.status = checked.status
          acc.lastError = checked.lastError
        }
        localAccounts.value.push(acc)
        localCount += 1
        created += 1
      }
    }

    const result = {
      created,
      updated,
      invalid: opts.invalid ?? 0,
      warnings: [
        ...(opts.warnings || []),
        ...(skipped ? [tt('console.importWarnSkipOk', { n: skipped })] : []),
        ...(quotaBlocked
          ? [tt('console.importWarnQuotaBlocked', { n: quotaBlocked, max: maxLocal })]
          : []),
      ],
      lineMessages: opts.lineMessages,
    }
    lastImport.value = result
    // Import mutates many rows; durable write (Console awaits flushPersist after)
    persistInBackground()
    return result
  }

  /**
   * Import selected partials to device cloud (encrypted server store).
   */
  /**
   * Upload selected local accounts to device cloud with hourly poll enabled.
   * Keeps a local row (secrets stay in browser); adds/updates server row for SyncWorker.
   */
  async function uploadLocalToCloud(
    ids: string[],
    opts: { syncEnabled?: boolean } = {},
  ): Promise<{ ok: number; fail: number; errors: string[] }> {
    const syncEnabled = opts.syncEnabled !== false
    let ok = 0
    let fail = 0
    const errors: string[] = []
    for (const id of ids) {
      const acc = localAccounts.value.find((a) => a.id === id)
      if (!acc) continue
      try {
        let clientSealed: string | undefined
        try {
          const vault = useVaultStore()
          if (vault.status === 'unlocked') {
            clientSealed = await vault.sealForCloud(sealPayloadFromAccount(acc))
          }
        } catch {
          /* ignore */
        }
        // Sealed cloud rows cannot be polled server-side
        const body = toCreateBody(
          {
            email: acc.email,
            type: acc.type,
            password: acc.password,
            refreshToken: acc.refreshToken,
            clientId: acc.clientId,
            apiUrl: acc.apiUrl,
            apiKey: acc.apiKey,
            imapHost: acc.imapHost,
            imapPort: acc.imapPort,
            smtpHost: acc.smtpHost,
            smtpPort: acc.smtpPort,
            authCode: acc.authCode,
            note: acc.note,
            proxy: acc.proxy,
            sessionCookies: acc.sessionCookies,
            sessionMeta: acc.sessionMeta,
          },
          { syncEnabled: clientSealed ? false : syncEnabled, clientSealed },
        )
        const row = await createServerAccount(body)
        // Sealed cloud rows cannot be polled server-side — keep local syncEnabled false
        const localSyncEnabled = clientSealed ? false : syncEnabled
        // mark local as linked to cloud for UI
        const li = localAccounts.value.findIndex((a) => a.id === id)
        if (li >= 0) {
          localAccounts.value[li] = {
            ...localAccounts.value[li]!,
            serverId: row.id,
            clientSealed: Boolean(clientSealed),
            syncEnabled: localSyncEnabled,
            updatedAt: Date.now(),
          }
        }
        const mapped = mapServerToLocal(row, {
          ...acc,
          id: `srv_${row.id}`,
          storage: 'server',
          serverId: row.id,
          clientSealed: Boolean(clientSealed),
          syncEnabled: localSyncEnabled,
          updatedAt: Date.now(),
        })
        const si = serverAccounts.value.findIndex(
          (a) => a.serverId === row.id || a.email.toLowerCase() === acc.email.toLowerCase(),
        )
        if (si >= 0) serverAccounts.value[si] = mapped
        else serverAccounts.value.push(mapped)
        ok += 1
      } catch (e) {
        fail += 1
        const msg =
          e instanceof ApiError
            ? e.message || `HTTP ${e.status}`
            : e instanceof Error
              ? e.message
              : String(e)
        errors.push(`${acc.email}: ${msg}`)
      }
    }
    // serverId link on local rows must survive immediate refresh
    if (ok) await flushPersist()
    return { ok, fail, errors }
  }

  async function importPartialsToCloud(
    partials: Array<
      Omit<
        MailAccount,
        'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'
      >
    >,
    opts: {
      skipOkExisting?: boolean
      groupId?: string
      syncEnabled?: boolean
      statusByEmail?: Record<string, { status: MailAccount['status']; lastError?: string }>
    } = {},
  ): Promise<ImportResult & { errors: string[] }> {
    let created = 0
    let updated = 0
    let skipped = 0
    const errors: string[] = []
    const gid = opts.groupId || importGroupId.value || 'default'
    const skipOk = opts.skipOkExisting !== false
    const existingByEmail = new Map(
      serverAccounts.value.map((a) => [a.email.toLowerCase(), a] as const),
    )

    for (const partial of partials) {
      const emailKey = partial.email.toLowerCase()
      const existing = existingByEmail.get(emailKey)
      const checked = opts.statusByEmail?.[emailKey]
      if (existing && skipOk && existing.status === 'ok' && checked?.status !== 'error') {
        skipped += 1
        continue
      }
      try {
        let clientSealed: string | undefined
        try {
          const vault = useVaultStore()
          if (vault.status === 'unlocked') {
            const p = partial as Partial<MailAccount> & {
              sessionCookies?: MailAccount['sessionCookies']
              sessionMeta?: MailAccount['sessionMeta']
              apiKey?: string
            }
            clientSealed = await vault.sealForCloud({
              email: partial.email,
              type: partial.type,
              password: partial.password,
              refreshToken: partial.refreshToken,
              clientId: partial.clientId,
              apiUrl: partial.apiUrl,
              apiKey: p.apiKey,
              imapHost: partial.imapHost,
              imapPort: partial.imapPort,
              smtpHost: partial.smtpHost,
              smtpPort: partial.smtpPort,
              authCode: partial.authCode,
              sessionCookies: p.sessionCookies,
              sessionMeta: p.sessionMeta,
            })
          }
        } catch {
          /* seal optional */
        }
        // Client-sealed: no server-side poll (cannot decrypt). Plain upload only if seal fails.
        const p = partial as Partial<MailAccount> & {
          sessionCookies?: MailAccount['sessionCookies']
          sessionMeta?: MailAccount['sessionMeta']
          apiKey?: string
        }
        const body = toCreateBody(
          {
            ...partial,
            apiKey: p.apiKey,
            sessionCookies: p.sessionCookies,
            sessionMeta: p.sessionMeta,
          },
          {
            syncEnabled: clientSealed ? false : opts.syncEnabled,
            clientSealed: clientSealed || undefined,
          },
        )
        if (checked?.status === 'error') {
          // still allow save; mark after
        }
        const row = await createServerAccount(body)
        if (checked?.status === 'error' || checked?.status === 'ok') {
          try {
            await updateServerAccount(row.id, {
              status: checked.status === 'error' ? 'error' : 'ok',
              note: partial.note,
            })
          } catch {
            /* ignore status patch */
          }
        }
        // Dual-write local vault: client-sealed cloud rows cannot be fetched
        // without browser secrets after reload.
        const localIdx = localAccounts.value.findIndex(
          (a) => a.email.toLowerCase() === emailKey,
        )
        if (localIdx >= 0) {
          const prev = localAccounts.value[localIdx]!
          localAccounts.value[localIdx] = {
            ...prev,
            ...partial,
            id: prev.id,
            storage: 'local',
            serverId: row.id,
            clientSealed: Boolean(clientSealed),
            status: checked?.status ?? prev.status,
            lastError: checked?.lastError,
            groupId: partial.groupId || prev.groupId || gid,
            updatedAt: Date.now(),
          }
        } else {
          const loc = createAccountFromParsed(partial, { groupId: gid })
          loc.serverId = row.id
          loc.clientSealed = Boolean(clientSealed)
          if (checked) {
            loc.status = checked.status
            loc.lastError = checked.lastError
          }
          localAccounts.value.push(loc)
        }
        const mapped = mapServerToLocal(row, {
          ...partial,
          id: existing?.id || `srv_${row.id}`,
          storage: 'server',
          status: checked?.status ?? 'unknown',
          tags: [],
          latestCode: undefined,
          createdAt: Date.now(),
          updatedAt: Date.now(),
          rawLine: partial.rawLine || partial.email,
          groupId: partial.groupId || gid,
          lastError: checked?.lastError,
          clientSealed: Boolean(clientSealed),
        } as MailAccount)
        mapped.groupId = partial.groupId || gid
        if (checked) {
          mapped.status = checked.status
          mapped.lastError = checked.lastError
        }
        const idx = serverAccounts.value.findIndex(
          (a) => a.serverId === row.id || a.email.toLowerCase() === emailKey,
        )
        if (idx >= 0) {
          serverAccounts.value[idx] = mapped
          updated += 1
        } else {
          serverAccounts.value.push(mapped)
          created += 1
        }
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? e.message || `HTTP ${e.status}`
            : e instanceof Error
              ? e.message
              : String(e)
        errors.push(`${partial.email}: ${msg}`)
      }
    }

    const result = {
      created,
      updated,
      invalid: errors.length,
      warnings: [
        ...(skipped ? [tt('console.importWarnSkipOkCloud', { n: skipped })] : []),
        ...(errors.length ? [tt('console.importWarnCloudFailed', { n: errors.length })] : []),
      ],
      errors,
    }
    lastImport.value = result
    await flushPersist()
    return result
  }

  async function removeSelected() {
    const ids = selectedIds.value
    const toRemove = accounts.value.filter((a) => ids.has(a.id))
    const serverIds = new Set<string>()
    for (const a of toRemove) {
      // Linked local rows also carry serverId — delete cloud twin whenever present
      if (a.serverId) serverIds.add(a.serverId)
    }
    const failedServerIds = new Set<string>()
    for (const sid of serverIds) {
      try {
        await deleteServerAccount(sid)
      } catch (error) {
        failedServerIds.add(sid)
        console.warn('[openmail] cloud delete failed; keeping local mirror', error)
      }
    }
    const removableIds = new Set(
      toRemove
        .filter((a) => !a.serverId || !failedServerIds.has(a.serverId))
        .map((a) => a.id),
    )
    const emailsToMaybeDrop = toRemove
      .filter((a) => removableIds.has(a.id))
      .map((a) => a.email)
    localAccounts.value = localAccounts.value.filter((a) => !removableIds.has(a.id))
    // Drop cloud mirrors by selected id or by deleted serverId
    serverAccounts.value = serverAccounts.value.filter(
      (a) =>
        !removableIds.has(a.id) &&
        !(a.serverId && serverIds.has(a.serverId) && !failedServerIds.has(a.serverId)),
    )
    const still = [
      ...localAccounts.value.map((a) => a.email),
      ...serverAccounts.value.map((a) => a.email),
    ]
    for (const email of emailsToMaybeDrop) {
      removeAccountUiMetaIfUnused(email, still)
    }
    clearMailCacheIfUnused(emailsToMaybeDrop, still)
    if (selectedId.value && removableIds.has(selectedId.value)) {
      selectedId.value = null
    }
    selectedIds.value = new Set(
      toRemove.filter((a) => a.serverId && failedServerIds.has(a.serverId)).map((a) => a.id),
    )
    await flushPersist()
  }

  async function removeById(id: string) {
    const acc = findById(id)
    const email = acc?.email
    const serverId = acc?.serverId
    // Always delete cloud when linked (local row with serverId), not only storage==='server'
    if (serverId) {
      // Keep the local mirror when remote deletion fails. Otherwise the next
      // cloud refresh resurrects an account the UI claimed was deleted.
      await deleteServerAccount(serverId)
      serverAccounts.value = serverAccounts.value.filter(
        (a) => a.id !== id && a.serverId !== serverId,
      )
    } else if (acc?.storage === 'server') {
      serverAccounts.value = serverAccounts.value.filter((a) => a.id !== id)
    }
    localAccounts.value = localAccounts.value.filter((a) => a.id !== id)
    if (email) {
      const still = [
        ...localAccounts.value.map((a) => a.email),
        ...serverAccounts.value.map((a) => a.email),
      ]
      removeAccountUiMetaIfUnused(email, still)
      clearMailCacheIfUnused([email], still)
    }
    if (selectedId.value === id) selectedId.value = null
    if (selectedIds.value.has(id)) {
      const next = new Set(selectedIds.value)
      next.delete(id)
      selectedIds.value = next
    }
    await flushPersist()
  }

  /**
   * Batch group move: mutate all rows first, one vault flush, then cloud meta dual-writes.
   */
  async function moveToGroup(ids: string[], groupId: string) {
    const gid = groupId || 'default'
    const cloudPatches: Array<{ serverId: string; email: string }> = []
    for (const id of ids) {
      const li = localAccounts.value.findIndex((a) => a.id === id)
      if (li >= 0) {
        const prev = localAccounts.value[li]!
        localAccounts.value[li] = {
          ...prev,
          groupId: gid,
          storage: 'local',
          updatedAt: Date.now(),
        }
        patchAccountUiMeta(prev.email, { groupId: gid, starred: prev.starred })
        if (prev.serverId) cloudPatches.push({ serverId: prev.serverId, email: prev.email })
        continue
      }
      const si = serverAccounts.value.findIndex((a) => a.id === id)
      if (si >= 0) {
        const prev = serverAccounts.value[si]!
        serverAccounts.value[si] = {
          ...prev,
          groupId: gid,
          storage: 'server',
          updatedAt: Date.now(),
        }
        patchAccountUiMeta(prev.email, { groupId: gid, starred: prev.starred })
      }
    }
    await flushPersist()
    // groupId is browser-only — no API column; cloudPatches reserved if we add one later
    void cloudPatches
  }

  /** Clear local vault rows and await durable write so refresh cannot resurrect them. */
  async function clearLocal(): Promise<void> {
    const emails = localAccounts.value.map((a) => a.email)
    localAccounts.value = []
    const still = serverAccounts.value.map((a) => a.email)
    for (const email of emails) removeAccountUiMetaIfUnused(email, still)
    clearMailCacheIfUnused(emails, still)
    selectedId.value = null
    deselectAll()
    await flushPersist()
  }

  async function clearAllLocalStorage(): Promise<void> {
    const emails = localAccounts.value.map((a) => a.email)
    localAccounts.value = []
    const still = serverAccounts.value.map((a) => a.email)
    for (const email of emails) removeAccountUiMetaIfUnused(email, still)
    clearMailCacheIfUnused(emails, still)
    selectedId.value = null
    deselectAll()
    lastImport.value = null
    await flushPersist()
  }

  function exportText(format: 'raw' | 'emails' = 'raw'): string {
    const list = filtered.value.length ? filtered.value : accounts.value
    if (format === 'emails') {
      return list.map((a) => a.email).join('\n')
    }
    // Always rebuild from current fields — never trust stale rawLine after edits
    return exportCredentialsTxt(list)
  }

  /**
   * Safe dual-write fields (API replaces credential_enc wholesale — never send
   * partial credential blobs). Secrets for linked rows stay vault-only unless
   * buildFullCredentialBody is used with the merged account snapshot.
   */
  function cloudMetaBody(
    patch: Partial<MailAccount>,
  ): Parameters<typeof updateServerAccount>[1] | null {
    const body: Parameters<typeof updateServerAccount>[1] = {}
    if (patch.note !== undefined) body.note = patch.note ?? ''
    if (patch.proxy !== undefined) body.proxy = patch.proxy ?? ''
    if (patch.syncEnabled !== undefined) body.sync_enabled = patch.syncEnabled
    if (patch.status !== undefined) {
      body.status =
        patch.status === 'error' ? 'error' : patch.status === 'ok' ? 'ok' : undefined
      if (body.status === undefined) delete body.status
    }
    if (patch.type !== undefined) body.provider = patch.type
    return Object.keys(body).length ? body : null
  }

  function cloudErrorMessage(error: unknown): string {
    if (error instanceof Error && error.message) return error.message.slice(0, 300)
    return String(error || 'cloud sync failed').slice(0, 300)
  }

  function clearPendingCloudState(acc: MailAccount): MailAccount {
    const {
      cloudPendingPatch: _patch,
      cloudSyncPending: _pending,
      cloudSyncError: _error,
      ...clean
    } = acc
    return clean
  }

  async function retryPendingCloudWrites(): Promise<void> {
    let changed = false
    let anyFail = false
    let lastErr: string | null = null
    for (let i = 0; i < localAccounts.value.length; i += 1) {
      const acc = localAccounts.value[i]!
      if (!acc.serverId || !acc.cloudSyncPending || !acc.cloudPendingPatch) continue
      try {
        const row = await updateServerAccount(
          acc.serverId,
          acc.cloudPendingPatch as ServerAccountPatch,
        )
        localAccounts.value[i] = clearPendingCloudState({
          ...acc,
          updatedAt: Date.now(),
        })
        const si = serverAccounts.value.findIndex((a) => a.serverId === acc.serverId)
        if (si >= 0) {
          serverAccounts.value[si] = mapServerToLocal(row, serverAccounts.value[si])
        }
        changed = true
      } catch (error) {
        const msg = cloudErrorMessage(error)
        lastErr = msg
        anyFail = true
        localAccounts.value[i] = {
          ...acc,
          cloudSyncPending: true,
          cloudSyncError: msg,
        }
        changed = true
      }
    }
    if (anyFail) lastCloudSyncError.value = lastErr
    else if (changed) lastCloudSyncError.value = null
    if (changed) await flushPersist()
  }

  /** True when patch intends to change server-stored secrets. */
  function patchTouchesSecrets(patch: Partial<MailAccount>): boolean {
    return (
      patch.password !== undefined ||
      patch.refreshToken !== undefined ||
      patch.clientId !== undefined ||
      patch.apiUrl !== undefined ||
      patch.apiKey !== undefined ||
      patch.imapHost !== undefined ||
      patch.imapPort !== undefined ||
      patch.smtpHost !== undefined ||
      patch.smtpPort !== undefined ||
      patch.authCode !== undefined
    )
  }

  /**
   * Full credential replace from merged account state (never partial keys).
   * Backend replaces entire credential_enc when credential is present.
   * Returns null when the browser snapshot is too incomplete to safely replace
   * server secrets (avoids wiping cloud tokens after reload without vault secrets).
   */
  function fullCredentialBody(
    acc: MailAccount,
  ): Parameters<typeof updateServerAccount>[1] | null {
    const body: Parameters<typeof updateServerAccount>[1] = {}
    // Prefer explicit password; for http_api also accept apiKey as password field
    const password = acc.password || (acc.type === 'http_api' ? acc.apiKey : undefined)
    if (password) body.password = password
    const credential: Record<string, unknown> = {}
    if (acc.refreshToken) credential.refresh_token = acc.refreshToken
    if (acc.clientId) credential.client_id = acc.clientId
    if (acc.apiUrl) credential.api_url = acc.apiUrl
    // http_api: do not drop api_key on full credential replace
    const apiSecret = acc.apiKey || acc.password
    if (acc.type === 'http_api' && apiSecret) {
      credential.api_key = apiSecret
    }
    if (acc.apiAuthStyle) credential.api_auth_style = acc.apiAuthStyle
    if (acc.imapHost) credential.imap_host = acc.imapHost
    if (acc.imapPort) credential.imap_port = acc.imapPort
    if (acc.smtpHost) credential.smtp_host = acc.smtpHost
    if (acc.smtpPort) credential.smtp_port = acc.smtpPort
    if (acc.authCode) credential.auth_code = acc.authCode
    // Cookie providers: embed session_meta inside full credential snapshot
    if (
      (acc.type === 'cookie' || acc.type === 'unknown') &&
      acc.sessionMeta &&
      Object.keys(acc.sessionMeta).length
    ) {
      credential.session_meta = acc.sessionMeta
    }
    if (Object.keys(credential).length) body.credential = credential
    // Rolling session cookies live on AccountUpdate.cookies (not only in credential)
    if (acc.sessionCookies?.length) body.cookies = acc.sessionCookies
    if (acc.type) body.provider = acc.type
    // Require a usable secret set for this provider before full replace
    const t = acc.type
    const hasUsable =
      (t === 'oauth' && acc.refreshToken && acc.clientId) ||
      // http_api: apiUrl required; api_key sent when present (open APIs may omit)
      (t === 'http_api' && acc.apiUrl) ||
      (t === 'imap' && (acc.password || acc.authCode)) ||
      (t === 'cookie' && (acc.password || (acc.sessionCookies && acc.sessionCookies.length))) ||
      (t === 'unknown' &&
        (acc.password ||
          acc.authCode ||
          (acc.refreshToken && acc.clientId) ||
          acc.apiUrl ||
          (acc.sessionCookies && acc.sessionCookies.length)))
    if (!hasUsable && !body.password && !Object.keys(credential).length) return null
    if (!hasUsable) return null
    return body
  }

  async function patchAccount(id: string, patch: Partial<MailAccount>): Promise<void> {
    const li = localAccounts.value.findIndex((a) => a.id === id)
    if (li >= 0) {
      const prev = localAccounts.value[li]!
      // Normalize empty note to '' so intentional clear is distinguishable from "never set"
      const normalized: Partial<MailAccount> = { ...patch }
      if (patch.note !== undefined) normalized.note = patch.note ?? ''
      if (patch.proxy !== undefined) normalized.proxy = patch.proxy ?? ''
      const merged: MailAccount = {
        ...prev,
        ...normalized,
        storage: 'local',
        updatedAt: Date.now(),
      }
      localAccounts.value[li] = merged
      // group/star: durable browser map (survives even if vault write races)
      if (normalized.groupId !== undefined || normalized.starred !== undefined) {
        patchAccountUiMeta(prev.email, {
          groupId: merged.groupId,
          starred: merged.starred,
        })
      }
      // Linked local+cloud: dual-write safe meta; secrets only as full snapshot
      const serverId = merged.serverId || prev.serverId
      if (serverId) {
        const body: Parameters<typeof updateServerAccount>[1] = {
          ...(merged.cloudPendingPatch as ServerAccountPatch | undefined),
          ...cloudMetaBody(normalized),
        }
        if (patchTouchesSecrets(normalized)) {
          if (merged.clientSealed) {
            // Re-seal envelope so cloud ciphertext matches new password/tokens
            try {
              const vault = useVaultStore()
              if (vault.status === 'unlocked') {
                const sealed = await vault.sealForCloud(sealPayloadFromAccount(merged))
                body.client_sealed = sealed
                body.sync_enabled = false
              }
            } catch (e) {
              console.warn('[openmail] re-seal for cloud failed', e)
            }
          } else {
            const full = fullCredentialBody(merged)
            if (full) Object.assign(body, full)
          }
        }
        // Rolling session dual-write for non-sealed linked accounts (server poll)
        if (
          !merged.clientSealed &&
          (normalized.sessionCookies !== undefined || normalized.sessionMeta !== undefined) &&
          merged.sessionCookies?.length
        ) {
          body.cookies = merged.sessionCookies
        }
        if (Object.keys(body).length) {
          try {
            const row = await updateServerAccount(serverId, body)
            lastCloudSyncError.value = null
            localAccounts.value[li] = clearPendingCloudState({
              ...localAccounts.value[li]!,
              clientSealed: Boolean(row.client_sealed ?? merged.clientSealed),
              updatedAt: Date.now(),
            })
            const si = serverAccounts.value.findIndex(
              (a) =>
                a.serverId === serverId ||
                a.email.toLowerCase() === prev.email.toLowerCase(),
            )
            if (si >= 0) {
              serverAccounts.value[si] = mapServerToLocal(row, {
                ...serverAccounts.value[si]!,
                ...normalized,
                storage: 'server',
                clientSealed: Boolean(row.client_sealed ?? merged.clientSealed),
                updatedAt: Date.now(),
              })
            }
          } catch (e) {
            const msg = cloudErrorMessage(e)
            lastCloudSyncError.value = msg
            localAccounts.value[li] = {
              ...localAccounts.value[li]!,
              cloudPendingPatch: body as Record<string, unknown>,
              cloudSyncPending: true,
              cloudSyncError: msg,
              updatedAt: Date.now(),
            }
            console.warn('[openmail] cloud dual-write queued for retry', e)
          }
        }
      }
      // Critical: purpose/note/star must hit vault before any refresh
      await flushPersist()
      return
    }
    const si = serverAccounts.value.findIndex((a) => a.id === id)
    if (si >= 0) {
      const prev = serverAccounts.value[si]!
      const normalized: Partial<MailAccount> = { ...patch }
      if (patch.note !== undefined) normalized.note = patch.note ?? ''
      if (patch.proxy !== undefined) normalized.proxy = patch.proxy ?? ''
      const next: MailAccount = {
        ...prev,
        ...normalized,
        storage: 'server',
        updatedAt: Date.now(),
      }
      // Cloud-only rows: persist group/star in browser map (API has no columns)
      if (normalized.groupId !== undefined || normalized.starred !== undefined) {
        patchAccountUiMeta(prev.email, {
          groupId: next.groupId,
          starred: next.starred,
        })
      }
      serverAccounts.value[si] = next
      if (prev.serverId) {
        const body: Parameters<typeof updateServerAccount>[1] = {
          ...cloudMetaBody(normalized),
        }
        if (patchTouchesSecrets(normalized)) {
          if (next.clientSealed) {
            try {
              const vault = useVaultStore()
              if (vault.status === 'unlocked') {
                body.client_sealed = await vault.sealForCloud(sealPayloadFromAccount(next))
                body.sync_enabled = false
              }
            } catch (e) {
              console.warn('[openmail] re-seal cloud-only failed', e)
            }
          } else {
            const full = fullCredentialBody(next)
            if (full) Object.assign(body, full)
          }
        }
        // Rolling session dual-write for non-sealed cloud-only rows
        if (
          !next.clientSealed &&
          (normalized.sessionCookies !== undefined || normalized.sessionMeta !== undefined) &&
          next.sessionCookies?.length
        ) {
          body.cookies = next.sessionCookies
        }
        if (Object.keys(body).length) {
          try {
            const row = await updateServerAccount(prev.serverId, body)
            lastCloudSyncError.value = null
            serverAccounts.value[si] = mapServerToLocal(row, {
              ...next,
              clientSealed: Boolean(row.client_sealed ?? next.clientSealed),
            })
          } catch (e) {
            lastCloudSyncError.value = cloudErrorMessage(e)
            // A cloud-only row has no encrypted local outbox. Roll back the
            // optimistic update and let the caller surface the failed save.
            serverAccounts.value[si] = prev
            throw e
          }
        }
      }
    }
  }

  function findById(id: string): MailAccount | undefined {
    return accounts.value.find((a) => a.id === id)
  }

  return {
    localAccounts,
    serverAccounts,
    cloudLoading,
    lastCloudSyncError,
    accounts,
    accountsFlat,
    selectedId,
    selectedIds,
    selected,
    filterQuery,
    filterStatus,
    filterStorage,
    filterBrand,
    filterGroup,
    importGroupId,
    lastImport,
    stats,
    filtered,
    vaultHydrated,
    select,
    toggleSelect,
    selectPage,
    deselectAll,
    loadServerAccounts,
    importText,
    importPartials,
    importPartialsToCloud,
    rebuildAllRawLines,
    uploadLocalToCloud,
    syncApiMailboxes,
    removeSelected,
    removeById,
    moveToGroup,
    clearLocal,
    clearAllLocalStorage,
    exportText,
    patchAccount,
    retryPendingCloudWrites,
    findById,
    hydrateFromVault,
    clearLocalSecrets,
    flushPersist,
  }
})
