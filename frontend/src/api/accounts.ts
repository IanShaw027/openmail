import { apiRequest, ApiError } from '@/api/client'
import type { AccountType } from '@/types/account'

export interface MailMessage {
  id: string
  subject?: string
  from?: string
  from_address?: string
  to?: string
  date?: string | null
  body_preview?: string
  body_text?: string
  body_html?: string
  folder?: string
  verification_code?: string | null
  /** IMAP UIDVALIDITY when known — scopes UID uniqueness */
  uidvalidity?: number | null
}

export interface FetchResult {
  ok: boolean
  messages?: MailMessage[]
  message_count?: number
  folder?: string
  fetched_at?: string
  error?: string | null
  code?: string | null
  latest_verification_code?: string | null
  account_id?: string | null
  /** Rolling cookies after successful cookie-provider login / restore */
  session_cookies?: Array<Record<string, unknown>> | null
  session_meta?: Record<string, unknown> | null
  session_restored?: boolean
  /** HttpApi multi-inbox: temp addresses under this api_url */
  mailboxes?: string[] | null
  /** IMAP folder UIDVALIDITY for this fetch */
  uidvalidity?: number | null
  phase?: 'full' | 'headers' | 'bodies'
  pending_body_ids?: string[]
  partial?: boolean
  credential_updates?: {
    refresh_token?: string
    access_token?: string
    oauth_transport?: string
  } | null
}

/** Body for proxy fetch (credentials not stored server-side). */
export interface ProxyFetchBody {
  email: string
  provider: AccountType | string
  folder?: string
  quick?: boolean
  password?: string | null
  credential?: Record<string, unknown> | null
  /** Prior session cookies (mail.com); server restores before password login */
  cookies?: Array<Record<string, unknown>> | null
  /** Fixed proxy for this mailbox (overrides instance pool) */
  proxy?: string | null
  /** ISO: only messages after this (incremental) */
  since?: string | null
  /** ISO: only messages strictly before this (load older) */
  before?: string | null
  /** Cap message count (e.g. 20 first page, 10 load-more) */
  max_messages?: number | null
  /** Force full recent list */
  full?: boolean
  phase?: 'full' | 'headers' | 'bodies'
  body_ids?: string[]
  uidvalidity?: number | null
  egress_mode?: 'interactive' | 'bulk'
}

export interface ServerAccountOut {
  id: string
  email: string
  provider: AccountType | string
  pool?: string
  owner_user_id?: string | null
  tag?: string | null
  note?: string | null
  status?: string
  last_error?: string | null
  latest_verification_code?: string | null
  latest_code_at?: string | null
  latest_code_folder?: string | null
  sync_enabled?: boolean
  last_sync_at?: string | null
  last_sync_error?: string | null
  proxy?: string | null
  has_password?: boolean
  has_credential?: boolean
  has_session?: boolean
  client_sealed?: boolean
  created_at?: string
  updated_at?: string
}

export interface AccountCreateBody {
  email: string
  provider: AccountType | string
  password?: string | null
  credential?: Record<string, unknown> | null
  tag?: string | null
  note?: string | null
  proxy?: string | null
  sync_enabled?: boolean
  cookies?: unknown[] | null
  /** Browser vault-sealed blob — server/admin cannot decrypt */
  client_sealed?: string | null
}

export interface AccountUpdateBody {
  email?: string
  provider?: AccountType | string
  password?: string | null
  credential?: Record<string, unknown> | null
  tag?: string | null
  note?: string | null
  proxy?: string | null
  sync_enabled?: boolean | null
  status?: string | null
  cookies?: unknown[] | null
  client_sealed?: string | null
}

export function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404
}

export function isServiceUnavailable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 503 || e.status === 501)
}

export async function listServerAccounts(): Promise<ServerAccountOut[]> {
  return apiRequest<ServerAccountOut[]>('/api/accounts')
}

export async function createServerAccount(body: AccountCreateBody): Promise<ServerAccountOut> {
  return apiRequest<ServerAccountOut>('/api/accounts', {
    method: 'POST',
    body,
  })
}

export async function updateServerAccount(
  id: string,
  body: AccountUpdateBody,
): Promise<ServerAccountOut> {
  return apiRequest<ServerAccountOut>(`/api/accounts/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body,
  })
}

export async function deleteServerAccount(id: string): Promise<void> {
  await apiRequest<void>(`/api/accounts/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export async function fetchServerAccount(
  id: string,
  opts: {
    folder?: string
    quick?: boolean
    since?: string
    before?: string
    maxMessages?: number
    full?: boolean
    phase?: 'full' | 'headers' | 'bodies'
    bodyIds?: string[]
    uidvalidity?: number | null
    timeoutMs?: number
    egressMode?: 'interactive' | 'bulk'
  } = {},
): Promise<FetchResult> {
  const q = new URLSearchParams()
  if (opts.folder) q.set('folder', opts.folder)
  if (opts.quick != null) q.set('quick', opts.quick ? 'true' : 'false')
  if (opts.since) q.set('since', opts.since)
  if (opts.before) q.set('before', opts.before)
  if (opts.maxMessages != null) q.set('max_messages', String(opts.maxMessages))
  if (opts.full != null) q.set('full', opts.full ? 'true' : 'false')
  if (opts.phase) q.set('phase', opts.phase)
  if (opts.uidvalidity != null) q.set('uidvalidity', String(opts.uidvalidity))
  if (opts.bodyIds?.length) q.set('body_ids', opts.bodyIds.join(','))
  if (opts.egressMode) q.set('egress_mode', opts.egressMode)
  const qs = q.toString()
  return apiRequest<FetchResult>(
    `/api/accounts/${encodeURIComponent(id)}/fetch${qs ? `?${qs}` : ''}`,
    { method: 'POST', timeoutMs: opts.timeoutMs ?? 55_000 },
  )
}

export type ProxyRequestOpts = {
  /** Client abort / batch cancel */
  signal?: AbortSignal
  /** Default 55s — prevents import precheck hanging forever */
  timeoutMs?: number
}

/** Cookie / mail.com first login needs more headroom than IMAP. */
export const COOKIE_PROXY_TIMEOUT_MS = 90_000

function defaultProxyTimeoutMs(provider?: string | null): number {
  const p = String(provider || '').toLowerCase()
  if (p === 'cookie' || p === 'unknown') return COOKIE_PROXY_TIMEOUT_MS
  return 55_000
}

/** Proxy fetch with credentials in body (local-first; not stored server-side). */
export async function proxyFetchMail(
  body: ProxyFetchBody,
  opts: ProxyRequestOpts = {},
): Promise<FetchResult> {
  return apiRequest<FetchResult>('/api/fetch/proxy', {
    method: 'POST',
    timeoutMs: opts.timeoutMs ?? defaultProxyTimeoutMs(body.provider),
    signal: opts.signal,
    body: {
      folder: body.folder ?? 'inbox',
      quick: body.quick ?? true,
      email: body.email,
      provider: body.provider,
      password: body.password ?? undefined,
      credential: body.credential ?? undefined,
      cookies: body.cookies ?? undefined,
      proxy: body.proxy || undefined,
      since: body.since || undefined,
      before: body.before || undefined,
      max_messages: body.max_messages ?? undefined,
      full: body.full || undefined,
      phase: body.phase || undefined,
      body_ids: body.body_ids?.length ? body.body_ids : undefined,
      uidvalidity: body.uidvalidity ?? undefined,
      egress_mode: body.egress_mode || undefined,
    },
  })
}

export interface SendMailBody {
  to: string[]
  subject?: string
  body_text?: string
  body_html?: string
  email?: string
  provider?: AccountType | string
  password?: string | null
  credential?: Record<string, unknown> | null
  proxy?: string | null
}

export interface SendMailResult {
  ok: boolean
  error?: string | null
  detail?: string | null
  credential_updates?: {
    refresh_token?: string
    access_token?: string
    oauth_transport?: string
  } | null
}

/** Send with credentials in body (local accounts). */
export async function proxySendMail(
  body: SendMailBody,
  opts: ProxyRequestOpts = {},
): Promise<SendMailResult> {
  return apiRequest<SendMailResult>('/api/fetch/send', {
    method: 'POST',
    timeoutMs: opts.timeoutMs ?? 55_000,
    signal: opts.signal,
    body,
  })
}

/** Build credential blob for proxy fetch/send from a local account-like object. */
export function credentialFromLocal(acc: {
  type: AccountType
  email?: string
  refreshToken?: string
  clientId?: string
  oauthTransport?: string
  apiUrl?: string
  apiKey?: string
  apiAuthStyle?: string
  imapHost?: string
  imapPort?: number
  smtpHost?: string
  smtpPort?: number
  authCode?: string
  password?: string
  sessionCookies?: Array<Record<string, unknown>>
  sessionMeta?: Record<string, unknown>
}): Record<string, unknown> | null {
  if (acc.type === 'oauth') {
    const c: Record<string, unknown> = {}
    if (acc.refreshToken) c.refresh_token = acc.refreshToken
    if (acc.clientId) c.client_id = acc.clientId
    if (acc.oauthTransport) c.oauth_transport = acc.oauthTransport
    return Object.keys(c).length ? c : null
  }
  if (acc.type === 'http_api') {
    if (!acc.apiUrl) return null
    const c: Record<string, unknown> = { api_url: acc.apiUrl }
    if (acc.email && !acc.email.startsWith('api@')) c.email = acc.email
    // Optional secret: open APIs leave empty
    const secret = acc.apiKey || acc.password || acc.authCode
    if (secret) {
      c.api_key = secret
      c.password = secret
    }
    if (acc.apiAuthStyle) c.api_auth_style = acc.apiAuthStyle
    return c
  }
  if (acc.type === 'imap') {
    const c: Record<string, unknown> = {}
    if (acc.imapHost) c.imap_host = acc.imapHost
    if (acc.imapPort) c.imap_port = acc.imapPort
    if (acc.smtpHost) c.smtp_host = acc.smtpHost
    if (acc.smtpPort) c.smtp_port = acc.smtpPort
    if (acc.authCode) c.auth_code = acc.authCode
    if (acc.password) c.password = acc.password
    return Object.keys(c).length ? c : null
  }
  // cookie / unknown (mail.com): attach rolling session so server can restore
  if (acc.type === 'cookie' || acc.type === 'unknown') {
    const c: Record<string, unknown> = {}
    if (acc.sessionCookies?.length) c.cookies = acc.sessionCookies
    if (acc.sessionMeta && Object.keys(acc.sessionMeta).length) c.session_meta = acc.sessionMeta
    if (acc.password) c.password = acc.password
    if (acc.smtpHost) c.smtp_host = acc.smtpHost
    if (acc.smtpPort) c.smtp_port = acc.smtpPort
    return Object.keys(c).length ? c : null
  }
  return null
}

/** Map browser account fields → API create body. */
export function toCreateBody(
  partial: {
    email: string
    type: AccountType
    password?: string
    refreshToken?: string
    clientId?: string
    oauthTransport?: string
    apiUrl?: string
    apiKey?: string
    apiAuthStyle?: string
    imapHost?: string
    imapPort?: number
    smtpHost?: string
    smtpPort?: number
    authCode?: string
    note?: string
    proxy?: string
    sessionCookies?: Array<Record<string, unknown>>
    sessionMeta?: Record<string, unknown>
  },
  opts: { syncEnabled?: boolean; clientSealed?: string | null } = {},
): AccountCreateBody {
  const provider = partial.type === 'unknown' ? 'cookie' : partial.type
  // Optional client-sealed backup only (explicit advanced path). Poll path must
  // send server-decryptable fields so SyncWorker can fetch — never seal by default.
  if (opts.clientSealed) {
    return {
      email: partial.email,
      provider,
      note: partial.note || undefined,
      proxy: partial.proxy || undefined,
      sync_enabled: false,
      client_sealed: opts.clientSealed,
    }
  }
  return {
    email: partial.email,
    provider,
    password: partial.password || partial.authCode || partial.apiKey || undefined,
    credential: credentialFromLocal({
      type: partial.type,
      email: partial.email,
      refreshToken: partial.refreshToken,
      clientId: partial.clientId,
      oauthTransport: partial.oauthTransport,
      apiUrl: partial.apiUrl,
      apiKey: partial.apiKey,
      apiAuthStyle: partial.apiAuthStyle,
      imapHost: partial.imapHost,
      imapPort: partial.imapPort,
      smtpHost: partial.smtpHost,
      smtpPort: partial.smtpPort,
      authCode: partial.authCode,
      password: partial.password,
      sessionCookies: partial.sessionCookies,
      sessionMeta: partial.sessionMeta,
    }),
    note: partial.note || undefined,
    proxy: partial.proxy || undefined,
    sync_enabled: Boolean(opts.syncEnabled),
  }
}

/** Pick best verification code from a fetch result. */
export function extractCode(result: FetchResult): string | undefined {
  if (result.latest_verification_code) return result.latest_verification_code
  if (result.code) return result.code
  const msgs = result.messages ?? []
  for (const m of msgs) {
    if (m.verification_code) return m.verification_code
  }
  return undefined
}
