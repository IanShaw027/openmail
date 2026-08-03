<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAccountsStore } from '@/stores/accounts'
import { useToast } from '@/composables/useToast'
import { useApiStatus } from '@/composables/useApiStatus'
import { copyText } from '@/utils/clipboard'
import type { MailAccount } from '@/types/account'
import {
  accountCanFetch,
  accountCanSend,
  accountHasLocalFetchSecrets,
} from '@/types/account'
import { IMPORT_PLACEHOLDER, parseImportText } from '@/utils/importParse'
import {
  type MailMessage,
  type FetchResult,
  proxyFetchMail,
  fetchServerAccount,
  credentialFromLocal,
  extractCode,
  proxySendMail,
} from '@/api/accounts'
import { ApiError, isAbortError, isTimeoutError } from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { useMailCacheStore } from '@/stores/mailCache'
import { exportCredentialsTxt, buildSystemSnapshot, parseSystemSnapshot } from '@/utils/exportImport'
import { getDeviceId, getLicenseToken, setLicenseToken } from '@/utils/device'
import { sanitizeHtml } from '@/utils/sanitizeHtml'
import {
  type MailGroup,
  DEFAULT_GROUP_ID,
  loadGroups,
  saveGroups,
  uidGroup,
} from '@/utils/groups'
import UiSelect, { type UiSelectOption } from '@/components/UiSelect.vue'
import { useTwoFaStore } from '@/stores/twofa'
import { mapPool } from '@/utils/mapPool'
import {
  brandLabel as brandLabelUtil,
  typeLabel as typeLabelUtil,
  secretHint as secretHintUtil,
  hostLabel as hostLabelUtil,
  hostCopyValue,
  formatRelativeTime,
  displayCode as displayCodeUtil,
  copyableSecret,
  isTokenError,
  canChangeMailboxPassword,
} from '@/utils/consoleAccountLabels'
import ConsoleSendModal from '@/components/console/ConsoleSendModal.vue'
import ConsoleGroupModal from '@/components/console/ConsoleGroupModal.vue'
import NotePurposeCell from '@/components/console/NotePurposeCell.vue'
import BrandMark from '@/components/BrandMark.vue'
import { resolveAccountBrand } from '@/utils/domainBrand'

const { t, locale } = useI18n()
const accounts = useAccountsStore()
const userSettings = useSettingsStore()
const mailCache = useMailCacheStore()
const twofa = useTwoFaStore()
const { flashMsg } = useToast()
const apiStatus = useApiStatus()

const importText = ref('')
const copiedKey = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const mailFolder = ref<'inbox' | 'spam' | 'sent'>('inbox')
const fetchingId = ref<string | null>(null)
const batchBusy = ref(false)
const showImportHelp = ref(false)
const importCollapsed = ref(localStorage.getItem('openmail.importCollapsed') === '1')
const importPlaceholderText = IMPORT_PLACEHOLDER
const importPreview = ref<string[]>([])
const page = ref(1)
const pageSize = ref(Number(localStorage.getItem('openmail.pageSize') || 5) || 5)
const pageSizeOptions = [5, 10, 20]
const brandOptions = [
  'microsoft',
  'gmail',
  'qq',
  'netease',
  'yahoo',
  'icloud',
  'aliyun',
  'mailcom',
  'gmx',
  'proton',
  'zoho',
  'cf_temp',
  'duckmail',
  'http_api',
  'other',
]

/** Collapsed CF / HttpApi source rows — children hidden until expanded */
const expandedApiSources = ref<Set<string>>(
  new Set(
    (() => {
      try {
        const raw = localStorage.getItem('openmail.expandedApiSources')
        const arr = raw ? (JSON.parse(raw) as string[]) : []
        return Array.isArray(arr) ? arr : []
      } catch {
        return []
      }
    })(),
  ),
)

function isApiExpanded(sourceId: string): boolean {
  return expandedApiSources.value.has(sourceId)
}

function toggleApiExpand(sourceId: string, e?: Event) {
  e?.stopPropagation()
  const next = new Set(expandedApiSources.value)
  if (next.has(sourceId)) next.delete(sourceId)
  else next.add(sourceId)
  expandedApiSources.value = next
  try {
    localStorage.setItem('openmail.expandedApiSources', JSON.stringify([...next]))
  } catch {
    /* ignore */
  }
}


const groups = ref<MailGroup[]>(loadGroups())
const showGroupManage = ref(false)
const newGroupName = ref('')
const editingGroupId = ref<string | null>(null)
const editingGroupName = ref('')

const groupSelectOptions = computed<UiSelectOption[]>(() =>
  groups.value.map((g) => ({ value: g.id, label: g.name })),
)
const brandSelectOptions = computed<UiSelectOption[]>(() => [
  { value: 'all', label: t('console.filterBrandAll') },
  ...brandOptions.map((b) => ({ value: b, label: brandLabel(b) })),
])
const statusSelectOptions = computed<UiSelectOption[]>(() => [
  { value: 'all', label: t('console.statusFilterAll') },
  { value: 'ok', label: t('console.statusOk') },
  { value: 'error', label: t('console.statusError') },
  { value: 'unknown', label: t('console.statusUnknown') },
])
const storageSelectOptions = computed<UiSelectOption[]>(() => [
  { value: 'all', label: t('console.storageFilterAll') },
  { value: 'local', label: t('console.storageFilterLocal') },
  { value: 'server', label: t('console.storageFilterServer') },
])
const filterGroupOptions = computed<UiSelectOption[]>(() => [
  { value: 'all', label: t('console.groupFilterAll') },
  ...groups.value.map((g) => ({ value: g.id, label: g.name })),
])
const moveGroupOptions = computed<UiSelectOption[]>(() => [
  { value: '', label: `${t('console.groupMove')}…` },
  ...groups.value.map((g) => ({ value: g.id, label: g.name })),
])
const pageSizeSelectOptions = computed<UiSelectOption[]>(() =>
  pageSizeOptions.map((n) => ({ value: n, label: String(n) })),
)
const apiAuthStyleOptions = computed<UiSelectOption[]>(() => [
  { value: 'auto', label: t('console.apiAuthAuto') },
  { value: 'none', label: t('console.apiAuthNone') },
  { value: 'x-admin-auth', label: 'x-admin-auth (cf_temp_email)' },
  { value: 'x-api-key', label: 'X-API-Key (MoeMail 等)' },
  { value: 'bearer', label: 'Authorization: Bearer' },
  { value: 'x-custom-auth', label: 'x-custom-auth' },
])

function onMoveGroupPick(v: string | number) {
  moveGroupId.value = String(v)
  onMoveGroup()
}
const showEdit = ref(false)
const editForm = ref({
  id: '',
  email: '',
  password: '',
  clientId: '',
  refreshToken: '',
  imapHost: '',
  apiUrl: '',
  apiKey: '',
  apiAuthStyle: 'auto',
  smtpHost: '',
  smtpPort: 587 as number | '',
  note: '',
  proxy: '',
  groupId: 'default',
  type: 'unknown' as MailAccount['type'],
})
const showSend = ref(false)
const sendForm = ref({ to: '', subject: '', body: '' })
const sendBusy = ref(false)

const moveGroupId = ref('')

/** Import confirm modal */
interface ImportDraftRow {
  id: string
  selected: boolean
  email: string
  type: MailAccount['type']
  brand?: string
  rawLine: string
  message: string
  warnings: string[]
  partial: Omit<MailAccount, 'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'>
  exists: boolean
  existingStatus?: MailAccount['status']
  checkStatus: 'idle' | 'checking' | 'ok' | 'error' | 'skipped'
  checkError?: string
  editing?: boolean
  editPassword?: string
  editClientId?: string
  editRefreshToken?: string
  editImapHost?: string
  editNote?: string
}
const showImportModal = ref(false)
const importDraftRows = ref<ImportDraftRow[]>([])
const importValidating = ref(false)
const importConfirmBusy = ref(false)
const importSkipOk = ref(true)
/** Cancel in-flight import precheck (close modal / re-run). */
let importAbort: AbortController | null = null
/** Import precheck: cookie needs longer; still below browser hang feel */
const PRECHECK_TIMEOUT_MS = 70_000
/** local = browser; cloud = device-scoped server store */
const importTarget = ref<'local' | 'cloud'>('local')
const importCloudPoll = ref(false)
/** Layout panes (percent of viewport / split track) */
const SIDE_MIN_PX = 200
const PANE_MIN_PX = 100
const sideW = ref(
  (() => {
    const n = Number(localStorage.getItem('openmail.sideW') || 22)
    return Number.isFinite(n) ? Math.min(40, Math.max(12, n)) : 22
  })(),
)
// Account table is primary: default mail pane to 40% so list stays usable
const mailRatio = ref(
  (() => {
    const n = Number(localStorage.getItem('openmail.mailRatio') || 40)
    return Number.isFinite(n) ? Math.min(75, Math.max(15, n)) : 40
  })(),
) // account:mail = (100-mailRatio):mailRatio of main
const mailCollapsed = ref(localStorage.getItem('openmail.mailCollapsed') === '1')
const dragging = ref<'side' | 'mail' | null>(null)

/** ≤1100px: single column + import as drawer (covers tablets / small laptop windows) */
const NARROW_MQ = '(max-width: 1100px)'
const isNarrow = ref(
  typeof window !== 'undefined' ? window.matchMedia(NARROW_MQ).matches : false,
)
let mqNarrow: MediaQueryList | null = null
function onNarrowChange(e: MediaQueryListEvent | MediaQueryList) {
  isNarrow.value = e.matches
  if (e.matches) {
    // Narrow: drawer closed so main content is full-width
    importCollapsed.value = true
    denseCols.value = true
  }
}

/** Compact = essential cols; full = all cols */
const denseCols = ref(localStorage.getItem('openmail.denseCols') === '1') // default full cols; '1' = compact
const codeMasked = ref(localStorage.getItem('openmail.codeMasked') !== '0')
const revealedCodes = ref<Set<string>>(new Set())

const lastFetchEmpty = ref(false)
const lastFetchOk = ref(false)

/** Effective compact mode (user toggle or forced on narrow) */
const effectiveDense = computed(() => denseCols.value || isNarrow.value)

/** Grid style — only apply resizable columns on desktop; aside ≥200px when open */
const consoleGridStyle = computed(() => {
  if (isNarrow.value) return undefined
  return {
    gridTemplateColumns: importCollapsed.value
      ? '48px 6px 1fr'
      : `minmax(${SIDE_MIN_PX}px, ${sideW.value}%) 6px minmax(0, 1fr)`,
  } as Record<string, string>
})

/** Desktop split rows — account table + mail panel; both ≥100px */
const splitMainStyle = computed(() => {
  if (isNarrow.value) return undefined
  const mail = mailRatio.value
  const table = 100 - mail
  return {
    gridTemplateRows: `minmax(${PANE_MIN_PX}px, ${table}fr) 6px minmax(${PANE_MIN_PX}px, ${mail}fr)`,
  } as Record<string, string>
})

watch(pageSize, (n) => {
  localStorage.setItem('openmail.pageSize', String(n))
  page.value = 1
})
watch(denseCols, (v) => localStorage.setItem('openmail.denseCols', v ? '1' : '0'))
watch(codeMasked, (v) => localStorage.setItem('openmail.codeMasked', v ? '1' : '0'))
watch(importCollapsed, (v) => localStorage.setItem('openmail.importCollapsed', v ? '1' : '0'))
watch(sideW, (v) => localStorage.setItem('openmail.sideW', String(Math.round(v))))
watch(mailRatio, (v) => localStorage.setItem('openmail.mailRatio', String(Math.round(v))))
watch(mailCollapsed, (v) => localStorage.setItem('openmail.mailCollapsed', v ? '1' : '0'))


function clearFilters() {
  accounts.filterQuery = ''
  accounts.filterStatus = 'all'
  accounts.filterStorage = 'all'
  accounts.filterBrand = 'all'
  accounts.filterGroup = 'all'
  page.value = 1
}

const messages = ref<MailMessage[]>([])
const selectedMessageId = ref<string | null>(null)
const mailLoading = ref(false)
/** How many cached mails to show in the panel (scroll load-more grows this). */
const mailVisibleCount = ref(20)
const MAIL_FIRST_PAGE = 20
const MAIL_LOAD_MORE = 10
/** Server fetch: loading older messages beyond cache */
const mailLoadingMore = ref(false)
/** Last load-more returned 0 new messages */
const mailNoMoreRemote = ref(false)
/** Mobile: expand sticky actions for a single row */
const expandedActId = ref<string | null>(null)

/** Filtered list with collapsed API children removed until parent is expanded. */
const listAll = computed(() => {
  const rows = accounts.filtered
  return rows.filter((a) => {
    if (!a.parentApiId) return true
    return expandedApiSources.value.has(a.parentApiId)
  })
})

/** Visible slice (newest-first); grows via "load more" without classic page numbers. */
const visibleMessages = computed(() => messages.value.slice(0, mailVisibleCount.value))
const hasMoreCached = computed(() => messages.value.length > mailVisibleCount.value)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(listAll.value.length / pageSize.value)),
)

const list = computed(() => {
  const p = Math.min(Math.max(1, page.value), totalPages.value)
  const start = (p - 1) * pageSize.value
  return listAll.value.slice(start, start + pageSize.value)
})

watch(listAll, () => {
  if (page.value > totalPages.value) page.value = totalPages.value
})

const selected = computed(() => accounts.selected)
const hasSelection = computed(() => Boolean(selected.value))
const hasAccounts = computed(() => accounts.accounts.length > 0)
const hasChecked = computed(() => accounts.selectedIds.size > 0)

const selectedMessage = computed(() => {
  if (!selectedMessageId.value) return messages.value[0] ?? null
  return messages.value.find((m) => m.id === selectedMessageId.value) ?? messages.value[0] ?? null
})

const detailHtml = computed(() => {
  const m = selectedMessage.value
  if (!m) return ''
  const html = (m.body_html || '').trim()
  return html ? sanitizeHtml(html) : ''
})

/**
 * Display recipient: prefer message.to; when providers (esp. CF temp) omit To,
 * fall back to the selected concrete mailbox address.
 */
function messageTo(m: { to?: string | null } | null | undefined): string {
  const raw = (m?.to || '').trim()
  if (raw) return raw
  const sel = selected.value
  if (
    sel &&
    !sel.isApiSource &&
    sel.email &&
    !/^api@/i.test(sel.email) &&
    sel.email.includes('@')
  ) {
    return sel.email
  }
  return ''
}

const detailText = computed(() => {
  const m = selectedMessage.value
  if (!m) return ''
  return m.body_text || m.body_preview || ''
})

/** Full-body modal (toolbar expand control) */
const showBodyModal = ref(false)

watch(selectedMessageId, () => {
  showBodyModal.value = false
})
watch(mailFolder, () => {
  showBodyModal.value = false
  // Re-filter list from cache for this folder (no network until fetch)
  const acc = selected.value
  if (acc) loadMessagesFromCache(acc)
})

const panelCode = computed(() => {
  if (selectedMessage.value?.verification_code) return selectedMessage.value.verification_code
  return selected.value?.latestCode
})

const mailEmptyKind = computed(() => {
  if (!selected.value) return 'no_account' as const
  if (mailLoading.value) return 'loading' as const
  if (messages.value.length) return 'has_mail' as const
  if (lastFetchOk.value && lastFetchEmpty.value) return 'empty_inbox' as const
  if (selected.value.status === 'error' && selected.value.lastError) return 'error' as const
  if (panelCode.value) return 'cached_code' as const
  return 'need_fetch' as const
})

/**
 * Load messages for account from local cache (filtered by current folder tab).
 * @param opts.preserveVisible — keep expanded load-more window (after merge)
 * @param opts.resetRemoteFlag — clear “no more older” (default true on select)
 */
function loadMessagesFromCache(
  acc: MailAccount,
  opts: { preserveVisible?: boolean; resetRemoteFlag?: boolean } = {},
) {
  const cached = mailCache.listFor(acc.email, mailFolder.value)
  messages.value = cached
  if (!opts.preserveVisible || !selectedMessageId.value) {
    selectedMessageId.value = cached[0]?.id ?? null
  } else if (!cached.some((m) => m.id === selectedMessageId.value)) {
    selectedMessageId.value = cached[0]?.id ?? null
  }
  if (opts.preserveVisible) {
    // Grow or clamp; never collapse below previous window after load-more merge
    mailVisibleCount.value = Math.min(
      Math.max(mailVisibleCount.value, Math.min(MAIL_FIRST_PAGE, cached.length)),
      cached.length || 0,
    )
  } else {
    mailVisibleCount.value = Math.min(MAIL_FIRST_PAGE, cached.length)
  }
  if (opts.resetRemoteFlag !== false && !opts.preserveVisible) {
    mailNoMoreRemote.value = false
  }
  lastFetchOk.value = cached.length > 0
  lastFetchEmpty.value = false
}

function errorMessage(e: unknown, fallback: string): string {
  if (isTimeoutError(e)) return t('console.requestTimeout')
  if (isAbortError(e)) return t('console.requestCancelled')
  if (e instanceof ApiError) {
    if (e.status === 404) return t('console.apiNotReady', { detail: e.message })
    return e.message || fallback
  }
  if (e instanceof Error) return e.message || fallback
  return fallback
}

async function doCopy(key: string, text: string | undefined | null) {
  const value = (text ?? '').trim()
  if (!value) return
  const ok = await copyText(value)
  if (ok) {
    copiedKey.value = key
    flashMsg(t('common.copied'))
    window.setTimeout(() => {
      if (copiedKey.value === key) copiedKey.value = ''
    }, 1200)
  }
}


/** Run async tasks with limited concurrency (in-browser, no extra server). */
const batchConcurrency = ref(Number(localStorage.getItem('openmail.batchConcurrency') || 5) || 5)
watch(batchConcurrency, (v) => localStorage.setItem('openmail.batchConcurrency', String(v)))

/**
 * Copy a cell value and also select that mailbox row (mobile often hits copy
 * instead of the row; we still want the mail panel to follow).
 */
function onCopyCell(
  e: Event,
  key: string,
  text: string | undefined | null,
  acc?: MailAccount,
) {
  e.stopPropagation()
  if (acc) {
    accounts.select(acc.id)
    if (fetchingId.value !== acc.id) loadMessagesFromCache(acc)
  }
  void doCopy(key, text)
}

function toggleRowActs(acc: MailAccount, e?: Event) {
  e?.stopPropagation()
  expandedActId.value = expandedActId.value === acc.id ? null : acc.id
}

function onImport() {
  if (!importText.value.trim()) return
  openImportPreview(importText.value)
}

function onClearInput() {
  importText.value = ''
}

function onImportTxt() {
  fileInput.value?.click()
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    importText.value = String(reader.result ?? '')
    openImportPreview(importText.value)
    input.value = ''
  }
  reader.readAsText(file)
}

function openImportPreview(text: string) {
  const { accounts: parsed, lines, warnings } = parseImportText(text)
  const rows: ImportDraftRow[] = []
  let pi = 0
  for (const line of lines) {
    if (!line.ok || !line.account) continue
    const partial = parsed[pi++]
    if (!partial) continue
    const email = partial.email.toLowerCase()
    const existing =
      accounts.localAccounts.find((a) => a.email.toLowerCase() === email) ||
      accounts.serverAccounts.find((a) => a.email.toLowerCase() === email)
    rows.push({
      id: `draft_${email}_${Math.random().toString(36).slice(2, 7)}`,
      selected: true,
      email: partial.email,
      type: partial.type,
      brand: partial.brand,
      rawLine: partial.rawLine || line.raw,
      message: line.message || '',
      warnings: [...(line.warnings || []), ...(warnings.filter((w) => w.includes(partial.email)))],
      partial: { ...partial, groupId: accounts.importGroupId || 'default' },
      exists: Boolean(existing),
      existingStatus: existing?.status,
      // Format-valid by default; live precheck may upgrade to ok/error
      checkStatus: 'ok',
      checkError: undefined,
      editPassword: partial.password || partial.authCode || '',
      editClientId: partial.clientId || '',
      editRefreshToken: partial.refreshToken || '',
      editImapHost: partial.imapHost || '',
      editNote: partial.note || '',
    })
  }
  if (!rows.length) {
    flashMsg(t('console.importNoValid'), 'danger')
    return
  }
  // Mark format-only when precheck is off
  if (!userSettings.s.importPrecheck) {
    for (const r of rows) {
      r.message = r.message
        ? `${r.message} · ${t('console.importFormatOnly')}`
        : t('console.importFormatOnly')
    }
  }
  importDraftRows.value = rows
  showImportModal.value = true
  void userSettings.loadPublicConfig()
  if (userSettings.s.importPrecheck) {
    void validateImportDrafts()
  }
}

function closeImportModal() {
  if (importAbort) {
    importAbort.abort()
    importAbort = null
  }
  importValidating.value = false
  // Leave rows that were still checking as skipped so user can still import
  for (const r of importDraftRows.value) {
    if (r.checkStatus === 'checking') {
      r.checkStatus = 'idle'
      r.checkError = undefined
    }
  }
  showImportModal.value = false
}

function skipImportPrecheck() {
  if (importAbort) {
    importAbort.abort()
    importAbort = null
  }
  importValidating.value = false
  for (const r of importDraftRows.value) {
    if (r.checkStatus === 'checking' || r.checkStatus === 'idle') {
      r.checkStatus = 'ok'
      r.checkError = undefined
      if (!r.message?.includes(t('console.importFormatOnly'))) {
        r.message = r.message
          ? `${r.message} · ${t('console.importFormatOnly')}`
          : t('console.importFormatOnly')
      }
    }
  }
}

function applyDraftEdits(row: ImportDraftRow) {
  row.partial = {
    ...row.partial,
    password: row.editPassword || undefined,
    authCode: row.editPassword || undefined,
    clientId: row.editClientId || undefined,
    refreshToken: row.editRefreshToken || undefined,
    imapHost: row.editImapHost || undefined,
    note: row.editNote || undefined,
  }
  row.email = row.partial.email
  row.editing = false
}

async function revalidateOneDraft(row: ImportDraftRow) {
  applyDraftEdits(row)
  row.selected = true
  row.checkStatus = 'checking'
  row.checkError = undefined
  try {
    const result = await proxyFetchMail(
      {
        email: row.partial.email,
        provider: row.partial.type === 'unknown' ? 'cookie' : row.partial.type,
        folder: 'inbox',
        quick: true,
        password: row.partial.password || row.partial.authCode,
        credential: credentialFromLocal({
          type: row.partial.type,
          refreshToken: row.partial.refreshToken,
          clientId: row.partial.clientId,
          apiUrl: row.partial.apiUrl,
          imapHost: row.partial.imapHost,
          imapPort: row.partial.imapPort,
          authCode: row.partial.authCode,
          password: row.partial.password,
        }),
        proxy: row.partial.proxy || undefined,
      },
      { timeoutMs: PRECHECK_TIMEOUT_MS },
    )
    if (result.ok === false) {
      row.checkStatus = 'error'
      row.checkError = result.error || t('console.fetchFailed')
    } else {
      row.checkStatus = 'ok'
      const code = extractCode(result)
      if (code) row.message = `code ${code}`
      // stash cookies on draft so import can persist them if needed
      if (result.session_cookies?.length) {
        ;(row.partial as { sessionCookies?: unknown }).sessionCookies = result.session_cookies
        if (result.session_meta) {
          ;(row.partial as { sessionMeta?: unknown }).sessionMeta = result.session_meta
        }
      }
    }
  } catch (e) {
    if (isAbortError(e) && !isTimeoutError(e)) {
      row.checkStatus = 'idle'
      return
    }
    row.checkStatus = 'error'
    row.checkError = errorMessage(e, t('console.fetchFailed'))
  }
}

async function validateImportDrafts() {
  // Cancel previous precheck batch
  if (importAbort) {
    importAbort.abort()
    importAbort = null
  }
  const ctrl = new AbortController()
  importAbort = ctrl
  importValidating.value = true
  try {
    await mapPool(importDraftRows.value, batchConcurrency.value, async (row) => {
      if (ctrl.signal.aborted) return
      if (!row.selected) {
        row.checkStatus = 'skipped'
        return
      }
      if (importSkipOk.value && row.exists && row.existingStatus === 'ok') {
        row.checkStatus = 'ok'
        row.message = t('console.importWillSkipOk')
        return
      }
      row.checkStatus = 'checking'
      row.checkError = undefined
      try {
        const result = await proxyFetchMail(
          {
            email: row.partial.email,
            provider: row.partial.type === 'unknown' ? 'cookie' : row.partial.type,
            folder: 'inbox',
            quick: true,
            password: row.partial.password || row.partial.authCode,
            credential: credentialFromLocal({
              type: row.partial.type,
              refreshToken: row.partial.refreshToken,
              clientId: row.partial.clientId,
              apiUrl: row.partial.apiUrl,
              imapHost: row.partial.imapHost,
              imapPort: row.partial.imapPort,
              authCode: row.partial.authCode,
              password: row.partial.password,
            }),
            proxy: row.partial.proxy || undefined,
          },
          { timeoutMs: PRECHECK_TIMEOUT_MS, signal: ctrl.signal },
        )
        if (ctrl.signal.aborted) return
        if (result.ok === false) {
          row.checkStatus = 'error'
          row.checkError = result.error || t('console.fetchFailed')
        } else {
          row.checkStatus = 'ok'
          const code = extractCode(result)
          if (code) row.message = `${row.message} · code ${code}`
          if (result.session_cookies?.length) {
            ;(row.partial as { sessionCookies?: unknown }).sessionCookies = result.session_cookies
            if (result.session_meta) {
              ;(row.partial as { sessionMeta?: unknown }).sessionMeta = result.session_meta
            }
          }
        }
      } catch (e) {
        if (ctrl.signal.aborted || (isAbortError(e) && !isTimeoutError(e))) {
          if (row.checkStatus === 'checking') row.checkStatus = 'idle'
          return
        }
        row.checkStatus = 'error'
        row.checkError = errorMessage(e, t('console.fetchFailed'))
      }
    })
  } finally {
    if (importAbort === ctrl) importAbort = null
    importValidating.value = false
  }
}

async function confirmImportDrafts() {
  const selected = importDraftRows.value.filter((r) => r.selected)
  if (!selected.length) {
    flashMsg(t('console.needCheckAccounts'), 'danger')
    return
  }
  // Stop precheck if still running — allow import of selected rows now
  if (importValidating.value || importAbort) {
    skipImportPrecheck()
  }
  // Apply any open editors
  for (const r of selected) {
    if (r.editing) applyDraftEdits(r)
  }
  importConfirmBusy.value = true
  try {
    if (!userSettings.publicConfigLoaded || !userSettings.quota) {
      await userSettings.loadPublicConfig()
    }
    const q = userSettings.quota
    const toCloud = importTarget.value === 'cloud'
    const existingEmails = new Set(
      (toCloud ? accounts.serverAccounts : accounts.localAccounts).map((a) =>
        a.email.toLowerCase(),
      ),
    )
    const newCount = selected.filter((r) => !existingEmails.has(r.email.toLowerCase())).length

    if (toCloud) {
      if (q && !q.licensed && q.max_cloud_accounts != null && q.max_cloud_accounts >= 0) {
        const used = q.cloud_used ?? accounts.serverAccounts.length
        const remaining = Math.max(0, q.max_cloud_accounts - used)
        if (newCount > remaining) {
          flashMsg(
            t('console.quotaCloudExceeded', {
              max: q.max_cloud_accounts,
              current: used,
              adding: newCount,
            }),
            'danger',
          )
          return
        }
      }
    } else if (q && !q.licensed && q.max_local_accounts != null && q.max_local_accounts >= 0) {
      const remaining = Math.max(0, q.max_local_accounts - accounts.localAccounts.length)
      if (newCount > remaining) {
        flashMsg(
          t('console.quotaLocalExceeded', {
            max: q.max_local_accounts,
            current: accounts.localAccounts.length,
            adding: newCount,
          }),
          'danger',
        )
        return
      }
    }

    const partials = selected.map((r) => r.partial)
    const statusByEmail: Record<string, { status: MailAccount['status']; lastError?: string }> = {}
    for (const r of selected) {
      if (r.checkStatus === 'error') {
        statusByEmail[r.email.toLowerCase()] = {
          status: 'error',
          lastError: r.checkError,
        }
      } else if (r.checkStatus === 'ok') {
        statusByEmail[r.email.toLowerCase()] = { status: 'ok' }
      }
      // idle / skipped / checking → leave status unknown until first fetch
    }

    if (toCloud) {
      const result = await accounts.importPartialsToCloud(partials, {
        skipOkExisting: importSkipOk.value,
        groupId: accounts.importGroupId || 'default',
        syncEnabled: importCloudPoll.value,
        statusByEmail,
      })
      await userSettings.loadPublicConfig()
      flashMsg(
        t('console.importResult', {
          created: result.created,
          updated: result.updated,
          invalid: result.invalid,
        }) +
          (result.errors?.length ? ` · ${result.errors[0]}` : '') +
          (result.warnings?.length ? ` · ${result.warnings[0]}` : ''),
        result.errors?.length ? 'danger' : undefined,
      )
      if (!result.errors?.length) {
        showImportModal.value = false
        importDraftRows.value = []
        importText.value = ''
      }
    } else {
      const result = accounts.importPartials(partials, {
        skipOkExisting: importSkipOk.value,
        groupId: accounts.importGroupId || 'default',
        statusByEmail,
        maxLocal:
          q && !q.licensed && q.max_local_accounts != null
            ? q.max_local_accounts
            : undefined,
      })
      flashMsg(
        t('console.importResult', {
          created: result.created,
          updated: result.updated,
          invalid: result.invalid,
        }) + (result.warnings?.length ? ` · ${result.warnings[0]}` : ''),
      )
      showImportModal.value = false
      importDraftRows.value = []
      importText.value = ''
    }
  } finally {
    importConfirmBusy.value = false
  }
}

const importQuotaLabel = computed(() => {
  const q = userSettings.quota
  if (!q) return ''
  if (q.licensed) return t('console.quotaUnlimited')
  if (importTarget.value === 'cloud') {
    const used = q.cloud_used ?? accounts.serverAccounts.length
    const max = q.max_cloud_accounts
    const poll = `${q.poll_used_hour ?? 0}/${q.max_poll_per_hour ?? '—'}`
    return t('console.quotaCloudBar', {
      used,
      max: max ?? '—',
      poll,
    })
  }
  const used = accounts.localAccounts.length
  const max = q.max_local_accounts
  return t('console.quotaLocalBar', { used, max: max ?? '—' })
})

function toggleAllDraft(sel: boolean) {
  for (const r of importDraftRows.value) r.selected = sel
}

function removeDraftSelected() {
  importDraftRows.value = importDraftRows.value.filter((r) => !r.selected)
}

function selectDraftErrors() {
  for (const r of importDraftRows.value) {
    r.selected = r.checkStatus === 'error' || (r.exists && r.existingStatus === 'error')
  }
}

/* Layout drag — enforce min aside 200px, account/mail panes ≥100px */
function onDragStart(kind: 'side' | 'mail', e: MouseEvent) {
  if (isNarrow.value) return
  dragging.value = kind
  e.preventDefault()
  document.body.style.cursor = kind === 'side' ? 'col-resize' : 'row-resize'
  document.body.style.userSelect = 'none'
  const onMove = (ev: MouseEvent) => {
    if (dragging.value === 'side') {
      // Measure against the console grid, not full window (padding / rail skew)
      const root = document.querySelector('.console') as HTMLElement | null
      const rect = root?.getBoundingClientRect()
      const total = rect?.width || window.innerWidth || 1
      const left = rect?.left || 0
      const minPct = Math.min(38, (SIDE_MIN_PX / total) * 100)
      const pct = ((ev.clientX - left) / total) * 100
      sideW.value = Math.min(48, Math.max(minPct, pct))
      if (importCollapsed.value && sideW.value > minPct) importCollapsed.value = false
    } else if (dragging.value === 'mail') {
      // Measure split track only (not toolbar/filters) so ratio stays accurate
      const split = document.querySelector('.split-main') as HTMLElement | null
      if (!split) return
      const rect = split.getBoundingClientRect()
      if (rect.height < PANE_MIN_PX * 2 + 12) return
      const minPct = (PANE_MIN_PX / rect.height) * 100
      const y = ev.clientY - rect.top
      // Mail pane is the bottom track
      const ratio = (1 - y / rect.height) * 100
      mailRatio.value = Math.min(100 - minPct, Math.max(minPct, ratio))
      mailCollapsed.value = false
    }
  }
  const onUp = () => {
    dragging.value = null
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

function toggleImportPanel() {
  importCollapsed.value = !importCollapsed.value
  if (!importCollapsed.value && sideW.value < 16) sideW.value = 22
}

function onClearLocal() {
  if (!window.confirm(t('console.clearLocalConfirm'))) return
  accounts.clearAllLocalStorage()
  messages.value = []
  flashMsg(t('common.clear'))
}


function onExportCredentials() {
  const text = exportCredentialsTxt(accounts.accounts)
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `openmail-credentials-${Date.now()}.txt`
  a.click()
  URL.revokeObjectURL(a.href)
  flashMsg(t('console.exportCredentialsDone'))
}

function onExportSystem() {
  const snap = buildSystemSnapshot({
    accounts: accounts.localAccounts,
    groups: groups.value,
    importGroupId: accounts.importGroupId,
    settings: {
      retentionDays: userSettings.s.retentionDays,
      lookbackDays: userSettings.s.lookbackDays,
      firstFullDone: { ...userSettings.s.firstFullDone },
      batchConcurrency: batchConcurrency.value,
      codeMasked: codeMasked.value,
      denseCols: denseCols.value,
    },
    deviceId: getDeviceId(),
    licenseToken: getLicenseToken() || undefined,
    mailCache: mailCache.byEmail as Record<string, unknown[]>,
    twofa: twofa.entries,
  })
  const json = JSON.stringify(snap)
  const blob = new Blob([json], { type: 'application/json;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `openmail-system-${Date.now()}.json`
  a.click()
  URL.revokeObjectURL(a.href)
  flashMsg(t('console.exportSystemDone'))
}

function onImportSystemFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const snap = parseSystemSnapshot(String(reader.result || ''))
      // restore groups
      if (snap.groups?.length) {
        groups.value = snap.groups
        saveGroups(snap.groups)
      }
      if (snap.importGroupId) accounts.importGroupId = snap.importGroupId
      if (snap.settings) {
        userSettings.s.retentionDays = snap.settings.retentionDays ?? 90
        userSettings.s.lookbackDays = snap.settings.lookbackDays ?? 3
        userSettings.s.firstFullDone = snap.settings.firstFullDone || {}
        batchConcurrency.value = snap.settings.batchConcurrency || 10
        codeMasked.value = snap.settings.codeMasked !== false
        denseCols.value = !!snap.settings.denseCols
      }
      if (snap.licenseToken) setLicenseToken(snap.licenseToken)
      if (snap.mailCache) {
        mailCache.replaceAll(
          snap.mailCache as Record<string, never[]>,
          userSettings.s.retentionDays,
        )
      }
      if (Array.isArray(snap.twofa) && snap.twofa.length) {
        twofa.replaceAll(snap.twofa as import('@/stores/twofa').TwoFaEntry[])
      }
      // restore local accounts wholesale (respect unlicensed local quota when known)
      const q = userSettings.quota
      let restored = snap.accounts
      let truncated = false
      if (q && !q.licensed && q.max_local_accounts != null && q.max_local_accounts >= 0) {
        if (restored.length > q.max_local_accounts) {
          restored = restored.slice(0, q.max_local_accounts)
          truncated = true
        }
      }
      accounts.localAccounts.splice(0, accounts.localAccounts.length, ...restored)
      if (truncated) {
        flashMsg(
          t('console.quotaLocalExceeded', {
            max: q!.max_local_accounts,
            current: 0,
            adding: snap.accounts.length,
          }),
          'danger',
        )
      } else {
        flashMsg(t('console.importSystemDone', { n: restored.length }))
      }
    } catch (err) {
      flashMsg(t('console.importSystemFailed'), 'danger')
    }
    input.value = ''
  }
  reader.readAsText(file)
}


function onSelectPage() {
  accounts.selectPage(list.value.map((a) => a.id))
}

function statusLabel(s: MailAccount['status']) {
  if (s === 'ok') return t('console.statusOk')
  if (s === 'error') return t('console.statusError')
  return t('console.statusUnknown')
}

function statusClass(s: MailAccount['status']) {
  if (s === 'ok') return 'is-ok'
  if (s === 'error') return 'is-err'
  return 'is-unknown'
}

function canFetch(acc: MailAccount): boolean {
  return accountCanFetch(acc)
}

function canSend(acc: MailAccount): boolean {
  return accountCanSend(acc)
}

function fetchDisabledReason(acc: MailAccount): string | undefined {
  if (fetchingId.value === acc.id) return undefined
  if (!canFetch(acc)) return t('console.cannotFetchHint')
  return undefined
}

function sendDisabledReason(acc: MailAccount): string | undefined {
  if (!canSend(acc)) return t('console.cannotSendHint')
  return undefined
}

async function toggleStar(acc: MailAccount, e?: Event) {
  e?.stopPropagation()
  await accounts.patchAccount(acc.id, { starred: !acc.starred })
}

function brandLabel(b?: string) {
  return brandLabelUtil(t, b)
}

/** Prefer IMAP host brand so custom domains on Gmail/QQ IMAP show the right chip. */
function accountBrand(acc: MailAccount): string {
  return resolveAccountBrand({
    email: acc.email,
    imapHost: acc.imapHost,
    smtpHost: acc.smtpHost,
    apiUrl: acc.apiUrl,
    type: acc.type,
    brand: acc.brand,
  })
}
function typeLabel(type: MailAccount['type']) {
  return typeLabelUtil(t, type)
}
function secretHint(acc: MailAccount): string {
  return secretHintUtil(t, acc)
}
function hostLabel(acc: MailAccount): string {
  return hostLabelUtil(t, acc)
}
function formatTime(ts?: number) {
  return formatRelativeTime(t, locale.value, ts)
}
function displayCode(code: string | undefined, id: string): string {
  return displayCodeUtil(code, id, codeMasked.value, revealedCodes.value)
}

function toggleRevealCode(e: Event, id: string, code?: string) {
  e.stopPropagation()
  if (!code) return
  if (codeMasked.value && !revealedCodes.value.has(id)) {
    const next = new Set(revealedCodes.value)
    next.add(id)
    revealedCodes.value = next
    return
  }
  void doCopy(`code-${id}`, code)
}

function applyFetchResult(acc: MailAccount, result: FetchResult) {
  const code = extractCode(result)
  const msgs = result.messages ?? []
  lastFetchOk.value = result.ok !== false
  const patch: Partial<MailAccount> = {
    latestCode: code ?? acc.latestCode,
    status: result.ok === false ? 'error' : 'ok',
    lastError: result.ok === false ? result.error || t('console.fetchFailed') : undefined,
  }
  // Persist rolling cookies after successful cookie/mail.com fetch (reuse next time)
  if (result.ok !== false && result.session_cookies?.length) {
    patch.sessionCookies = result.session_cookies as Array<Record<string, unknown>>
    if (result.session_meta) {
      patch.sessionMeta = result.session_meta as Record<string, unknown>
    }
  }
  // HttpApi: discovered temp mailboxes under this Worker / api_url
  const mboxes =
    result.mailboxes ||
    (result.session_meta && Array.isArray((result.session_meta as { mailboxes?: string[] }).mailboxes)
      ? (result.session_meta as { mailboxes: string[] }).mailboxes
      : null)
  if (result.ok !== false && mboxes?.length && (acc.isApiSource || acc.type === 'http_api')) {
    patch.isApiSource = acc.isApiSource ?? !acc.parentApiId
    patch.apiMailboxes = mboxes
  }
  void accounts.patchAccount(acc.id, patch)
  if (result.ok !== false && mboxes?.length && (acc.isApiSource || (!acc.parentApiId && acc.type === 'http_api'))) {
    accounts.syncApiMailboxes(acc.id, mboxes)
    // Auto-expand so newly discovered temp mailboxes are visible under the source
    if (!expandedApiSources.value.has(acc.id)) {
      const next = new Set(expandedApiSources.value)
      next.add(acc.id)
      expandedApiSources.value = next
      try {
        localStorage.setItem('openmail.expandedApiSources', JSON.stringify([...next]))
      } catch {
        /* ignore */
      }
    }
  }
  if (result.ok !== false) {
    try {
      // Tag folder so inbox/spam/sent tabs filter correctly
      const folderTag = mailCache.normalizeFolder(result.folder || mailFolder.value)
      const tagged = msgs.map((m) => ({
        ...m,
        folder: mailCache.normalizeFolder(m.folder || folderTag),
      }))
      // Merge into durable local cache, then show full cached list
      // API source row: don't dump all mailboxes' mail into api@host cache unless no filter
      if (!acc.isApiSource || tagged.length) {
        mailCache.merge(acc.email, tagged, userSettings.s.retentionDays)
      }
      userSettings.markFetched(
        acc.email,
        userSettings.needsFullFetch(acc.email) || tagged.length > 0,
      )
      // Preserve expanded list when appending older pages
      loadMessagesFromCache(acc, { preserveVisible: true, resetRemoteFlag: false })
      lastFetchEmpty.value = messages.value.length === 0
    } catch {
      messages.value = msgs
      selectedMessageId.value = msgs[0]?.id ?? null
      lastFetchEmpty.value = msgs.length === 0
    }
  }
  if (code) {
    flashMsg(t('console.fetchGotCode', { code }))
  } else if (result.ok === false && result.error) {
    flashMsg(result.error, 'danger')
  } else {
    flashMsg(t('console.fetchDone', { n: msgs.length }))
  }
}

/**
 * Core fetch for one account.
 * When silent=true (batch): do not clobber global selection / message panel.
 * Returns true if fetch succeeded (ok !== false).
 */
/**
 * Resolve secrets for fetch: cloud list rows often lack password after reload;
 * merge matching local-vault row (same email) so proxy fetch still works.
 */
function resolveFetchAccount(acc: MailAccount): MailAccount {
  if (accountHasLocalFetchSecrets(acc)) return acc
  const local = accounts.localAccounts.find(
    (a) => a.email.toLowerCase() === acc.email.toLowerCase() && a.id !== acc.id,
  )
  if (!local || !accountHasLocalFetchSecrets(local)) return acc
  return {
    ...acc,
    password: acc.password || local.password,
    authCode: acc.authCode || local.authCode,
    refreshToken: acc.refreshToken || local.refreshToken,
    clientId: acc.clientId || local.clientId,
    apiUrl: acc.apiUrl || local.apiUrl,
    apiKey: acc.apiKey || local.apiKey,
    apiAuthStyle: acc.apiAuthStyle || local.apiAuthStyle,
    imapHost: acc.imapHost || local.imapHost,
    imapPort: acc.imapPort || local.imapPort,
    smtpHost: acc.smtpHost || local.smtpHost,
    smtpPort: acc.smtpPort || local.smtpPort,
    sessionCookies: acc.sessionCookies?.length ? acc.sessionCookies : local.sessionCookies,
    sessionMeta: acc.sessionMeta || local.sessionMeta,
    proxy: acc.proxy || local.proxy,
  }
}

type FetchOneOpts = {
  silent?: boolean
  /** Load older messages before this ISO (pagination). */
  before?: string
  maxMessages?: number
  /** Clear local cache first, then pull latest N. */
  clearFirst?: boolean
  /** Skip incremental `since` (always recent window). */
  forceRecent?: boolean
}

async function fetchOne(
  acc: MailAccount,
  quick = true,
  opts: FetchOneOpts = {},
): Promise<boolean> {
  const silent = Boolean(opts.silent)
  if (!silent) {
    fetchingId.value = acc.id
    mailLoading.value = true
    accounts.select(acc.id)
    // Keep showing cache while fetching
    if (!messages.value.length && !opts.clearFirst) loadMessagesFromCache(acc)
    lastFetchEmpty.value = false
    lastFetchOk.value = false
  }
  try {
    if (opts.clearFirst) {
      mailCache.clearMailbox(acc.email)
      messages.value = []
      selectedMessageId.value = null
      mailVisibleCount.value = MAIL_FIRST_PAGE
      mailNoMoreRemote.value = false
    }
    const folder = mailFolder.value
    // Manual / clear / first-full → recent window (no since).
    // Silent batch may use since based on newest *mail* date only (see settings.sinceFor).
    const wantRecent =
      Boolean(opts.forceRecent) ||
      Boolean(opts.clearFirst) ||
      (!opts.before && userSettings.needsFullFetch(acc.email))
    const since =
      opts.before || wantRecent ? undefined : userSettings.sinceFor(acc.email)
    const full = wantRecent && !opts.before
    const maxMessages =
      opts.maxMessages ??
      (opts.before
        ? MAIL_LOAD_MORE
        : wantRecent || !since
          ? MAIL_FIRST_PAGE
          : MAIL_FIRST_PAGE)
    // Prefer local-vault secrets (cloud mirror of client-sealed rows has none)
    const src = resolveFetchAccount(acc)
    // Child mailbox under HttpApi: fetch with parent api_url + this address as filter
    const parent =
      src.parentApiId ? accounts.findById(src.parentApiId) : undefined
    const apiUrl = src.apiUrl || parent?.apiUrl
    const apiSecret = src.apiKey || src.password || parent?.apiKey || parent?.password
    const apiAuthStyle = src.apiAuthStyle || parent?.apiAuthStyle || 'auto'
    const fetchEmail =
      src.isApiSource && src.email.startsWith('api@')
        ? src.email
        : src.email
    const hasLocalSecrets = accountHasLocalFetchSecrets(src) || Boolean(apiUrl && (apiSecret || parent))
    let result: FetchResult
    // Server decrypt only when NOT client-sealed and browser has no secrets.
    // Client-sealed blobs are intentional: admin cannot read plaintext.
    if (
      !hasLocalSecrets &&
      src.storage === 'server' &&
      src.serverId &&
      !src.clientSealed
    ) {
      result = await fetchServerAccount(src.serverId, { folder, quick })
    } else if (!hasLocalSecrets && src.clientSealed) {
      result = {
        ok: false,
        error: t('console.clientSealedNeedLocal'),
        messages: [],
        folder,
      }
    } else if (!hasLocalSecrets) {
      result = {
        ok: false,
        error: t('console.needLocalSecrets'),
        messages: [],
        folder,
      }
    } else {
      const provider =
        src.type === 'http_api' || parent?.type === 'http_api'
          ? 'http_api'
          : src.type === 'unknown'
            ? 'cookie'
            : src.type
      const credential =
        provider === 'http_api'
          ? {
              api_url: apiUrl,
              email: fetchEmail,
              ...(apiSecret
                ? { api_key: apiSecret, password: apiSecret, api_auth_style: apiAuthStyle }
                : { api_auth_style: 'none' }),
            }
          : credentialFromLocal(src)
      // Always send an explicit page size so providers do not fall back to tiny quick=5.
      // full=true only for "recent window" (ignore since); load-older uses before only.
      result = await proxyFetchMail({
        email: fetchEmail,
        provider,
        folder,
        quick,
        password: apiSecret || src.password || parent?.password,
        credential,
        cookies: src.sessionCookies?.length ? src.sessionCookies : undefined,
        proxy: src.proxy || parent?.proxy || undefined,
        since: full || opts.before ? undefined : since || undefined,
        before: opts.before || undefined,
        max_messages: maxMessages ?? MAIL_FIRST_PAGE,
        full: Boolean(full),
      })
    }

    if (silent) {
      // Apply status/code/cache without toast spam or panel overwrite
      const code = extractCode(result)
      const msgs = result.messages ?? []
      const mboxes =
        result.mailboxes ||
        (result.session_meta &&
        Array.isArray((result.session_meta as { mailboxes?: string[] }).mailboxes)
          ? (result.session_meta as { mailboxes: string[] }).mailboxes
          : null)
      void accounts.patchAccount(acc.id, {
        latestCode: code ?? acc.latestCode,
        status: result.ok === false ? 'error' : 'ok',
        lastError: result.ok === false ? result.error || t('console.fetchFailed') : undefined,
        ...(result.ok !== false && result.session_cookies?.length
          ? {
              sessionCookies: result.session_cookies as Array<Record<string, unknown>>,
              sessionMeta: result.session_meta as Record<string, unknown> | undefined,
            }
          : {}),
        ...(result.ok !== false && mboxes?.length && (acc.isApiSource || acc.type === 'http_api')
          ? { isApiSource: acc.isApiSource ?? !acc.parentApiId, apiMailboxes: mboxes }
          : {}),
      })
      if (
        result.ok !== false &&
        mboxes?.length &&
        (acc.isApiSource || (!acc.parentApiId && acc.type === 'http_api'))
      ) {
        accounts.syncApiMailboxes(acc.id, mboxes)
        if (!expandedApiSources.value.has(acc.id)) {
          const next = new Set(expandedApiSources.value)
          next.add(acc.id)
          expandedApiSources.value = next
          try {
            localStorage.setItem('openmail.expandedApiSources', JSON.stringify([...next]))
          } catch {
            /* ignore */
          }
        }
      }
      if (result.ok !== false && msgs.length) {
        try {
          const folderTag = mailCache.normalizeFolder(result.folder || mailFolder.value)
          const tagged = msgs.map((m) => ({
            ...m,
            folder: mailCache.normalizeFolder(m.folder || folderTag),
          }))
          if (!acc.isApiSource || tagged.length) {
            mailCache.merge(acc.email, tagged, userSettings.s.retentionDays)
          }
          userSettings.markFetched(acc.email, true)
        } catch {
          /* ignore */
        }
      }
      return result.ok !== false
    }

    applyFetchResult(acc, result)
    return result.ok !== false
  } catch (e) {
    void accounts.patchAccount(acc.id, {
      status: 'error',
      lastError: errorMessage(e, t('console.fetchFailed')),
    })
    if (!silent) {
      if (!messages.value.length) loadMessagesFromCache(acc)
      lastFetchOk.value = false
      flashMsg(errorMessage(e, t('console.fetchFailed')), 'danger')
    }
    return false
  } finally {
    if (!silent) {
      fetchingId.value = null
      mailLoading.value = false
    }
  }
}

async function onFetchSelected(quick = true) {
  const acc = selected.value
  if (!acc) {
    flashMsg(t('console.needSelectAccount'), 'danger')
    return
  }
  mailNoMoreRemote.value = false
  // Always pull latest N (not since=lastFetchAt). Incremental since was too aggressive
  // after a successful fetch and looked like "broken" until Clear & refetch.
  await fetchOne(acc, quick, {
    silent: false,
    maxMessages: MAIL_FIRST_PAGE,
    forceRecent: true,
  })
}

/** Expand visible list from cache, or fetch older from server. */
async function onLoadMoreMails() {
  const acc = selected.value
  if (!acc) {
    flashMsg(t('console.needSelectAccount'), 'danger')
    return
  }
  // 1) Still more in local cache → just grow the window
  if (messages.value.length > mailVisibleCount.value) {
    mailVisibleCount.value = Math.min(
      mailVisibleCount.value + MAIL_LOAD_MORE,
      messages.value.length,
    )
    return
  }
  if (mailNoMoreRemote.value || mailLoadingMore.value || mailLoading.value) return

  // 2) Pull older page from server (before oldest cached)
  const before =
    mailCache.oldestUtcIso(acc.email, mailFolder.value) ||
    messages.value[messages.value.length - 1]?.date ||
    undefined
  if (!before) {
    // No date anchor — try a larger recent window instead
    mailLoadingMore.value = true
    try {
      const prevCount = messages.value.length
      await fetchOne(acc, false, {
        silent: false,
        maxMessages: Math.max(MAIL_FIRST_PAGE, prevCount + MAIL_LOAD_MORE),
        forceRecent: true,
      })
      if (messages.value.length <= prevCount) {
        mailNoMoreRemote.value = true
        flashMsg(t('console.mailNoMore'), 'danger')
      } else {
        mailVisibleCount.value = Math.min(
          messages.value.length,
          mailVisibleCount.value + MAIL_LOAD_MORE,
        )
      }
    } finally {
      mailLoadingMore.value = false
    }
    return
  }

  mailLoadingMore.value = true
  try {
    const prevIds = new Set(messages.value.map((m) => m.id))
    const prevCount = messages.value.length
    const ok = await fetchOne(acc, true, {
      silent: false,
      before: String(before),
      maxMessages: MAIL_LOAD_MORE,
    })
    if (!ok) return
    const added = messages.value.filter((m) => !prevIds.has(m.id)).length
    if (added === 0 && messages.value.length <= prevCount) {
      mailNoMoreRemote.value = true
      flashMsg(t('console.mailNoMore'), 'danger')
    } else {
      mailVisibleCount.value = Math.min(
        messages.value.length,
        Math.max(mailVisibleCount.value + Math.max(added, MAIL_LOAD_MORE), mailVisibleCount.value + 1),
      )
      if (added === 0) {
        // Merge may have refreshed same ids — still grow window if cache grew somehow
        mailVisibleCount.value = Math.min(
          messages.value.length,
          mailVisibleCount.value + MAIL_LOAD_MORE,
        )
      }
    }
  } finally {
    mailLoadingMore.value = false
  }
}

/** Clear local mails for selected mailbox, then pull latest 20. */
async function onClearAndRefetch() {
  const acc = selected.value
  if (!acc) {
    flashMsg(t('console.needSelectAccount'), 'danger')
    return
  }
  if (!window.confirm(t('console.mailClearConfirm'))) return
  mailNoMoreRemote.value = false
  await fetchOne(acc, true, {
    silent: false,
    clearFirst: true,
    forceRecent: true,
    maxMessages: MAIL_FIRST_PAGE,
  })
}

async function onBatchFetch() {
  const ids = [...accounts.selectedIds]
  if (!ids.length) {
    flashMsg(t('console.needCheckAccounts'), 'danger')
    return
  }
  batchBusy.value = true
  let ok = 0
  let fail = 0
  try {
    const list = ids.map((id) => accounts.findById(id)).filter(Boolean) as MailAccount[]
    await mapPool(list, batchConcurrency.value, async (acc) => {
      const success = await fetchOne(acc, true, { silent: true })
      if (success) ok += 1
      else fail += 1
    })
    flashMsg(t('console.batchFetchResult', { ok, fail }))
    // Refresh panel for currently selected if it was in the batch
    const cur = selected.value
    if (cur && ids.includes(cur.id)) {
      loadMessagesFromCache(cur)
    }
  } finally {
    batchBusy.value = false
  }
}

/** Auto-detect accounts still in 未检测 (unknown): poll every 5s, few concurrent. */
const AUTO_DETECT_INTERVAL_MS = 5_000
const AUTO_DETECT_BATCH = 3
const autoDetectBusy = ref(false)
const autoDetectInflight = new Set<string>()
let autoDetectTimer: ReturnType<typeof setInterval> | null = null

function unknownAccountsToDetect(): MailAccount[] {
  return accounts.accounts.filter((a) => {
    if (a.parentApiId) return false // children wait for parent expansion / own click
    if (a.status && a.status !== 'unknown') return false
    if (!accountCanFetch(a) && !(a.type === 'http_api' && a.apiUrl)) return false
    if (autoDetectInflight.has(a.id)) return false
    if (fetchingId.value === a.id) return false
    return true
  })
}

async function tickAutoDetect() {
  if (autoDetectBusy.value || batchBusy.value) return
  if (document.visibilityState === 'hidden') return
  const pending = unknownAccountsToDetect()
  if (!pending.length) return
  autoDetectBusy.value = true
  try {
    const batch = pending.slice(0, AUTO_DETECT_BATCH)
    await mapPool(batch, Math.min(AUTO_DETECT_BATCH, batchConcurrency.value || 3), async (acc) => {
      autoDetectInflight.add(acc.id)
      try {
        await fetchOne(acc, true, { silent: true })
      } finally {
        autoDetectInflight.delete(acc.id)
      }
    })
    const cur = selected.value
    if (cur && batch.some((a) => a.id === cur.id)) {
      loadMessagesFromCache(cur)
    }
  } finally {
    autoDetectBusy.value = false
  }
}

function startAutoDetect() {
  if (autoDetectTimer) return
  autoDetectTimer = setInterval(() => {
    void tickAutoDetect()
  }, AUTO_DETECT_INTERVAL_MS)
  // first tick soon after mount (vault may still hydrate)
  window.setTimeout(() => void tickAutoDetect(), 800)
}

function stopAutoDetect() {
  if (autoDetectTimer) {
    clearInterval(autoDetectTimer)
    autoDetectTimer = null
  }
  autoDetectInflight.clear()
}


/** Click row = select + load local cache (no network). Fetch button pulls remote. */
function onRowClick(acc: MailAccount) {
  accounts.select(acc.id)
  if (expandedActId.value && expandedActId.value !== acc.id) expandedActId.value = null
  if (fetchingId.value === acc.id) return
  loadMessagesFromCache(acc)
}

async function onBatchUploadCloud() {
  const ids = [...accounts.selectedIds].filter((id) => {
    const a = accounts.findById(id)
    return a && a.storage === 'local'
  })
  if (!ids.length) {
    flashMsg(t('console.needLocalForCloud'), 'danger')
    return
  }
  batchBusy.value = true
  try {
    const r = await accounts.uploadLocalToCloud(ids, { syncEnabled: true })
    await userSettings.loadPublicConfig()
    flashMsg(
      t('console.uploadCloudResult', { ok: r.ok, fail: r.fail }) +
        (r.errors[0] ? ` · ${r.errors[0]}` : ''),
      r.fail ? 'danger' : undefined,
    )
  } finally {
    batchBusy.value = false
  }
}

function onReimportHint(_acc?: MailAccount) {
  flashMsg(t('console.reimportHint'), 'danger')
  importCollapsed.value = false
}

function persistGroups() {
  saveGroups(groups.value)
}

function groupName(id?: string) {
  const g = groups.value.find((x) => x.id === (id || DEFAULT_GROUP_ID))
  return g?.name || t('console.groupDefault')
}

function groupStats(groupId: string) {
  const list = accounts.accounts.filter(
    (a) => (a.groupId || DEFAULT_GROUP_ID) === groupId,
  )
  return {
    total: list.length,
    error: list.filter((a) => a.status === 'error').length,
    unknown: list.filter((a) => a.status === 'unknown' || !a.status).length,
    ok: list.filter((a) => a.status === 'ok').length,
  }
}

function addGroup() {
  const name = newGroupName.value.trim()
  if (!name) {
    flashMsg(t('console.groupNewName'), 'danger')
    return
  }
  if (groups.value.some((x) => x.name === name)) {
    flashMsg(t('console.groupExists'), 'danger')
    return
  }
  const g = { id: uidGroup(), name, order: groups.value.length, color: '#6366f1' }
  groups.value = [...groups.value, g]
  persistGroups()
  newGroupName.value = ''
  accounts.importGroupId = g.id
  flashMsg(t('console.groupCreated', { name }))
}

function startRenameGroup(g: MailGroup) {
  editingGroupId.value = g.id
  editingGroupName.value = g.name
}

function saveRenameGroup() {
  const id = editingGroupId.value
  if (!id) return
  const name = editingGroupName.value.trim()
  if (!name) {
    flashMsg(t('console.groupNewName'), 'danger')
    return
  }
  if (groups.value.some((x) => x.name === name && x.id !== id)) {
    flashMsg(t('console.groupExists'), 'danger')
    return
  }
  groups.value = groups.value.map((g) => (g.id === id ? { ...g, name } : g))
  persistGroups()
  editingGroupId.value = null
  editingGroupName.value = ''
}

function removeGroup(id: string) {
  if (id === DEFAULT_GROUP_ID) {
    flashMsg(t('console.groupCannotDeleteDefault'), 'danger')
    return
  }
  const g = groups.value.find((x) => x.id === id)
  if (!g) return
  if (!window.confirm(t('console.groupDeleteConfirm', { name: g.name }))) return
  groups.value = groups.value.filter((x) => x.id !== id)
  persistGroups()
  for (const a of accounts.accounts) {
    if (a.groupId === id) void accounts.patchAccount(a.id, { groupId: DEFAULT_GROUP_ID })
  }
  if (accounts.filterGroup === id) accounts.filterGroup = 'all'
  if (accounts.importGroupId === id) accounts.importGroupId = DEFAULT_GROUP_ID
}

function openEdit(acc: MailAccount) {
  // Temp mailbox children inherit credentials from the API source — do not edit them.
  if (acc.parentApiId) {
    flashMsg(t('console.apiChildReadonly'), 'danger')
    return
  }
  editForm.value = {
    id: acc.id,
    email: acc.email,
    password: acc.password || acc.authCode || '',
    clientId: acc.clientId || '',
    refreshToken: acc.refreshToken || '',
    imapHost: acc.imapHost || '',
    apiUrl: acc.apiUrl || '',
    apiKey: acc.apiKey || acc.password || '',
    apiAuthStyle: acc.apiAuthStyle || 'auto',
    smtpHost: acc.smtpHost || '',
    smtpPort: acc.smtpPort || 587,
    note: acc.note || '',
    proxy: acc.proxy || '',
    groupId: acc.groupId || DEFAULT_GROUP_ID,
    type: acc.type,
  }
  showEdit.value = true
}

async function saveEdit() {
  const f = editForm.value
  if (!f.id) return
  const patch: Partial<MailAccount> = {
    note: f.note || undefined,
    proxy: f.proxy?.trim() || undefined,
    groupId: f.groupId || DEFAULT_GROUP_ID,
    status: 'unknown',
    lastError: undefined,
  }
  if (f.type === 'http_api') {
    patch.apiUrl = f.apiUrl || undefined
    patch.apiKey = f.apiKey || undefined
    patch.password = f.apiKey || undefined
    patch.apiAuthStyle = f.apiAuthStyle || 'auto'
  } else {
    patch.password = f.password || undefined
    patch.authCode = f.password || undefined
    patch.clientId = f.clientId || undefined
    patch.refreshToken = f.refreshToken || undefined
    patch.imapHost = f.imapHost || undefined
    patch.smtpHost = f.smtpHost?.trim() || undefined
    patch.smtpPort = f.smtpPort === '' ? undefined : Number(f.smtpPort) || undefined
  }
  await accounts.patchAccount(f.id, patch)
  showEdit.value = false
  flashMsg(t('console.saveEdit'))
}

function twoFaFor(acc: MailAccount) {
  // Depend on nowTick so remaining seconds re-render every second
  void twofa.nowTick
  return twofa.findByAccountId(acc.id) || twofa.findByAccountEmail(acc.email)
}

function twoFaCode(acc: MailAccount): string {
  const entry = twoFaFor(acc)
  return entry ? twofa.codeFor(entry) : ''
}

function twoFaRemain(acc: MailAccount): number {
  const entry = twoFaFor(acc)
  return entry ? twofa.remainingFor(entry) : 0
}

async function copyTwoFa(acc: MailAccount, e?: Event) {
  e?.stopPropagation()
  accounts.select(acc.id)
  if (fetchingId.value !== acc.id) loadMessagesFromCache(acc)
  const entry = twoFaFor(acc)
  if (!entry) return
  const code = twofa.codeFor(entry)
  if (await copyText(code)) flashMsg(t('common.copied'))
}

async function deleteOne(acc: MailAccount) {
  if (!window.confirm(t('console.deleteConfirm'))) return
  try {
    await accounts.removeById(acc.id)
    if (selected.value?.id === acc.id) {
      messages.value = []
    }
    flashMsg(t('console.deleteAccount'))
  } catch (e) {
    flashMsg(errorMessage(e, t('console.deleteAccount')), 'danger')
  }
}

async function patchAccountNote(acc: MailAccount, note: string) {
  await accounts.patchAccount(acc.id, { note: note || undefined })
}

function openSend(acc?: MailAccount | null) {
  const target = acc ?? selected.value
  if (!target) {
    flashMsg(t('console.needSelectAccount'), 'danger')
    return
  }
  if (!canSend(target)) {
    flashMsg(t('console.cannotSendHint'), 'danger')
    return
  }
  accounts.select(target.id)
  sendForm.value = { to: '', subject: '', body: '' }
  showSend.value = true
}

async function doSend() {
  const acc = selected.value
  if (!acc) return
  const to = sendForm.value.to.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean)
  if (!to.length) {
    flashMsg(t('console.sendTo'), 'danger')
    return
  }
  if (!acc.password && !acc.refreshToken && !acc.authCode) {
    flashMsg(t('console.sendNeedLocal'), 'danger')
    return
  }
  sendBusy.value = true
  try {
    const result = await proxySendMail({
      email: acc.email,
      provider: acc.type === 'unknown' ? 'imap' : acc.type,
      password: acc.password || acc.authCode,
      credential: credentialFromLocal(acc),
      proxy: acc.proxy || undefined,
      to,
      subject: sendForm.value.subject,
      body_text: sendForm.value.body,
    })
    if (result.ok) {
      void accounts.patchAccount(acc.id, {
        status: 'ok',
        lastError: undefined,
      })
      flashMsg(t('console.sendOk'))
      showSend.value = false
    } else {
      const err = result.error || t('console.sendFailed')
      void accounts.patchAccount(acc.id, {
        status: 'error',
        lastError: err,
      })
      flashMsg(err, 'danger')
    }
  } catch (e) {
    const err = errorMessage(e, t('console.sendFailed'))
    void accounts.patchAccount(acc.id, {
      status: 'error',
      lastError: err,
    })
    flashMsg(err, 'danger')
  } finally {
    sendBusy.value = false
  }
}

function onMoveGroup() {
  if (!moveGroupId.value || !accounts.selectedIds.size) return
  accounts.moveToGroup([...accounts.selectedIds], moveGroupId.value)
  flashMsg(t('console.groupMove'))
  moveGroupId.value = ''
}

function filterErrorsOnly() {
  accounts.filterStatus = 'error'
  page.value = 1
}

function onKeydown(e: KeyboardEvent) {
  const el = e.target as HTMLElement | null
  const tag = el?.tagName
  const editable =
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    !!el?.isContentEditable
  if (editable) {
    if (e.key === 'Escape') {
      el?.blur()
      showImportHelp.value = false
      showGroupManage.value = false
      showSend.value = false
    }
    return
  }
  // Esc: close drawers / deselect
  if (e.key === 'Escape') {
    e.preventDefault()
    if (showSend.value) {
      showSend.value = false
      return
    }
    if (showGroupManage.value) {
      showGroupManage.value = false
      return
    }
    if (showImportHelp.value) {
      showImportHelp.value = false
      return
    }
    if (isNarrow.value && !importCollapsed.value) {
      importCollapsed.value = true
      return
    }
    accounts.deselectAll()
    return
  }
  // Enter: fetch selected
  if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (hasSelection.value && !mailLoading.value) {
      e.preventDefault()
      void onFetchSelected(true)
    }
    return
  }
  // Tab with Alt: cycle mail folder inbox → spam → sent
  if (e.key === 'Tab' && e.altKey) {
    e.preventDefault()
    const order: Array<'inbox' | 'spam' | 'sent'> = ['inbox', 'spam', 'sent']
    const i = order.indexOf(mailFolder.value)
    mailFolder.value = order[(i + (e.shiftKey ? 2 : 1)) % 3]!
    return
  }
  if (e.key === '/') {
    e.preventDefault()
    document.querySelector<HTMLInputElement>('.search-wide')?.focus()
  } else if (e.key === 'f' || e.key === 'F') {
    e.preventDefault()
    void onFetchSelected(true)
  } else if (e.key === 'c' || e.key === 'C') {
    e.preventDefault()
    if (panelCode.value) void doCopy('hot-code', panelCode.value)
  } else if (e.key === 'e' || e.key === 'E') {
    e.preventDefault()
    if (selected.value) openEdit(selected.value)
  } else if (e.key === 'i' || e.key === 'I') {
    e.preventDefault()
    toggleImportPanel()
  } else if (e.key === 'j' || e.key === 'ArrowDown') {
    e.preventDefault()
    stepSelect(1)
  } else if (e.key === 'k' || e.key === 'ArrowUp') {
    e.preventDefault()
    stepSelect(-1)
  } else if (e.key === '?' || (e.shiftKey && e.key === '/')) {
    e.preventDefault()
    flashMsg(t('console.shortcutHint'), 'info')
  }
}

function stepSelect(delta: number) {
  const list = listAll.value
  if (!list.length) return
  const cur = accounts.selectedId
  let idx = list.findIndex((a) => a.id === cur)
  if (idx < 0) idx = delta > 0 ? -1 : 0
  const next = list[Math.min(list.length - 1, Math.max(0, idx + delta))]
  if (next) accounts.select(next.id)
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  mqNarrow = window.matchMedia(NARROW_MQ)
  onNarrowChange(mqNarrow)
  mqNarrow.addEventListener('change', onNarrowChange)
  twofa.startTicker()
  // Enforce layout mins against current viewport (saved % may be too small)
  if (typeof window !== 'undefined' && !isNarrow.value) {
    const minSidePct = Math.min(35, (SIDE_MIN_PX / window.innerWidth) * 100)
    if (sideW.value < minSidePct) sideW.value = minSidePct
  }
  void apiStatus.probe()
  void userSettings.loadPublicConfig().then(() => {
    userSettings.applyRetentionNow()
  })
  void accounts.loadServerAccounts().then(() => {
    userSettings.loadPublicConfig()
    // Drop firstFullDone keys for deleted / temp churn emails → free localStorage
    userSettings.pruneFetchMaps(accounts.accounts.map((a) => a.email))
  })
  // Also prune against current local list immediately (vault already hydrated)
  userSettings.pruneFetchMaps(accounts.accounts.map((a) => a.email))
  // Restore mail panel from durable local cache after refresh
  const sel = accounts.selected
  if (sel) loadMessagesFromCache(sel)
  // 未检测账号自动轮询检测（5s）
  startAutoDetect()
})

// When account set shrinks (delete / CF re-sync), keep settings maps tight
watch(
  () => accounts.accounts.length,
  () => {
    userSettings.pruneFetchMaps(accounts.accounts.map((a) => a.email))
  },
)

watch(
  () => accounts.selectedId,
  (id) => {
    if (!id || fetchingId.value) return
    const acc = accounts.findById(id)
    if (acc) loadMessagesFromCache(acc)
  },
)

// New imports often land as unknown — kick a detect pass soon
watch(
  () => accounts.accounts.filter((a) => !a.status || a.status === 'unknown').length,
  (n, prev) => {
    if (n > (prev ?? 0)) {
      window.setTimeout(() => void tickAutoDetect(), 400)
    }
  },
)

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  mqNarrow?.removeEventListener('change', onNarrowChange)
  twofa.stopTicker()
  stopAutoDetect()
})
</script>

<template>
  <div
    class="console"
    :class="{
      'import-collapsed': importCollapsed,
      dragging: !!dragging,
      'is-narrow': isNarrow,
      'import-open': isNarrow && !importCollapsed,
    }"
    :style="consoleGridStyle"
  >
    <!-- Mobile import drawer backdrop -->
    <div
      v-if="isNarrow && !importCollapsed"
      class="import-backdrop"
      @click="importCollapsed = true"
    />

    <!-- Left: import (desktop: hide when collapsed; mobile: always mounted for drawer slide) -->
    <aside
      v-show="isNarrow || !importCollapsed"
      class="sidebar glass"
    >
      <div class="side-head">
        <div class="side-kicker">OpenMail</div>
        <div class="side-title-row">
          <div class="side-title">{{ t('console.importTitle') }}</div>
          <div class="side-head-actions">
            <button type="button" class="btn btn-ghost btn-sm" @click="showImportHelp = true">
              {{ t('console.importHelpBtn') }}
            </button>
            <button
              type="button"
              class="btn btn-ghost btn-sm collapse-btn"
              :title="t('console.collapseImport')"
              @click="toggleImportPanel"
            >
              «
            </button>
          </div>
        </div>
        <p class="side-sub">{{ t('console.importHintShort') }}</p>
        <label class="import-group">
          <span>{{ t('console.groupImportInto') }}</span>
          <UiSelect
            v-model="accounts.importGroupId"
            :options="groupSelectOptions"
            class="import-group-select"
          />
        </label>
      </div>

      <textarea
        v-model="importText"
        class="textarea import-area"
        :placeholder="importPlaceholderText"
        rows="8"
      />

      <div class="btn-row">
        <button type="button" class="btn btn-primary btn-sm" @click="onImport">
          {{ t('console.importPreviewBtn') }}
        </button>
        <button type="button" class="btn btn-outline btn-sm" @click="onImportTxt">
          {{ t('console.importTxt') }}
        </button>
        <button type="button" class="btn btn-ghost btn-sm" @click="onClearInput">
          {{ t('console.clearInput') }}
        </button>
      </div>
      <input
        ref="fileInput"
        type="file"
        accept=".txt,text/plain"
        class="sr-only"
        @change="onFileChange"
      />

      <ul v-if="importPreview.length" class="import-preview">
        <li v-for="(line, i) in importPreview" :key="i">{{ line }}</li>
      </ul>

      <div class="side-foot">
        <div class="stats-grid">
          <div class="stat">
            <span class="stat-n">{{ accounts.stats.all }}</span>
            <span class="stat-l">{{ t('console.statsTotal') }}</span>
          </div>
          <div class="stat ok">
            <span class="stat-n">{{ accounts.stats.ready }}</span>
            <span class="stat-l">{{ t('console.statsOk') }}</span>
          </div>
          <div class="stat err">
            <span class="stat-n">{{ accounts.stats.error }}</span>
            <span class="stat-l">{{ t('console.statsError') }}</span>
          </div>
          <div class="stat unk">
            <span class="stat-n">{{ accounts.stats.unknown }}</span>
            <span class="stat-l">{{ t('console.statsUnknown') }}</span>
          </div>
        </div>
        <div class="btn-row">
          <button type="button" class="btn btn-outline btn-sm" @click="onExportCredentials">
            {{ t('console.exportCredentials') }}
          </button>
          <button type="button" class="btn btn-outline btn-sm" @click="onExportSystem">
            {{ t('console.exportSystem') }}
          </button>
          <label class="btn btn-outline btn-sm" style="cursor:pointer">
            {{ t('console.importSystem') }}
            <input type="file" accept="application/json,.json" class="sr-only" @change="onImportSystemFile" />
          </label>
          <button type="button" class="btn btn-danger btn-sm" @click="onClearLocal">
            {{ t('console.clearLocal') }}
          </button>
        </div>
      </div>
    </aside>

    <!-- Collapsed import rail (desktop only) -->
    <button
      v-if="importCollapsed && !isNarrow"
      type="button"
      class="import-rail glass"
      :title="t('console.expandImport')"
      @click="toggleImportPanel"
    >
      <span class="import-rail-icon">＋</span>
      <span class="import-rail-text">{{ t('console.openImport') }}</span>
    </button>

    <div
      v-if="!isNarrow"
      class="splitter splitter-v"
      role="separator"
      aria-orientation="vertical"
      :title="t('console.dragResizeWidth')"
      @mousedown="onDragStart('side', $event)"
    >
      <span class="splitter-grip" aria-hidden="true">
        <span /><span /><span />
      </span>
    </div>

    <!-- Right -->
    <section class="main-col">
      <div class="filter-bar glass">
        <input
          v-model="accounts.filterQuery"
          class="input search-wide"
          type="search"
          :placeholder="t('console.filterSearch')"
        />
        <UiSelect
          v-model="accounts.filterBrand"
          :options="brandSelectOptions"
          class="filter-select"
          :title="t('console.colBrand')"
        />
        <UiSelect
          v-model="accounts.filterStatus"
          :options="statusSelectOptions"
          class="filter-select"
          :title="t('console.filterStatus')"
        />
        <UiSelect
          v-model="accounts.filterStorage"
          :options="storageSelectOptions"
          class="filter-select"
          :title="t('console.filterStorage')"
        />
        <UiSelect
          v-model="accounts.filterGroup"
          :options="filterGroupOptions"
          class="filter-select"
          :title="t('console.groupLabel')"
        />
        <button type="button" class="btn btn-ghost btn-sm" @click="filterErrorsOnly">
          {{ t('console.filterErrorsOnly') }}
        </button>
        <button type="button" class="btn btn-ghost btn-sm" @click="clearFilters">
          {{ t('console.clearFilters') }}
        </button>
      </div>

      <div
        class="split-main"
        :style="splitMainStyle"
      >
      <div class="table-card glass">
        <div class="table-toolbar">
          <div class="toolbar-left">
            <button type="button" class="btn btn-ghost btn-sm" @click="onSelectPage">
              {{ t('console.tableSelectPage') }}
            </button>
            <button type="button" class="btn btn-ghost btn-sm" @click="accounts.deselectAll()">
              {{ t('console.tableDeselect') }}
            </button>
            <button
              type="button"
              class="btn btn-outline btn-sm"
              :disabled="!hasChecked || batchBusy"
              :title="!hasChecked ? t('console.needCheckAccounts') : undefined"
              @click="onBatchFetch"
            >
              {{ batchBusy ? t('common.loading') : t('console.tableBatchFetch') }}
            </button>
            <button
              type="button"
              class="btn btn-outline btn-sm"
              :disabled="!hasChecked || batchBusy"
              :title="t('console.uploadCloudHint')"
              @click="onBatchUploadCloud"
            >
              {{ t('console.uploadCloud') }}
            </button>
            <label class="tog batch-conc" :title="t('console.batchConcurrencyHint')">
              <span>{{ t('console.batchConcurrency') }}</span>
              <input v-model.number="batchConcurrency" class="input conc-input" type="number" min="1" max="32" />
            </label>
            <button
              type="button"
              class="btn btn-danger btn-sm"
              :disabled="!hasChecked"
              :title="!hasChecked ? t('console.needCheckAccounts') : undefined"
              @click="void accounts.removeSelected()"
            >
              {{ t('console.tableDelete') }}
            </button>
            <span v-if="!hasChecked" class="batch-hint">{{ t('console.needCheckAccounts') }}</span>
            <UiSelect
              v-if="hasChecked"
              :model-value="moveGroupId"
              :options="moveGroupOptions"
              class="move-group"
              size="sm"
              :block="false"
              @update:model-value="onMoveGroupPick"
            />
            <button type="button" class="btn btn-ghost btn-sm" @click="showGroupManage = true">
              {{ t('console.groupManage') }}
            </button>
          </div>
          <div class="toolbar-right">
            <label class="toggle">
              <input v-model="denseCols" type="checkbox" />
              <span class="toggle-track" aria-hidden="true" />
              <span>{{ t('console.colModeCompact') }}</span>
            </label>
            <label class="toggle">
              <input v-model="codeMasked" type="checkbox" />
              <span class="toggle-track" aria-hidden="true" />
              <span>{{ t('console.codeMaskToggle') }}</span>
            </label>
            <span class="copy-tip">{{ t('console.clickToCopyTip') }}</span>
            <span class="total">{{ t('console.tableTotal', { n: listAll.length }) }}</span>
          </div>
        </div>

        <div v-if="!hasAccounts" class="empty">
          <div class="empty-icon">✉</div>
          <div class="empty-title">{{ t('console.emptyAccountsTitle') }}</div>
          <div class="empty-desc">{{ t('console.emptyAccountsDesc') }}</div>
        </div>

        <div v-else class="table-wrap">
          <table class="data" :class="{ compact: effectiveDense }">
            <thead>
              <tr>
                <th class="col-check"></th>
                <th v-if="!isNarrow" class="col-idx">#</th>
                <th class="col-star" :title="t('console.colStar')"></th>
                <th class="col-email">{{ t('console.colEmail') }}</th>
                <th class="col-type">{{ t('console.colBrand') }}</th>
                <th v-if="!effectiveDense">{{ t('console.colHost') }}</th>
                <th v-if="!effectiveDense">{{ t('console.colSecret') }}</th>
                <th>{{ t('console.colCode') }}</th>
                <th class="col-note">{{ t('console.colNote') }}</th>
                <th>{{ t('console.colStatus') }}</th>
                <th v-if="!effectiveDense">{{ t('console.colStorage') }}</th>
                <th v-if="!effectiveDense">{{ t('console.colUpdated') }}</th>
                <th class="col-act" :class="{ 'sticky-act': !isNarrow }">
                  {{ t('console.colActions') }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(acc, idx) in list"
                :key="acc.id"
                :class="{
                  'is-selected': accounts.selectedId === acc.id,
                  'is-fetching': fetchingId === acc.id,
                  'is-starred': !!acc.starred,
                }"
                @click="onRowClick(acc)"
              >
                <td class="col-check" @click.stop>
                  <input
                    type="checkbox"
                    :checked="accounts.selectedIds.has(acc.id)"
                    @change="accounts.toggleSelect(acc.id)"
                  />
                </td>
                <td v-if="!isNarrow" class="col-idx muted">{{ (page - 1) * pageSize + idx + 1 }}</td>

                <td class="col-star" @click.stop>
                  <button
                    type="button"
                    class="star-btn"
                    :class="{ on: acc.starred }"
                    :title="acc.starred ? t('console.unstarAccount') : t('console.starAccount')"
                    @click="toggleStar(acc, $event)"
                  >
                    {{ acc.starred ? '★' : '☆' }}
                  </button>
                </td>

                <td class="col-email">
                  <div class="email-cell" :class="{ 'is-child': !!acc.parentApiId, 'is-api': !!acc.isApiSource }">
                    <button
                      v-if="acc.isApiSource"
                      type="button"
                      class="api-expand-btn"
                      :title="isApiExpanded(acc.id) ? t('console.apiSourceCollapse') : t('console.apiSourceExpand')"
                      @click="toggleApiExpand(acc.id, $event)"
                    >
                      {{ isApiExpanded(acc.id) ? '▾' : '▸' }}
                    </button>
                    <span v-else-if="acc.parentApiId" class="tree-pad" aria-hidden="true">└</span>
                    <button
                      type="button"
                      class="copy-cell email"
                      :class="{ copied: copiedKey === `email-${acc.id}` }"
                      :title="t('console.clickToCopy')"
                      @click="onCopyCell($event, `email-${acc.id}`, acc.email, acc)"
                    >
                      {{ acc.isApiSource ? (acc.note || acc.email) : acc.email }}
                    </button>
                  </div>
                  <div v-if="acc.isApiSource" class="group-tag muted">
                    {{ t('console.apiSourceTag') }}
                    <template v-if="acc.apiMailboxes?.length">
                      · {{ t('console.apiMailboxCount', { n: acc.apiMailboxes.length }) }}
                    </template>
                    <button
                      type="button"
                      class="linkish"
                      @click="toggleApiExpand(acc.id, $event)"
                    >
                      {{
                        isApiExpanded(acc.id)
                          ? t('console.apiSourceCollapse')
                          : t('console.apiSourceExpand')
                      }}
                    </button>
                  </div>
                  <div v-else-if="acc.parentApiId" class="group-tag muted">
                    {{ t('console.apiChildFetchOnly') }}
                  </div>
                  <div v-else class="group-tag muted">{{ groupName(acc.groupId) }}</div>
                  <!-- Live 2FA when bound -->
                  <button
                    v-if="twoFaFor(acc)"
                    type="button"
                    class="twofa-live"
                    :title="t('console.twoFaCopyHint')"
                    @click="copyTwoFa(acc, $event)"
                  >
                    <span class="twofa-label">{{ t('console.twoFaCode') }}</span>
                    <span class="twofa-code mono">{{ twoFaCode(acc) }}</span>
                    <span
                      v-if="twoFaFor(acc)!.type === 'totp'"
                      class="twofa-remain"
                      :class="{ urgent: twoFaRemain(acc) <= 5 }"
                    >{{ twoFaRemain(acc) }}s</span>
                  </button>
                  <div v-if="acc.lastError" class="err-block" :title="acc.lastError">
                    <span class="err-line">{{
                      acc.lastError.length > 72 ? acc.lastError.slice(0, 72) + '…' : acc.lastError
                    }}</span>
                  </div>
                </td>

                <td class="col-type">
                  <span
                    class="type-chip"
                    :class="`type-${accountBrand(acc)}`"
                    :title="`${brandLabel(accountBrand(acc))} · ${typeLabel(acc.type)}${acc.imapHost ? ' · ' + acc.imapHost : ''}`"
                  >
                    <BrandMark :brand="accountBrand(acc)" :size="15" />
                    <span>{{ brandLabel(accountBrand(acc)) }}</span>
                  </span>
                </td>

                <td v-if="!effectiveDense">
                  <span class="mono host" :title="hostCopyValue(acc)">
                    {{ hostLabel(acc) }}
                  </span>
                </td>

                <td v-if="!effectiveDense">
                  <button
                    type="button"
                    class="copy-cell mono secret"
                    :class="{
                      copied: copiedKey === `sec-${acc.id}`,
                      'is-warn':
                        acc.type === 'oauth' && (!acc.refreshToken || !acc.clientId),
                    }"
                    :title="t('console.clickToCopySecret')"
                    @click="onCopyCell($event, `sec-${acc.id}`, copyableSecret(acc), acc)"
                  >
                    {{ secretHint(acc) }}
                  </button>
                </td>

                <td>
                  <button
                    v-if="acc.latestCode"
                    type="button"
                    class="code-pill copy-cell"
                    :class="{ copied: copiedKey === `code-${acc.id}` }"
                    :title="
                      codeMasked && !revealedCodes.has(acc.id)
                        ? t('console.codeClickReveal')
                        : t('console.clickToCopy')
                    "
                    @click="
                      accounts.select(acc.id);
                      if (fetchingId !== acc.id) loadMessagesFromCache(acc);
                      toggleRevealCode($event, acc.id, acc.latestCode)
                    "
                  >
                    {{ displayCode(acc.latestCode, acc.id) }}
                  </button>
                  <span v-else class="muted dash">—</span>
                </td>

                <td class="col-note" @click.stop>
                  <NotePurposeCell
                    :account="acc"
                    @patch="(note) => patchAccountNote(acc, note)"
                  />
                </td>

                <td>
                  <span class="status-dot" :class="statusClass(acc.status)">
                    {{ statusLabel(acc.status) }}
                  </span>
                  <div class="cap-tags">
                    <span
                      class="cap-tag"
                      :class="{ on: canFetch(acc) }"
                      :title="canFetch(acc) ? t('console.capFetchOn') : t('console.capFetchOff')"
                    >{{ t('console.capFetch') }}</span>
                    <span
                      class="cap-tag"
                      :class="{ on: canSend(acc) }"
                      :title="canSend(acc) ? t('console.capSendOn') : t('console.capSendOff')"
                    >{{ t('console.capSend') }}</span>
                  </div>
                  <div v-if="acc.storage === 'server' && acc.syncEnabled" class="muted poll-tag">
                    {{ t('console.pollOn') }}
                  </div>
                </td>

                <td v-if="!effectiveDense">
                  <span
                    class="chip storage-chip"
                    :class="acc.storage === 'server' || acc.serverId ? 'cloud' : 'local'"
                    :title="
                      acc.storage === 'server' || acc.syncEnabled
                        ? t('console.storageCloudHint')
                        : t('console.storageLocalHint')
                    "
                  >
                    {{
                      acc.storage === 'server'
                        ? t('console.storageServer')
                        : acc.serverId || acc.syncEnabled
                          ? t('console.storageLinked')
                          : t('console.storageLocal')
                    }}
                  </span>
                </td>

                <td v-if="!effectiveDense">
                  <span
                    class="muted time"
                    :title="acc.updatedAt ? new Date(acc.updatedAt).toLocaleString() : ''"
                  >
                    {{ formatTime(acc.updatedAt) }}
                  </span>
                </td>

                <td
                  class="col-act"
                  :class="{
                    'sticky-act': !isNarrow,
                    'acts-expanded': isNarrow && expandedActId === acc.id,
                    'acts-collapsed': isNarrow && expandedActId !== acc.id,
                  }"
                  @click.stop
                >
                  <!-- Mobile: collapsed ⋯ toggle; expanded shows full actions -->
                  <button
                    v-if="isNarrow && expandedActId !== acc.id"
                    type="button"
                    class="btn btn-ghost btn-xs act-more"
                    :title="t('console.expandActions')"
                    @click="toggleRowActs(acc, $event)"
                  >
                    ⋯
                  </button>
                  <div
                    v-else
                    class="row-acts"
                    :class="{ 'row-acts-mobile': isNarrow }"
                  >
                    <button
                      v-if="isNarrow"
                      type="button"
                      class="btn btn-ghost btn-xs act-more"
                      :title="t('console.collapseActions')"
                      @click="toggleRowActs(acc, $event)"
                    >
                      ×
                    </button>
                    <button
                      type="button"
                      class="btn btn-primary btn-xs act-btn"
                      :disabled="fetchingId === acc.id || !canFetch(acc)"
                      :title="fetchDisabledReason(acc)"
                      @click="fetchOne(acc, true, { maxMessages: MAIL_FIRST_PAGE, forceRecent: true })"
                    >
                      {{ fetchingId === acc.id ? '…' : t('console.quickFetch') }}
                    </button>
                    <button
                      v-if="!acc.parentApiId"
                      type="button"
                      class="btn btn-ghost btn-xs"
                      @click="openEdit(acc)"
                    >
                      {{ t('console.editAccount') }}
                    </button>
                    <button
                      v-if="!isNarrow"
                      type="button"
                      class="btn btn-ghost btn-xs"
                      :disabled="!canSend(acc)"
                      :title="sendDisabledReason(acc)"
                      @click="openSend(acc)"
                    >
                      {{ t('console.sendMail') }}
                    </button>
                    <button type="button" class="btn btn-ghost btn-xs act-del" @click="deleteOne(acc)">
                      {{ t('console.deleteAccount') }}
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="hasAccounts && listAll.length" class="pager">
          <div class="pager-left">
            <span class="pager-label">{{ t('console.pageSize') }}</span>
            <UiSelect
              :model-value="pageSize"
              :options="pageSizeSelectOptions"
              class="page-size"
              size="sm"
              :block="false"
              @update:model-value="(v) => (pageSize = Number(v))"
            />
          </div>
          <div class="pager-center">
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              :disabled="page <= 1"
              @click="page = Math.max(1, page - 1)"
            >
              {{ t('common.prev') }}
            </button>
            <span class="pager-info">{{ page }} / {{ totalPages }}</span>
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              :disabled="page >= totalPages"
              @click="page = Math.min(totalPages, page + 1)"
            >
              {{ t('common.next') }}
            </button>
          </div>
          <div class="pager-right muted">
            {{ t('console.tableTotal', { n: listAll.length }) }}
          </div>
        </div>
      </div>

      <div
        v-if="!isNarrow"
        class="splitter splitter-h"
        role="separator"
        aria-orientation="horizontal"
        :title="t('console.dragResizeHeight')"
        @mousedown="onDragStart('mail', $event)"
      >
        <span class="splitter-grip splitter-grip-h" aria-hidden="true">
          <span /><span /><span />
        </span>
      </div>
      <!-- Mail panel -->
      <div class="mail-panel glass">
        <div class="mail-head">
          <div class="mail-tabs">
            <button
              type="button"
              class="tab"
              :class="{ active: mailFolder === 'inbox' }"
              @click="mailFolder = 'inbox'"
            >
              {{ t('console.folderInbox') }}
            </button>
            <button
              type="button"
              class="tab"
              :class="{ active: mailFolder === 'spam' }"
              @click="mailFolder = 'spam'"
            >
              {{ t('console.folderSpam') }}
            </button>
            <button
              type="button"
              class="tab"
              :class="{ active: mailFolder === 'sent' }"
              @click="mailFolder = 'sent'"
            >
              {{ t('console.folderSent') }}
            </button>
          </div>
          <div class="mail-head-right">
            <button
              v-if="selected"
              type="button"
              class="copy-cell mail-email"
              :class="{ copied: copiedKey === 'sel-email' }"
              @click="doCopy('sel-email', selected.email)"
            >
              {{ selected.email }}
            </button>
            <button
              type="button"
              class="btn btn-primary btn-sm"
              :disabled="!hasSelection || mailLoading"
              @click="onFetchSelected(true)"
            >
              {{ mailLoading ? t('common.loading') : t('console.quickFetch') }}
            </button>
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              :disabled="!hasSelection || mailLoading"
              :title="t('console.mailClearHint')"
              @click="onClearAndRefetch"
            >
              {{ t('console.mailClearRefetch') }}
            </button>
          </div>
        </div>

        <button
          v-if="panelCode"
          type="button"
          class="code-banner copy-cell"
          :class="{ copied: copiedKey === 'panel-code' }"
          :title="t('console.clickToCopy')"
          @click="
            codeMasked && !revealedCodes.has('panel')
              ? toggleRevealCode($event, 'panel', panelCode)
              : doCopy('panel-code', panelCode)
          "
        >
          <span class="code-banner-label">{{ t('console.mailCode') }}</span>
          <span class="code-banner-value">{{ displayCode(panelCode, 'panel') }}</span>
          <span class="code-banner-hint">
            {{
              codeMasked && !revealedCodes.has('panel')
                ? t('console.codeClickReveal')
                : t('console.clickToCopy')
            }}
          </span>
        </button>

        <div class="mail-body">
          <div class="mail-list-pane">
            <div v-if="mailEmptyKind === 'loading'" class="empty-sm">
              {{ t('common.loading') }}
            </div>
            <div v-else-if="mailEmptyKind === 'no_account'" class="empty-sm">
              {{ t('console.mailNoSelection') }}
            </div>
            <div v-else-if="mailEmptyKind === 'cached_code'" class="empty-sm empty-rich">
              <div class="empty-title">{{ t('console.mailCachedCodeTitle') }}</div>
              <div class="empty-desc">{{ t('console.mailCachedCodeDesc') }}</div>
              <button
                type="button"
                class="btn btn-primary btn-sm"
                :disabled="mailLoading"
                @click="onFetchSelected(true)"
              >
                {{ t('console.quickFetch') }}
              </button>
            </div>
            <div v-else-if="mailEmptyKind === 'error'" class="empty-sm empty-rich">
              <div class="empty-title">{{ t('console.mailFetchErrorTitle') }}</div>
              <div class="empty-desc err-text">{{ selected?.lastError }}</div>
              <div class="btn-row center">
                <button type="button" class="btn btn-outline btn-sm" @click="onFetchSelected(true)">
                  {{ t('console.retryFetch') }}
                </button>
                <button
                  v-if="selected && isTokenError(selected)"
                  type="button"
                  class="btn btn-ghost btn-sm"
                  @click="selected && onReimportHint(selected)"
                >
                  {{ t('console.reimportToken') }}
                </button>
              </div>
            </div>
            <div v-else-if="mailEmptyKind === 'empty_inbox'" class="empty-sm">
              {{ t('console.mailEmptyInbox') }}
            </div>
            <div v-else-if="mailEmptyKind === 'need_fetch'" class="empty-sm empty-rich">
              <div class="empty-title">{{ t('console.mailNeedFetchTitle') }}</div>
              <div class="empty-desc">{{ t('console.mailNeedFetchDesc') }}</div>
              <button
                type="button"
                class="btn btn-primary btn-sm"
                :disabled="!hasSelection || mailLoading"
                @click="onFetchSelected(true)"
              >
                {{ t('console.quickFetch') }}
              </button>
            </div>
            <template v-else>
              <button
                v-for="m in visibleMessages"
                :key="m.id"
                type="button"
                class="mail-item"
                :class="{ active: selectedMessage?.id === m.id }"
                @click="selectedMessageId = m.id"
              >
                <div class="mail-item-top">
                  <span class="mail-from">
                    {{ m.from || m.from_address || '—' }}
                  </span>
                  <span
                    v-if="m.verification_code"
                    class="code-chip copy-cell"
                    @click.stop="
                      toggleRevealCode($event, `m-${m.id}`, m.verification_code || undefined)
                    "
                  >
                    {{ displayCode(m.verification_code, `m-${m.id}`) }}
                  </span>
                </div>
                <div class="mail-sub">
                  {{ m.subject || t('console.mailNoSubject') }}
                </div>
                <div v-if="messageTo(m)" class="mail-to muted">
                  → {{ messageTo(m) }}
                </div>
              </button>
              <div class="mail-load-more">
                <span class="muted mail-count">
                  {{ t('console.mailShowing', { n: visibleMessages.length, total: messages.length }) }}
                </span>
                <button
                  v-if="hasMoreCached || !mailNoMoreRemote"
                  type="button"
                  class="btn btn-outline btn-sm"
                  :disabled="mailLoading || mailLoadingMore"
                  @click="onLoadMoreMails"
                >
                  {{
                    mailLoadingMore
                      ? t('common.loading')
                      : hasMoreCached
                        ? t('console.mailLoadMoreCache')
                        : t('console.mailLoadMore')
                  }}
                </button>
                <p v-if="mailNoMoreRemote && !hasMoreCached" class="muted mail-no-more">
                  {{ t('console.mailNoMore') }}
                </p>
              </div>
            </template>
          </div>

          <div class="mail-detail-pane">
            <template v-if="selectedMessage">
              <div class="detail-head">
                <h3 class="detail-title">
                  {{ selectedMessage.subject || t('console.mailNoSubject') }}
                </h3>
                <div class="detail-head-actions">
                  <button
                    type="button"
                    class="btn btn-ghost btn-xs"
                    :title="t('console.copyBody')"
                    @click="doCopy('d-body', detailText || selectedMessage.subject || '')"
                  >
                    {{ t('console.copyBody') }}
                  </button>
                  <button
                    type="button"
                    class="btn btn-outline btn-xs"
                    :title="t('console.expandBody')"
                    @click="showBodyModal = true"
                  >
                    {{ t('console.expandBody') }}
                  </button>
                </div>
              </div>
              <div class="detail-meta">
                <span class="meta-part">
                  <span class="meta-label">{{ t('console.mailFrom') }}</span>
                  {{ selectedMessage.from || selectedMessage.from_address || '—' }}
                </span>
                <template v-if="messageTo(selectedMessage)">
                  <span class="dot">·</span>
                  <span class="meta-part" :title="messageTo(selectedMessage)">
                    <span class="meta-label">{{ t('console.mailTo') }}</span>
                    {{ messageTo(selectedMessage) }}
                  </span>
                </template>
                <span class="dot">·</span>
                <span class="meta-part">{{ selectedMessage.date || '—' }}</span>
              </div>
              <div class="detail-body-scroll">
                <div
                  v-if="detailHtml"
                  class="detail-body detail-html"
                  v-html="detailHtml"
                />
                <pre v-else class="detail-body">{{ detailText || t('console.mailNoBody') }}</pre>
              </div>
            </template>
            <div v-else class="empty-sm">{{ t('console.mailDetailEmpty') }}</div>
          </div>
        </div>
      </div>
      </div>
    </section>

    <div v-if="showImportHelp" class="modal-backdrop" @click.self="showImportHelp = false">
      <div class="modal card-solid import-help-modal">
        <header class="modal-head">
          <h2>{{ t('console.importHelpTitle') }}</h2>
          <button type="button" class="btn btn-ghost btn-sm" @click="showImportHelp = false">
            {{ t('common.close') }}
          </button>
        </header>
        <div class="modal-body prose">
          <p class="help-tip">{{ t('console.helpIntro') }}</p>
          <p class="help-tip muted-tip">{{ t('console.clickToCopyTip') }}</p>

          <!-- Microsoft OAuth -->
          <section class="help-block">
            <h3>{{ t('console.helpOauthTitle') }}</h3>
            <p class="help-need"><strong>{{ t('console.helpNeed') }}：</strong>{{ t('console.helpOauthNeed') }}</p>
            <p class="help-how-label">{{ t('console.helpHow') }}</p>
            <p class="help-body-pre">{{ t('console.helpOauthBody') }}</p>
            <div class="help-links">
              <span class="help-links-label">{{ t('console.helpLinks') }}</span>
              <a
                class="help-link"
                href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpOauthLinkAzure') }}</a>
              <a
                class="help-link"
                href="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpOauthLinkEntra') }}</a>
              <a
                class="help-link"
                href="https://account.live.com/proofs/manage"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpOauthLinkAccount') }}</a>
            </div>
            <p class="help-format-label">{{ t('console.helpFormat') }}</p>
            <pre class="code-block">user@outlook.com----password----client_id----M.refresh_token
user@hotmail.com----client_id----M.refresh_token</pre>
          </section>

          <!-- Gmail -->
          <section class="help-block">
            <h3>{{ t('console.helpGmailTitle') }}</h3>
            <p class="help-need"><strong>{{ t('console.helpNeed') }}：</strong>{{ t('console.helpGmailNeed') }}</p>
            <p class="help-how-label">{{ t('console.helpHow') }}</p>
            <p class="help-body-pre">{{ t('console.helpGmailBody') }}</p>
            <div class="help-links">
              <span class="help-links-label">{{ t('console.helpLinks') }}</span>
              <a
                class="help-link"
                href="https://myaccount.google.com/signinoptions/two-step-verification"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpGmailLink2fa') }}</a>
              <a
                class="help-link"
                href="https://myaccount.google.com/apppasswords"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpGmailLinkAppPass') }}</a>
              <a
                class="help-link"
                href="https://support.google.com/mail/answer/7126229"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpGmailLinkImap') }}</a>
            </div>
            <p class="help-format-label">{{ t('console.helpFormat') }}</p>
            <pre class="code-block">user@gmail.com----xxxx xxxx xxxx xxxx</pre>
          </section>

          <!-- QQ / 163 / corporate IMAP -->
          <section class="help-block">
            <h3>{{ t('console.helpImapTitle') }}</h3>
            <p class="help-need"><strong>{{ t('console.helpNeed') }}：</strong>{{ t('console.helpImapNeed') }}</p>
            <p class="help-how-label">{{ t('console.helpHow') }}</p>
            <p class="help-body-pre">{{ t('console.helpImapBody') }}</p>
            <div class="help-links">
              <span class="help-links-label">{{ t('console.helpLinks') }}</span>
              <a
                class="help-link"
                href="https://wx.mail.qq.com/list/readtemplate?name=app_intro.html#/agreement/authorizationCode"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpImapLinkQq') }}</a>
              <a
                class="help-link"
                href="https://config.mail.163.com/settings/client/index.jsp"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpImapLink163') }}</a>
              <a
                class="help-link"
                href="https://help.aliyun.com/document_detail/36576.html"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpImapLinkAliyun') }}</a>
            </div>
            <p class="help-format-label">{{ t('console.helpFormat') }}</p>
            <pre class="code-block">user@126.com----auth_code
user@qq.com----auth_code
imap----admin@kilan.cn----auth_code----imap.qiye.aliyun.com----993</pre>
          </section>

          <!-- iCloud -->
          <section class="help-block">
            <h3>{{ t('console.helpIcloudTitle') }}</h3>
            <p class="help-need"><strong>{{ t('console.helpNeed') }}：</strong>{{ t('console.helpIcloudNeed') }}</p>
            <p class="help-how-label">{{ t('console.helpHow') }}</p>
            <p class="help-body-pre">{{ t('console.helpIcloudBody') }}</p>
            <div class="help-links">
              <span class="help-links-label">{{ t('console.helpLinks') }}</span>
              <a
                class="help-link"
                href="https://appleid.apple.com/account/manage"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpIcloudLink') }}</a>
            </div>
            <p class="help-format-label">{{ t('console.helpFormat') }}</p>
            <pre class="code-block">name@icloud.com----xxxx-xxxx-xxxx-xxxx
name@me.com----xxxx-xxxx-xxxx-xxxx</pre>
          </section>

          <!-- mail.com -->
          <section class="help-block">
            <h3>{{ t('console.helpMailcomTitle') }}</h3>
            <p class="help-need"><strong>{{ t('console.helpNeed') }}：</strong>{{ t('console.helpMailcomNeed') }}</p>
            <p class="help-how-label">{{ t('console.helpHow') }}</p>
            <p class="help-body-pre">{{ t('console.helpMailcomBody') }}</p>
            <div class="help-links">
              <span class="help-links-label">{{ t('console.helpLinks') }}</span>
              <a
                class="help-link"
                href="https://www.mail.com/"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpMailcomLink') }}</a>
            </div>
            <p class="help-format-label">{{ t('console.helpFormat') }}</p>
            <pre class="code-block">name@mail.com----password</pre>
          </section>

          <!-- CF Worker / HttpApi -->
          <section class="help-block">
            <h3>{{ t('console.helpHttpTitle') }}</h3>
            <p class="help-need"><strong>{{ t('console.helpNeed') }}：</strong>{{ t('console.helpHttpNeed') }}</p>
            <p class="help-how-label">{{ t('console.helpHow') }}</p>
            <p class="help-body-pre">{{ t('console.helpHttpBody') }}</p>
            <div class="help-links">
              <span class="help-links-label">{{ t('console.helpLinks') }}</span>
              <a
                class="help-link"
                href="https://github.com/dreamhunter2333/cloudflare_temp_email"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpHttpLinkCfTemp') }}</a>
              <a
                class="help-link"
                href="https://github.com/beilunyang/moemail"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('console.helpHttpLinkMoe') }}</a>
            </div>
            <p class="help-format-label">{{ t('console.helpFormat') }}</p>
            <pre class="code-block"># open (no secret)
https://mail.example.workers.dev
# with secret (admin / API key)
https://mail.example.workers.dev----YOUR_SECRET
# mailbox + secret + url
user@temp.dev----YOUR_SECRET----https://mail.example.workers.dev</pre>
          </section>
        </div>
      </div>
    </div>

    <!-- Edit modal -->
    <div v-if="showEdit" class="modal-backdrop" @click.self="showEdit = false">
      <div class="modal card-solid import-help-modal">
        <header class="modal-head">
          <h2>{{ t('console.editTitle') }}</h2>
          <button type="button" class="btn btn-ghost btn-sm" @click="showEdit = false">{{ t('common.close') }}</button>
        </header>
        <div class="modal-body">
          <div class="field">
            <label class="label">{{ t('console.colEmail') }}</label>
            <input class="input" :value="editForm.email" disabled />
          </div>
          <div class="field">
            <label class="label">{{ t('console.groupLabel') }}</label>
            <UiSelect v-model="editForm.groupId" :options="groupSelectOptions" />
          </div>
          <div v-if="editForm.type !== 'http_api'" class="field">
            <label class="label">{{ t('console.fieldPassword') }}</label>
            <input
              v-model="editForm.password"
              class="input"
              type="text"
              autocomplete="off"
              :disabled="!canChangeMailboxPassword(editForm.type)"
              :title="!canChangeMailboxPassword(editForm.type) ? t('console.oauthPasswordDisabledHint') : ''"
            />
            <p v-if="!canChangeMailboxPassword(editForm.type)" class="hint">
              {{ t('console.oauthPasswordDisabledHint') }}
            </p>
          </div>
          <div v-if="editForm.type === 'oauth'" class="field">
            <label class="label">{{ t('console.fieldClientId') }}</label>
            <input v-model="editForm.clientId" class="input" type="text" autocomplete="off" />
          </div>
          <div v-if="editForm.type === 'oauth'" class="field">
            <label class="label">{{ t('console.fieldRefreshToken') }}</label>
            <textarea v-model="editForm.refreshToken" class="textarea" rows="3" />
          </div>
          <div v-if="editForm.type === 'imap'" class="field">
            <label class="label">{{ t('console.fieldImapHost') }}</label>
            <input
              v-model="editForm.imapHost"
              class="input mono"
              type="text"
              spellcheck="false"
              :placeholder="t('console.fieldImapHostPh')"
            />
          </div>
          <!-- SMTP for send: imap / cookie / unknown (not oauth Graph / http_api) -->
          <template v-if="editForm.type === 'imap' || editForm.type === 'cookie' || editForm.type === 'unknown'">
            <div class="field-row-smtp">
              <div class="field">
                <label class="label">{{ t('console.fieldSmtpHost') }}</label>
                <input
                  v-model="editForm.smtpHost"
                  class="input mono"
                  type="text"
                  spellcheck="false"
                  :placeholder="t('console.fieldSmtpHostPh')"
                />
              </div>
              <div class="field field-smtp-port">
                <label class="label">{{ t('console.fieldSmtpPort') }}</label>
                <input
                  v-model="editForm.smtpPort"
                  class="input mono"
                  type="number"
                  min="1"
                  max="65535"
                  placeholder="587"
                />
              </div>
            </div>
            <p class="hint">{{ t('console.fieldSmtpHint') }}</p>
          </template>
          <template v-if="editForm.type === 'http_api'">
            <div class="field">
              <label class="label">{{ t('console.fieldApiUrl') }}</label>
              <input v-model="editForm.apiUrl" class="input mono" type="text" spellcheck="false" />
            </div>
            <div class="field">
              <label class="label">{{ t('console.fieldApiKey') }}</label>
              <input
                v-model="editForm.apiKey"
                class="input mono"
                type="text"
                autocomplete="off"
                spellcheck="false"
                :placeholder="t('console.fieldApiKeyPh')"
              />
              <p class="hint">{{ t('console.fieldApiKeyHint') }}</p>
            </div>
            <div class="field">
              <label class="label">{{ t('console.fieldApiAuthStyle') }}</label>
              <UiSelect
                v-model="editForm.apiAuthStyle"
                :options="apiAuthStyleOptions"
              />
              <p class="hint">{{ t('console.fieldApiAuthStyleHint') }}</p>
            </div>
          </template>
          <div class="field">
            <label class="label">{{ t('console.fieldNote') }}</label>
            <input v-model="editForm.note" class="input" type="text" />
          </div>
          <div class="field">
            <label class="label">{{ t('console.fieldProxy') }}</label>
            <input
              v-model="editForm.proxy"
              class="input"
              type="text"
              spellcheck="false"
              :placeholder="t('console.fieldProxyPh')"
            />
            <p class="hint">{{ t('console.fieldProxyHint') }}</p>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-primary" @click="saveEdit">{{ t('console.saveEdit') }}</button>
          </div>
        </div>
      </div>
    </div>

    <ConsoleSendModal
      v-model:show="showSend"
      v-model:to="sendForm.to"
      v-model:subject="sendForm.subject"
      v-model:body="sendForm.body"
      :email="selected?.email"
      :busy="sendBusy"
      @send="doSend"
    />


    <!-- Import preview / validate modal -->
    <div v-if="showImportModal" class="modal-backdrop" @click.self="closeImportModal">
      <div class="modal card-solid import-modal">
        <header class="modal-head">
          <h2>{{ t('console.importPreviewTitle') }}</h2>
          <button type="button" class="btn btn-ghost btn-sm" @click="closeImportModal">{{ t('common.close') }}</button>
        </header>
        <div class="modal-body">
          <p class="hint">{{ t('console.importPreviewHint') }}</p>
          <p v-if="importValidating" class="hint import-checking-hint">
            {{ t('console.importCheckingHint', { n: importDraftRows.filter((r) => r.checkStatus === 'checking').length }) }}
          </p>

          <div class="import-target card-inset">
            <div class="import-target-row">
              <span class="label">{{ t('console.importTarget') }}</span>
              <label class="tog">
                <input v-model="importTarget" type="radio" value="local" />
                {{ t('console.importTargetLocal') }}
              </label>
              <label class="tog">
                <input v-model="importTarget" type="radio" value="cloud" />
                {{ t('console.importTargetCloud') }}
              </label>
            </div>
            <label v-if="importTarget === 'cloud'" class="tog poll-opt">
              <input v-model="importCloudPoll" type="checkbox" />
              {{ t('console.importCloudPoll') }}
            </label>
            <p v-if="importTarget === 'cloud'" class="hint sm">{{ t('console.importCloudPollHint') }}</p>
            <div v-if="importQuotaLabel" class="quota-bar">{{ importQuotaLabel }}</div>
          </div>

          <div class="btn-row" style="margin-bottom:10px">
            <button type="button" class="btn btn-ghost btn-sm" @click="toggleAllDraft(true)">{{ t('console.tableSelectPage') }}</button>
            <button type="button" class="btn btn-ghost btn-sm" @click="toggleAllDraft(false)">{{ t('console.tableDeselect') }}</button>
            <button type="button" class="btn btn-outline btn-sm" @click="selectDraftErrors">{{ t('console.importSelectErrors') }}</button>
            <button type="button" class="btn btn-danger btn-sm" @click="removeDraftSelected">{{ t('console.tableDelete') }}</button>
            <button type="button" class="btn btn-outline btn-sm" :disabled="importValidating" @click="validateImportDrafts">
              {{ importValidating ? t('common.loading') : t('console.importRevalidate') }}
            </button>
            <button
              v-if="importValidating"
              type="button"
              class="btn btn-ghost btn-sm"
              @click="skipImportPrecheck"
            >
              {{ t('console.importSkipPrecheck') }}
            </button>
            <label class="tog">
              <input v-model="importSkipOk" type="checkbox" />
              {{ t('console.importSkipOk') }}
            </label>
          </div>
          <div class="draft-table-wrap">
            <table class="data draft-table">
              <thead>
                <tr>
                  <th></th>
                  <th>{{ t('console.colEmail') }}</th>
                  <th>{{ t('console.colType') }}</th>
                  <th>{{ t('console.colStatus') }}</th>
                  <th>{{ t('console.importColDetail') }}</th>
                  <th>{{ t('console.colActions') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in importDraftRows" :key="row.id" :class="{ 'is-err': row.checkStatus === 'error' }">
                  <td><input v-model="row.selected" type="checkbox" /></td>
                  <td>
                    <div class="email">{{ row.email }}</div>
                    <div v-if="row.exists" class="muted" style="font-size:10px">{{ t('console.importExists') }} · {{ row.existingStatus }}</div>
                  </td>
                  <td><span class="chip">{{ row.type }}</span></td>
                  <td>
                    <span v-if="row.checkStatus === 'checking'" class="muted">{{ t('common.loading') }}</span>
                    <span v-else-if="row.checkStatus === 'ok'" class="status-dot is-ok">{{ t('console.statusOk') }}</span>
                    <span v-else-if="row.checkStatus === 'error'" class="status-dot is-err">{{ t('console.statusError') }}</span>
                    <span v-else class="muted">—</span>
                  </td>
                  <td class="draft-detail">
                    <div v-if="!row.editing">
                      <div>{{ row.message }}</div>
                      <div v-if="row.checkError" class="err-line">{{ row.checkError }}</div>
                      <div v-for="(w, wi) in row.warnings" :key="wi" class="muted" style="font-size:10px">{{ w }}</div>
                    </div>
                    <div v-else class="draft-edit-form">
                      <label class="label sm">{{ t('console.editPassword') }}</label>
                      <input v-model="row.editPassword" class="input input-sm" type="text" autocomplete="off" />
                      <template v-if="row.type === 'oauth'">
                        <label class="label sm">client_id</label>
                        <input v-model="row.editClientId" class="input input-sm mono" type="text" />
                        <label class="label sm">refresh_token</label>
                        <input v-model="row.editRefreshToken" class="input input-sm mono" type="text" />
                      </template>
                      <template v-if="row.type === 'imap'">
                        <label class="label sm">IMAP host</label>
                        <input v-model="row.editImapHost" class="input input-sm" type="text" />
                      </template>
                      <label class="label sm">{{ t('console.colNote') }}</label>
                      <input v-model="row.editNote" class="input input-sm" type="text" />
                    </div>
                  </td>
                  <td>
                    <button
                      v-if="!row.editing"
                      type="button"
                      class="btn btn-ghost btn-sm"
                      @click="row.editing = true"
                    >
                      {{ t('console.editFailedRow') }}
                    </button>
                    <template v-else>
                      <button type="button" class="btn btn-primary btn-sm" :disabled="importValidating" @click="revalidateOneDraft(row)">
                        {{ t('console.importRevalidateOne') }}
                      </button>
                      <button type="button" class="btn btn-ghost btn-sm" @click="row.editing = false">
                        {{ t('common.cancel') }}
                      </button>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="btn-row" style="margin-top:14px;justify-content:flex-end;flex-wrap:wrap;gap:8px">
            <button type="button" class="btn btn-ghost" @click="closeImportModal">{{ t('common.cancel') }}</button>
            <button
              v-if="importValidating"
              type="button"
              class="btn btn-outline"
              @click="skipImportPrecheck"
            >
              {{ t('console.importSkipPrecheck') }}
            </button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="importConfirmBusy || !importDraftRows.some((r) => r.selected)"
              @click="confirmImportDrafts"
            >
              {{ importConfirmBusy ? t('common.loading') : t('console.importConfirm') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <ConsoleGroupModal
      v-model:show="showGroupManage"
      v-model:new-group-name="newGroupName"
      v-model:editing-group-id="editingGroupId"
      v-model:editing-group-name="editingGroupName"
      :groups="groups"
      :group-stats="groupStats"
      @add="addGroup"
      @start-rename="startRenameGroup"
      @save-rename="saveRenameGroup"
      @remove="removeGroup"
    />

    <!-- Full body modal (toolbar expand) -->
    <div
      v-if="showBodyModal && selectedMessage"
      class="modal-backdrop body-modal-backdrop"
      @click.self="showBodyModal = false"
    >
      <div class="modal card-solid body-modal" role="dialog" aria-modal="true">
        <header class="modal-head">
          <h2 class="body-modal-title">
            {{ selectedMessage.subject || t('console.mailNoSubject') }}
          </h2>
          <div class="body-modal-actions">
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              @click="doCopy('modal-body', detailText || selectedMessage.subject || '')"
            >
              {{ t('console.copyBody') }}
            </button>
            <button type="button" class="btn btn-ghost btn-sm" @click="showBodyModal = false">
              {{ t('common.close') }}
            </button>
          </div>
        </header>
        <div class="modal-body body-modal-meta muted">
          <span>
            <span class="meta-label">{{ t('console.mailFrom') }}</span>
            {{ selectedMessage.from || selectedMessage.from_address || '—' }}
          </span>
          <template v-if="messageTo(selectedMessage)">
            <span class="dot">·</span>
            <span :title="messageTo(selectedMessage)">
              <span class="meta-label">{{ t('console.mailTo') }}</span>
              {{ messageTo(selectedMessage) }}
            </span>
          </template>
          <span class="dot">·</span>
          <span>{{ selectedMessage.date || '—' }}</span>
        </div>
        <div class="modal-body body-modal-content">
          <div v-if="detailHtml" class="detail-body detail-html" v-html="detailHtml" />
          <pre v-else class="detail-body">{{ detailText || t('console.mailNoBody') }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Base console (mobile-first defaults; desktop overrides in min-width:1101px) */
.console {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: calc(100dvh - var(--nav-h, 56px));
  height: auto;
  max-height: none;
  padding: 8px;
  overflow-x: hidden;
  overflow-y: auto;
  box-sizing: border-box;
}
.console.import-collapsed {
  /* no special grid on narrow */
}

/* .glass from base.css */

.import-expand {
  writing-mode: vertical-rl;
  text-orientation: mixed;
  padding: 12px 6px;
  font-size: 12px;
  font-weight: 650;
  color: var(--accent);
  cursor: pointer;
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0.08em;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  min-height: 0;
  overflow: hidden;
}
.console.import-collapsed .sidebar {
  display: none;
}
.side-kicker {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 2px;
}
.side-head {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
  min-width: 0;
}
.side-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.side-head-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  align-items: center;
}
.side-title {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.02em;
  min-width: 0;
}
.side-sub {
  font-size: 11px;
  color: var(--muted);
  margin: 0;
  line-height: 1.45;
}
.import-area {
  flex: 1;
  min-height: 100px;
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.45;
  resize: none;
  border-radius: 12px;
}
.import-preview {
  max-height: 72px;
  overflow: auto;
  font-size: 11px;
  color: var(--muted);
  list-style: none;
  margin: 0;
  padding: 8px 10px;
  background: var(--panel-soft);
  border-radius: 10px;
  border: 1px solid var(--border);
}
.side-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
.stat {
  padding: 8px 4px;
  border-radius: 10px;
  background: var(--panel-soft);
  border: 1px solid var(--border);
  text-align: center;
}
.stat-n {
  display: block;
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.stat-l {
  font-size: 10px;
  color: var(--muted);
}
.stat.ok .stat-n {
  color: var(--success);
}
.stat.err .stat-n {
  color: var(--danger);
}
.stat.unk .stat-n {
  color: var(--muted);
}

.main-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.main-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
}
.main-top h1 {
  font-size: 20px;
  font-weight: 750;
  letter-spacing: -0.03em;
}
.main-sub {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
  max-width: 520px;
}
.main-top-left {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}
.pill {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--panel);
  color: var(--muted);
  border: 1px solid var(--border);
  max-width: 280px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}
.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
  padding: 10px 12px;
}
.filter-bar .input {
  min-width: 120px;
  flex: 1 1 140px;
  max-width: 240px;
}
.filter-bar .search-wide {
  flex: 2 1 240px;
  max-width: 360px;
}
.filter-bar .filter-select {
  flex: 1 1 150px;
  max-width: 220px;
  min-width: 120px;
}
.filter-bar .move-group,
.toolbar-left .move-group {
  width: 140px;
  flex: 0 0 auto;
}
.pager .page-size {
  width: 72px;
  max-width: 88px;
  flex: 0 0 auto;
  position: relative;
  z-index: 5;
}
.mail-pager .page-size-sm {
  width: 68px;
  flex: 0 0 auto;
}

.table-card {
  flex: 0 1 auto;
  /* Base min only — desktop uses grid minmax; narrow overrides below */
  min-height: 220px;
  max-height: none;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.table-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 10px;
  flex-wrap: wrap;
}
.toolbar-left,
.toolbar-right {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.batch-hint {
  font-size: 11px;
  color: var(--muted);
}
.copy-tip {
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 3px 10px;
  border-radius: 999px;
  font-weight: 600;
  white-space: nowrap;
}
.tog {
  font-size: var(--control-font-xs, 11px);
  color: var(--muted);
}
.table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.total {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.data {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 12px;
}
.data th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: color-mix(in srgb, var(--panel-solid) 92%, var(--accent) 4%);
  backdrop-filter: blur(8px);
  text-align: left;
  padding: 9px 10px;
  font-weight: 650;
  font-size: 11px;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.data td {
  padding: 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.data tbody tr {
  cursor: pointer;
  transition: background 0.12s ease;
  min-height: 48px;
}
.data tbody tr:hover {
  background: color-mix(in srgb, var(--accent) 5%, transparent);
}
.data tbody tr.is-selected {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  box-shadow: inset 3px 0 0 var(--accent);
}
.data tbody tr.is-fetching {
  opacity: 0.75;
}
.col-check {
  width: 36px;
}
.col-idx {
  width: 36px;
  font-variant-numeric: tabular-nums;
}
.col-email {
  min-width: 200px;
  max-width: 300px;
}
.email-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.twofa-live {
  appearance: none;
  border: 0;
  background: var(--accent-soft);
  color: var(--accent);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 11px;
  line-height: 1.4;
  max-width: 100%;
}
.twofa-live:hover {
  filter: brightness(0.97);
}
.twofa-label {
  font-weight: 650;
  opacity: 0.85;
}
.twofa-code {
  font-weight: 750;
  letter-spacing: 0.08em;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}
.twofa-remain {
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  min-width: 1.6em;
  text-align: right;
}
.twofa-remain.urgent {
  color: var(--danger, #dc2626);
  font-weight: 700;
}
.field-row-smtp {
  display: grid;
  grid-template-columns: 1fr 100px;
  gap: 10px;
}
.field-smtp-port {
  min-width: 0;
}
@media (max-width: 520px) {
  .field-row-smtp {
    grid-template-columns: 1fr;
  }
}
.email-cell.is-child .email {
  font-weight: 500;
  font-size: 12px;
}
.email-cell.is-api .email {
  color: var(--accent);
}
.tree-pad {
  color: var(--muted-soft);
  font-family: var(--mono);
  font-size: 11px;
  flex-shrink: 0;
}
.col-star {
  width: 36px;
  text-align: center;
}
.star-btn {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--muted-soft);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
}
.star-btn:hover {
  color: var(--warning, #d97706);
  background: var(--warning-soft, rgba(217, 119, 6, 0.12));
}
.star-btn.on {
  color: var(--warning, #d97706);
}
.data tbody tr.is-starred td.col-email .email {
  font-weight: 700;
}
.cap-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.cap-tag {
  font-size: 10px;
  font-weight: 650;
  padding: 1px 6px;
  border-radius: 999px;
  color: var(--muted-soft);
  background: var(--panel-soft);
  border: 1px solid var(--border);
  line-height: 1.3;
}
.cap-tag.on {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: transparent;
}
/* Freeze actions column on desktop only (sticky-act class omitted on narrow) */
.col-act.sticky-act,
th.col-act.sticky-act {
  position: sticky;
  right: 0;
  z-index: 3;
  white-space: nowrap;
  min-width: 168px;
  width: 1%;
  box-shadow: -8px 0 12px -8px rgba(15, 23, 42, 0.18);
  background: var(--panel-solid);
}
th.col-act.sticky-act {
  z-index: 4;
  background: color-mix(in srgb, var(--panel-solid) 92%, var(--accent) 4%);
}
.data tbody tr:hover .col-act.sticky-act {
  background: color-mix(in srgb, var(--panel-solid) 88%, var(--accent) 8%);
}
.data tbody tr.is-selected .col-act.sticky-act {
  background: color-mix(in srgb, var(--panel-solid) 82%, var(--accent) 12%);
}
.col-act {
  white-space: nowrap;
  width: 1%;
  vertical-align: middle;
}
.col-act.acts-collapsed {
  min-width: 40px;
  width: 40px;
  box-shadow: none;
  position: static;
}
.col-act.acts-expanded {
  min-width: 0;
  position: static;
  box-shadow: none;
}
.act-more {
  min-width: 32px;
  font-weight: 800;
  letter-spacing: 0.04em;
}
.act-btn {
  min-width: 52px;
}
.row-acts {
  justify-content: flex-end;
}
.row-acts-mobile {
  flex-wrap: wrap;
  max-width: 200px;
  justify-content: flex-end;
}
.mail-load-more {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 10px 16px;
}
.mail-count {
  font-size: 11px;
}
.mail-no-more {
  margin: 0;
  font-size: 12px;
  text-align: center;
}

.copy-cell {
  appearance: none;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  padding: 0;
  margin: 0;
  text-align: left;
  cursor: copy;
  border-radius: 6px;
  transition: background 0.12s ease, color 0.12s ease;
  max-width: 100%;
}
.copy-cell:hover {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
}
.copy-cell.copied {
  background: var(--success-soft);
  color: var(--success);
}
.email {
  font-weight: 650;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.err-text {
  color: var(--danger) !important;
  word-break: break-word;
  max-width: 260px;
  margin: 0 auto 10px !important;
}
.mail-pager {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 6px;
  border-top: 1px solid var(--border);
  font-size: 11px;
  flex-wrap: wrap;
}
.page-size-sm {
  height: 26px;
  width: 64px;
  font-size: 11px;
}
.storage-chip.local {
  background: color-mix(in srgb, var(--muted) 12%, transparent);
}
.storage-chip.cloud {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent);
}
.mono {
  font-family: var(--mono);
  font-size: 11px;
}
.host,
.secret,
.time {
  display: inline-block;
  max-width: 140px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 2px 4px;
}
.secret {
  color: var(--muted);
}
.secret.is-warn {
  color: var(--warning);
}
.dash {
  padding: 0 4px;
}
.muted {
  color: var(--muted);
}

/* Mailbox type chip — solid soft fills so text never sits on bare white */
.type-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 24px;
  padding: 0 10px 0 7px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 650;
  line-height: 1;
  cursor: default;
  white-space: nowrap;
  max-width: 100%;
  background: #eef2ff;
  color: #4338ca;
}
.type-chip :deep(.brand-mark) {
  opacity: 0.95;
}
.type-chip.type-microsoft {
  background: #e8f3fc;
  color: #0b6cbd;
}
.type-chip.type-gmail {
  background: #fdecea;
  color: #c5221f;
}
.type-chip.type-qq {
  background: #e6f7fd;
  color: #0a8ec0;
}
.type-chip.type-netease {
  background: #fde8ea;
  color: #c4000f;
}
.type-chip.type-yahoo {
  background: #f3e8ff;
  color: #6b21a8;
}
.type-chip.type-icloud {
  background: #eef2f7;
  color: #374151;
}
.type-chip.type-mailcom,
.type-chip.type-aliyun {
  background: #fff3e8;
  color: #c2410c;
}
.type-chip.type-http_api {
  background: #ecfdf5;
  color: #047857;
}
.type-chip.type-other {
  background: #f1f5f9;
  color: #475569;
}
.chip {
  cursor: copy;
}
.code-pill {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 8px;
  border: 0;
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent);
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  font-family: var(--mono);
  font-size: 12px;
  cursor: pointer;
  letter-spacing: 0.06em;
}
.code-pill:hover,
.code-pill.copied {
  background: var(--accent);
  color: #fff;
}

/* status-dot from base.css */

.linkish {
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 11px;
  font-weight: 650;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 6px;
}
.linkish:hover:not(:disabled) {
  background: var(--accent-soft);
}
.linkish:disabled {
  opacity: 0.5;
}

.pager {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--panel-soft);
}
.pager-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pager-center {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
}
.pager-right {
  text-align: right;
  font-size: 12px;
}
.pager-label {
  font-size: 11px;
  color: var(--muted);
}
/* UiSelect — only constrain width; never force height/padding on the root
   (that broke the custom select layout and clipped the dropdown). */
.page-size {
  width: 72px !important;
  min-width: 72px !important;
  max-width: 88px !important;
  flex: 0 0 auto;
}
.page-size :deep(.ui-select-trigger) {
  height: var(--control-h-sm, 32px);
  min-height: var(--control-h-sm, 32px);
  padding: 0 8px 0 10px;
  font-size: 12px;
}
.pager-info {
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.mail-panel {
  flex: 1 1 auto;
  min-height: 220px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.mail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 8px;
}
.mail-tabs {
  display: flex;
  gap: 4px;
  padding: 3px;
  background: var(--panel-soft);
  border-radius: 10px;
  border: 1px solid var(--border);
}
.tab {
  border: 0;
  background: transparent;
  padding: 6px 12px;
  border-radius: 8px;
  color: var(--muted);
  font-weight: 650;
  font-size: 12px;
}
.tab.active {
  background: var(--bg-elevated);
  color: var(--accent);
  box-shadow: var(--shadow-sm);
}
.mail-head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mail-email {
  font-size: 12px;
  color: var(--muted);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 6px;
}

.code-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 14px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--accent) 16%, transparent),
    color-mix(in srgb, var(--accent-2) 10%, transparent)
  );
  border: 0;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  cursor: pointer;
  text-align: left;
}
.code-banner-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent);
  opacity: 0.85;
}
.code-banner-value {
  font-family: var(--mono);
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.code-banner-hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
}

.mail-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(220px, 300px) 1fr;
}
.mail-list-pane {
  overflow: auto;
  border-right: 1px solid var(--border);
  background: color-mix(in srgb, var(--panel-soft) 60%, transparent);
}
.mail-item {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  padding: 10px 12px;
}
.mail-item:hover {
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}
.mail-item.active {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  box-shadow: inset 3px 0 0 var(--accent);
}
.mail-item-top {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 12px;
  font-weight: 650;
}
.mail-from {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 70%;
  padding: 1px 3px;
}
.mail-sub {
  font-size: 11px;
  color: var(--muted);
  margin-top: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 1px 3px;
  display: block;
  width: 100%;
  text-align: left;
}
.mail-to {
  font-size: 10px;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.meta-label {
  font-weight: 650;
  opacity: 0.75;
  margin-right: 4px;
}
.meta-label::after {
  content: ':';
}
.code-chip {
  font-size: 10px;
  font-weight: 750;
  color: var(--accent);
  font-family: var(--mono);
  background: var(--accent-soft);
  padding: 2px 6px;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
}

.mail-detail-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  padding: 16px 18px;
}
.detail-title {
  font-weight: 750;
  font-size: 15px;
  letter-spacing: -0.02em;
  display: block;
  width: 100%;
  padding: 2px 4px;
  text-align: left;
}
.detail-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
  margin: 6px 0 12px;
  flex-shrink: 0;
}
.meta-part {
  padding: 2px 4px;
  color: var(--muted);
}
.dot {
  opacity: 0.5;
}
.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 2px;
  flex-shrink: 0;
}
.detail-head .detail-title {
  flex: 1;
  min-width: 0;
}
.detail-head-actions {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
}
.detail-body-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: var(--panel-soft);
}
.detail-body {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.55;
  margin: 0;
  font-family: inherit;
  padding: 12px 14px;
  border-radius: 0;
  background: transparent;
  border: 0;
}
.detail-html {
  white-space: normal;
  cursor: default;
}
.detail-html :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
}
.detail-html :deep(a) {
  color: var(--accent);
  word-break: break-all;
}
.detail-html :deep(p) {
  margin: 0 0 0.75em;
}
.body-modal-backdrop {
  z-index: 120;
}
.body-modal {
  width: min(920px, 96vw);
  max-height: min(90vh, 900px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.body-modal-title {
  margin: 0;
  font-size: 16px;
  font-weight: 750;
  line-height: 1.35;
  flex: 1;
  min-width: 0;
  word-break: break-word;
}
.body-modal .modal-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex-shrink: 0;
}
.body-modal-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.body-modal-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  padding-top: 0 !important;
  flex-shrink: 0;
}
.body-modal-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-top: 8px !important;
}

.empty,
.empty-sm {
  padding: 28px 20px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}
.empty-rich {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.empty-icon {
  width: 44px;
  height: 44px;
  margin: 0 auto 10px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 18px;
}
.empty-title {
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}
.empty-desc {
  font-size: 12px;
  color: var(--muted);
  max-width: 240px;
  line-height: 1.45;
  margin-bottom: 6px;
}
.import-help-modal {
  width: min(720px, 100%);
  max-height: min(86vh, 860px);
  overflow: auto;
  padding: 0;
}
.help-tip {
  padding: 10px 12px !important;
  background: var(--accent-soft);
  color: var(--accent) !important;
  border-radius: 10px;
  font-weight: 600 !important;
  line-height: 1.45 !important;
  margin: 0 0 10px !important;
}
.help-tip.muted-tip {
  background: var(--panel-soft);
  color: var(--muted) !important;
  font-weight: 500 !important;
}
.help-block {
  margin: 0 0 18px;
  padding: 14px 14px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--panel-soft) 70%, transparent);
}
.help-block h3 {
  margin: 0 0 8px !important;
  font-size: 14px !important;
  font-weight: 700 !important;
  color: var(--text) !important;
}
.help-need {
  font-size: 12px !important;
  color: var(--text-secondary) !important;
  margin: 0 0 8px !important;
  line-height: 1.45 !important;
}
.help-how-label,
.help-format-label {
  font-size: 11px !important;
  font-weight: 700 !important;
  color: var(--muted) !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin: 8px 0 4px !important;
}
.help-body-pre {
  font-size: 12px !important;
  color: var(--muted) !important;
  white-space: pre-line !important;
  line-height: 1.55 !important;
  margin: 0 0 8px !important;
}
.help-links {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 8px 0 10px;
}
.help-links-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  width: 100%;
}
.help-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid transparent;
  text-decoration: none;
  transition:
    background 0.12s ease,
    border-color 0.12s ease;
}
.help-link:hover {
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  color: var(--accent-hover);
}
.help-link::after {
  content: '↗';
  font-size: 10px;
  opacity: 0.75;
}
.help-block .code-block {
  margin: 0;
}

/* weak legacy media removed — see consolidated @media (max-width: 1100px) at end */

.import-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--muted);
  min-width: 0;
  width: 100%;
}
.import-group-select {
  width: 100%;
  max-width: 100%;
  min-width: 0;
}
.import-group :deep(.ui-select-trigger) {
  min-height: 40px;
}
/* import-collapsed grid override only on desktop (see min-width:1101px block) */
.console.import-collapsed .import-expand {
  display: none;
}
.row-acts {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  white-space: nowrap;
}
.act-del {
  color: var(--danger) !important;
}
.act-del:hover {
  background: var(--danger-soft) !important;
}
.err-block {
  margin-top: 4px;
  max-width: 100%;
}
.err-line {
  display: block;
  font-size: 11px;
  line-height: 1.35;
  color: var(--danger, #dc2626);
  word-break: break-word;
  cursor: default;
  user-select: text;
}

/* Desktop: fill grid track; keep a real min so empty state is not toolbar+pager only */
@media (min-width: 1101px) {
  .table-card {
    flex: unset !important;
    max-height: none !important;
    min-height: 280px !important;
    height: 100%;
  }
  .mail-panel {
    min-height: 220px !important;
    height: 100%;
  }
  .empty {
    flex: 1 1 auto;
    min-height: 160px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
}
.table-wrap {
  flex: 1 1 auto;
  overflow: auto;
}
.pager {
  position: sticky;
  bottom: 0;
  z-index: 2;
}
.move-group {
  width: 120px !important;
  min-width: 120px !important;
  max-width: 140px !important;
  height: 30px !important;
  padding: 0 8px !important;
  font-size: 12px;
}
.shortcut-hint {
  font-size: 11px;
  color: var(--muted);
  padding: 0 4px;
}

.group-tag {
  font-size: 10px;
  margin-top: 2px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.email-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.email-cell .email {
  min-width: 0;
}
.email-cell.is-child {
  padding-left: 6px;
}
.tree-pad {
  flex-shrink: 0;
  color: var(--muted);
  font-size: 12px;
  width: 14px;
  text-align: center;
}
.api-expand-btn {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel-soft);
  color: var(--text);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}
.api-expand-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.linkish {
  border: 0;
  background: none;
  padding: 0;
  margin: 0;
  color: var(--accent);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.linkish:hover {
  opacity: 0.85;
}

/* Desktop 3-pane ONLY above 1100px — never apply !important grid on small screens */
@media (min-width: 1101px) {
  .console {
    display: grid !important;
    /* Do NOT set grid-template-columns !important — Vue binds side width via :style */
    grid-template-columns: minmax(200px, 22%) 10px minmax(0, 1fr);
    gap: 0 !important;
    height: calc(100vh - var(--nav-h, 56px));
    max-height: calc(100vh - var(--nav-h, 56px));
    padding: 10px !important;
    overflow: hidden;
    box-sizing: border-box;
  }
  .console.import-collapsed {
    grid-template-columns: 48px 10px minmax(0, 1fr);
  }
  .console.import-collapsed .sidebar {
    display: none !important;
  }
  .console.import-collapsed .import-rail {
    display: flex !important;
  }
  .console.import-collapsed .import-expand {
    display: none !important;
  }
}
.import-backdrop {
  display: none;
}
.import-rail {
  display: none;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 12px 4px;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--accent);
  min-height: 120px;
  z-index: 5;
}
.import-rail:hover {
  background: var(--accent-soft);
}
.import-rail-icon {
  font-size: 18px;
  font-weight: 700;
}
.import-rail-text {
  writing-mode: vertical-rl;
  text-orientation: mixed;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
}
.splitter {
  background: transparent;
  position: relative;
  z-index: 4;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s ease;
}
.splitter-v {
  cursor: col-resize;
  width: 10px;
  margin: 0;
  touch-action: none;
}
.splitter-h {
  cursor: row-resize;
  height: 10px;
  touch-action: none;
}
.splitter-grip {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--border-strong) 80%, transparent);
  opacity: 0.55;
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    background 0.15s ease,
    box-shadow 0.15s ease;
}
.splitter-grip span {
  display: block;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--muted);
}
.splitter-grip-h {
  flex-direction: row;
  padding: 2px 6px;
}
.splitter-grip-h span {
  width: 3px;
  height: 3px;
}
.splitter:hover,
.splitter:active,
.console.dragging .splitter {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
}
.splitter:hover .splitter-grip,
.console.dragging .splitter .splitter-grip {
  opacity: 1;
  background: var(--accent-soft);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent);
}
.splitter:hover .splitter-grip span,
.console.dragging .splitter .splitter-grip span {
  background: var(--accent);
}
.main-col {
  display: flex !important;
  flex-direction: column;
  gap: 8px !important;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.pill.soft {
  font-size: 11px;
  max-width: 200px;
}
/* Default: stacked flow (mobile-first). Desktop grid only ≥1101px */
.split-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  flex: none;
  overflow: visible;
}
@media (min-width: 1101px) {
  .main-col {
    overflow: hidden;
  }
  .split-main {
    flex: 1 1 auto;
    min-height: 0;
    display: grid;
    gap: 0;
    overflow: hidden;
  }
  .table-card {
    flex: unset !important;
    max-height: none !important;
    min-height: 0 !important;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .mail-panel {
    flex: unset !important;
    min-height: 0 !important;
    overflow: hidden;
  }
}
.table-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.mail-panel {
  overflow: hidden;
}
.import-modal {
  width: min(920px, 96vw);
  max-height: min(88vh, 900px);
  overflow: auto;
  padding: 0;
}
.draft-table-wrap {
  max-height: 48vh;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 12px;
}
.draft-table { font-size: 12px; }
.draft-detail { max-width: 320px; font-size: 11px; color: var(--muted); }
.draft-table tr.is-err { background: color-mix(in srgb, var(--danger) 6%, transparent); }
.sidebar {
  min-width: 0;
  max-width: none;
}

.conc-input { width: 52px !important; height: 28px !important; padding: 0 6px !important; }
.batch-conc { gap: 6px; font-size: 11px; color: var(--muted); }
.hint { font-size: 11px; color: var(--muted); margin-top: 4px; }
.hint.sm { font-size: 10px; margin-top: 2px; }
.import-target {
  margin: 10px 0 14px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel-soft, rgba(0,0,0,0.03));
}
.import-target-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}
.quota-bar {
  margin-top: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
}
.poll-opt { margin-top: 8px; display: inline-flex; }
.draft-edit-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 200px;
}
.input-sm { height: 28px !important; font-size: 12px !important; padding: 0 8px !important; }
.label.sm { font-size: 10px; margin: 4px 0 0; }
.col-note { min-width: 120px; max-width: 200px; }
.note-cell {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  font-size: 12px;
  padding: 2px 0;
}
.note-cell:hover { color: var(--accent); }
.note-quick { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.note-chip {
  font-size: 10px !important;
  padding: 1px 6px !important;
  cursor: pointer;
}
.note-chip.sm { opacity: 0.75; }
.note-chip.sm:hover { opacity: 1; }
.note-edit { display: flex; flex-direction: column; gap: 6px; }
.note-input { width: 100%; }
.note-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.poll-tag { font-size: 10px; margin-top: 2px; }
.card-inset { /* alias */ }

/* ── Responsive: tablet / phone / small laptop windows ───────── */
@media (max-width: 1100px) {
  .console,
  .console.import-collapsed,
  .console.is-narrow {
    display: flex !important;
    flex-direction: column !important;
    grid-template-columns: none !important;
    height: auto !important;
    max-height: none !important;
    min-height: calc(100dvh - var(--nav-h, 56px));
    overflow-x: hidden !important;
    overflow-y: auto !important;
    padding: 8px !important;
    gap: 8px !important;
  }
  .copy-tip {
    display: none;
  }

  .import-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 45;
    background: rgba(15, 23, 42, 0.4);
    backdrop-filter: blur(2px);
  }

  .console .sidebar {
    display: flex !important;
    position: fixed;
    top: var(--nav-h, 56px);
    left: 0;
    bottom: 0;
    width: min(92vw, 360px);
    max-width: 100%;
    z-index: 50;
    max-height: none !important;
    border-radius: 0 var(--radius) var(--radius) 0;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
    overflow-y: auto;
    transform: translateX(-105%);
    transition: transform 0.22s ease;
  }
  .console.import-open .sidebar {
    transform: translateX(0);
  }
  .console.import-collapsed .sidebar {
    display: flex !important;
    transform: translateX(-105%);
    pointer-events: none;
  }

  .import-rail,
  .splitter-v,
  .splitter-h,
  .import-expand,
  .main-col {
    display: flex !important;
    flex-direction: column !important;
    width: 100%;
    min-width: 0;
    min-height: 0;
    overflow: visible !important;
    gap: 8px !important;
  }

  .filter-bar {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    padding: 10px;
  }
  .filter-bar .search-wide {
    grid-column: 1 / -1;
    max-width: none !important;
    flex: none !important;
    width: 100%;
  }
  .filter-bar .filter-select,
  .filter-bar .input {
    min-width: 0 !important;
    max-width: none !important;
    width: 100%;
    flex: none !important;
  }

  /* Stack table + mail; never use %-row grid (collapses when parent height is auto) */
  .split-main {
    display: flex !important;
    flex-direction: column !important;
    flex: none !important;
    min-height: 0 !important;
    height: auto !important;
    overflow: visible !important;
    gap: 10px !important;
    grid-template-rows: none !important;
  }

  .table-card {
    flex: none !important;
    max-height: none !important;
    min-height: 280px !important;
    height: auto !important;
    overflow: visible !important;
  }
  .table-toolbar {
    gap: 6px;
    padding: 8px 10px;
  }
  .table-toolbar .btn-sm {
    padding: 4px 8px;
    font-size: 11px;
  }
  .table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    max-height: min(52vh, 480px);
    min-height: 180px;
  }
  .data {
    min-width: 520px;
  }
  .data.compact {
    min-width: 0;
    width: 100%;
  }
  .col-note {
    min-width: 72px;
    max-width: 100px;
  }
  .note-quick {
    display: none;
  }
  .col-act.sticky-act,
  th.col-act.sticky-act {
    position: static !important;
    box-shadow: none !important;
    min-width: 40px;
  }
  .col-act.acts-collapsed {
    min-width: 40px;
    width: 40px;
  }
  .row-acts {
    max-width: 200px;
    gap: 2px;
    flex-wrap: wrap;
  }
  .row-acts .btn-sm,
  .row-acts .btn-xs {
    padding: 4px 6px;
    font-size: 11px;
  }

  .pager {
    display: flex !important;
    flex-wrap: wrap;
    justify-content: center;
    align-items: center;
    gap: 8px;
    grid-template-columns: none !important;
    position: relative !important;
  }
  .pager-right {
    display: none;
  }

  .mail-panel {
    min-height: 320px !important;
    flex: none !important;
    height: auto !important;
    overflow: visible !important;
  }
  .mail-head {
    flex-wrap: wrap;
    gap: 8px;
    align-items: stretch;
  }
  .mail-tabs {
    flex: 1 1 auto;
    min-width: 0;
  }
  .tab {
    flex: 1;
    white-space: nowrap;
    text-align: center;
    padding: 8px 10px;
  }
  .mail-head-right {
    flex: 1 1 100%;
    justify-content: space-between;
    min-width: 0;
  }
  .mail-email {
    max-width: min(55vw, 200px);
    flex: 1;
    min-width: 0;
  }
  .mail-body {
    grid-template-columns: 1fr !important;
    min-height: 240px;
  }

  .import-modal {
    width: min(100vw - 16px, 920px) !important;
    max-height: min(92dvh, 900px);
    margin: 8px;
  }
  .draft-table-wrap {
    max-height: 40vh;
  }
  .modal {
    max-width: calc(100vw - 16px);
  }
}

@media (max-width: 480px) {
  .console.is-narrow {
    padding: 6px !important;
  }
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .data th,
  .data td {
    padding: 6px 6px;
  }
  .row-acts {
    flex-wrap: wrap;
    max-width: 140px;
  }
}
</style>
