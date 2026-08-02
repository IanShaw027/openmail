/** Quick-fill note templates for account list (browser-local). */

const KEY = 'openmail.noteTemplates'

export const DEFAULT_NOTE_TEMPLATES = [
  '注册用',
  '已绑卡',
  '待验证',
  '废弃',
  '主号',
  '备用',
]

export function loadNoteTemplates(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return [...DEFAULT_NOTE_TEMPLATES]
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return [...DEFAULT_NOTE_TEMPLATES]
    const list = parsed.map((x) => String(x).trim()).filter(Boolean)
    return list.length ? list : [...DEFAULT_NOTE_TEMPLATES]
  } catch {
    return [...DEFAULT_NOTE_TEMPLATES]
  }
}

export function saveNoteTemplates(list: string[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.filter((x) => x.trim())))
  } catch {
    /* ignore */
  }
}

export function addNoteTemplate(label: string): string[] {
  const t = label.trim()
  if (!t) return loadNoteTemplates()
  const cur = loadNoteTemplates()
  if (cur.includes(t)) return cur
  const next = [...cur, t]
  saveNoteTemplates(next)
  return next
}
