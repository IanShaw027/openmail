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
import {
  LOCAL_ACCOUNTS_KEY,
  loadAccountsPlain,
  mapServerToLocal,
  normalizeLocalAccounts,
} from './accounts/mappers'

export const useAccountsStore = defineStore('accounts', () => {
  /** Local-only rows — secrets live in encrypted vault when unlocked */
  const localAccounts = ref<MailAccount[]>([])
  /** Device cloud rows (server); secrets not returned by API */
  const serverAccounts = ref<MailAccount[]>([])
  const cloudLoading = ref(false)
  const vaultHydrated = ref(false)
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
    try {
      const { useVaultStore } = await import('@/stores/vault')
      const vault = useVaultStore()
      if (vault.status !== 'unlocked') return
      await vault.saveAccounts(localAccounts.value)
      // wipe legacy plaintext if still present
      try {
        localStorage.removeItem(LOCAL_ACCOUNTS_KEY)
      } catch {
        /* ignore */
      }
    } catch {
      /* vault locked or crypto fail — do not write plaintext */
    }
  }

  watch(
    localAccounts,
    () => {
      if (!vaultHydrated.value) return
      if (persistTimer) clearTimeout(persistTimer)
      persistTimer = setTimeout(() => {
        void persistLocalEncrypted()
      }, 200)
    },
    { deep: true },
  )

  /** Call after vault unlock: load encrypted accounts (or migrate legacy plaintext). */
  async function hydrateFromVault(): Promise<void> {
    try {
      const { useVaultStore } = await import('@/stores/vault')
      const vault = useVaultStore()
      if (vault.status !== 'unlocked') {
        localAccounts.value = []
        vaultHydrated.value = false
        return
      }
      const raw = await vault.loadAccounts()
      if (Array.isArray(raw) && raw.length) {
        localAccounts.value = normalizeLocalAccounts(raw as MailAccount[])
      } else {
        // one-shot legacy
        const legacy = loadAccountsPlain()
        localAccounts.value = legacy
        if (legacy.length) await vault.saveAccounts(legacy)
        try {
          localStorage.removeItem(LOCAL_ACCOUNTS_KEY)
        } catch {
          /* ignore */
        }
      }
      vaultHydrated.value = true
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
    const li = localAccounts.value.findIndex((a) => a.id === sourceId)
    if (li >= 0) {
      localAccounts.value[li] = {
        ...localAccounts.value[li]!,
        isApiSource: true,
        apiMailboxes: unique,
        updatedAt: Date.now(),
      }
    }
    const existingKids = localAccounts.value.filter((a) => a.parentApiId === sourceId)
    const have = new Set(existingKids.map((a) => a.email.toLowerCase()))
    const now = Date.now()
    for (const email of unique) {
      if (have.has(email)) continue
      // skip if same email already a top-level local account
      if (localAccounts.value.some((a) => a.email.toLowerCase() === email && !a.parentApiId)) {
        continue
      }
      localAccounts.value.push({
        id: `mb_${sourceId}_${email.replace(/[^a-z0-9]/gi, '_').slice(0, 40)}`,
        email,
        type: 'http_api',
        brand: 'http_api',
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
        note: src.note ? `${src.note} · 子邮箱` : 'API 临时邮箱',
        createdAt: now,
        updatedAt: now,
      })
    }
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
      // Link local rows to cloud ids when email matches (keeps secrets in vault)
      for (const srv of serverAccounts.value) {
        if (!srv.serverId) continue
        const li = localAccounts.value.findIndex(
          (a) => a.email.toLowerCase() === srv.email.toLowerCase(),
        )
        if (li < 0) continue
        const loc = localAccounts.value[li]!
        if (loc.serverId !== srv.serverId || loc.clientSealed !== srv.clientSealed) {
          localAccounts.value[li] = {
            ...loc,
            serverId: srv.serverId,
            clientSealed: srv.clientSealed,
            updatedAt: Date.now(),
          }
        }
      }
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
        ...(skipped ? [`跳过 ${skipped} 个状态正常的已有账号（未覆盖）`] : []),
        ...(quotaBlocked
          ? [
              `配额限制：未导入 ${quotaBlocked} 个新账号（本机上限 ${maxLocal}，可配置 LICENSE 解除）`,
            ]
          : []),
      ],
      lineMessages: opts.lineMessages,
    }
    lastImport.value = result
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
          const { useVaultStore } = await import('@/stores/vault')
          const vault = useVaultStore()
          if (vault.status === 'unlocked') {
            clientSealed = await vault.sealForCloud({
              email: acc.email,
              type: acc.type,
              password: acc.password,
              refreshToken: acc.refreshToken,
              clientId: acc.clientId,
              apiUrl: acc.apiUrl,
              imapHost: acc.imapHost,
              imapPort: acc.imapPort,
              smtpHost: acc.smtpHost,
              smtpPort: acc.smtpPort,
              authCode: acc.authCode,
              sessionCookies: acc.sessionCookies,
              sessionMeta: acc.sessionMeta,
            })
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
            imapHost: acc.imapHost,
            imapPort: acc.imapPort,
            authCode: acc.authCode,
            note: acc.note,
            proxy: acc.proxy,
          },
          { syncEnabled: clientSealed ? false : syncEnabled, clientSealed },
        )
        const row = await createServerAccount(body)
        // mark local as linked to cloud for UI
        const li = localAccounts.value.findIndex((a) => a.id === id)
        if (li >= 0) {
          localAccounts.value[li] = {
            ...localAccounts.value[li]!,
            serverId: row.id,
            syncEnabled,
            updatedAt: Date.now(),
          }
        }
        const mapped = mapServerToLocal(row, {
          ...acc,
          id: `srv_${row.id}`,
          storage: 'server',
          serverId: row.id,
          syncEnabled,
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
          const { useVaultStore } = await import('@/stores/vault')
          const vault = useVaultStore()
          if (vault.status === 'unlocked') {
            clientSealed = await vault.sealForCloud({
              email: partial.email,
              type: partial.type,
              password: partial.password,
              refreshToken: partial.refreshToken,
              clientId: partial.clientId,
              apiUrl: partial.apiUrl,
              imapHost: partial.imapHost,
              imapPort: partial.imapPort,
              smtpHost: partial.smtpHost,
              smtpPort: partial.smtpPort,
              authCode: partial.authCode,
              sessionCookies: (partial as { sessionCookies?: unknown }).sessionCookies,
              sessionMeta: (partial as { sessionMeta?: unknown }).sessionMeta,
            })
          }
        } catch {
          /* seal optional */
        }
        // Client-sealed: no server-side poll (cannot decrypt). Plain upload only if seal fails.
        const body = toCreateBody(partial, {
          syncEnabled: clientSealed ? false : opts.syncEnabled,
          clientSealed: clientSealed || undefined,
        })
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
        ...(skipped ? [`跳过 ${skipped} 个状态正常的已有云端账号`] : []),
        ...(errors.length ? [`云端失败 ${errors.length} 个`] : []),
      ],
      errors,
    }
    lastImport.value = result
    return result
  }

  async function removeSelected() {
    const ids = selectedIds.value
    const toRemove = accounts.value.filter((a) => ids.has(a.id))
    for (const a of toRemove) {
      if (a.storage === 'server' && a.serverId) {
        try {
          await deleteServerAccount(a.serverId)
        } catch {
          /* continue */
        }
      }
    }
    localAccounts.value = localAccounts.value.filter((a) => !ids.has(a.id))
    serverAccounts.value = serverAccounts.value.filter((a) => !ids.has(a.id))
    if (selectedId.value && ids.has(selectedId.value)) {
      selectedId.value = null
    }
    deselectAll()
  }

  async function removeById(id: string) {
    const acc = findById(id)
    if (acc?.storage === 'server' && acc.serverId) {
      await deleteServerAccount(acc.serverId)
      serverAccounts.value = serverAccounts.value.filter((a) => a.id !== id)
    } else {
      localAccounts.value = localAccounts.value.filter((a) => a.id !== id)
    }
    if (selectedId.value === id) selectedId.value = null
    if (selectedIds.value.has(id)) {
      const next = new Set(selectedIds.value)
      next.delete(id)
      selectedIds.value = next
    }
  }

  function moveToGroup(ids: string[], groupId: string) {
    for (const id of ids) {
      void patchAccount(id, { groupId })
    }
  }

  function clearLocal() {
    localAccounts.value = []
    selectedId.value = null
    deselectAll()
  }

  function clearAllLocalStorage() {
    localAccounts.value = []
    selectedId.value = null
    deselectAll()
    lastImport.value = null
  }

  function exportText(format: 'raw' | 'emails' = 'raw'): string {
    const list = filtered.value.length ? filtered.value : accounts.value
    if (format === 'emails') {
      return list.map((a) => a.email).join('\n')
    }
    return list.map((a) => a.rawLine || a.email).join('\n')
  }

  async function patchAccount(id: string, patch: Partial<MailAccount>): Promise<void> {
    const li = localAccounts.value.findIndex((a) => a.id === id)
    if (li >= 0) {
      localAccounts.value[li] = {
        ...localAccounts.value[li]!,
        ...patch,
        storage: 'local',
        updatedAt: Date.now(),
      }
      return
    }
    const si = serverAccounts.value.findIndex((a) => a.id === id)
    if (si >= 0) {
      const prev = serverAccounts.value[si]!
      const next = { ...prev, ...patch, storage: 'server' as const, updatedAt: Date.now() }
      serverAccounts.value[si] = next
      if (prev.serverId) {
        const body: Parameters<typeof updateServerAccount>[1] = {}
        if (patch.note !== undefined) body.note = patch.note ?? ''
        if (patch.proxy !== undefined) body.proxy = patch.proxy ?? ''
        if (patch.syncEnabled !== undefined) body.sync_enabled = patch.syncEnabled
        if (patch.password !== undefined) body.password = patch.password
        if (
          patch.refreshToken !== undefined ||
          patch.clientId !== undefined ||
          patch.apiUrl !== undefined ||
          patch.imapHost !== undefined ||
          patch.imapPort !== undefined ||
          patch.smtpHost !== undefined ||
          patch.smtpPort !== undefined ||
          patch.authCode !== undefined ||
          patch.type !== undefined
        ) {
          body.credential = {
            ...(patch.refreshToken ? { refresh_token: patch.refreshToken } : {}),
            ...(patch.clientId ? { client_id: patch.clientId } : {}),
            ...(patch.apiUrl ? { api_url: patch.apiUrl } : {}),
            ...(patch.imapHost ? { imap_host: patch.imapHost } : {}),
            ...(patch.imapPort ? { imap_port: patch.imapPort } : {}),
            ...(patch.smtpHost ? { smtp_host: patch.smtpHost } : {}),
            ...(patch.smtpPort ? { smtp_port: patch.smtpPort } : {}),
            ...(patch.authCode ? { auth_code: patch.authCode } : {}),
          }
          if (patch.type) body.provider = patch.type
        }
        if (Object.keys(body).length) {
          try {
            const row = await updateServerAccount(prev.serverId, body)
            serverAccounts.value[si] = mapServerToLocal(row, next)
          } catch (e) {
            console.warn('patch cloud account failed', e)
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
    uploadLocalToCloud,
    syncApiMailboxes,
    removeSelected,
    removeById,
    moveToGroup,
    clearLocal,
    clearAllLocalStorage,
    exportText,
    patchAccount,
    findById,
    hydrateFromVault,
    clearLocalSecrets,
  }
})
