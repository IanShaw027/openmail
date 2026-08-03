import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import en from './locales/en'

export type AppLocale = 'zh-CN' | 'en'

const STORAGE_KEY = 'openmail.locale'

/**
 * Pick UI language from the browser (first visit / no saved preference).
 * Uses navigator.languages when available so regional tags (zh-TW, zh-HK) map to zh-CN.
 */
export function detectBrowserLocale(): AppLocale {
  try {
    const list: string[] = []
    if (typeof navigator !== 'undefined') {
      if (Array.isArray(navigator.languages)) {
        for (const l of navigator.languages) {
          if (l) list.push(String(l).toLowerCase())
        }
      }
      if (navigator.language) list.push(navigator.language.toLowerCase())
    }
    for (const nav of list) {
      if (nav.startsWith('zh')) return 'zh-CN'
      if (nav.startsWith('en')) return 'en'
    }
  } catch {
    /* ignore */
  }
  return 'en'
}

/** Saved preference wins; otherwise browser language. */
export function detectLocale(): AppLocale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh-CN' || saved === 'en') return saved
  } catch {
    /* private mode */
  }
  return detectBrowserLocale()
}

export function persistLocale(locale: AppLocale) {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    /* ignore */
  }
}

const initial = detectLocale()

export const i18n = createI18n({
  legacy: false,
  locale: initial,
  fallbackLocale: 'en',
  messages: {
    'zh-CN': zhCN,
    en,
  },
})

// Align <html lang> as early as possible (before AppShell mounts)
try {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = initial
  }
} catch {
  /* ignore */
}

export default i18n
