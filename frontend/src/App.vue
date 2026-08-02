<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterView } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import ToastHost from '@/components/ToastHost.vue'
import VaultGate from '@/components/VaultGate.vue'
import { useVaultStore } from '@/stores/vault'
import { useAccountsStore } from '@/stores/accounts'
import { useTwoFaStore } from '@/stores/twofa'
import { useMailCacheStore } from '@/stores/mailCache'

const vault = useVaultStore()
const accounts = useAccountsStore()
const twofa = useTwoFaStore()
const mailCache = useMailCacheStore()

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
}

function onActivity() {
  if (vault.unlocked) vault.touch()
}

function onVisibility() {
  if (document.visibilityState === 'visible' && vault.unlocked) vault.touch()
}

onMounted(() => {
  window.addEventListener('pointerdown', onActivity, { passive: true })
  window.addEventListener('keydown', onActivity, { passive: true })
  document.addEventListener('visibilitychange', onVisibility)

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
  window.removeEventListener('pointerdown', onActivity)
  window.removeEventListener('keydown', onActivity)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <div v-if="!bootReady" class="boot-splash" aria-busy="true">
    <div class="boot-mark">OpenMail</div>
  </div>
  <VaultGate v-else-if="!showApp" />
  <template v-else>
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
</style>
