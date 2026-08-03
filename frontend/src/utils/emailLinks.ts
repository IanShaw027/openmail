/**
 * Unwrap tracking / redirect URLs in email HTML and confirm before navigating.
 *
 * Marketing mail (OpenAI, etc.) often embeds:
 *   href="/?redirectUrl=https%3A%2F%2Furl3243.email.openai.com%2F..."
 * which SPA-routes to mail.clomio.ai/?redirectUrl=... and never leaves the app.
 *
 * Also handles unencoded nested queries:
 *   /?redirectUrl=http://tracker.example/ls/click?upn=...
 * where the inner `?` / `&` would otherwise break naive parsing.
 */

const REDIRECT_PARAM_KEYS = [
  'redirectUrl',
  'redirecturl',
  'redirect_url',
  'redirect',
  'url',
  'u',
  'target',
  'targeturl',
  'target_url',
  'dest',
  'destination',
  'goto',
  'link',
  'r',
  'continue',
  'return',
  'returnurl',
  'return_url',
  'next',
  'to',
]

function looksLikeHttpUrl(s: string): boolean {
  const t = (s || '').trim()
  return /^https?:\/\//i.test(t)
}

/** Decode URI component up to 3 times (double-encoded trackers). */
export function deepDecodeURIComponent(raw: string): string {
  let decoded = raw
  for (let i = 0; i < 3; i++) {
    try {
      const next = decodeURIComponent(decoded)
      if (next === decoded) break
      decoded = next
    } catch {
      break
    }
  }
  return decoded
}

/**
 * Extract redirect target from a query string, including unencoded nested URLs.
 * When the value starts with http(s), take the remainder of the query (trackers
 * often leave `?upn=…&…` unencoded so URLSearchParams would split on `&`).
 */
function extractRedirectFromSearch(search: string): string | undefined {
  const s = search.startsWith('?') ? search.slice(1) : search
  if (!s) return undefined

  for (const key of REDIRECT_PARAM_KEYS) {
    const re = new RegExp(`(?:^|&)(${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})=`, 'i')
    const m = re.exec(s)
    if (!m) continue
    const start = m.index + m[0].length
    let val = s.slice(start)
    if (!val) continue

    const probe = deepDecodeURIComponent(val.slice(0, Math.min(val.length, 32)))
    // Unencoded absolute URL: rest of query is part of the target
    if (/^https?:\/\//i.test(val) || /^https?:\/\//i.test(probe) || /^https?%3A/i.test(val)) {
      return deepDecodeURIComponent(val).trim()
    }

    // Normal form-encoded value: stop at next &
    const amp = val.indexOf('&')
    if (amp >= 0) val = val.slice(0, amp)
    const decoded = deepDecodeURIComponent(val).trim()
    if (decoded) return decoded
  }
  return undefined
}

function searchHasRedirectKey(search: string): boolean {
  const s = search.startsWith('?') ? search.slice(1) : search
  if (!s) return false
  for (const key of REDIRECT_PARAM_KEYS) {
    const re = new RegExp(`(?:^|&)${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}=`, 'i')
    if (re.test(s)) return true
  }
  return false
}

/**
 * Extract the real destination from a tracking/wrapper URL.
 * Returns absolute http(s) URL or null if nothing safe/usable found.
 */
export function unwrapEmailHref(raw: string, baseHref?: string): string | null {
  if (!raw || typeof raw !== 'string') return null
  let href = raw.trim()
  if (!href || href.startsWith('#') || href.toLowerCase().startsWith('mailto:')) {
    return null
  }
  if (href.toLowerCase().startsWith('javascript:')) return null

  let absolute: string
  try {
    absolute = new URL(
      href,
      baseHref || (typeof location !== 'undefined' ? location.href : 'https://local.invalid/'),
    ).href
  } catch {
    return looksLikeHttpUrl(href) ? href : null
  }

  // Walk redirect params (max depth 4 to unwrap nested trackers)
  let current = absolute
  for (let depth = 0; depth < 4; depth++) {
    let u: URL
    try {
      u = new URL(current)
    } catch {
      break
    }

    let found = extractRedirectFromSearch(u.search)

    // Fallback: URLSearchParams (encoded values)
    if (!found) {
      for (const key of REDIRECT_PARAM_KEYS) {
        for (const [k, v] of u.searchParams.entries()) {
          if (k.toLowerCase() !== key.toLowerCase()) continue
          const decoded = deepDecodeURIComponent(v).trim()
          if (looksLikeHttpUrl(decoded)) {
            found = decoded
            break
          }
          if (decoded.startsWith('/') || decoded.startsWith('./')) {
            try {
              found = new URL(decoded, u.origin).href
              break
            } catch {
              /* ignore */
            }
          }
        }
        if (found) break
      }
    }

    if (!found) break

    if (looksLikeHttpUrl(found)) {
      current = found.trim()
      continue
    }
    if (found.startsWith('/') || found.startsWith('./')) {
      try {
        current = new URL(found, u.origin).href
        continue
      } catch {
        break
      }
    }
    break
  }

  if (!looksLikeHttpUrl(current)) return null
  try {
    const final = new URL(current)
    if (final.protocol !== 'http:' && final.protocol !== 'https:') return null
    return final.href
  } catch {
    return null
  }
}

/**
 * Resolve a clickable email href to the best absolute destination for navigation.
 * Relative paths that are not unwrap-able are rejected (avoid SPA self-navigation).
 */
export function resolveEmailLinkTarget(raw: string, baseHref?: string): string | null {
  const unwrapped = unwrapEmailHref(raw, baseHref)
  if (unwrapped) {
    // Prefer unwrapped external target when the raw href is a same-origin redirect shell
    try {
      const base =
        baseHref || (typeof location !== 'undefined' ? location.href : 'https://local.invalid/')
      const abs = new URL(raw.trim(), base)
      if (typeof location !== 'undefined' && abs.origin === location.origin) {
        const dest = new URL(unwrapped)
        if (dest.origin !== location.origin) return unwrapped
      }
    } catch {
      /* fall through */
    }
    return unwrapped
  }
  // Direct absolute http(s) without redirect wrapper
  const t = (raw || '').trim()
  if (looksLikeHttpUrl(t)) {
    try {
      const u = new URL(t)
      if (u.protocol === 'http:' || u.protocol === 'https:') return u.href
    } catch {
      return null
    }
  }
  // Absolute URL built from relative root that is already http(s) on another host
  try {
    const abs = new URL(
      t,
      baseHref || (typeof location !== 'undefined' ? location.href : 'https://local.invalid/'),
    )
    if (abs.protocol !== 'http:' && abs.protocol !== 'https:') return null
    // Same-origin relative paths (/, ./foo) without unwrap → don't treat as external
    if (typeof location !== 'undefined' && abs.origin === location.origin) {
      if (!looksLikeHttpUrl(t) && !t.startsWith('//')) return null
    }
    return abs.href
  } catch {
    return null
  }
}

/**
 * If the current page URL is a redirect shell (e.g. /?redirectUrl=https://…),
 * return the external destination. Used on app boot when a relative email link
 * already navigated into the SPA.
 */
export function peekLandingRedirect(pageHref?: string): string | null {
  if (typeof location === 'undefined' && !pageHref) return null
  const href = pageHref || location.href
  let u: URL
  try {
    u = new URL(href)
  } catch {
    return null
  }
  if (!searchHasRedirectKey(u.search)) return null

  const dest = unwrapEmailHref(href)
  if (!dest) return null

  try {
    const d = new URL(dest)
    // Only offer external navigation (avoid loops on same-origin)
    if (d.origin === u.origin) return null
    if (d.protocol !== 'http:' && d.protocol !== 'https:') return null
    return d.href
  } catch {
    return null
  }
}

/** Short host + path for confirm dialogs. */
export function formatLinkPreview(url: string): string {
  try {
    const u = new URL(url)
    let shown =
      u.host + (u.pathname.length > 48 ? u.pathname.slice(0, 48) + '…' : u.pathname)
    if (u.search) shown += '?…'
    return shown
  } catch {
    return url.length > 80 ? url.slice(0, 80) + '…' : url
  }
}

/**
 * On SPA load: if URL is /?redirectUrl=… (or similar), confirm and leave the app.
 * Strips the query first so cancel/refresh does not re-prompt forever.
 * Returns true if a redirect was offered (confirmed or cancelled).
 */
export function handleLandingRedirectConfirm(confirmNavigate: (url: string) => boolean): boolean {
  if (typeof location === 'undefined' || typeof history === 'undefined') return false
  const dest = peekLandingRedirect()
  if (!dest) return false

  // Clean address bar before confirm so Back/refresh stays in the app
  try {
    const clean = location.pathname + (location.hash || '')
    history.replaceState(null, '', clean || '/')
  } catch {
    /* ignore */
  }

  if (!confirmNavigate(dest)) return true
  // Same-tab navigation: user already "opened" this URL intending to follow the link
  window.location.assign(dest)
  return true
}

export type EmailLinkClickOptions = {
  /** Confirm message factory; return false to cancel */
  confirmNavigate: (url: string) => boolean
}

/**
 * Click handler for containers with sanitized email HTML.
 * Intercepts <a> clicks, unwraps trackers, confirms, then opens in a new tab.
 */
export function onEmailHtmlClick(ev: MouseEvent, opts: EmailLinkClickOptions): void {
  const target = ev.target
  if (!(target instanceof Element)) return
  const a = target.closest('a')
  if (!a) return
  const href = a.getAttribute('href')
  if (!href) return
  const low = href.trim().toLowerCase()
  if (low.startsWith('mailto:') || low.startsWith('#') || low.startsWith('tel:')) {
    return // let browser handle
  }

  ev.preventDefault()
  ev.stopPropagation()

  const dest = resolveEmailLinkTarget(
    href,
    typeof location !== 'undefined' ? location.href : undefined,
  )
  if (!dest) {
    // Nothing safe to open
    return
  }
  if (!opts.confirmNavigate(dest)) return
  window.open(dest, '_blank', 'noopener,noreferrer')
}
