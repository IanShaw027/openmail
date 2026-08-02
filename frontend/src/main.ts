import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import './styles/tokens.css'
import './styles/base.css'
import { getDeviceId } from '@/utils/device'

// Ensure device id exists early (quota / license headers)
try {
  getDeviceId()
} catch {
  /* ignore */
}

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(i18n)
app.mount('#app')
