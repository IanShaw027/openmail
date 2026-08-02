/** Credential TXT + full system snapshot export/import. */

import type { MailAccount } from '@/types/account'
import type { MailGroup } from '@/utils/groups'

export function accountToImportLine(a: MailAccount): string {
  if (a.rawLine && a.rawLine.includes('----')) return a.rawLine
  if (a.type === 'oauth' && a.refreshToken && a.clientId) {
    return [a.email, a.password || '', a.clientId, a.refreshToken].join('----')
  }
  if (a.type === 'http_api' && a.apiUrl) {
    return `${a.email}----${a.apiUrl}`
  }
  if (a.imapHost) {
    return `imap----${a.email}----${a.password || a.authCode || ''}----${a.imapHost}----${a.imapPort || 993}`
  }
  if (a.password || a.authCode) {
    return `${a.email}----${a.password || a.authCode}`
  }
  return a.email
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
  return data
}
