/**
 * Purpose-tag notes for mail accounts (browser-local presets).
 *
 * - Display text uses space-separated tokens; inactive brands are stored as
 *   "!key" so free-form text and multi-tag state can live in `acc.note`.
 * - Preset catalog (order, custom labels) is per-browser in localStorage and
 *   shared across all accounts in this vault/browser.
 * - Product logos come from the unified `brandLogos` registry.
 */

import {
  type LogoSvgPart,
  logoAccent,
  logoParts,
  normalizeBrandId,
} from '@/utils/brandLogos'

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
  { key: 'claude', label: 'Claude', kind: 'brand', color: '#D4A27F' },
  { key: 'chatgpt', label: 'ChatGPT', kind: 'brand', color: '#412991' },
  { key: 'kiro', label: 'Kiro', kind: 'brand', color: '#8b5cf6' },
  { key: 'grok', label: 'Grok', kind: 'brand', color: '#000000' },
  { key: 'gemini', label: 'Gemini', kind: 'brand', color: '#4285f4' },
  { key: 'cursor', label: 'Cursor', kind: 'brand', color: '#0ea5e9' },
  { key: 'github', label: 'GitHub', kind: 'brand', color: '#181717' },
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

export type PurposeSvgPart = LogoSvgPart

/** Generic purpose glyphs (not product brands) — currentColor friendly. */
const GENERIC_PURPOSE: Record<string, PurposeSvgPart[]> = {
  personal: [
    {
      d: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5z',
    },
  ],
  work: [
    {
      d: 'M9 6V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v1h4a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4zm2 0h2V5h-2v1z',
    },
  ],
  register: [
    {
      d: 'M7 3h10a2 2 0 0 1 2 2v14l-7-3-7 3V5a2 2 0 0 1 2-2zm0 2v11.2l5-2.1 5 2.1V5H7zm2 2h6v2H9V7zm0 4h4v2H9v-2z',
    },
  ],
  bound: [
    {
      d: 'M4 8a3 3 0 0 1 3-3h4v2H7a1 1 0 0 0-1 1v2H4V8zm16 0v2h-2V8a1 1 0 0 0-1-1h-4V5h4a3 3 0 0 1 3 3zM4 14h2v2a1 1 0 0 0 1 1h4v2H7a3 3 0 0 1-3-3v-2zm14 0h2v2a3 3 0 0 1-3 3h-4v-2h4a1 1 0 0 0 1-1v-2zM9 11h6v2H9v-2z',
    },
  ],
  verify: [
    {
      d: 'M12 2 4 5v6c0 5 3.4 9.4 8 10.5C16.6 20.4 20 16 20 11V5l-8-3zm0 2.2 6 2.2v5.6c0 3.8-2.5 7.1-6 8.1-3.5-1-6-4.3-6-8.1V6.4l6-2.2zm-1.1 9.4 4.6-4.6 1.4 1.4-6 6-3.2-3.2 1.4-1.4 1.8 1.8z',
    },
  ],
  backup: [
    {
      d: 'M12 4a7 7 0 0 1 6.7 5H20a4 4 0 0 1 0 8h-1v-2h1a2 2 0 0 0 0-4h-2.1l-.3-1.2A5 5 0 0 0 7.4 9 3.5 3.5 0 0 0 6 15.8V16H5a2 2 0 0 1 0-4h.3A7 7 0 0 1 12 4zm-1 7h2v3.2l2 2-1.4 1.4L11 14.4V11z',
    },
  ],
  main: [
    {
      d: 'M12 2 3 7v2h18V7L12 2zm-7 9v8h4v-5h6v5h4v-8H5zm6 10h2v2h-2v-2z',
    },
  ],
  discard: [
    {
      d: 'M9 4h6l1 2h4v2H4V6h4l1-2zm1 6h2v8h-2v-8zm4 0h2v8h-2v-8zM7 10h2v8H7v-8zm-1 10h12v2H6v-2z',
    },
  ],
  kiro: [
    {
      d: 'M6 4h3.2v7.2L15.6 4H19l-6.2 8.4L19.2 20h-3.5l-5.3-6.6V20H6V4zm11.2 9.2h3V20h-3v-6.8z',
    },
  ],
  cursor: [
    {
      d: 'M5 3.5 19 12.2l-6.2 1.6L10.8 21 5 3.5zm3.4 4.6 2.9 7.2 1.1-3 .5-.1 2.9-.7-7.4-3.4z',
    },
  ],
  gemini: [
    {
      d: 'M12 2c.4 3.6 2.4 5.6 6 6-3.6.4-5.6 2.4-6 6-.4-3.6-2.4-5.6-6-6 3.6-.4 5.6-2.4 6-6zm0 7.2c.2 1.8 1.2 2.8 3 3-1.8.2-2.8 1.2-3 3-.2-1.8-1.2-2.8-3-3 1.8-.2 2.8-1.2 3-3z',
    },
  ],
}

/**
 * Purpose shortcut icons: product brands → unified registry (OpenAI/Claude/…);
 * ops generics stay local currentColor glyphs.
 */
export function purposeSvgParts(key: string): PurposeSvgPart[] {
  const k = String(key || '').toLowerCase()
  if (GENERIC_PURPOSE[k]) return GENERIC_PURPOSE[k]!
  // brand products (chatgpt/claude/openai/google/…) from brandLogos
  // Prefer exact registry ids over fuzzy alias matches for purpose keys.
  const brandId = k === 'chatgpt' || k === 'openai' ? 'openai' : normalizeBrandId(k)
  if (brandId !== 'other') return logoParts(brandId)
  return logoParts('other')
}

export function purposeAccent(key: string): string {
  const k = String(key || '').toLowerCase()
  if (GENERIC_PURPOSE[k]) {
    const p = findPurpose(k)
    return p?.color || '#64748b'
  }
  if (k === 'chatgpt' || k === 'openai') return logoAccent('openai')
  return logoAccent(k)
}

/** @deprecated use purposeSvgParts */
export function purposeSvgPath(key: string): string {
  return purposeSvgParts(key)[0]?.d || purposeSvgParts('other')[0]!.d
}
