import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'console',
    component: () => import('@/pages/ConsolePage.vue'),
    meta: { titleKey: 'nav.console' },
  },
  {
    path: '/mails',
    name: 'mails',
    component: () => import('@/pages/MyMailsLocalPage.vue'),
    meta: { titleKey: 'nav.myMail' },
  },
  {
    path: '/2fa',
    name: 'twofa',
    component: () => import('@/pages/TwoFaPage.vue'),
    meta: { titleKey: 'nav.twofa' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/pages/SettingsPage.vue'),
    meta: { titleKey: 'nav.settings' },
  },
  {
    path: '/transfer',
    name: 'transfer',
    component: () => import('@/pages/TransferPage.vue'),
    meta: { titleKey: 'nav.transfer' },
  },
  {
    path: '/privacy',
    name: 'privacy',
    component: () => import('@/pages/PrivacyPage.vue'),
    meta: { titleKey: 'legal.privacyTitle' },
  },
  {
    path: '/terms',
    name: 'terms',
    component: () => import('@/pages/TermsPage.vue'),
    meta: { titleKey: 'legal.termsTitle' },
  },
  // Legacy URLs → home / mails
  { path: '/login', redirect: '/' },
  { path: '/register', redirect: '/' },
  { path: '/admin', redirect: '/' },
  { path: '/me/mails', redirect: '/mails' },
  { path: '/me/accounts', redirect: '/' },
  { path: '/me/password', redirect: '/settings' },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

export default router
