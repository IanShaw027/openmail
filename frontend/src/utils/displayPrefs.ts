/**
 * Runtime display preferences (timezone / theme bridge).
 * Settings store writes; formatters / CSS helpers read — avoids pinia cycles.
 */

import { DEFAULT_TIMEZONE, resolveTimeZone } from '@/utils/timezones'
import type { ThemeMode } from '@/utils/theme'

let _timeZoneStored = DEFAULT_TIMEZONE
let _theme: ThemeMode = 'system'

export function setDisplayTimeZone(stored: string): void {
  _timeZoneStored = stored || DEFAULT_TIMEZONE
}

export function getDisplayTimeZone(): string {
  return resolveTimeZone(_timeZoneStored)
}

export function getStoredTimeZone(): string {
  return _timeZoneStored || DEFAULT_TIMEZONE
}

export function setDisplayTheme(mode: ThemeMode): void {
  _theme = mode
}

export function getDisplayTheme(): ThemeMode {
  return _theme
}
