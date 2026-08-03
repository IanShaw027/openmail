/**
 * Which fetch limits each account type actually honors on the server.
 *
 * - since_before: IMAP / Graph native time filters
 * - local_filter: cookie / http_api pull list then filter by date (best-effort)
 * - none: no time filtering
 */

import type { MailAccount } from '@/types/account'

export type ProviderTimePaging = 'since_before' | 'local_filter' | 'none'

export function providerTimePaging(
  type: MailAccount['type'] | string | undefined,
): ProviderTimePaging {
  const t = String(type || '').toLowerCase()
  if (t === 'oauth' || t === 'imap') return 'since_before'
  if (t === 'cookie' || t === 'http_api' || t === 'unknown') return 'local_filter'
  return 'none'
}

/** True when remote load-older is supported (native or multi-page local filter). */
export function supportsRemoteLoadOlder(
  acc: Pick<MailAccount, 'type'> | null | undefined,
): boolean {
  if (!acc) return false
  const mode = providerTimePaging(acc.type)
  // cookie: multi-page messagelist + before=; http_api still best-effort
  return mode === 'since_before' || mode === 'local_filter'
}

/** True when silent incremental should send `since=` for this account type. */
export function supportsIncrementalSince(
  acc: Pick<MailAccount, 'type'> | null | undefined,
): boolean {
  if (!acc) return false
  const mode = providerTimePaging(acc.type)
  return mode === 'since_before' || mode === 'local_filter'
}
