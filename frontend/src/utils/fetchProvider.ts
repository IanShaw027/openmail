import { resolveDomainProfile } from '@/utils/domainBrand'

export type FetchProvider = 'imap' | 'oauth' | 'cookie' | 'http_api'

/** Provider string sent to /api/fetch/proxy. Never treat Gmail as mail.com cookie. */
export function resolveFetchProvider(acc: {
  type?: string | null
  email?: string | null
}): FetchProvider {
  const t = String(acc.type || '').trim().toLowerCase()
  if (t === 'http_api' || t === 'oauth' || t === 'imap' || t === 'cookie') {
    return t
  }
  const protocol = resolveDomainProfile(acc.email || '').protocol
  if (protocol === 'imap' || protocol === 'http_api') return protocol
  if (protocol === 'cookie') return 'cookie'
  return 'cookie'
}

export function isCookieFetchProvider(provider: FetchProvider | string | null | undefined): boolean {
  const p = String(provider || '').toLowerCase()
  return p === 'cookie' || p === 'unknown'
}
