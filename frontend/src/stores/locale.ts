import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { i18n, persistLocale, type AppLocale } from '@/i18n'

export const useLocaleStore = defineStore('locale', () => {
  const locale = ref<AppLocale>((i18n.global.locale.value as AppLocale) || 'zh-CN')

  function setLocale(next: AppLocale) {
    locale.value = next
    i18n.global.locale.value = next
    persistLocale(next)
    document.documentElement.lang = next
  }

  function toggle() {
    setLocale(locale.value === 'zh-CN' ? 'en' : 'zh-CN')
  }

  watch(
    locale,
    (v) => {
      document.documentElement.lang = v
    },
    { immediate: true },
  )

  return { locale, setLocale, toggle }
})
