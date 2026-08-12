<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterView, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AppShell from '@/layouts/AppShell.vue'
import ToastHost from '@/components/ToastHost.vue'
import VaultGate from '@/components/VaultGate.vue'
import { useVaultStore } from '@/stores/vault'
import { useAccountsStore } from '@/stores/accounts'
import { useTwoFaStore } from '@/stores/twofa'
import { useMailCacheStore } from '@/stores/mailCache'
import { useSettingsStore } from '@/stores/settings'
import { useCloudSyncStore } from '@/stores/cloudSync'
import {
  formatLinkPreview,
  peekLandingRedirect,
} from '@/utils/emailLinks'
import { dismissToast, pushToast } from '@/composables/useToast'
import { trackUserActivity } from '@/composables/useVaultActivity'

const { t } = useI18n()
const router = useRouter()
const vault = useVaultStore()
const accounts = useAccountsStore()
const twofa = useTwoFaStore()
const mailCache = useMailCacheStore()
const settings = useSettingsStore()
const cloudSync = useCloudSyncStore()

const bootReady = ref(false)

/**
 * Enter main app only when unlocked AND recovery key (if just created)
 * has been acknowledged. Otherwise VaultGate stays mounted for setup /
 * unlock / save-recovery steps.
 */
const showApp = computed(() => {
  if (!bootReady.value) return false
  if (vault.status === 'unavailable') return true
  if (vault.status !== 'unlocked') return false
  // Block shell until user confirms recovery key after create/enable
  if (vault.pendingRecoveryKey) return false
  return true
})

async function hydrateStores() {
  await accounts.hydrateFromVault()
  await twofa.hydrateFromVault()
  await mailCache.hydrateFromVault()
  void accounts.loadServerAccounts()
  // Cloud mail delta → mailCache (soft-fails if API not ready)
  void cloudSync.pullCloudMailDelta()
  cloudSync.startCloudDeltaPolling()
}

// Stop interval poll when vault locks (explicit lock or idle timeout)
watch(
  () => vault.unlocked,
  (u) => {
    if (!u) cloudSync.stopCloudDeltaPolling()
  },
)

/**
 * Give the user a chance to react before an idle lock hides the app: any
 * activity resets the timer, and the notice goes away with it.
 */
let lockWarnToastId: number | undefined
watch(
  () => vault.lockWarningSeconds,
  (seconds) => {
    if (seconds > 0) {
      lockWarnToastId = pushToast(
        t('vault.idleLockWarning', { seconds }),
        'info',
        seconds * 1000,
      )
    } else if (lockWarnToastId !== undefined) {
      dismissToast(lockWarnToastId)
      lockWarnToastId = undefined
    }
  },
)

let stopActivityTracking: (() => void) | null = null

function onVisibility() {
  if (document.visibilityState === 'visible' && vault.unlocked) {
    vault.touch()
  } else if (document.visibilityState === 'hidden') {
    // Mobile Safari / backgrounding: flush while the page can still run async crypto
    flushAllStores()
  }
}

/**
 * Best-effort durable flush before the tab goes away.
 * Debounced vault writes (accounts / mail / 2FA) used to lose data when the
 * user refreshed within the debounce window — weimail-style tools write
 * localStorage synchronously; we mirror that with an immediate flush.
 */
function flushAllStores() {
  if (!vault.unlocked) {
    try {
      settings.flushPersist()
    } catch {
      /* ignore */
    }
    return
  }
  try {
    settings.flushPersist()
  } catch {
    /* ignore */
  }
  // Fire without await — pagehide cannot wait; critical paths (note/fetch)
  // already await flushPersist so the common cases are durable.
  void accounts.flushPersist().catch((error) => console.warn('[openmail] account flush failed', error))
  void twofa.flushPersist().catch((error) => console.warn('[openmail] 2FA flush failed', error))
  void mailCache
    .flushPersist()
    .catch((error) => console.warn('[openmail] mail cache flush failed', error))
}

function onPageHide() {
  flushAllStores()
}

onMounted(() => {
  // Email tracking links like /?redirectUrl=https://… land on the SPA;
  // confirm and jump to the real destination (or stay in app if cancelled).
  const landingDest = peekLandingRedirect()
  if (landingDest) {
    // Drop query so cancel/refresh does not re-prompt; keep path for the app
    void router.replace({ path: router.currentRoute.value.path || '/', query: {}, hash: '' })
    const shown = formatLinkPreview(landingDest)
    if (window.confirm(t('console.openLinkConfirm', { url: shown, full: landingDest }))) {
      window.location.assign(landingDest)
      return
    }
  }

  stopActivityTracking = trackUserActivity(() => {
    if (vault.unlocked) vault.touch()
  })
  document.addEventListener('visibilitychange', onVisibility)
  // pagehide covers refresh / close / bfcache; beforeunload as extra for older WebKit
  window.addEventListener('pagehide', onPageHide)
  window.addEventListener('beforeunload', onPageHide)

  void (async () => {
    try {
      const ok = await vault.tryResumeSession()
      if (ok) await hydrateStores()
    } finally {
      bootReady.value = true
    }
  })()
})

onUnmounted(() => {
  stopActivityTracking?.()
  stopActivityTracking = null
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('pagehide', onPageHide)
  window.removeEventListener('beforeunload', onPageHide)
  cloudSync.stopCloudDeltaPolling()
})
</script>

<template>
  <div v-if="!bootReady" class="boot-splash" aria-busy="true">
    <div class="boot-mark">OpenMail</div>
  </div>
  <VaultGate v-else-if="!showApp" />
  <template v-else>
    <div
      v-if="vault.deviceStatus === 'pending'"
      class="device-pending-banner"
      role="status"
    >
      {{ t('vault.devicePendingBanner') }}
      <button type="button" class="banner-refresh" @click="vault.refreshDeviceStatus()">
        {{ t('vault.devicePendingRefresh') }}
      </button>
    </div>
    <AppShell>
      <RouterView />
    </AppShell>
    <ToastHost />
  </template>
</template>

<style scoped>
.boot-splash {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: var(--bg, #eef1f8);
  color: var(--muted, #647089);
  font-weight: 700;
  letter-spacing: 0.04em;
}
.boot-mark {
  opacity: 0.7;
  font-size: 14px;
}
.device-pending-banner {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: center;
  padding: 10px 16px;
  background: color-mix(in srgb, var(--warn, #b45309) 14%, var(--bg, #eef1f8));
  color: var(--text, #1a2333);
  font-size: 13px;
  border-bottom: 1px solid color-mix(in srgb, var(--warn, #b45309) 35%, transparent);
}
.banner-refresh {
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}
</style>
