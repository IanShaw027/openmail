import type { MailAccount } from '@/types/account'
import { formatInUserTz } from '@/utils/datetime'

/** i18n-aware label helpers for console account columns. */
export type TranslateFn = (key: string, values?: Record<string, unknown>) => string

export function brandLabel(t: TranslateFn, b?: string): string {
  if (!b) return t('brand.other')
  const key = `brand.${b}`
  const v = t(key)
  return v === key ? b : v
}

export function typeLabel(t: TranslateFn, type: MailAccount['type']): string {
  const key = `console.type_${type}`
  const v = t(key)
  return v === key ? type : v
}

export function secretHint(t: TranslateFn, acc: MailAccount): string {
  if (acc.type === 'oauth') {
    if (acc.refreshToken && acc.clientId) return t('console.secretOauthOk')
    return t('console.secretOauthMissing')
  }
  if (acc.type === 'http_api' && acc.apiUrl) return 'API'
  if (acc.password || acc.authCode) return '••••'
  if (acc.storage === 'server') return 'srv'
  return '—'
}

export function hostLabel(t: TranslateFn, acc: MailAccount): string {
  if (acc.type === 'http_api' && acc.apiUrl) {
    try {
      return new URL(acc.apiUrl).host
    } catch {
      return acc.apiUrl.slice(0, 28)
    }
  }
  if (acc.imapHost) return `${acc.imapHost}${acc.imapPort ? ':' + acc.imapPort : ''}`
  if (acc.type === 'oauth' || acc.brand === 'microsoft') return t('console.hostOauth')
  if (acc.type === 'cookie' || acc.brand === 'mailcom') return t('console.hostCookie')
  return '—'
}

export function hostCopyValue(acc: MailAccount): string {
  if (acc.apiUrl) return acc.apiUrl
  if (acc.imapHost) return `${acc.imapHost}${acc.imapPort ? ':' + acc.imapPort : ''}`
  if (acc.type === 'oauth' || acc.brand === 'microsoft') return 'graph.microsoft.com'
  if (acc.type === 'http_api' && acc.apiUrl) {
    try {
      return new URL(acc.apiUrl).host
    } catch {
      return acc.apiUrl.slice(0, 28)
    }
  }
  if (acc.type === 'cookie' || acc.brand === 'mailcom') return 'mail.com'
  return acc.imapHost || acc.email
}

export function formatRelativeTime(
  t: TranslateFn,
  locale: string,
  ts?: number,
): string {
  if (!ts) return '—'
  try {
    const now = Date.now()
    const diff = now - ts
    if (diff < 60_000) return t('console.timeJustNow')
    if (diff < 3600_000) return t('console.timeMinsAgo', { n: Math.floor(diff / 60_000) })
    if (diff < 86400_000) return t('console.timeHoursAgo', { n: Math.floor(diff / 3600_000) })
    // Absolute clock in the configured display timezone (not UTC wall-clock)
    return formatInUserTz(ts, { locale, kind: 'short' })
  } catch {
    return '—'
  }
}

export function displayCode(
  code: string | undefined,
  id: string,
  codeMasked: boolean,
  revealed: Set<string>,
): string {
  if (!code) return ''
  if (!codeMasked || revealed.has(id)) return code
  if (code.length <= 2) return '••'
  return `${code.slice(0, 1)}${'•'.repeat(Math.min(code.length - 1, 5))}`
}

export function copyableSecret(acc: MailAccount): string {
  if (acc.type === 'oauth') {
    return [acc.email, acc.password, acc.clientId, acc.refreshToken].filter(Boolean).join('----')
  }
  if (acc.type === 'http_api' && acc.apiUrl) return `${acc.email}----${acc.apiUrl}`
  if (acc.rawLine) return acc.rawLine
  if (acc.password) return `${acc.email}----${acc.password}`
  return acc.email
}

export function isTokenError(acc: MailAccount): boolean {
  const e = (acc.lastError || '').toLowerCase()
  return (
    acc.status === 'error' &&
    (/refresh|token|oauth|reauth|过期|无效|expired|invalid|unauthorized|401/.test(e) ||
      acc.type === 'oauth')
  )
}

/** OAuth tokens are not mailbox login passwords. */
export function canChangeMailboxPassword(type: MailAccount['type']): boolean {
  return type === 'imap' || type === 'cookie' || type === 'unknown'
}
