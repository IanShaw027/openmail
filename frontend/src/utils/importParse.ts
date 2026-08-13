import type { MailAccount } from '@/types/account'
import { i18n } from '@/i18n'
import { resolveAccountBrand, resolveDomainProfile } from '@/utils/domainBrand'

function tt(key: string, params?: Record<string, unknown>): string {
  // vue-i18n Composer typings are strict about message keys; runtime keys are fine.
  return String((i18n.global as { t: (k: string, p?: Record<string, unknown>) => unknown }).t(key, params))
}

function uid(): string {
  return `acc_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`
}

function looksLikeEmail(s: string): boolean {
  return /^[^\s@]+@[0-9A-Za-z.-]+\.[A-Za-z]{2,}$/.test(s)
}

/** Gmail app password display form: "xxxx xxxx xxxx xxxx" → compact for IMAP. */
export function normalizeImapSecret(password: string, email = ''): string {
  const pw = String(password || '').trim()
  if (!pw) return pw
  const compact = pw.replace(/\s+/g, '')
  const domain = email.includes('@') ? email.split('@').pop()!.toLowerCase() : ''
  if (domain === 'gmail.com' || domain === 'googlemail.com') return compact
  if (/^(?:[A-Za-z0-9]{4}\s+){3}[A-Za-z0-9]{4}$/.test(pw)) return compact
  return pw
}

function looksLikeUrl(s: string): boolean {
  return /^https?:\/\//i.test(s)
}

function hostFromUrl(url: string): string {
  try {
    return new URL(url).hostname || 'api'
  } catch {
    return 'api'
  }
}

/** Normalize CF Worker URL: bare origin is enough (backend probes /api/*). */
function normalizeWorkerApiUrl(url: string): string {
  const u = url.trim().replace(/\/+$/, '')
  return u
}

/** Bare Worker URL, optional secret: `https://…` or `https://…----secret`. */
function parseBareApiUrl(raw: string): ParsedLine | null {
  const text = raw.trim()
  if (!text.includes('http')) return null
  // Single URL (root workers.dev is OK — backend expands /api/mails etc.)
  if (looksLikeUrl(text) && !text.includes('----') && !/\s/.test(text)) {
    const apiUrl = normalizeWorkerApiUrl(text)
    const host = hostFromUrl(apiUrl)
    const isCf = host.endsWith('workers.dev')
    return {
      ok: true,
      kind: 'http_api',
      raw,
      message: tt('importParse.httpApiSourceNoKey', { host }),
      account: {
        email: `api@${host}`,
        type: 'http_api',
        apiUrl,
        isApiSource: true,
        brand: isCf ? 'cf_temp' : 'http_api',
        rawLine: raw,
        note: isCf
          ? tt('importParse.noteCfTemp', { host })
          : tt('importParse.noteCfApi', { host }),
      },
    }
  }
  // URL----secret  or secret----URL
  if (text.includes('----')) {
    const parts = text.split('----').map((p) => p.trim()).filter(Boolean)
    const urlPart = parts.find((p) => looksLikeUrl(p))
    if (!urlPart || parts.length < 2) return null
    // Only URL + secret (no email) → API source with key
    const nonUrl = parts.filter((p) => p !== urlPart && !looksLikeEmail(p))
    const hasEmail = parts.some((p) => looksLikeEmail(p))
    if (hasEmail) return null
    const secret = nonUrl[0]
    const apiUrl = normalizeWorkerApiUrl(urlPart)
    const host = hostFromUrl(apiUrl)
    const isCf = host.endsWith('workers.dev')
    const label = isCf
      ? tt('importParse.cfTempLabel')
      : tt('importParse.httpApiSourceLabel')
    return {
      ok: true,
      kind: 'http_api',
      raw,
      message: secret
        ? `${label} · ${host}${tt('importParse.withKeySuffix')}`
        : `${label} · ${host}${tt('importParse.noKeySuffix')}`,
      account: {
        email: `api@${host}`,
        type: 'http_api',
        apiUrl,
        apiKey: secret,
        password: secret,
        isApiSource: true,
        brand: isCf ? 'cf_temp' : 'http_api',
        rawLine: raw,
        note: secret
          ? isCf
            ? tt('importParse.noteCfTempWithKey', { host })
            : tt('importParse.noteWithKey', { host })
          : isCf
            ? tt('importParse.noteCfTemp', { host })
            : tt('importParse.noteCfApi', { host }),
      },
    }
  }
  return null
}

/** Microsoft app client id (UUID). */
export function looksLikeMicrosoftClientId(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    String(s || '').trim(),
  )
}

/** Microsoft refresh token (M.… or very long opaque string). */
export function looksLikeMicrosoftRefreshToken(s: string): boolean {
  const t = String(s || '').trim()
  return /^M\./i.test(t) || t.length > 180
}

export type ImportKind =
  | 'oauth'
  | 'http_api'
  | 'imap'
  | 'cookie'
  | 'smtp_hint'
  | 'unknown'
  | 'invalid'

export interface ParsedLine {
  ok: boolean
  kind: ImportKind
  account?: Omit<
    MailAccount,
    'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'
  >
  raw: string
  /** Human message for validation UI */
  message?: string
  warnings?: string[]
}

function splitLine(raw: string): string[] {
  const text = raw.trim()
  if (!text) return []
  if (text.includes('----')) return text.split('----').map((p) => p.trim()).filter((p) => p !== '')
  if (text.includes('\t')) return text.split('\t').map((p) => p.trim()).filter(Boolean)
  // multi-space: keep email/password groups carefully — prefer ---- for structured
  return text.split(/\s{2,}/).map((p) => p.trim()).filter(Boolean)
}

/**
 * mail-public compatible parser with client_id / refresh_token auto-swap.
 */

function enrichAccount(
  account: NonNullable<ParsedLine['account']>,
  line?: ParsedLine,
): NonNullable<ParsedLine['account']> {
  const profile = resolveDomainProfile(account.email)
  // Normalize app-password display forms (Gmail "xxxx xxxx xxxx xxxx")
  if (account.password) {
    account = {
      ...account,
      password: normalizeImapSecret(account.password, account.email),
    }
  }
  if (account.authCode) {
    account = {
      ...account,
      authCode: normalizeImapSecret(account.authCode, account.email),
    }
  }
  // Auto-fill IMAP host/port from domain when missing
  if (account.type === 'imap' || (account.type === 'unknown' && profile.protocol === 'imap')) {
    if (!account.imapHost && profile.imapHost) {
      account = {
        ...account,
        type: 'imap',
        imapHost: profile.imapHost,
        imapPort: account.imapPort || profile.imapPort || 993,
      }
    }
  }
  // mail.com family without oauth tokens → cookie
  if (profile.protocol === 'cookie' && account.type !== 'oauth' && account.type !== 'http_api') {
    account = { ...account, type: 'cookie' }
  }
  // Microsoft: only password → still mark brand; prefer oauth if tokens present
  if (profile.oauthPreferred && account.refreshToken && account.clientId) {
    account = { ...account, type: 'oauth' }
  }
  // Brand: IMAP/SMTP host takes priority over email domain (custom domains on Gmail IMAP etc.)
  const brand = resolveAccountBrand({
    email: account.email,
    imapHost: account.imapHost,
    smtpHost: account.smtpHost,
    type: account.type,
    brand: profile.brand,
  })
  // Microsoft without OAuth tokens → warn only when brand is microsoft and no IMAP host
  // (password+IMAP on outlook.office365.com may still work with app password in some tenants)
  if (
    brand === 'microsoft' &&
    account.type !== 'http_api' &&
    account.type !== 'imap' &&
    (!account.refreshToken || !account.clientId)
  ) {
    const warn = tt('importParse.msMissingTokens')
    if (line) {
      line.warnings = [...(line.warnings || []), warn]
    }
  }
  return { ...account, brand }
}

export function parseAccountLine(line: string): ParsedLine {
  const raw = line.trim()
  if (!raw || raw.startsWith('#'))
    return { ok: false, kind: 'invalid', raw, message: tt('importParse.emptyOrComment') }

  // Bare CF Worker / HttpApi URL as a single list row (source, not a mailbox)
  const bare = parseBareApiUrl(raw)
  if (bare) return bare

  // Space-separated loose formats (user pastes without ----)
  // e.g. "email  app password" or "email password host port"
  let parts = splitLine(raw)

  // Single-space lines with email first: try soft split
  if (parts.length === 1 && raw.includes(' ')) {
    const soft = raw.split(/\s+/).filter(Boolean)
    if (soft.length >= 2 && looksLikeEmail(soft[0]!)) {
      parts = soft
    }
  }

  // imap----email----auth----host----port
  if (parts[0]?.toLowerCase() === 'imap' && parts.length >= 4) {
    const email = parts[1] ?? ''
    if (!looksLikeEmail(email)) {
      return {
        ok: false,
        kind: 'invalid',
        raw,
        message: tt('importParse.invalidImapEmail'),
      }
    }
    const port = parts[4] ? Number(parts[4]) || 993 : 993
    return {
      ok: true,
      kind: 'imap',
      raw,
      message: tt('importParse.imapHost', { email, host: `${parts[3]}:${port}` }),
      account: {
        email,
        type: 'imap',
        authCode: parts[2],
        imapHost: parts[3],
        imapPort: port,
        password: parts[2],
        rawLine: raw,
      },
    }
  }

  // Find email position (first email-like token)
  let emailIdx = parts.findIndex((p) => looksLikeEmail(p))
  if (emailIdx < 0) {
    return { ok: false, kind: 'invalid', raw, message: tt('importParse.noEmail') }
  }
  const email = parts[emailIdx]!.toLowerCase()
  const rest = [...parts.slice(0, emailIdx), ...parts.slice(emailIdx + 1)]

  // URL anywhere → http_api (CF Worker / multi-inbox source)
  const urlPart = rest.find((p) => looksLikeUrl(p))
  if (urlPart) {
    const apiUrl = normalizeWorkerApiUrl(urlPart)
    const host = hostFromUrl(apiUrl)
    const isPlaceholder =
      email.startsWith('api@') ||
      email === host ||
      email.endsWith('.workers.dev') ||
      !email.includes('@')
    const sourceEmail = isPlaceholder ? `api@${host}` : email
    // secret: non-url field that is not the email (admin password / API key)
    const secret =
      rest.find(
        (p) =>
          p !== urlPart &&
          !looksLikeUrl(p) &&
          p.toLowerCase() !== email.toLowerCase() &&
          p.length >= 1,
      ) || undefined
    const isCf = host.endsWith('workers.dev')
    return {
      ok: true,
      kind: 'http_api',
      raw,
      message: isPlaceholder
        ? tt('importParse.httpApiSourceExpand', {
            host,
            secret: secret ? tt('importParse.withKeySuffix') : tt('importParse.noKeySuffix'),
          })
        : tt('importParse.httpApiMailbox', {
            email,
            secret: secret ? tt('importParse.secretSuffix') : '',
          }),
      account: {
        email: sourceEmail,
        type: 'http_api',
        apiUrl,
        apiKey: secret,
        password: secret,
        isApiSource: isPlaceholder,
        brand: isCf ? 'cf_temp' : 'http_api',
        rawLine: raw,
        note: isPlaceholder
          ? secret
            ? isCf
              ? tt('importParse.noteCfTempWithKey', { host })
              : tt('importParse.noteWithKey', { host })
            : isCf
              ? tt('importParse.noteCfTemp', { host })
              : tt('importParse.noteCfApi', { host })
          : secret
            ? tt('importParse.noteApiKeySaved')
            : undefined,
      },
    }
  }

  // OAuth: need client_id + refresh among fields
  const uuidField = rest.find((p) => looksLikeMicrosoftClientId(p))
  const refreshField = rest.find((p) => looksLikeMicrosoftRefreshToken(p))
  if (uuidField && refreshField) {
    const password =
      rest.find(
        (p) =>
          p !== uuidField &&
          p !== refreshField &&
          !looksLikeMicrosoftClientId(p) &&
          !looksLikeMicrosoftRefreshToken(p),
      ) || undefined
    return {
      ok: true,
      kind: 'oauth',
      raw,
      message: tt('importParse.msOauthOk', { email }),
      warnings:
        rest.indexOf(uuidField) < rest.indexOf(refreshField)
          ? [tt('importParse.msOauthOrder')]
          : undefined,
      account: {
        email,
        type: 'oauth',
        password,
        clientId: uuidField,
        refreshToken: refreshField,
        rawLine: raw,
      },
    }
  }

  // SMTP host hints (465/587) → keep both IMAP (receive) and SMTP (send)
  const smtpHostField = rest.find((p) => /smtp\./i.test(p) || /:465$|:587$/.test(p))
  if (smtpHostField || rest.some((p) => p === '465' || p === '587')) {
    const hostRaw = smtpHostField || rest.find((p) => p.includes('.')) || ''
    const smtpHostOnly = hostRaw.replace(/:\d+$/, '')
    const portFromHost = hostRaw.match(/:(\d+)$/)?.[1]
    const portPart = rest.find((p) => p === '465' || p === '587') || portFromHost
    const smtpPort = portPart ? Number(portPart) : 587
    // Map smtp.* → imap.* for receive; keep explicit smtp for send
    let imapHost = smtpHostOnly.replace(/^smtp\./i, 'imap.')
    let smtpHost = smtpHostOnly
    if (!/^smtp\./i.test(smtpHost) && /^imap\./i.test(smtpHost)) {
      smtpHost = smtpHost.replace(/^imap\./i, 'smtp.')
    }
    if (/qiye\.aliyun|mxhichina/i.test(hostRaw)) {
      imapHost = 'imap.qiye.aliyun.com'
      smtpHost = 'smtp.qiye.aliyun.com'
    }
    const password = rest.find(
      (p) =>
        p !== smtpHostField &&
        p !== '465' &&
        p !== '587' &&
        !looksLikeEmail(p) &&
        p.length >= 4 &&
        !/^(imap|smtp)\./i.test(p),
    )
    if (password) {
      return {
        ok: true,
        kind: 'imap',
        raw,
        message: tt('importParse.imapSmtp', {
          email,
          imapHost,
          smtpHost,
          smtpPort,
        }),
        warnings: [tt('importParse.imapSmtpWarn')],
        account: {
          email,
          type: 'imap',
          password,
          authCode: password,
          imapHost,
          imapPort: 993,
          smtpHost,
          smtpPort,
          rawLine: raw,
        },
      }
    }
  }

  // Gmail app password: 16 chars with spaces "xxxx xxxx xxxx xxxx"
  const joined = rest.join(' ')
  const appPass = joined.match(/\b([a-z]{4}\s+[a-z]{4}\s+[a-z]{4}\s+[a-z]{4})\b/i)
  if (appPass && /@gmail\.com$/i.test(email)) {
    const password = appPass[1]!.replace(/\s+/g, '')
    return {
      ok: true,
      kind: 'imap',
      raw,
      message: tt('importParse.gmailAppPass'),
      account: {
        email,
        type: 'imap',
        password,
        authCode: password,
        imapHost: 'imap.gmail.com',
        imapPort: 993,
        smtpHost: 'smtp.gmail.com',
        smtpPort: 587,
        rawLine: raw,
      },
    }
  }

  // Domain default IMAP when only email + password
  if (rest.length >= 1) {
    const password = rest[0]!
    const domain = email.split('@')[1]?.toLowerCase() || ''
    const imapDefaults: Record<string, string> = {
      'gmail.com': 'imap.gmail.com',
      'googlemail.com': 'imap.gmail.com',
      'qq.com': 'imap.qq.com',
      '163.com': 'imap.163.com',
      '126.com': 'imap.126.com',
      'outlook.com': '', // prefer oauth
      'hotmail.com': '',
      'live.com': '',
      'mail.com': '', // cookie
    }
    const defaultHost = imapDefaults[domain]

    // mail.com → cookie
    if (domain === 'mail.com' || domain.endsWith('.mail.com')) {
      return {
        ok: true,
        kind: 'cookie',
        raw,
        message: tt('importParse.mailcomCookie', { email }),
        warnings: [tt('importParse.mailcomCookieWarn')],
        account: {
          email,
          type: 'cookie',
          password,
          rawLine: raw,
        },
      }
    }

    // outlook-family with only password → warn need oauth
    if (defaultHost === '') {
      return {
        ok: false,
        kind: 'invalid',
        raw,
        message: tt('importParse.msNeedOauth', { domain }),
      }
    }

    if (defaultHost) {
      const smtpDefaults: Record<string, { host: string; port: number }> = {
        'imap.gmail.com': { host: 'smtp.gmail.com', port: 587 },
        'imap.qq.com': { host: 'smtp.qq.com', port: 587 },
        'imap.163.com': { host: 'smtp.163.com', port: 465 },
        'imap.126.com': { host: 'smtp.126.com', port: 465 },
      }
      const smtp = smtpDefaults[defaultHost]
      return {
        ok: true,
        kind: 'imap',
        raw,
        message: smtp
          ? tt('importParse.imapWithSmtp', {
              email,
              host: defaultHost,
              smtpHost: smtp.host,
              smtpPort: smtp.port,
            })
          : tt('importParse.imapOnly', { email, host: defaultHost }),
        account: {
          email,
          type: 'imap',
          password,
          authCode: password,
          imapHost: defaultHost,
          imapPort: 993,
          ...(smtp ? { smtpHost: smtp.host, smtpPort: smtp.port } : {}),
          rawLine: raw,
        },
      }
    }

    // generic password → cookie attempt for unknown webmail
    if (rest.length === 1 || rest.length === 2) {
      return {
        ok: true,
        kind: 'cookie',
        raw,
        message: tt('importParse.cookieWebmail', { email }),
        warnings: [tt('importParse.cookieUnknownWarn')],
        account: {
          email,
          type: 'cookie',
          password,
          rawLine: raw,
        },
      }
    }
  }

  // 4+ fields without oauth markers: try password + host
  if (rest.length >= 2) {
    const password = rest[0]!
    const host = rest.find((p) => p.includes('.') && !looksLikeEmail(p))
    if (host) {
      return {
        ok: true,
        kind: 'imap',
        raw,
        message: tt('importParse.imapHost', { email, host }),
        account: {
          email,
          type: 'imap',
          password,
          authCode: password,
          imapHost: host.replace(/:\d+$/, ''),
          imapPort: 993,
          rawLine: raw,
        },
      }
    }
  }

  return {
    ok: false,
    kind: 'invalid',
    raw,
    message: tt('importParse.unrecognized'),
  }
}

export function parseImportText(text: string): {
  accounts: Array<
    Omit<MailAccount, 'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'>
  >
  invalid: number
  lines: ParsedLine[]
  warnings: string[]
} {
  const linesOut: ParsedLine[] = []
  const accounts: Array<
    Omit<MailAccount, 'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'>
  > = []
  let invalid = 0
  const warnings: string[] = []

  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue
    const parsed = parseAccountLine(line)
    linesOut.push(parsed)
    if (parsed.ok && parsed.account) {
      const enriched = enrichAccount(parsed.account, parsed)
      accounts.push(enriched)
      if (parsed.warnings)
        warnings.push(...parsed.warnings.map((w) => `${parsed.account!.email}: ${w}`))
    } else if (line.trim() && !line.trim().startsWith('#')) {
      invalid += 1
    }
  }

  return { accounts, invalid, lines: linesOut, warnings }
}

export function createAccountFromParsed(
  partial: Omit<
    MailAccount,
    'id' | 'createdAt' | 'updatedAt' | 'storage' | 'status' | 'tags' | 'latestCode'
  >,
  opts?: { groupId?: string },
): MailAccount {
  const now = Date.now()
  return {
    ...partial,
    id: uid(),
    storage: 'local',
    status: 'unknown',
    tags: [],
    groupId: opts?.groupId || partial.groupId || 'default',
    latestCode: undefined,
    createdAt: now,
    updatedAt: now,
  }
}

/** Short placeholder — full examples live in import help modal */
export function importPlaceholder(): string {
  return tt('importParse.placeholder')
}

/** @deprecated Prefer importPlaceholder() for locale-aware text */
export const IMPORT_PLACEHOLDER = `Paste one account per line…

user@outlook.com----password----client_id----M.refresh_token
user@gmail.com----app-password
name@mail.com----password
# CF Worker no secret
https://mail-api.example.workers.dev
# CF Worker with secret
https://mail-api.example.workers.dev----your-admin-secret
`
