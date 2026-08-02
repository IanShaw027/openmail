import type { MailAccount } from '@/types/account'
import { resolveAccountBrand, resolveDomainProfile } from '@/utils/domainBrand'
import type { ServerAccountOut } from '@/api/accounts'

export const LOCAL_ACCOUNTS_KEY = 'openmail.accounts.local'

export function normalizeLocalAccounts(parsed: MailAccount[]): MailAccount[] {
  if (!Array.isArray(parsed)) return []
  return parsed.map((a) => {
    let next = a
    // Always re-resolve so IMAP host brands win over stale "other"
    const brand = resolveAccountBrand({
      email: next.email,
      imapHost: next.imapHost,
      smtpHost: next.smtpHost,
      type: next.type,
      brand: next.brand,
    })
    if (next.brand !== brand) next = { ...next, brand }
    if (!next.groupId) next = { ...next, groupId: 'default' }
    if (next.storage !== 'local') next = { ...next, storage: 'local' }
    return next
  })
}

/** Legacy plaintext load (only during vault migration; prefer vault). */
export function loadAccountsPlain(): MailAccount[] {
  try {
    const raw = localStorage.getItem(LOCAL_ACCOUNTS_KEY)
    if (!raw) return []
    return normalizeLocalAccounts(JSON.parse(raw) as MailAccount[])
  } catch {
    return []
  }
}

export function mapServerToLocal(row: ServerAccountOut, prev?: MailAccount): MailAccount {
  const email = row.email
  const provider = String(row.provider || 'unknown') as MailAccount['type']
  const type = provider === 'unknown' ? prev?.type || 'unknown' : provider
  const brand = resolveAccountBrand({
    email,
    imapHost: prev?.imapHost,
    smtpHost: prev?.smtpHost,
    type,
    brand: prev?.brand || resolveDomainProfile(email).brand,
  })
  let status: MailAccount['status'] = 'unknown'
  if (row.status === 'ok') status = 'ok'
  else if (row.status === 'error' || row.status === 'need_reauth') status = 'error'
  const createdAt = row.created_at ? Date.parse(row.created_at) || Date.now() : Date.now()
  const updatedAt = row.updated_at ? Date.parse(row.updated_at) || Date.now() : Date.now()
  return {
    id: prev?.id && prev.storage === 'server' ? prev.id : `srv_${row.id}`,
    email,
    type,
    brand,
    storage: 'server',
    status,
    serverId: row.id,
    note: row.note || prev?.note,
    proxy: row.proxy || prev?.proxy,
    latestCode: row.latest_verification_code || prev?.latestCode,
    lastError: row.last_error || undefined,
    syncEnabled: Boolean(row.sync_enabled),
    tags: prev?.tags || [],
    groupId: prev?.groupId || 'default',
    password: prev?.password,
    refreshToken: prev?.refreshToken,
    clientId: prev?.clientId,
    apiUrl: prev?.apiUrl,
    imapHost: prev?.imapHost,
    imapPort: prev?.imapPort,
    smtpHost: prev?.smtpHost,
    smtpPort: prev?.smtpPort,
    authCode: prev?.authCode,
    rawLine: prev?.rawLine || email,
    createdAt: prev?.createdAt || createdAt,
    updatedAt,
  }
}
