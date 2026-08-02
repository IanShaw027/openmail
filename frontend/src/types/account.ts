import type { MailBrand } from '@/utils/domainBrand'

export type AccountType = 'oauth' | 'cookie' | 'http_api' | 'imap' | 'unknown'
export type AccountStorage = 'local' | 'server'
export type AccountStatus = 'ok' | 'error' | 'unknown'
export type AccountPool = 'public' | 'user_private'

export interface MailAccount {
  id: string
  email: string
  type: AccountType
  /** Brand family from domain (microsoft / gmail / qq …) */
  brand?: MailBrand
  storage: AccountStorage
  status: AccountStatus
  /** Present when storage === 'server' */
  serverId?: string
  pool?: AccountPool
  password?: string
  refreshToken?: string
  clientId?: string
  apiUrl?: string
  /**
   * HttpApi / CF Worker secret (optional). Open APIs leave empty.
   * Sent as common auth headers (x-admin-auth, X-API-Key, Bearer, …).
   */
  apiKey?: string
  /**
   * Auth style: auto | none | x-admin-auth | x-api-key | bearer | x-custom-auth
   * Default auto = send multiple common headers with apiKey.
   */
  apiAuthStyle?: string
  imapHost?: string
  imapPort?: number
  /** Outbound SMTP (if empty, inferred from domain; never use imap host raw) */
  smtpHost?: string
  smtpPort?: number
  authCode?: string
  /**
   * Fixed outbound proxy for this mailbox only (http/socks URL).
   * Overrides admin multi-channel pool. Stored in browser / server account.proxy.
   */
  proxy?: string
  /**
   * Rolling web-session cookies (mail.com etc.). Reused on next fetch;
   * provider auto re-logins when restore fails.
   */
  sessionCookies?: Array<Record<string, unknown>>
  /** Provider session meta (folder_url etc.) paired with sessionCookies */
  sessionMeta?: Record<string, unknown>
  /**
   * HttpApi (CF Worker): true when this row is the API source (one list line),
   * not a concrete mailbox. Children are temp addresses under apiMailboxes.
   */
  isApiSource?: boolean
  /** Parent MailAccount.id when this row is a mailbox under an HttpApi source */
  parentApiId?: string
  /** Discovered temp addresses under an HttpApi source (on the source row only) */
  apiMailboxes?: string[]
  tags: string[]
  /** Local group id; defaults to 'default' */
  groupId?: string
  note?: string
  latestCode?: string
  /** Code-fetch URL from create-or-return */
  codeApiUrl?: string
  lastError?: string
  /** Cloud hourly poll (server SyncWorker) */
  syncEnabled?: boolean
  /** User-marked priority mailbox; filterable in My Mail */
  starred?: boolean
  rawLine: string
  createdAt: number
  updatedAt: number
}

/** Whether this account can receive (fetch) mail with current credentials. */
export function accountCanFetch(acc: Pick<
  MailAccount,
  | 'type'
  | 'status'
  | 'password'
  | 'authCode'
  | 'refreshToken'
  | 'clientId'
  | 'apiUrl'
  | 'sessionCookies'
  | 'storage'
  | 'serverId'
  | 'isApiSource'
>): boolean {
  if (acc.type === 'oauth') return Boolean(acc.refreshToken && acc.clientId)
  if (acc.type === 'http_api') return Boolean(acc.apiUrl)
  if (acc.type === 'imap') return Boolean(acc.password || acc.authCode)
  if (acc.type === 'cookie') {
    return Boolean(
      acc.password ||
        (acc.sessionCookies && acc.sessionCookies.length) ||
        (acc.storage === 'server' && acc.serverId),
    )
  }
  // unknown: allow try if any secret present
  return Boolean(
    acc.password ||
      acc.authCode ||
      acc.apiUrl ||
      (acc.refreshToken && acc.clientId) ||
      (acc.storage === 'server' && acc.serverId),
  )
}

/** Whether this account can send mail (local secrets required). */
export function accountCanSend(acc: Pick<
  MailAccount,
  'type' | 'password' | 'authCode' | 'refreshToken' | 'clientId' | 'isApiSource'
>): boolean {
  // Temp-mail API sources / pure cookie without password: no outbound send
  if (acc.isApiSource) return false
  if (acc.type === 'http_api') return false
  if (acc.type === 'oauth') return Boolean(acc.refreshToken && acc.clientId)
  if (acc.type === 'imap') return Boolean(acc.password || acc.authCode)
  if (acc.type === 'cookie') return Boolean(acc.password)
  return Boolean(acc.password || acc.authCode || (acc.refreshToken && acc.clientId))
}

export interface ImportResult {
  created: number
  updated: number
  invalid: number
  warnings?: string[]
  lineMessages?: string[]
}
