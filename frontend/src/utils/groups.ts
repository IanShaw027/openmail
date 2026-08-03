/** Local account groups (folders) for console organization. */

import { i18n } from '@/i18n'

export interface MailGroup {
  id: string
  name: string
  color?: string
  order: number
}

export const DEFAULT_GROUP_ID = 'default'
export const GROUPS_STORAGE_KEY = 'openmail.groups'
export const ACTIVE_GROUP_KEY = 'openmail.activeGroup'
export const IMPORT_GROUP_KEY = 'openmail.importGroup'

export function defaultGroups(): MailGroup[] {
  // Name is also localized at display time via console.groupDefault
  const name = String(
    (i18n.global as { t: (k: string) => unknown }).t('console.groupDefault'),
  )
  return [
    {
      id: DEFAULT_GROUP_ID,
      name,
      order: 0,
      color: '#4f46e5',
    },
  ]
}

export function loadGroups(): MailGroup[] {
  try {
    const raw = localStorage.getItem(GROUPS_STORAGE_KEY)
    if (!raw) return defaultGroups()
    const parsed = JSON.parse(raw) as MailGroup[]
    if (!Array.isArray(parsed) || !parsed.length) return defaultGroups()
    // Ensure default exists
    if (!parsed.some((g) => g.id === DEFAULT_GROUP_ID)) {
      return [...defaultGroups(), ...parsed.map((g, i) => ({ ...g, order: i + 1 }))]
    }
    return parsed.sort((a, b) => a.order - b.order)
  } catch {
    return defaultGroups()
  }
}

export function saveGroups(groups: MailGroup[]): void {
  localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(groups))
}

export function uidGroup(): string {
  return `grp_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
}
