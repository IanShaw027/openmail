/**
 * Purpose-tag notes for mail accounts (browser-local presets).
 *
 * - Display text uses space-separated tokens; inactive brands are stored as
 *   "!key" so free-form text and multi-tag state can live in `acc.note`.
 * - Preset catalog (order, custom labels) is per-browser in localStorage and
 *   shared across all accounts in this vault/browser.
 */

export type PurposeKind = 'brand' | 'generic'

export interface PurposeDef {
  /** Token stored in note text (e.g. claude, personal) */
  key: string
  /** Fallback label when i18n missing */
  label: string
  kind: PurposeKind
  /** Brand accent (CSS color) when active */
  color: string
}

const PRESETS_KEY = 'openmail.notePurposePresets.v1'

/** Built-in catalog — personal use + AI tools + common ops labels */
export const BUILTIN_PURPOSES: PurposeDef[] = [
  { key: 'personal', label: '个人', kind: 'generic', color: '#6366f1' },
  { key: 'claude', label: 'Claude', kind: 'brand', color: '#d97706' },
  { key: 'chatgpt', label: 'ChatGPT', kind: 'brand', color: '#10a37f' },
  { key: 'kiro', label: 'Kiro', kind: 'brand', color: '#8b5cf6' },
  { key: 'grok', label: 'Grok', kind: 'brand', color: '#111827' },
  { key: 'gemini', label: 'Gemini', kind: 'brand', color: '#4285f4' },
  { key: 'cursor', label: 'Cursor', kind: 'brand', color: '#0ea5e9' },
  { key: 'work', label: '工作', kind: 'generic', color: '#0f766e' },
  { key: 'register', label: '注册用', kind: 'generic', color: '#2563eb' },
  { key: 'bound', label: '已绑卡', kind: 'generic', color: '#059669' },
  { key: 'verify', label: '待验证', kind: 'generic', color: '#d97706' },
  { key: 'backup', label: '备用', kind: 'generic', color: '#64748b' },
  { key: 'main', label: '主号', kind: 'generic', color: '#7c3aed' },
  { key: 'discard', label: '废弃', kind: 'generic', color: '#94a3b8' },
]

export type TokenState = 'active' | 'inactive' | 'plain'

export interface ParsedToken {
  raw: string
  key: string
  state: TokenState
}

function inactiveRaw(key: string): string {
  return `!${key}`
}

export function parseNoteTokens(note?: string | null): ParsedToken[] {
  if (!note?.trim()) return []
  return note
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((raw) => {
      if (raw.startsWith('!') && raw.length > 1) {
        return { raw, key: raw.slice(1).toLowerCase(), state: 'inactive' as const }
      }
      const known = findPurpose(raw.toLowerCase())
      if (known) return { raw, key: known.key, state: 'active' as const }
      return { raw, key: raw.toLowerCase(), state: 'plain' as const }
    })
}

/** Human-facing note line: only active/plain tokens (inactive hidden from label). */
export function noteDisplayText(note?: string | null): string {
  const tokens = parseNoteTokens(note)
  return tokens
    .filter((t) => t.state !== 'inactive')
    .map((t) => {
      const p = findPurpose(t.key)
      return p?.label || t.raw.replace(/^!/, '')
    })
    .join(' ')
}

export function isPurposeActive(note: string | undefined | null, key: string): boolean {
  const k = key.toLowerCase()
  return parseNoteTokens(note).some((t) => t.key === k && t.state === 'active')
}

export function isPurposeInactive(note: string | undefined | null, key: string): boolean {
  const k = key.toLowerCase()
  return parseNoteTokens(note).some((t) => t.key === k && t.state === 'inactive')
}

export function hasPurposeToken(note: string | undefined | null, key: string): boolean {
  const k = key.toLowerCase()
  return parseNoteTokens(note).some((t) => t.key === k)
}

/** Cycle: absent → active → inactive → absent */
export function togglePurposeInNote(note: string | undefined | null, key: string): string {
  const k = key.toLowerCase()
  const tokens = parseNoteTokens(note)
  const idx = tokens.findIndex((t) => t.key === k)
  if (idx < 0) {
    tokens.push({ raw: k, key: k, state: 'active' })
  } else if (tokens[idx]!.state === 'active') {
    tokens[idx] = { raw: inactiveRaw(k), key: k, state: 'inactive' }
  } else {
    tokens.splice(idx, 1)
  }
  return serializeTokens(tokens)
}

export function setPurposeActive(note: string | undefined | null, key: string, active: boolean): string {
  const k = key.toLowerCase()
  const tokens = parseNoteTokens(note).filter((t) => t.key !== k)
  if (active) tokens.push({ raw: k, key: k, state: 'active' })
  else tokens.push({ raw: inactiveRaw(k), key: k, state: 'inactive' })
  return serializeTokens(tokens)
}

function serializeTokens(tokens: ParsedToken[]): string {
  return tokens
    .map((t) => {
      if (t.state === 'inactive') return inactiveRaw(t.key)
      if (t.state === 'active') return t.key
      return t.raw
    })
    .join(' ')
    .trim()
}

export function loadPurposeCatalog(): PurposeDef[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY)
    if (!raw) return [...BUILTIN_PURPOSES]
    const parsed = JSON.parse(raw) as PurposeDef[]
    if (!Array.isArray(parsed) || !parsed.length) return [...BUILTIN_PURPOSES]
    const cleaned = parsed
      .filter((p) => p && typeof p.key === 'string' && p.key.trim())
      .map((p) => ({
        key: String(p.key).trim().toLowerCase().replace(/\s+/g, '-'),
        label: String(p.label || p.key).trim() || p.key,
        kind: (p.kind === 'brand' ? 'brand' : 'generic') as PurposeKind,
        color: String(p.color || '#64748b'),
      }))
    return cleaned.length ? cleaned : [...BUILTIN_PURPOSES]
  } catch {
    return [...BUILTIN_PURPOSES]
  }
}

export function savePurposeCatalog(list: PurposeDef[]): void {
  try {
    localStorage.setItem(PRESETS_KEY, JSON.stringify(list))
  } catch {
    /* ignore */
  }
}

export function addCustomPurpose(label: string, color = '#64748b'): PurposeDef[] {
  const name = label.trim()
  if (!name) return loadPurposeCatalog()
  const key = name
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9\u4e00-\u9fff_-]/gi, '')
    .slice(0, 24)
  if (!key) return loadPurposeCatalog()
  const cur = loadPurposeCatalog()
  if (cur.some((p) => p.key === key)) return cur
  const next = [...cur, { key, label: name, kind: 'generic' as const, color }]
  savePurposeCatalog(next)
  return next
}

export function findPurpose(key: string, catalog?: PurposeDef[]): PurposeDef | undefined {
  const list = catalog || loadPurposeCatalog()
  const k = key.toLowerCase()
  return list.find((p) => p.key === k)
}

/** Compact SVG mark for known brands (inline, monochrome currentColor). */
export function purposeSvgPath(key: string): string {
  switch (key) {
    case 'claude':
      // stylized "C" / spark
      return 'M12 3c-4.5 0-8 3-8 7.2 0 2.8 1.5 5.2 3.8 6.6L6 21l3.2-1.6c.9.2 1.8.4 2.8.4 4.5 0 8-3.2 8-7.6S16.5 3 12 3zm0 2c3.4 0 6 2.4 6 5.6S15.4 16 12 16c-.8 0-1.5-.1-2.2-.3l-.7-.2-.6.3-.9.4.3-1 .2-.7-.4-.6C6.5 12.4 6 11 6 9.6 6 6.4 8.6 5 12 5z'
    case 'chatgpt':
      return 'M16.5 7.2a3.6 3.6 0 0 0-2.4-3.4 3.7 3.7 0 0 0-4 1.2A3.6 3.6 0 0 0 6.4 7a3.6 3.6 0 0 0-1.2 4.7 3.6 3.6 0 0 0 1.2 4.6 3.6 3.6 0 0 0 2.4 3.4 3.7 3.7 0 0 0 4-1.2 3.6 3.6 0 0 0 3.7-1.9 3.6 3.6 0 0 0 1.2-4.7 3.6 3.6 0 0 0-1.2-4.7zm-2.1 7.9a2.6 2.6 0 0 1-1.7 1.1l-.4.1-.3.3a2.5 2.5 0 0 1-2.7.8 2.5 2.5 0 0 1-1.7-2.4v-.4l-.3-.3a2.5 2.5 0 0 1-.8-2.7 2.5 2.5 0 0 1 2.4-1.7h.4l.3-.3a2.5 2.5 0 0 1 2.7-.8 2.5 2.5 0 0 1 1.7 2.4v.4l.3.3a2.5 2.5 0 0 1 .8 2.7 2.5 2.5 0 0 1-1.7 1.5z'
    case 'grok':
      return 'M4 4h6v2H6v5h3v2H6v7H4V4zm10 0h6v16h-2v-6.5L14.2 20H12l3.6-6.2L12 8h2.3l2.7 4.6V4z'
    case 'kiro':
      return 'M6 4h3l3.5 7L16 4h3l-5 9.5V20h-3v-6.5L6 4zm11 9h3v7h-3v-7z'
    case 'gemini':
      return 'M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2zm0 5.2L11.2 10l-2.8.8 2.8.8.8 2.8.8-2.8 2.8-.8-2.8-.8L12 7.2z'
    case 'cursor':
      return 'M5 3l14 9-6 1.5L11 21 5 3zm3.2 5.2l3.3 8.2 1.2-3.4 3.2-.8-7.7-4z'
    case 'personal':
      return 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5z'
    case 'work':
      return 'M9 6V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v1h4a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4zm2 0h2V5h-2v1z'
    default:
      return 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 3a2.5 2.5 0 1 1-2.5 2.5A2.5 2.5 0 0 1 12 5zm0 14.2a7.2 7.2 0 0 1-6-3.2c.05-2 4-3.1 6-3.1s5.95 1.1 6 3.1a7.2 7.2 0 0 1-6 3.2z'
  }
}
