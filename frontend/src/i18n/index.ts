import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import en from './locales/en'

export type AppLocale = 'zh-CN' | 'en'

const STORAGE_KEY = 'openmail.locale'

export function detectLocale(): AppLocale {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'zh-CN' || saved === 'en') return saved
  const nav = navigator.language?.toLowerCase() ?? ''
  if (nav.startsWith('zh')) return 'zh-CN'
  return 'en'
}

export function persistLocale(locale: AppLocale) {
  localStorage.setItem(STORAGE_KEY, locale)
}

export const i18n = createI18n({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'en',
  messages: {
    'zh-CN': zhCN,
    en,
  },
})

export default i18n
