/**
 * Email brand / protocol defaults by domain suffix.
 *
 * Strategy:
 * - Most consumer/corporate mail → IMAP with known host:993 (auth code / app password)
 * - Microsoft consumer: OAuth preferred when tokens present; IMAP (outlook.office365.com)
 *   is supported but often needs OAuth/XOAUTH2 — basic password frequently fails
 * - mail.com family → cookie web session (no public IMAP for free tier typically)
 * - Custom API URLs stay http_api
 */

export type MailBrand =
  | 'microsoft'
  | 'gmail'
  | 'qq'
  | 'netease'
  | 'yahoo'
  | 'icloud'
  | 'aliyun'
  | 'mailcom'
  | 'proton'
  | 'zoho'
  | 'gmx'
  | 'cf_temp'
  | 'duckmail'
  | 'http_api'
  | 'other'

export interface DomainProfile {
  brand: MailBrand
  /** Short label key suffix for i18n: brand.microsoft */
  labelKey: string
  protocol: 'oauth' | 'imap' | 'cookie' | 'http_api'
  imapHost?: string
  imapPort?: number
  /** Prefer OAuth when credentials allow */
  oauthPreferred?: boolean
  note?: string
}

const EXACT: Record<string, DomainProfile> = {
  // Microsoft
  'outlook.com': {
    brand: 'microsoft',
    labelKey: 'brand.microsoft',
    protocol: 'oauth',
    imapHost: 'outlook.office365.com',
    imapPort: 993,
    oauthPreferred: true,
  },
  'hotmail.com': {
    brand: 'microsoft',
    labelKey: 'brand.microsoft',
    protocol: 'oauth',
    imapHost: 'outlook.office365.com',
    imapPort: 993,
    oauthPreferred: true,
  },
  'live.com': {
    brand: 'microsoft',
    labelKey: 'brand.microsoft',
    protocol: 'oauth',
    imapHost: 'outlook.office365.com',
    imapPort: 993,
    oauthPreferred: true,
  },
  'msn.com': {
    brand: 'microsoft',
    labelKey: 'brand.microsoft',
    protocol: 'oauth',
    imapHost: 'outlook.office365.com',
    imapPort: 993,
    oauthPreferred: true,
  },
  // Google
  'gmail.com': {
    brand: 'gmail',
    labelKey: 'brand.gmail',
    protocol: 'imap',
    imapHost: 'imap.gmail.com',
    imapPort: 993,
  },
  'googlemail.com': {
    brand: 'gmail',
    labelKey: 'brand.gmail',
    protocol: 'imap',
    imapHost: 'imap.gmail.com',
    imapPort: 993,
  },
  // Tencent
  'qq.com': {
    brand: 'qq',
    labelKey: 'brand.qq',
    protocol: 'imap',
    imapHost: 'imap.qq.com',
    imapPort: 993,
  },
  'foxmail.com': {
    brand: 'qq',
    labelKey: 'brand.qq',
    protocol: 'imap',
    imapHost: 'imap.qq.com',
    imapPort: 993,
  },
  // NetEase
  '163.com': {
    brand: 'netease',
    labelKey: 'brand.netease',
    protocol: 'imap',
    imapHost: 'imap.163.com',
    imapPort: 993,
  },
  '126.com': {
    brand: 'netease',
    labelKey: 'brand.netease',
    protocol: 'imap',
    imapHost: 'imap.126.com',
    imapPort: 993,
  },
  'yeah.net': {
    brand: 'netease',
    labelKey: 'brand.netease',
    protocol: 'imap',
    imapHost: 'imap.yeah.net',
    imapPort: 993,
  },
  // Yahoo
  'yahoo.com': {
    brand: 'yahoo',
    labelKey: 'brand.yahoo',
    protocol: 'imap',
    imapHost: 'imap.mail.yahoo.com',
    imapPort: 993,
  },
  'ymail.com': {
    brand: 'yahoo',
    labelKey: 'brand.yahoo',
    protocol: 'imap',
    imapHost: 'imap.mail.yahoo.com',
    imapPort: 993,
  },
  // Apple
  'icloud.com': {
    brand: 'icloud',
    labelKey: 'brand.icloud',
    protocol: 'imap',
    imapHost: 'imap.mail.me.com',
    imapPort: 993,
  },
  'me.com': {
    brand: 'icloud',
    labelKey: 'brand.icloud',
    protocol: 'imap',
    imapHost: 'imap.mail.me.com',
    imapPort: 993,
  },
  'mac.com': {
    brand: 'icloud',
    labelKey: 'brand.icloud',
    protocol: 'imap',
    imapHost: 'imap.mail.me.com',
    imapPort: 993,
  },
  // Aliyun enterprise
  'aliyun.com': {
    brand: 'aliyun',
    labelKey: 'brand.aliyun',
    protocol: 'imap',
    imapHost: 'imap.aliyun.com',
    imapPort: 993,
  },
  // mail.com / United Internet free webmail (cookie)
  'mail.com': {
    brand: 'mailcom',
    labelKey: 'brand.mailcom',
    protocol: 'cookie',
  },
  'email.com': {
    brand: 'mailcom',
    labelKey: 'brand.mailcom',
    protocol: 'cookie',
  },
  'usa.com': {
    brand: 'mailcom',
    labelKey: 'brand.mailcom',
    protocol: 'cookie',
  },
  'consultant.com': {
    brand: 'mailcom',
    labelKey: 'brand.mailcom',
    protocol: 'cookie',
  },
  'europe.com': {
    brand: 'mailcom',
    labelKey: 'brand.mailcom',
    protocol: 'cookie',
  },
  'asia.com': {
    brand: 'mailcom',
    labelKey: 'brand.mailcom',
    protocol: 'cookie',
  },
  // GMX
  'gmx.com': {
    brand: 'gmx',
    labelKey: 'brand.gmx',
    protocol: 'imap',
    imapHost: 'imap.gmx.com',
    imapPort: 993,
  },
  'gmx.net': {
    brand: 'gmx',
    labelKey: 'brand.gmx',
    protocol: 'imap',
    imapHost: 'imap.gmx.net',
    imapPort: 993,
  },
  'gmx.de': {
    brand: 'gmx',
    labelKey: 'brand.gmx',
    protocol: 'imap',
    imapHost: 'imap.gmx.net',
    imapPort: 993,
  },
  // Proton (bridge / IMAP when available)
  'proton.me': {
    brand: 'proton',
    labelKey: 'brand.proton',
    protocol: 'imap',
    imapHost: '127.0.0.1',
    imapPort: 1143,
    note: 'Proton Bridge',
  },
  'protonmail.com': {
    brand: 'proton',
    labelKey: 'brand.proton',
    protocol: 'imap',
    imapHost: '127.0.0.1',
    imapPort: 1143,
    note: 'Proton Bridge',
  },
  // Zoho
  'zoho.com': {
    brand: 'zoho',
    labelKey: 'brand.zoho',
    protocol: 'imap',
    imapHost: 'imap.zoho.com',
    imapPort: 993,
  },
  'zohomail.com': {
    brand: 'zoho',
    labelKey: 'brand.zoho',
    protocol: 'imap',
    imapHost: 'imap.zoho.com',
    imapPort: 993,
  },
  // DuckMail / public temp
  'duck.com': {
    brand: 'duckmail',
    labelKey: 'brand.duckmail',
    protocol: 'http_api',
  },
}

const SUFFIX: Array<[string, DomainProfile]> = [
  ['.qiye.aliyun.com', EXACT['aliyun.com']!],
  ['.mxhichina.com', { brand: 'aliyun', labelKey: 'brand.aliyun', protocol: 'imap', imapHost: 'imap.mxhichina.com', imapPort: 993 }],
  ['.mail.com', EXACT['mail.com']!],
  ['.gmx.com', EXACT['gmx.com']!],
  ['.gmx.net', EXACT['gmx.net']!],
  ['.zoho.com', EXACT['zoho.com']!],
  ['.proton.me', EXACT['proton.me']!],
  ['.protonmail.com', EXACT['protonmail.com']!],
]

export function emailDomain(email: string): string {
  const e = (email || '').trim().toLowerCase()
  if (!e.includes('@')) return ''
  return e.split('@').pop() || ''
}

export function resolveDomainProfile(email: string): DomainProfile {
  const domain = emailDomain(email)
  if (!domain) {
    return { brand: 'other', labelKey: 'brand.other', protocol: 'imap' }
  }
  if (EXACT[domain]) return EXACT[domain]!
  for (const [suf, profile] of SUFFIX) {
    if (domain.endsWith(suf) || domain === suf.slice(1)) return profile
  }
  // longest exact suffix from EXACT keys
  for (const [key, profile] of Object.entries(EXACT)) {
    if (domain.endsWith('.' + key)) return profile
  }
  return { brand: 'other', labelKey: 'brand.other', protocol: 'imap' }
}

/**
 * Map IMAP / SMTP hostnames → brand (overrides weak email-domain "other").
 * e.g. imap.gmail.com, outlook.office365.com, imap.qq.com
 */
const IMAP_HOST_BRAND: Array<{ test: RegExp; brand: MailBrand; profile?: Partial<DomainProfile> }> = [
  { test: /(^|\.)gmail\.com$/i, brand: 'gmail' },
  { test: /(^|\.)googlemail\.com$/i, brand: 'gmail' },
  { test: /(^|\.)google\.com$/i, brand: 'gmail' },
  { test: /outlook\.office365\.com$/i, brand: 'microsoft' },
  { test: /smtp\.office365\.com$/i, brand: 'microsoft' },
  { test: /office365\.com$/i, brand: 'microsoft' },
  { test: /(^|\.)outlook\.com$/i, brand: 'microsoft' },
  { test: /(^|\.)hotmail\.com$/i, brand: 'microsoft' },
  { test: /qq\.com$/i, brand: 'qq' },
  { test: /foxmail\.com$/i, brand: 'qq' },
  { test: /163\.com$/i, brand: 'netease' },
  { test: /126\.com$/i, brand: 'netease' },
  { test: /yeah\.net$/i, brand: 'netease' },
  { test: /mail\.yahoo\./i, brand: 'yahoo' },
  { test: /yahoo\./i, brand: 'yahoo' },
  { test: /mail\.me\.com$/i, brand: 'icloud' },
  { test: /icloud\.com$/i, brand: 'icloud' },
  { test: /aliyun\.com$/i, brand: 'aliyun' },
  { test: /mxhichina\.com$/i, brand: 'aliyun' },
  { test: /qiye\.aliyun/i, brand: 'aliyun' },
  { test: /(^|\.)mail\.com$/i, brand: 'mailcom' },
  { test: /(^|\.)gmx\./i, brand: 'gmx' },
  { test: /zoho\./i, brand: 'zoho' },
  { test: /workers\.dev$/i, brand: 'cf_temp' },
]

export function normalizeMailHost(host?: string | null): string {
  return String(host || '')
    .trim()
    .toLowerCase()
    .replace(/^\[|\]$/g, '')
    .replace(/:\d+$/, '')
    .replace(/^\./, '')
}

/** Resolve brand primarily from IMAP/SMTP server hostname. */
export function resolveBrandFromHost(host?: string | null): MailBrand | null {
  const h = normalizeMailHost(host)
  if (!h) return null
  for (const row of IMAP_HOST_BRAND) {
    if (row.test.test(h)) return row.brand
  }
  return null
}

/**
 * Best brand for an account: IMAP host wins when present, else email domain.
 */
export function resolveAccountBrand(opts: {
  email?: string
  imapHost?: string | null
  smtpHost?: string | null
  apiUrl?: string | null
  type?: string
  brand?: MailBrand | string | null
}): MailBrand {
  const fromHost =
    resolveBrandFromHost(opts.imapHost) || resolveBrandFromHost(opts.smtpHost)
  if (fromHost) return fromHost
  // CF temp-mail Worker / self-hosted API
  if (opts.type === 'http_api') {
    const api = String(opts.apiUrl || opts.email || '').toLowerCase()
    if (api.includes('workers.dev') || api.includes('cf_temp') || api.startsWith('api@')) {
      return 'cf_temp'
    }
    if (api.includes('duck')) return 'duckmail'
    return 'http_api'
  }
  if (opts.type === 'cookie') {
    const d = resolveDomainProfile(opts.email || '')
    if (d.brand === 'mailcom') return 'mailcom'
  }
  if (opts.brand && opts.brand !== 'other') return opts.brand as MailBrand
  return resolveDomainProfile(opts.email || '').brand
}

export const BRAND_OPTIONS: MailBrand[] = [
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
