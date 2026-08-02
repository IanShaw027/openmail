/**
 * Sanitize email HTML for safe v-html rendering.
 * Uses DOMParser + allowlist (not regex-only).
 */

const ALLOWED_TAGS = new Set([
  'a',
  'abbr',
  'b',
  'blockquote',
  'br',
  'caption',
  'code',
  'div',
  'em',
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
  'span',
  'strong',
  'sub',
  'sup',
  'table',
  'tbody',
  'td',
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
  'cellpadding',
  'cellspacing',
  'class',
  'id',
  'target',
  'rel',
  'style', // further filtered
])

/** Strip dangerous CSS from style attribute. */
function sanitizeStyle(style: string): string {
  let s = style
  // expression(), url(javascript:), -moz-binding, behavior
  s = s.replace(/expression\s*\(/gi, '')
  s = s.replace(/url\s*\(\s*['"]?\s*javascript\s*:/gi, 'url(')
  s = s.replace(/-moz-binding/gi, '')
  s = s.replace(/behavior\s*:/gi, '')
  s = s.replace(/@import/gi, '')
  return s
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
        neo.setAttribute('rel', 'noopener noreferrer nofollow')
        if (!neo.getAttribute('target')) neo.setAttribute('target', '_blank')
      }
    }
    if (name === 'style') {
      val = sanitizeStyle(val)
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
