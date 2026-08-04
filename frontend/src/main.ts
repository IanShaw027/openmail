import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import './styles/tokens.css'
import './styles/base.css'
import { getDeviceId } from '@/utils/device'
import { useSettingsStore } from '@/stores/settings'

// Ensure device id exists early (quota / license headers)
try {
  getDeviceId()
} catch {
  /* ignore */
}

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
// Hydrate theme/timezone from localStorage before first paint of app shell
useSettingsStore()
app.use(router)
app.use(i18n)
app.mount('#app')
