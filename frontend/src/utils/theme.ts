/**
 * Light / dark / system theme.
 * CSS tokens react to html[data-theme="light"|"dark"|"system"].
 */

export type ThemeMode = 'system' | 'light' | 'dark'

const ATTR = 'data-theme'
const VALID: ThemeMode[] = ['system', 'light', 'dark']

export function normalizeTheme(v: unknown): ThemeMode {
  if (typeof v === 'string' && (VALID as string[]).includes(v)) return v as ThemeMode
  return 'system'
}

/** Apply theme to documentElement. Call on boot and when settings change. */
export function applyTheme(mode: ThemeMode): void {
  if (typeof document === 'undefined') return
  const m = normalizeTheme(mode)
  document.documentElement.setAttribute(ATTR, m)
  // Hint native form controls / scrollbars
  const resolved = resolveTheme(m)
  document.documentElement.style.colorScheme = resolved === 'dark' ? 'dark' : 'light'
}

export function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'light' || mode === 'dark') return mode
  try {
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark'
    }
  } catch {
    /* ignore */
  }
  return 'light'
}

/** Keep system theme in sync when OS preference flips. */
let mqBound = false
export function bindSystemThemeListener(getMode: () => ThemeMode): void {
  if (typeof window === 'undefined' || mqBound) return
  try {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      if (getMode() === 'system') applyTheme('system')
    }
    if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onChange)
    else if (typeof mq.addListener === 'function') mq.addListener(onChange)
    mqBound = true
  } catch {
    /* ignore */
  }
}
