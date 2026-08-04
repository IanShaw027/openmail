/**
 * Common IANA zones for the settings picker.
 * Default app zone is Asia/Shanghai (see settings store).
 * Special value `browser` = follow the browser's resolved timezone.
 */

export const BROWSER_TZ = 'browser'

export const DEFAULT_TIMEZONE = 'Asia/Shanghai'

export interface TimeZoneOption {
  value: string
  /** i18n key under settings.tz_* or raw label fallback */
  labelKey?: string
  labelEn: string
  labelZh: string
}

export const TIMEZONE_OPTIONS: TimeZoneOption[] = [
  { value: DEFAULT_TIMEZONE, labelEn: 'China (Shanghai)', labelZh: '中国（上海）' },
  { value: 'Asia/Hong_Kong', labelEn: 'Hong Kong', labelZh: '香港' },
  { value: 'Asia/Taipei', labelEn: 'Taipei', labelZh: '台北' },
  { value: 'Asia/Singapore', labelEn: 'Singapore', labelZh: '新加坡' },
  { value: 'Asia/Tokyo', labelEn: 'Tokyo', labelZh: '东京' },
  { value: 'Asia/Seoul', labelEn: 'Seoul', labelZh: '首尔' },
  { value: 'Asia/Bangkok', labelEn: 'Bangkok', labelZh: '曼谷' },
  { value: 'Asia/Kolkata', labelEn: 'India (Kolkata)', labelZh: '印度（加尔各答）' },
  { value: 'Asia/Dubai', labelEn: 'Dubai', labelZh: '迪拜' },
  { value: 'Europe/London', labelEn: 'London', labelZh: '伦敦' },
  { value: 'Europe/Paris', labelEn: 'Paris', labelZh: '巴黎' },
  { value: 'Europe/Berlin', labelEn: 'Berlin', labelZh: '柏林' },
  { value: 'Europe/Moscow', labelEn: 'Moscow', labelZh: '莫斯科' },
  { value: 'America/New_York', labelEn: 'New York', labelZh: '纽约' },
  { value: 'America/Chicago', labelEn: 'Chicago', labelZh: '芝加哥' },
  { value: 'America/Denver', labelEn: 'Denver', labelZh: '丹佛' },
  { value: 'America/Los_Angeles', labelEn: 'Los Angeles', labelZh: '洛杉矶' },
  { value: 'America/Sao_Paulo', labelEn: 'São Paulo', labelZh: '圣保罗' },
  { value: 'Australia/Sydney', labelEn: 'Sydney', labelZh: '悉尼' },
  { value: 'Pacific/Auckland', labelEn: 'Auckland', labelZh: '奥克兰' },
  { value: 'UTC', labelEn: 'UTC', labelZh: 'UTC' },
  { value: BROWSER_TZ, labelEn: 'Browser / system', labelZh: '浏览器 / 系统时区' },
]

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** Resolve settings value → IANA zone used by Intl. */
export function resolveTimeZone(stored?: string | null): string {
  const v = (stored || '').trim()
  if (!v || v === BROWSER_TZ) return browserTimeZone()
  // Validate lightly — invalid zones fall back to default
  try {
    Intl.DateTimeFormat('en-US', { timeZone: v }).format(new Date())
    return v
  } catch {
    return DEFAULT_TIMEZONE
  }
}

export function normalizeTimeZone(v: unknown): string {
  if (typeof v !== 'string' || !v.trim()) return DEFAULT_TIMEZONE
  const s = v.trim()
  if (s === BROWSER_TZ) return BROWSER_TZ
  if (TIMEZONE_OPTIONS.some((o) => o.value === s)) return s
  // allow any valid IANA string even if not in the short list
  try {
    Intl.DateTimeFormat('en-US', { timeZone: s }).format(new Date())
    return s
  } catch {
    return DEFAULT_TIMEZONE
  }
}
