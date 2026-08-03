/**
 * Display times in the browser's local timezone.
 * Storage/API remain UTC ISO or epoch ms — only formatting uses the user zone.
 *
 * Display format (fixed, locale-independent digits):
 *   datetime → YYYY-MM-DD HH:mm:ss
 *   short    → YYYY-MM-DD HH:mm   (same calendar day may omit year? no — always full for clarity)
 *   date     → YYYY-MM-DD
 *   time     → HH:mm:ss
 *   mail     → YYYY-MM-DD HH:mm   (list / detail default)
 */

/** IANA timezone from the browser (e.g. Asia/Shanghai). */
export function userTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** BCP 47 locale tag for Intl (from app locale). */
export function intlLocale(appLocale?: string): string {
  if (appLocale === 'zh-CN' || appLocale?.toLowerCase().startsWith('zh')) return 'zh-CN'
  if (appLocale === 'en' || appLocale?.toLowerCase().startsWith('en')) return 'en'
  try {
    const nav = typeof navigator !== 'undefined' ? navigator.language : 'en'
    return nav || 'en'
  } catch {
    return 'en'
  }
}

/** Parse date string / epoch / Date → ms, or null. */
export function toEpochMs(input?: string | number | Date | null): number | null {
  if (input == null || input === '') return null
  if (typeof input === 'number') {
    return Number.isFinite(input) ? input : null
  }
  if (input instanceof Date) {
    const t = input.getTime()
    return Number.isFinite(t) ? t : null
  }
  const t = Date.parse(String(input))
  return Number.isFinite(t) ? t : null
}

export type DateTimeFormatKind = 'datetime' | 'date' | 'time' | 'short' | 'mail'

type Parts = {
  year: string
  month: string
  day: string
  hour: string
  minute: string
  second: string
}

/** Extract calendar parts in the user's timezone (24h). */
function partsInUserTz(ms: number): Parts | null {
  try {
    const fmt = new Intl.DateTimeFormat('en-CA', {
      timeZone: userTimeZone(),
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      hourCycle: 'h23',
    })
    const bag: Record<string, string> = {}
    for (const p of fmt.formatToParts(new Date(ms))) {
      if (p.type !== 'literal') bag[p.type] = p.value
    }
    // en-CA hour can be "24" for midnight in some engines — normalize
    let hour = bag.hour || '00'
    if (hour === '24') hour = '00'
    return {
      year: bag.year || '0000',
      month: bag.month || '01',
      day: bag.day || '01',
      hour,
      minute: bag.minute || '00',
      second: bag.second || '00',
    }
  } catch {
    return null
  }
}

function formatParts(p: Parts, kind: DateTimeFormatKind): string {
  switch (kind) {
    case 'date':
      return `${p.year}-${p.month}-${p.day}`
    case 'time':
      return `${p.hour}:${p.minute}:${p.second}`
    case 'short':
    case 'mail':
      // Compact list format
      return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`
    case 'datetime':
    default:
      return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`
  }
}

/**
 * Format any timestamp/mail date in the user's timezone.
 * Fixed pattern: YYYY-MM-DD HH:mm[:ss] (24h).
 */
export function formatInUserTz(
  input?: string | number | Date | null,
  opts: {
    locale?: string
    kind?: DateTimeFormatKind
    fallback?: string
  } = {},
): string {
  const fallback = opts.fallback ?? '—'
  const ms = toEpochMs(input)
  if (ms == null) {
    // Unparseable raw string: show as-is rather than hide
    if (typeof input === 'string' && input.trim()) return input.trim()
    return fallback
  }
  const parts = partsInUserTz(ms)
  if (!parts) {
    try {
      return new Date(ms).toISOString()
    } catch {
      return fallback
    }
  }
  return formatParts(parts, opts.kind || 'datetime')
}

/** Full title / tooltip with zone name. */
export function formatInUserTzTitle(
  input?: string | number | Date | null,
  _locale?: string,
): string {
  const base = formatInUserTz(input, { kind: 'datetime', fallback: '' })
  if (!base) return ''
  return `${base} (${userTimeZone()})`
}
