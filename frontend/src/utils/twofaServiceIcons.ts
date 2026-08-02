/**
 * 2FA service / issuer marks (24×24). Multi-path, brand colors for card avatars.
 */

export type ServiceSvgPart = {
  d: string
  fill?: string
  opacity?: number
}

/** Normalize logo id / issuer string → preset id */
export function normalizeServiceLogoId(raw?: string | null): string {
  const s = String(raw || '')
    .trim()
    .toLowerCase()
  if (!s || s === 'other') return 'other'
  if (s === 'google' || s.includes('google') || s === 'gmail') return 'google'
  if (s === 'microsoft' || s.includes('microsoft') || s.includes('outlook') || s === 'azure')
    return 'microsoft'
  if (s === 'github' || s.includes('github')) return 'github'
  if (s === 'apple' || s.includes('apple') || s === 'icloud') return 'apple'
  if (s === 'amazon' || s.includes('amazon') || s === 'aws') return 'amazon'
  if (s === 'discord' || s.includes('discord')) return 'discord'
  if (s === 'twitter' || s === 'x' || s.includes('twitter') || s === 'x / twitter') return 'twitter'
  if (s === 'facebook' || s.includes('facebook') || s === 'meta') return 'facebook'
  if (s === 'dropbox' || s.includes('dropbox')) return 'dropbox'
  if (s === 'steam' || s.includes('steam')) return 'steam'
  if (s === 'binance' || s.includes('binance')) return 'binance'
  // also match preset ids exactly
  const known = [
    'google',
    'microsoft',
    'github',
    'apple',
    'amazon',
    'discord',
    'twitter',
    'facebook',
    'dropbox',
    'steam',
    'binance',
  ]
  if (known.includes(s)) return s
  return 'other'
}

export function serviceSvgParts(logoOrIssuer?: string | null): ServiceSvgPart[] {
  const id = normalizeServiceLogoId(logoOrIssuer)
  switch (id) {
    case 'google':
      // Google "G"
      return [
        {
          d: 'M21.6 12.23c0-.74-.06-1.45-.18-2.13H12v4.03h5.38a4.6 4.6 0 0 1-2 3.02v2.5h3.24c1.9-1.75 2.98-4.33 2.98-7.42z',
          fill: '#4285F4',
        },
        {
          d: 'M12 22c2.7 0 4.96-.9 6.62-2.43l-3.24-2.5c-.9.6-2.05.96-3.38.96-2.6 0-4.8-1.76-5.59-4.12H3.07v2.59A9.99 9.99 0 0 0 12 22z',
          fill: '#34A853',
        },
        {
          d: 'M6.41 13.91A6 6 0 0 1 6.1 12c0-.66.11-1.3.3-1.91V7.5H3.07A10 10 0 0 0 2 12c0 1.61.39 3.14 1.07 4.5l3.34-2.59z',
          fill: '#FBBC05',
        },
        {
          d: 'M12 5.98c1.47 0 2.79.5 3.83 1.5l2.87-2.87C16.95 2.99 14.7 2 12 2A9.99 9.99 0 0 0 3.07 7.5l3.34 2.59C7.2 7.74 9.4 5.98 12 5.98z',
          fill: '#EA4335',
        },
      ]

    case 'microsoft':
      return [
        { d: 'M3 3h8.2v8.2H3V3z', fill: '#F25022' },
        { d: 'M12.8 3H21v8.2h-8.2V3z', fill: '#7FBA00' },
        { d: 'M3 12.8h8.2V21H3v-8.2z', fill: '#00A4EF' },
        { d: 'M12.8 12.8H21V21h-8.2v-8.2z', fill: '#FFB900' },
      ]

    case 'github':
      // Octocat mark (simplified)
      return [
        {
          d: 'M12 2C6.48 2 2 6.58 2 12.26c0 4.52 2.87 8.35 6.84 9.7.5.1.68-.22.68-.49 0-.24-.01-.87-.01-1.71-2.78.62-3.37-1.37-3.37-1.37-.45-1.18-1.11-1.5-1.11-1.5-.91-.64.07-.63.07-.63 1 .07 1.53 1.06 1.53 1.06.89 1.56 2.34 1.11 2.91.85.09-.66.35-1.11.63-1.37-2.22-.26-4.55-1.14-4.55-5.07 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.32.1-2.75 0 0 .84-.27 2.75 1.05A9.3 9.3 0 0 1 12 6.84c.85 0 1.71.12 2.51.34 1.91-1.32 2.75-1.05 2.75-1.05.55 1.43.2 2.49.1 2.75.64.72 1.03 1.63 1.03 2.75 0 3.94-2.34 4.8-4.57 5.06.36.32.68.95.68 1.92 0 1.38-.01 2.5-.01 2.84 0 .27.18.59.69.49A10.03 10.03 0 0 0 22 12.26C22 6.58 17.52 2 12 2z',
          fill: '#24292F',
        },
      ]

    case 'apple':
      return [
        {
          d: 'M16.7 12.7c0-2.3 1.9-3.4 2-3.5-1.1-1.6-2.8-1.8-3.4-1.8-1.4-.2-2.8.9-3.5.9-.7 0-1.9-.8-3.1-.8-1.6 0-3.1 1-3.9 2.4-1.7 2.9-.4 7.2 1.2 9.6.8 1.1 1.7 2.4 3 2.3 1.2 0 1.6-.8 3.1-.8s1.8.8 3.1.8c1.3 0 2.1-1.1 2.9-2.2.9-1.3 1.3-2.5 1.3-2.6-.1 0-2.5-1-2.7-3.9zM14.4 5.9c.6-.8 1.1-1.9 1-3-.9 0-2 .6-2.7 1.4-.6.7-1.1 1.8-1 2.9 1 .1 2-.5 2.7-1.3z',
          fill: '#111111',
        },
      ]

    case 'amazon':
      // Smile + a
      return [
        {
          d: 'M14.2 12.6c0 1.1.4 1.9 1.9 1.9.7 0 1.3-.2 1.8-.5v-1.5c-.4.2-.8.3-1.2.3-.5 0-.7-.2-.7-.6V8.6h2.1V7.1h-2.1V5.2l-2 1.2v.7h1.2v5.5zm-4.3.1c0 .6.3.9.9.9.5 0 .9-.2 1.3-.6l.1.5h1.7c-.1-.3-.2-.7-.2-1.3V8.6H12v3.5c-.2.3-.5.5-.9.5-.3 0-.4-.2-.4-.5V8.6H9.1v4.1h.8zM7.3 13.8c.7 0 1.3-.2 1.8-.5v-1.4c-.4.3-.9.4-1.4.4-1 0-1.4-.6-1.4-1.3 0-1 .7-1.5 2.1-1.5.3 0 .6 0 .9.1V9.2c-.2-.1-.5-.1-.8-.1-1.2 0-2 .5-2 1.5 0 .9.6 1.5 1.8 1.5.3 0 .6 0 .9-.1v.5c0 .3-.3.5-.7.5-.4 0-.7-.1-1-.3v1.1c.4.2.8.3 1.2.3z',
          fill: '#232F3E',
        },
        {
          d: 'M16.2 15.6c-2.1 1.6-5.1 2.4-7.7 2.4-3.6 0-6.9-1.3-9.4-3.5-.2-.2 0-.4.2-.3 2.7 1.6 6.1 2.5 9.6 2.5 2.3 0 4.9-.5 7.3-1.5.3-.1.6.2.4.4h-.4z',
          fill: '#FF9900',
        },
        {
          d: 'M17.2 14.7c-.3-.3-1.7-.1-2.3 0-.2 0-.2-.1-.1-.3.6-1.4 1.7-1.3 1.9-1.3.2 0 1.3.1 1.9 1 .1.1 0 .2-.1.3-.1.1-.2.2-.3.3z',
          fill: '#FF9900',
        },
      ]

    case 'discord':
      // Discord Clyde simplified
      return [
        {
          d: 'M19.3 5.1A16.3 16.3 0 0 0 15.2 4l-.3.6a14.7 14.7 0 0 1 3.5 1.4 13.5 13.5 0 0 0-12.8 0A14 14 0 0 1 9 4.6L8.7 4a16 16 0 0 0-4.1 1.1C2.2 8.5 1.5 11.8 1.7 15.1a16.4 16.4 0 0 0 5 2.5l.7-1.1a10.7 10.7 0 0 1-1.6-.8l.4-.3c3.1 1.4 6.4 1.4 9.4 0l.4.3c-.5.3-1 .6-1.6.8l.7 1.1a16.3 16.3 0 0 0 5-2.5c.3-3.7-.5-7-2.2-10zM9.2 13.5c-.9 0-1.7-.9-1.7-1.9s.7-1.9 1.7-1.9 1.7.9 1.7 1.9-.7 1.9-1.7 1.9zm5.6 0c-.9 0-1.7-.9-1.7-1.9s.7-1.9 1.7-1.9 1.7.9 1.7 1.9-.8 1.9-1.7 1.9z',
          fill: '#5865F2',
        },
      ]

    case 'twitter':
      // X logo
      return [
        {
          d: 'M16.6 3.5h2.9l-6.3 7.2 7.4 9.8h-5.8l-4.5-5.9-5.2 5.9H2.2l6.8-7.7L2 3.5h5.9l4.1 5.4 4.6-5.4zm-1 15.3h1.6L8.5 5.1H6.8l8.8 13.7z',
          fill: '#0F1419',
        },
      ]

    case 'facebook':
      return [
        { d: 'M12 2C6.5 2 2 6.5 2 12c0 4.8 3.4 8.8 7.9 9.8v-6.9H7.6V12h2.3V9.8c0-2.3 1.4-3.6 3.5-3.6 1 0 2 .2 2 .2v2.2h-1.1c-1.1 0-1.5.7-1.5 1.4V12h2.5l-.4 2.9h-2.1v6.9C18.6 20.8 22 16.8 22 12c0-5.5-4.5-10-10-10z', fill: '#1877F2' },
      ]

    case 'dropbox':
      return [
        { d: 'M6.5 3.5 2 6.8l4.5 3.3L11 6.8 6.5 3.5zm11 0L13 6.8l4.5 3.3L22 6.8l-4.5-3.3zM2 13.4l4.5 3.3L11 13.4 6.5 10.1 2 13.4zm15.5 3.3 4.5-3.3-4.5-3.3L13 13.4l4.5 3.3zM6.5 17.5 11 20.8l4.5-3.3L11 14.2l-4.5 3.3z', fill: '#0061FF' },
      ]

    case 'steam':
      return [
        {
          d: 'M12 2a10 10 0 0 0-1.5 19.9l3.3-1.4a3.4 3.4 0 0 0 6.4-1.5 3.4 3.4 0 0 0-3.3-3.5l-3.7-2.7V12a3.5 3.5 0 1 0-3.5 3.5h.1l2.6 1.9a3.4 3.4 0 0 0 .1.6 3.4 3.4 0 0 0 3.4 3.4 3.4 3.4 0 0 0 3.3-2.7l2.8 1.2A10 10 0 0 0 12 2zm-3.5 14.1a1.5 1.5 0 0 1-1.4-2l2 .8a1.5 1.5 0 0 1-1.5 1.2zm7.2.9a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm0-1.1a.9.9 0 1 0 0-1.8.9.9 0 0 0 0 1.8zM8.5 9.5a2 2 0 1 1 4 0 2 2 0 0 1-4 0zm1.1 0a.9.9 0 1 0 1.8 0 .9.9 0 0 0-1.8 0z',
          fill: '#1B2838',
        },
        {
          d: 'M15.7 14.1a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8zm0 .7a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4zM10.5 7.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm0 .8a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4z',
          fill: '#66C0F4',
        },
      ]

    case 'binance':
      // Binance diamond BNB-like
      return [
        { d: 'M12 3.2 8.6 6.6l1.4 1.4L12 6l1.9 2 1.5-1.4L12 3.2zM6.6 8.6 3.2 12l3.4 3.4 1.4-1.4L6 12l1.9-1.9-1.3-1.5zm10.8 0-1.4 1.4L18 12l-1.9 1.9 1.4 1.4L20.8 12l-3.4-3.4zM12 9.1 9.1 12 12 14.9 14.9 12 12 9.1zM12 15.2l-1.9 1.9-1.4-1.4L3.2 20.8h17.6l-5.5-5.1-1.4 1.4L12 15.2z', fill: '#F0B90B' },
      ]

    default:
      // Key / shield
      return [
        {
          d: 'M12 2.5 4.5 6v5.8c0 4.5 3.1 8.3 7.5 9.7 4.4-1.4 7.5-5.2 7.5-9.7V6L12 2.5zm0 3.2 5.2 2.4v4.2c0 3.3-2.2 6.1-5.2 7.1-3-1-5.2-3.8-5.2-7.1V8.1L12 5.7z',
          fill: '#6366F1',
        },
        {
          d: 'M11 10.5a1.5 1.5 0 1 1 1.5 1.5H11v2h2.5a3.5 3.5 0 1 0-3.5-3.5v.5H11v-.5z',
          fill: '#FFFFFF',
        },
      ]
  }
}

export function serviceAccent(logoOrIssuer?: string | null): string {
  const id = normalizeServiceLogoId(logoOrIssuer)
  switch (id) {
    case 'google':
      return '#4285F4'
    case 'microsoft':
      return '#00A4EF'
    case 'github':
      return '#24292F'
    case 'apple':
      return '#111111'
    case 'amazon':
      return '#FF9900'
    case 'discord':
      return '#5865F2'
    case 'twitter':
      return '#0F1419'
    case 'facebook':
      return '#1877F2'
    case 'dropbox':
      return '#0061FF'
    case 'steam':
      return '#1B2838'
    case 'binance':
      return '#F0B90B'
    default:
      return '#6366F1'
  }
}
