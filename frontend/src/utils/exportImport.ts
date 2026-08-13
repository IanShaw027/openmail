/** Credential TXT + full system snapshot export/import. */

import type { MailAccount } from '@/types/account'
import type { MailGroup } from '@/utils/groups'

/**
 * Build an importable credential line from the **current** account fields.
 *
 * Never prefer a stale `rawLine` when secrets were edited after import — that
 * was causing TXT export to ship the old password while `password` on the
 * account (and system JSON) already had the new one.
 */
export function accountToImportLine(
  a: Pick<
    MailAccount,
    | 'email'
    | 'type'
    | 'password'
    | 'authCode'
    | 'clientId'
    | 'refreshToken'
    | 'apiUrl'
    | 'apiKey'
    | 'imapHost'
    | 'imapPort'
    | 'smtpHost'
    | 'smtpPort'
    | 'rawLine'
  >,
): string {
  const email = (a.email || '').trim()
  const secret = (a.password || a.authCode || '').trim()

  if (a.type === 'oauth' && a.refreshToken && a.clientId) {
    return [email, secret, a.clientId, a.refreshToken].join('----')
  }
  if (a.type === 'http_api' && a.apiUrl) {
    const key = (a.apiKey || a.password || '').trim()
    return key ? `${email}----${a.apiUrl}----${key}` : `${email}----${a.apiUrl}`
  }
  if (a.imapHost || a.type === 'imap') {
    const host = a.imapHost || 'imap'
    const port = a.imapPort || 993
    const line = `imap----${email}----${secret}----${host}----${port}`
    if (a.smtpHost) {
      return `${line}----${a.smtpHost}${a.smtpPort ? `:${a.smtpPort}` : ''}`
    }
    return line
  }
  if (secret) {
    return `${email}----${secret}`
  }
  // Last resort: legacy raw line only when we have no structured secret
  if (a.rawLine && a.rawLine.includes('----')) return a.rawLine
  return email
}

/** Keep rawLine in sync after password/token edits (import preview + list edit). */
export function rebuildRawLine(
  a: Pick<
    MailAccount,
    | 'email'
    | 'type'
    | 'password'
    | 'authCode'
    | 'clientId'
    | 'refreshToken'
    | 'apiUrl'
    | 'apiKey'
    | 'imapHost'
    | 'imapPort'
    | 'smtpHost'
    | 'smtpPort'
    | 'rawLine'
  >,
): string {
  return accountToImportLine(a)
}

export function exportCredentialsTxt(accounts: MailAccount[]): string {
  return accounts.map(accountToImportLine).join('\n')
}

export interface SystemSnapshot {
  v: 1
  exportedAt: number
  deviceId?: string
  licenseToken?: string
  groups: MailGroup[]
  importGroupId?: string
  settings: {
    retentionDays: number
    lookbackDays: number
    firstFullDone: Record<string, boolean>
    batchConcurrency: number
    codeMasked: boolean
    denseCols: boolean
  }
  accounts: MailAccount[]
  /** Lightweight mail cache for search (optional) */
  mailCache?: Record<string, unknown[]>
  /** Local 2FA entries (optional) */
  twofa?: unknown[]
}

export function buildSystemSnapshot(opts: {
  accounts: MailAccount[]
  groups: MailGroup[]
  importGroupId?: string
  settings: SystemSnapshot['settings']
  deviceId?: string
  licenseToken?: string
  mailCache?: Record<string, unknown[]>
  twofa?: unknown[]
}): SystemSnapshot {
  return {
    v: 1,
    exportedAt: Date.now(),
    deviceId: opts.deviceId,
    licenseToken: opts.licenseToken,
    groups: opts.groups,
    importGroupId: opts.importGroupId,
    settings: opts.settings,
    accounts: opts.accounts,
    mailCache: opts.mailCache,
    twofa: opts.twofa,
  }
}

export function parseSystemSnapshot(text: string): SystemSnapshot {
  const data = JSON.parse(text) as SystemSnapshot
  if (!data || data.v !== 1 || !Array.isArray(data.accounts)) {
    throw new Error('invalid snapshot')
  }
  if (data.accounts.length > 5000) {
    throw new Error('invalid snapshot')
  }
  for (const row of data.accounts) {
    if (!row || typeof row !== 'object' || typeof row.email !== 'string' || !row.email.trim()) {
      throw new Error('invalid snapshot')
    }
  }
  if (data.mailCache != null && (typeof data.mailCache !== 'object' || Array.isArray(data.mailCache))) {
    throw new Error('invalid snapshot')
  }
  if (data.twofa != null && !Array.isArray(data.twofa)) {
    throw new Error('invalid snapshot')
  }
  return data
}

/** Snapshot restore must not keep another device's cloud row ids. */
export function detachSnapshotAccounts(accounts: MailAccount[]): MailAccount[] {
  return accounts.map((a) => ({
    ...a,
    storage: 'local',
    serverId: undefined,
    clientSealed: false,
    cloudSyncPending: false,
    cloudPendingPatch: undefined,
    updatedAt: Date.now(),
  }))
}
