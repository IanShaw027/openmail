<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useLocaleStore } from '@/stores/locale'
import { useApiStatus } from '@/composables/useApiStatus'
import { pushToast } from '@/composables/useToast'
import { useVaultStore } from '@/stores/vault'
import { useAccountsStore } from '@/stores/accounts'
import { useTwoFaStore } from '@/stores/twofa'
import { useMailCacheStore } from '@/stores/mailCache'
import OpenMailLogo from '@/components/OpenMailLogo.vue'

const { t } = useI18n()
const route = useRoute()
const localeStore = useLocaleStore()
const apiStatus = useApiStatus()
const vault = useVaultStore()
const accounts = useAccountsStore()
const twofa = useTwoFaStore()
const mailCache = useMailCacheStore()

const menuOpen = ref(false)

const isConsole = computed(() => route.name === 'console')

function toggleLocale() {
  localeStore.toggle()
}

function lockVault() {
  accounts.clearLocalSecrets()
  twofa.clearSecrets()
  mailCache.clearSecrets()
  vault.lock()
  menuOpen.value = false
}

onMounted(async () => {
  const ok = await apiStatus.probe()
  if (!ok && apiStatus.showBanner()) {
    pushToast(t('app.apiOffline'), 'danger', 6000)
    apiStatus.dismiss()
  }
})
</script>

<template>
  <div class="shell" :class="{ 'shell--console': isConsole }">
    <header class="topnav">
      <div class="topnav-inner">
        <div class="topnav-left">
          <RouterLink to="/" class="brand" @click="menuOpen = false">
            <span class="brand-mark" aria-hidden="true">
              <OpenMailLogo :size="28" />
            </span>
            <span class="brand-text">
              <span class="brand-name">{{ t('app.name') }}</span>
              <span class="brand-tag">{{ t('app.tagline') }}</span>
            </span>
          </RouterLink>

          <nav class="nav-links" :class="{ open: menuOpen }">
            <RouterLink to="/" class="nav-link" active-class="is-active" @click="menuOpen = false">
              {{ t('nav.console') }}
            </RouterLink>
            <RouterLink
              to="/mails"
              class="nav-link"
              active-class="is-active"
              @click="menuOpen = false"
            >
              {{ t('nav.myMail') }}
            </RouterLink>
            <RouterLink
              to="/2fa"
              class="nav-link"
              active-class="is-active"
              @click="menuOpen = false"
            >
              {{ t('nav.twofa') }}
            </RouterLink>
            <RouterLink
              to="/settings"
              class="nav-link"
              active-class="is-active"
              @click="menuOpen = false"
            >
              {{ t('nav.settings') }}
            </RouterLink>
          </nav>
        </div>

        <div class="topnav-right">
          <button
            v-if="vault.unlocked"
            type="button"
            class="btn btn-ghost btn-sm"
            :title="t('nav.lockVault')"
            @click="lockVault"
          >
            {{ t('nav.lockVault') }}
          </button>
          <button
            type="button"
            class="btn btn-ghost btn-sm lang-toggle"
            :title="t('locale.label')"
            @click="toggleLocale"
          >
            {{ localeStore.locale === 'zh-CN' ? 'EN' : '中文' }}
          </button>
          <button
            type="button"
            class="menu-toggle btn btn-ghost btn-sm"
            aria-label="Menu"
            @click="menuOpen = !menuOpen"
          >
            ☰
          </button>
        </div>
      </div>
    </header>

    <main class="main">
      <slot />
    </main>

    <footer v-if="!isConsole" class="footer">
      <p>{{ t('app.footer') }}</p>
      <div class="footer-links">
        <RouterLink to="/privacy">{{ t('auth.privacyPolicy') }}</RouterLink>
        <span>·</span>
        <RouterLink to="/terms">{{ t('auth.termsOfUse') }}</RouterLink>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.shell--console .main {
  flex: 1;
  min-height: 0;
  padding: 0;
  max-width: none;
}
.topnav {
  height: var(--nav-h, 56px);
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  backdrop-filter: var(--glass);
  position: sticky;
  top: 0;
  z-index: 40;
}
.topnav-inner {
  height: 100%;
  max-width: var(--content-max, 1600px);
  margin: 0 auto;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.topnav-left {
  display: flex;
  align-items: center;
  gap: 18px;
  min-width: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: inherit;
  text-decoration: none;
}
.brand-mark {
  color: var(--accent);
  display: flex;
}
.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}
.brand-name {
  font-weight: 750;
  font-size: 15px;
}
.brand-tag {
  font-size: 10px;
  color: var(--muted);
}
.nav-links {
  display: flex;
  gap: 4px;
}
.nav-link {
  padding: 7px 12px;
  border-radius: var(--control-radius-sm, 8px);
  font-size: var(--control-font, 13px);
  font-weight: 600;
  color: var(--muted);
  text-decoration: none;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}
.nav-link:hover,
.nav-link.is-active {
  color: var(--accent);
  background: var(--accent-soft);
}
.lang-toggle {
  min-width: 48px;
}
.topnav-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.menu-toggle {
  display: none;
}
.main {
  flex: 1;
  width: 100%;
  max-width: var(--content-max, 1600px);
  margin: 0 auto;
  padding: 0;
}
.footer {
  padding: 20px 16px 28px;
  text-align: center;
  font-size: 12px;
  color: var(--muted);
}
.footer-links {
  margin-top: 8px;
  display: flex;
  justify-content: center;
  gap: 8px;
}
@media (max-width: 720px) {
  .topnav {
    position: sticky;
  }
  .topnav-inner {
    padding: 0 10px;
  }
  .menu-toggle {
    display: inline-flex;
  }
  .nav-links {
    display: none;
    position: absolute;
    top: var(--nav-h, 56px);
    left: 0;
    right: 0;
    flex-direction: column;
    background: var(--panel-solid, var(--panel));
    border-bottom: 1px solid var(--border);
    padding: 8px;
    z-index: 50;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  }
  .nav-links.open {
    display: flex;
  }
  .nav-link {
    padding: 12px 14px;
  }
  .brand-tag {
    display: none;
  }
  .footer {
    padding: 16px 12px 24px;
  }
}

@media (max-width: 480px) {
  .brand-name {
    font-size: 14px;
  }
}
</style>
