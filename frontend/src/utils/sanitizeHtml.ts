/**
 * Sanitize email HTML for safe v-html rendering.
 * Uses DOMParser + allowlist (not regex-only).
 */

import { unwrapEmailHref } from '@/utils/emailLinks'

const ALLOWED_TAGS = new Set([
  'a',
  'abbr',
  'article',
  'b',
  'blockquote',
  'br',
  'caption',
  'center',
  'code',
  'col',
  'colgroup',
  'div',
  'em',
  'font',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'img',
  'li',
  'ol',
  'p',
  'pre',
  'section',
  'small',
  'span',
  'strong',
  'sub',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'u',
  'ul',
])

const URL_ATTRS = new Set(['href', 'src'])

const ALLOWED_ATTRS = new Set([
  'href',
  'src',
  'alt',
  'title',
  'width',
  'height',
  'colspan',
  'rowspan',
  'align',
  'valign',
  'border',
  'bgcolor',
  'color',
  'face',
  'size',
  'cellpadding',
  'cellspacing',
  'class',
  'id',
  'target',
  'rel',
  'style', // further filtered
  'role',
  'dir',
])

/** Allowlisted CSS properties for email inline styles. */
const ALLOWED_STYLE_PROPS = new Set([
  'color',
  'background-color',
  'font-size',
  'font-weight',
  'font-style',
  'font-family',
  'text-align',
  'text-decoration',
  'margin',
  'margin-top',
  'margin-right',
  'margin-bottom',
  'margin-left',
  'padding',
  'padding-top',
  'padding-right',
  'padding-bottom',
  'padding-left',
  'border',
  'border-top',
  'border-right',
  'border-bottom',
  'border-left',
  'border-color',
  'border-width',
  'border-style',
  'border-radius',
  'width',
  'height',
  'max-width',
  'max-height',
  'min-width',
  'min-height',
  'line-height',
  'display',
  'vertical-align',
  'white-space',
  'word-break',
  'overflow-wrap',
  'letter-spacing',
  'text-indent',
  'opacity',
])

const ALLOWED_DISPLAY = new Set(['block', 'inline', 'inline-block'])

/**
 * Strip dangerous CSS from style attribute.
 * Allowlist properties; block url(), expression, position, and script schemes.
 */
function sanitizeStyle(style: string): string {
  if (!style || typeof style !== 'string') return ''

  // Collapse CSS escape sequences that hide keywords (e.g. expr\ession, \75rl)
  let s = style.replace(/\\[0-9a-fA-F]{1,6}\s?/g, '').replace(/\\./g, '')

  // Strip high-risk tokens; remaining declarations go through the property allowlist
  s = s.replace(/expression\s*\([^)]*\)?/gi, '')
  s = s.replace(/url\s*\([^)]*\)?/gi, '')
  s = s.replace(/@import[^;]*/gi, '')
  s = s.replace(/-moz-binding\s*:[^;]*/gi, '')
  s = s.replace(/behavior\s*:[^;]*/gi, '')
  s = s.replace(/javascript\s*:/gi, '')
  s = s.replace(/vbscript\s*:/gi, '')
  s = s.replace(/data\s*:/gi, '')
  // Drop positioning (overlay / clickjack risk in email HTML)
  s = s.replace(/position\s*:[^;]*/gi, '')

  const kept: string[] = []
  for (const part of s.split(';')) {
    const decl = part.trim()
    if (!decl) continue
    const colon = decl.indexOf(':')
    if (colon <= 0) continue
    const prop = decl.slice(0, colon).trim().toLowerCase()
    let val = decl.slice(colon + 1).trim()
    if (!prop || !val) continue
    if (!ALLOWED_STYLE_PROPS.has(prop)) continue
    // Re-check value for smuggled vectors
    const valLower = val.toLowerCase()
    if (
      valLower.includes('expression') ||
      valLower.includes('url(') ||
      valLower.includes('javascript:') ||
      valLower.includes('vbscript:') ||
      valLower.includes('data:') ||
      valLower.includes('@import') ||
      valLower.includes('behavior') ||
      valLower.includes('-moz-binding')
    ) {
      continue
    }
    if (prop === 'display') {
      const d = valLower.replace(/\s+/g, ' ').trim()
      if (!ALLOWED_DISPLAY.has(d)) continue
      val = d
    }
    kept.push(`${prop}: ${val}`)
  }
  return kept.join('; ')
}

function isSafeUrl(raw: string): boolean {
  const v = raw.trim()
  if (!v) return false
  // Decode common entity tricks once
  const decoded = v
    .replace(/&#x([0-9a-f]+);?/gi, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&#(\d+);?/g, (_, d) => String.fromCharCode(parseInt(d, 10)))
    .replace(/&colon;/gi, ':')
    .replace(/&tab;/gi, '')
    .replace(/&newline;/gi, '')
    .replace(/[\u0000-\u001f\u007f]/g, '')
  const lower = decoded.trim().toLowerCase()
  if (lower.startsWith('javascript:')) return false
  if (lower.startsWith('vbscript:')) return false
  if (lower.startsWith('data:text/html')) return false
  if (lower.startsWith('data:image/svg')) return false
  // allow http(s), mailto, cid, data:image (raster), relative
  if (
    lower.startsWith('http:') ||
    lower.startsWith('https:') ||
    lower.startsWith('mailto:') ||
    lower.startsWith('cid:') ||
    lower.startsWith('#') ||
    lower.startsWith('/') ||
    lower.startsWith('./') ||
    lower.startsWith('../') ||
    lower.startsWith('data:image/png') ||
    lower.startsWith('data:image/jpeg') ||
    lower.startsWith('data:image/gif') ||
    lower.startsWith('data:image/webp')
  ) {
    return true
  }
  // relative path without scheme
  if (!/^[a-z][a-z0-9+.-]*:/i.test(decoded.trim())) return true
  return false
}

function walk(node: Node, out: DocumentFragment, doc: Document) {
  if (node.nodeType === Node.TEXT_NODE) {
    out.appendChild(doc.createTextNode(node.textContent || ''))
    return
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return
  const el = node as Element
  const tag = el.tagName.toLowerCase()

  // Drop script/style/svg/math/iframe etc. entirely (including children)
  if (
    tag === 'script' ||
    tag === 'style' ||
    tag === 'iframe' ||
    tag === 'object' ||
    tag === 'embed' ||
    tag === 'link' ||
    tag === 'meta' ||
    tag === 'base' ||
    tag === 'form' ||
    tag === 'input' ||
    tag === 'button' ||
    tag === 'textarea' ||
    tag === 'select' ||
    tag === 'svg' ||
    tag === 'math' ||
    tag === 'template' ||
    tag === 'noscript'
  ) {
    return
  }

  if (!ALLOWED_TAGS.has(tag)) {
    // unwrap: keep children
    for (const child of Array.from(el.childNodes)) {
      walk(child, out, doc)
    }
    return
  }

  const neo = doc.createElement(tag)
  for (const attr of Array.from(el.attributes)) {
    const name = attr.name.toLowerCase()
    if (name.startsWith('on')) continue
    if (!ALLOWED_ATTRS.has(name)) continue
    let val = attr.value
    if (URL_ATTRS.has(name)) {
      if (!isSafeUrl(val)) continue
      if (name === 'href' && tag === 'a') {
        // Rewrite tracking wrappers (e.g. /?redirectUrl=https%3A%2F%2F…) to the real URL
        // so the link is absolute https and SPA does not capture it.
        const base =
          typeof location !== 'undefined' ? location.href : 'https://local.invalid/'
        const unwrapped = unwrapEmailHref(val.trim(), base)
        if (unwrapped) val = unwrapped
        neo.setAttribute('rel', 'noopener noreferrer nofollow')
        if (!neo.getAttribute('target')) neo.setAttribute('target', '_blank')
      }
    }
    if (name === 'style') {
      val = sanitizeStyle(val)
      if (!val) continue
    }
    if (name === 'id' || name === 'class') {
      // strip characters that break out of attributes oddly
      val = val.replace(/[^\w\s\-]/g, '')
    }
    try {
      neo.setAttribute(name, val)
    } catch {
      /* ignore invalid */
    }
  }

  const frag = doc.createDocumentFragment()
  for (const child of Array.from(el.childNodes)) {
    walk(child, frag, doc)
  }
  neo.appendChild(frag)
  out.appendChild(neo)
}

/** Strip obvious XSS vectors from email HTML before v-html. */
export function sanitizeHtml(html: string): string {
  if (!html || typeof html !== 'string') return ''
  if (typeof DOMParser === 'undefined') {
    // SSR / non-browser fallback: aggressive strip
    return html
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .replace(/<svg[\s\S]*?<\/svg>/gi, '')
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
      .replace(/javascript\s*:/gi, '')
  }
  try {
    const doc = new DOMParser().parseFromString(
      `<div id="om-root">${html}</div>`,
      'text/html',
    )
    const root = doc.getElementById('om-root')
    if (!root) return ''
    const out = doc.createDocumentFragment()
    for (const child of Array.from(root.childNodes)) {
      walk(child, out, doc)
    }
    const wrap = doc.createElement('div')
    wrap.appendChild(out)
    return wrap.innerHTML
  } catch {
    return ''
  }
}

/** Parse mail date to UTC ISO string, or null. */
export function toUtcIso(date?: string | null): string | null {
  if (!date) return null
  const t = Date.parse(date)
  if (!Number.isFinite(t)) return null
  return new Date(t).toISOString()
}

/** Newest message date as UTC ISO among a list. */
export function newestMessageUtcIso(
  messages: Array<{ date?: string | null }>,
): string | null {
  let best: number | null = null
  for (const m of messages) {
    if (!m.date) continue
    const t = Date.parse(m.date)
    if (!Number.isFinite(t)) continue
    if (best === null || t > best) best = t
  }
  return best == null ? null : new Date(best).toISOString()
}
