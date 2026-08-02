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
  // mail.com
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
}

const SUFFIX: Array<[string, DomainProfile]> = [
  ['.qiye.aliyun.com', EXACT['aliyun.com']!],
  ['.mxhichina.com', { brand: 'aliyun', labelKey: 'brand.aliyun', protocol: 'imap', imapHost: 'imap.mxhichina.com', imapPort: 993 }],
  ['.mail.com', EXACT['mail.com']!],
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

export const BRAND_OPTIONS: MailBrand[] = [
  'microsoft',
  'gmail',
  'qq',
  'netease',
  'yahoo',
  'icloud',
  'aliyun',
  'mailcom',
  'http_api',
  'other',
]
