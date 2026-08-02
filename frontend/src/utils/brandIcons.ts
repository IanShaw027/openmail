/**
 * Compact monochrome brand marks (24×24 viewBox, currentColor).
 * Used on console type chips — keep paths simple for crisp small sizes.
 */

import type { MailBrand } from '@/utils/domainBrand'

/** Inline SVG path(s) for each brand. */
export function brandSvgPath(brand?: string | null): string {
  switch ((brand || 'other').toLowerCase()) {
    case 'gmail':
      // Gmail envelope "M"
      return 'M3 5.5A2.5 2.5 0 0 1 5.5 3h13A2.5 2.5 0 0 1 21 5.5v13a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 18.5v-13zm2.2.7 6.3 4.7c.3.22.7.22 1 0l6.3-4.7H5.2zm13.3 1.9-5.7 4.25a2.7 2.7 0 0 1-3.2 0L3.9 8.1V18.2c0 .44.36.8.8.8h14.6c.44 0 .8-.36.8-.8V8.1z'
    case 'microsoft':
      // 4-square Windows mark
      return 'M3 3h8.2v8.2H3V3zm9.8 0H21v8.2h-8.2V3zM3 12.8h8.2V21H3v-8.2zm9.8 0H21V21h-8.2v-8.2z'
    case 'qq':
      // Penguin-ish blob
      return 'M12 3c-3.2 0-5.6 2.7-5.6 6.4 0 1.5.4 2.8 1.1 3.9-.9.7-1.5 1.7-1.5 2.8 0 1.3.8 2.3 2 2.8-.3.5-.5 1.1-.5 1.7 0 1.6 1.4 2.4 3.3 2.4 1.1 0 2.1-.3 2.9-.7.2 0 .3 0 .5 0 .8.4 1.8.7 2.9.7 1.9 0 3.3-.8 3.3-2.4 0-.6-.2-1.2-.5-1.7 1.2-.5 2-1.5 2-2.8 0-1.1-.6-2.1-1.5-2.8.7-1.1 1.1-2.4 1.1-3.9C17.6 5.7 15.2 3 12 3zm-3.2 14.2c-.7 0-1.2-.3-1.2-.8s.5-.8 1.2-.8 1.2.3 1.2.8-.5.8-1.2.8zm6.4 0c-.7 0-1.2-.3-1.2-.8s.5-.8 1.2-.8 1.2.3 1.2.8-.5.8-1.2.8zM9.4 9.2c.7 0 1.2.6 1.2 1.3S10.1 12 9.4 12s-1.3-.7-1.3-1.5.6-1.3 1.3-1.3zm5.2 0c.7 0 1.3.6 1.3 1.3S15.3 12 14.6 12s-1.2-.7-1.2-1.5.5-1.3 1.2-1.3z'
    case 'netease':
      // stylized "N" / 163 block
      return 'M5 4h4.2l5.3 9.2V4H19v16h-4.1L9.6 10.7V20H5V4z'
    case 'yahoo':
      return 'M4.2 4h3.4l4.4 8.1L16.4 4H20l-6.2 11.2V20h-3.5v-4.8L4.2 4z'
    case 'icloud':
      return 'M12.5 6.2a4.8 4.8 0 0 1 4.5 3.1A4.2 4.2 0 0 1 20 13.4c0 2.4-1.9 4.3-4.3 4.3H8.1A4.1 4.1 0 0 1 4 13.6c0-1.9 1.3-3.5 3.1-4a4.7 4.7 0 0 1 5.4-3.4z'
    case 'aliyun':
      return 'M4 16.5 8.2 7h3.1l-4.2 9.5H4zm5.4 0L13.6 7h3.1l-4.2 9.5H9.4zm6.2 0L19.8 7H22l-4.2 9.5h-2.4z'
    case 'mailcom':
      return 'M3 6.5A2.5 2.5 0 0 1 5.5 4h13A2.5 2.5 0 0 1 21 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-11zm2 .6 6.5 4.4c.3.2.7.2 1 0L19 7.1V7H5v.1zm14 1.7-5.9 4a2.7 2.7 0 0 1-3.2 0L4 8.8v8.7c0 .4.3.7.7.7h14.6c.4 0 .7-.3.7-.7V8.8z'
    case 'http_api':
      return 'M8 4h8v2H8V4zm-3 4h14v2H5V8zm2 4h10v2H7v-2zm-2 4h14v2H5v-2zm3 4h8v2H8v-2z'
    default:
      return 'M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5v-11zM6.5 6 12 9.8 17.5 6h-11zM18 8.2l-5.5 3.8a1 1 0 0 1-1 0L6 8.2V17.5c0 .3.2.5.5.5h11c.3 0 .5-.2.5-.5V8.2z'
  }
}

/** Accent colors matching console type-chip palette */
export function brandAccent(brand?: string | null): string {
  switch ((brand || 'other').toLowerCase()) {
    case 'gmail':
      return '#c5221f'
    case 'microsoft':
      return '#0b6cbd'
    case 'qq':
      return '#0a8ec0'
    case 'netease':
      return '#c4000f'
    case 'yahoo':
      return '#6b21a8'
    case 'icloud':
      return '#374151'
    case 'aliyun':
    case 'mailcom':
      return '#c2410c'
    case 'http_api':
      return '#047857'
    default:
      return '#475569'
  }
}

export function isKnownBrand(brand?: string | null): brand is MailBrand {
  return [
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
  ].includes(String(brand || ''))
}
