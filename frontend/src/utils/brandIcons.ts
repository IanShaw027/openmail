/**
 * Mail-brand marks for console chips (24×24 viewBox).
 * Multi-path official-color glyphs sized for ~12–16px chips.
 */

import type { MailBrand } from '@/utils/domainBrand'

export type BrandSvgPart = {
  d: string
  /** Absolute fill; if omitted uses currentColor / accent */
  fill?: string
  opacity?: number
}

/** SVG path parts for a brand. Multi-color logos set `fill` per part. */
export function brandSvgParts(brand?: string | null): BrandSvgPart[] {
  switch ((brand || 'other').toLowerCase()) {
    case 'gmail':
      // Simplified Gmail M (red) — readable at 14px
      return [
        { d: 'M3 5.5A2.5 2.5 0 0 1 5.5 3h13A2.5 2.5 0 0 1 21 5.5v13a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 18.5v-13z', fill: '#FFFFFF' },
        // left flap
        { d: 'M3 5.5 12 12 3 18.5V5.5z', fill: '#C5221F' },
        // right flap
        { d: 'M21 5.5 12 12 21 18.5V5.5z', fill: '#C5221F' },
        // center M peak (lighter red)
        { d: 'M3 5.5 12 12 21 5.5 12 11Z', fill: '#EA4335' },
        // bottom body
        { d: 'M3 12.5 12 18.5 21 12.5V18.5A2.5 2.5 0 0 1 18.5 21h-13A2.5 2.5 0 0 1 3 18.5v-6z', fill: '#F6ADA9' },
      ]

    case 'microsoft':
      // Windows four-square
      return [
        { d: 'M3 3h8.2v8.2H3V3z', fill: '#F25022' },
        { d: 'M12.8 3H21v8.2h-8.2V3z', fill: '#7FBA00' },
        { d: 'M3 12.8h8.2V21H3v-8.2z', fill: '#00A4EF' },
        { d: 'M12.8 12.8H21V21h-8.2v-8.2z', fill: '#FFB900' },
      ]

    case 'qq':
      // QQ penguin (blue) — simplified silhouette
      return [
        {
          d: 'M12 2.8c-3.5 0-6 2.7-6 6.4 0 1.5.4 2.8 1.1 3.9-.9.7-1.5 1.7-1.5 2.9 0 1.3.8 2.4 2.1 2.9-.3.5-.5 1.1-.5 1.7 0 1.6 1.4 2.5 3.4 2.5 1 0 1.9-.3 2.6-.7h.6c.7.4 1.6.7 2.6.7 2 0 3.4-.9 3.4-2.5 0-.6-.2-1.2-.5-1.7 1.3-.5 2.1-1.6 2.1-2.9 0-1.2-.6-2.2-1.5-2.9.7-1.1 1.1-2.4 1.1-3.9 0-3.7-2.5-6.4-6-6.4z',
          fill: '#12B7F5',
        },
        { d: 'M9.1 9c.75 0 1.35.65 1.35 1.45S9.85 11.9 9.1 11.9 7.75 11.25 7.75 10.45 8.35 9 9.1 9zm5.8 0c.75 0 1.35.65 1.35 1.45s-.6 1.45-1.35 1.45-1.35-.65-1.35-1.45S14.15 9 14.9 9z', fill: '#111111' },
        { d: 'M9.6 13.1c.45.85 1.4 1.4 2.65 1.4s2.2-.55 2.65-1.4c-.15.15-1.15 1-2.65 1s-2.5-.85-2.65-1z', fill: '#FF6A00' },
        { d: 'M8.5 16.9c-.55 0-1-.35-1-.8s.45-.8 1-.8 1 .35 1 .8-.45.8-1 .8zm7 0c-.55 0-1-.35-1-.8s.45-.8 1-.8 1 .35 1 .8-.45.8-1 .8z', fill: '#FFCE00' },
      ]

    case 'netease':
      // 163 red tile + white wing
      return [
        { d: 'M3.5 3.5h17A1.5 1.5 0 0 1 22 5v14a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 19V5a1.5 1.5 0 0 1 1.5-1.5z', fill: '#E60012' },
        {
          d: 'M6.5 15.8c2.4-4.6 5.6-7.4 10.2-8.8-1.7 2.5-2.4 4.5-2.2 6.4 1.9-.3 3.5 0 4.9.8-2.6 1.5-5.4 2.3-8.4 2.3-1.6 0-3.2-.2-4.5-.7z',
          fill: '#FFFFFF',
        },
      ]

    case 'yahoo':
      return [{ d: 'M3.8 3.5h3.8L12 11.4l4.4-7.9h3.8l-6.4 11.3V20.5h-3.2v-5.7L3.8 3.5z', fill: '#6001D2' }]

    case 'icloud':
      return [
        {
          d: 'M8.1 18.2h8.7c2.25 0 4.1-1.75 4.1-3.95 0-1.85-1.25-3.4-3-3.9A4.55 4.55 0 0 0 8.9 9.2 3.85 3.85 0 0 0 4 13.5c0 2 1.7 3.7 4.1 3.7z',
          fill: '#5AC8FA',
        },
        {
          d: 'M8.1 18.2h8.7c1.35 0 2.55-.65 3.25-1.65-.55 1.95-2.4 3.35-4.6 3.35H9.4c-1.95 0-3.6-1.05-4.35-2.6.7.55 1.65.9 2.85.9h.2z',
          fill: '#0A84FF',
          opacity: 0.45,
        },
      ]

    case 'aliyun':
      return [
        { d: 'M12 3 3.2 18.8h3.5L12 8.5l5.3 10.3h3.5L12 3z', fill: '#FF6A00' },
        { d: 'M8 14.6h8l-1.4 2.8H9.4L8 14.6z', fill: '#FFB380' },
      ]

    case 'mailcom':
      // Blue rounded m / mail tile
      return [
        { d: 'M3.2 5.5A2.3 2.3 0 0 1 5.5 3.2h13A2.3 2.3 0 0 1 20.8 5.5v13a2.3 2.3 0 0 1-2.3 2.3h-13a2.3 2.3 0 0 1-2.3-2.3v-13z', fill: '#1A73E8' },
        {
          d: 'M6.2 8.2c0-.6.5-1.1 1.1-1.1h.2c.5 0 .9.3 1.1.7L12 13.2l3.4-5.4c.2-.4.6-.7 1.1-.7h.2c.6 0 1.1.5 1.1 1.1V16h-2V10.6L12.6 15c-.15.25-.4.4-.7.4s-.55-.15-.7-.4L8.2 10.6V16h-2V8.2z',
          fill: '#FFFFFF',
        },
      ]

    case 'gmx':
      return [
        { d: 'M3.5 4h17A1.5 1.5 0 0 1 22 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 18.5v-13A1.5 1.5 0 0 1 3.5 4z', fill: '#1C449B' },
        {
          d: 'M12 7c-2.8 0-4.8 2-4.8 5s2 5 4.8 5c1.6 0 2.9-.55 3.8-1.5l-1.25-1.25c-.6.6-1.45 1-2.55 1-1.75 0-3-1.25-3-3.25S10.25 8.75 12 8.75c1.1 0 1.95.4 2.55 1L15.8 8.5C14.9 7.55 13.6 7 12 7zm1.5 3.75v1.7h3.3v-1.7h-3.3z',
          fill: '#FFFFFF',
        },
      ]

    case 'proton':
      return [
        { d: 'M12 2.2 4.2 5.8v6.5c0 4.7 3.3 8.7 7.8 10 4.5-1.3 7.8-5.3 7.8-10V5.8L12 2.2z', fill: '#6D4AFF' },
        {
          d: 'M12 7c-1.75 0-3.1 1.25-3.1 3.1v1.2H8v5.4h8v-5.4h-.9V10.1C15.1 8.25 13.75 7 12 7zm0 1.5c.95 0 1.6.65 1.6 1.6v1.2h-3.2V10.1c0-.95.65-1.6 1.6-1.6z',
          fill: '#FFFFFF',
        },
      ]

    case 'zoho':
      return [
        { d: 'M3.5 4h17A1.5 1.5 0 0 1 22 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 18.5v-13A1.5 1.5 0 0 1 3.5 4z', fill: '#E42527' },
        { d: 'M6.8 8h10.4v2.2L9.6 15.2h7.6V17H6.8v-2.2l7.6-5H6.8V8z', fill: '#FFFFFF' },
      ]

    case 'cf_temp':
      // Cloudflare orange cloud
      return [
        {
          d: 'M8.2 17.5h8.6c2.1 0 3.9-1.65 3.9-3.7 0-1.75-1.2-3.25-2.9-3.65A4.35 4.35 0 0 0 9.3 8.9 3.55 3.55 0 0 0 4.2 12.4c0 2.15 1.75 3.9 4 3.9v.2z',
          fill: '#F6821F',
        },
        {
          d: 'M10.4 11c.35-1.35 1.5-2.3 2.95-2.3 1.15 0 2.15.55 2.7 1.4l1.35-.85A4.25 4.25 0 0 0 13.35 7.4c-2.25 0-4.15 1.55-4.55 3.7l1.6.05v-.15z',
          fill: '#FBAD41',
        },
      ]

    case 'duckmail':
      return [
        {
          d: 'M13.2 5.2c-3.3 0-5.7 2.5-5.7 5.8 0 1.55.55 2.9 1.45 3.9L6.5 19.5h3.3l1.45-2.7c.6.2 1.25.35 1.95.35 3.3 0 5.7-2.55 5.7-5.85S16.5 5.2 13.2 5.2z',
          fill: '#DE5833',
        },
        { d: 'M10.9 10a1.25 1.25 0 1 0 .05 2.5 1.25 1.25 0 0 0-.05-2.5z', fill: '#1A1A1A' },
        {
          d: 'M15.7 11.1c1.35.15 2.7.7 3.65 1.5-.95 1.05-2.4 1.7-3.9 1.7h-.25c.4-.55.65-1.2.65-1.9 0-.4-.05-.8-.15-1.3z',
          fill: '#F0C14A',
        },
      ]

    case 'http_api':
      return [
        {
          d: 'M8.5 4.5 4.8 8.2v7.6l3.7 3.7h1.9l-4.1-4.1V9.6l4.1-4.1H8.5zm7 0h-1.9l4.1 4.1v6.1l-4.1 4.1h1.9l3.7-3.7V8.2L15.5 4.5z',
          fill: '#0F766E',
        },
        { d: 'M10.4 11.2h3.2v1.6h-3.2v-1.6z', fill: '#2DD4BF' },
      ]

    default:
      return [
        {
          d: 'M3.5 6A2.5 2.5 0 0 1 6 3.5h12A2.5 2.5 0 0 1 20.5 6v12a2.5 2.5 0 0 1-2.5 2.5H6A2.5 2.5 0 0 1 3.5 18V6z',
          fill: '#64748B',
        },
        { d: 'M4.2 6.3 12 11.8l7.8-5.5V6L12 11 4.2 6v.3z', fill: '#E2E8F0' },
      ]
  }
}

/** @deprecated single-path monochrome — prefer brandSvgParts */
export function brandSvgPath(brand?: string | null): string {
  const parts = brandSvgParts(brand)
  return parts[0]?.d || ''
}

export function brandAccent(brand?: string | null): string {
  switch ((brand || 'other').toLowerCase()) {
    case 'gmail':
      return '#EA4335'
    case 'microsoft':
      return '#00A4EF'
    case 'qq':
      return '#12B7F5'
    case 'netease':
      return '#E60012'
    case 'yahoo':
      return '#6001D2'
    case 'icloud':
      return '#0A84FF'
    case 'aliyun':
      return '#FF6A00'
    case 'mailcom':
      return '#1A73E8'
    case 'gmx':
      return '#1C449B'
    case 'proton':
      return '#6D4AFF'
    case 'zoho':
      return '#E42527'
    case 'cf_temp':
      return '#F6821F'
    case 'duckmail':
      return '#DE5833'
    case 'http_api':
      return '#0F766E'
    default:
      return '#64748B'
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
    'gmx',
    'proton',
    'zoho',
    'cf_temp',
    'duckmail',
    'http_api',
    'other',
  ].includes(String(brand || ''))
}
