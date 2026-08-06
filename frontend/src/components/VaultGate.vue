<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useVaultStore } from '@/stores/vault'
import { useAccountsStore } from '@/stores/accounts'
import { useTwoFaStore } from '@/stores/twofa'
import { useMailCacheStore } from '@/stores/mailCache'
import { useCloudSyncStore } from '@/stores/cloudSync'
import { VaultCryptoError } from '@/utils/cryptoVault'
import { copyText } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const vault = useVaultStore()
const accounts = useAccountsStore()
const twofa = useTwoFaStore()
const mailCache = useMailCacheStore()
const cloudSync = useCloudSyncStore()
const { flashMsg } = useToast()

const password = ref('')
const password2 = ref('')
const recoveryInput = ref('')
const showPw = ref(false)
const useRecovery = ref(false)
const error = ref('')
const copied = ref(false)
const showReset = ref(false)
const resetConfirm = ref('')

const isSetup = computed(() => vault.status === 'setup')
const isLocked = computed(() => vault.status === 'locked')
const isUnavailable = computed(() => vault.status === 'unavailable')

/** Store is source of truth so App.vue can keep this gate mounted. */
const recoveryToSave = computed(() => vault.pendingRecoveryKey || '')
const showRecoveryStep = computed(() => Boolean(recoveryToSave.value))

async function hydrateStores() {
  await accounts.hydrateFromVault()
  await twofa.hydrateFromVault()
  await mailCache.hydrateFromVault()
  void accounts.loadServerAccounts()
  void cloudSync.pullCloudMailDelta()
  cloudSync.startCloudDeltaPolling()
}

async function onCreate() {
  error.value = ''
  if (password.value.length < 8) {
    error.value = t('vault.errShort')
    return
  }
  if (password.value !== password2.value) {
    error.value = t('vault.errMismatch')
    return
  }
  try {
    await vault.createVault(password.value)
    password.value = ''
    password2.value = ''
    // pendingRecoveryKey set on store → showRecoveryStep; App will not open shell yet
    await hydrateStores()
  } catch (e) {
    error.value =
      e instanceof VaultCryptoError && e.message === 'password_too_short'
        ? t('vault.errShort')
        : t('vault.errCreate')
  }
}

async function onUnlock() {
  error.value = ''
  try {
    if (useRecovery.value) {
      if (!recoveryInput.value.trim()) {
        error.value = t('vault.errEmptyRecovery')
        return
      }
      await vault.unlockWithRecovery(recoveryInput.value)
      recoveryInput.value = ''
    } else {
      if (!password.value) {
        error.value = t('vault.errEmpty')
        return
      }
      await vault.unlock(password.value)
      password.value = ''
    }
    await hydrateStores()
  } catch (e) {
    if (e instanceof VaultCryptoError && e.message === 'corrupt_data') {
      error.value = t('vault.errCorruptData')
    } else if (e instanceof VaultCryptoError && e.message === 'bad_recovery') {
      error.value = t('vault.errBadRecovery')
    } else {
      error.value = t('vault.errBadPassword')
    }
  }
}

async function copyRecovery() {
  if (!recoveryToSave.value) return
  if (await copyText(recoveryToSave.value)) {
    copied.value = true
    flashMsg(t('common.copied'))
    setTimeout(() => {
      copied.value = false
    }, 2000)
  }
}

function confirmSavedRecovery() {
  if (!recoveryToSave.value) return
  vault.dismissRecoveryKey()
  // App.vue showApp becomes true → leave gate
}

function onFactoryReset() {
  const expect = t('vault.resetConfirmWord')
  if (resetConfirm.value.trim() !== expect) {
    error.value = t('vault.resetConfirmMismatch')
    return
  }
  vault.factoryResetLocal()
}

// If user lands with pending key (e.g. HMR), keep focus on recovery step
watch(
  recoveryToSave,
  (v) => {
    if (v) error.value = ''
  },
  { immediate: true },
)
</script>

<template>
  <div class="vault-gate">
    <div class="card-solid panel">
      <div class="brand">OpenMail</div>
      <h1 class="title">{{ t('vault.title') }}</h1>
      <p class="desc">{{ t('vault.desc') }}</p>

      <div v-if="isUnavailable" class="err">{{ t('vault.unavailable') }}</div>

      <!-- Must stay mounted until user confirms (blocks main app via pendingRecoveryKey) -->
      <div v-else-if="showRecoveryStep" class="recovery-box">
        <h2 class="sub-title">{{ t('vault.recoveryTitle') }}</h2>
        <p class="hint">{{ t('vault.recoverySaveHint') }}</p>
        <div class="explain-mini">
          <strong>{{ t('vault.explainRecoveryTitle') }}</strong>
          <span>{{ t('vault.explainRecoveryBody') }}</span>
        </div>
        <code class="recovery-key" data-testid="recovery-key">{{ recoveryToSave }}</code>
        <div class="btn-row">
          <button type="button" class="btn btn-outline btn-sm" @click="copyRecovery">
            {{ copied ? t('common.copied') : t('common.copy') }}
          </button>
          <button type="button" class="btn btn-primary btn-sm" @click="confirmSavedRecovery">
            {{ t('vault.recoverySaved') }}
          </button>
        </div>
        <p class="warn">{{ t('vault.recoveryMustSave') }}</p>
      </div>

      <template v-else-if="isSetup">
        <div class="explain card-inset">
          <div class="explain-title">{{ t('vault.explainTitle') }}</div>
          <div class="explain-row">
            <strong>{{ t('vault.explainPasswordTitle') }}</strong>
            <p>{{ t('vault.explainPasswordBody') }}</p>
          </div>
          <div class="explain-row">
            <strong>{{ t('vault.explainRecoveryTitle') }}</strong>
            <p>{{ t('vault.explainRecoveryBody') }}</p>
          </div>
          <p class="explain-warn">{{ t('vault.explainBothLost') }}</p>
        </div>
        <p class="hint">{{ t('vault.setupHint') }}</p>
        <label class="label">{{ t('vault.password') }}</label>
        <input
          v-model="password"
          class="input"
          :type="showPw ? 'text' : 'password'"
          autocomplete="new-password"
          :placeholder="t('vault.passwordPh')"
          @keydown.enter="onCreate"
        />
        <label class="label">{{ t('vault.password2') }}</label>
        <input
          v-model="password2"
          class="input"
          :type="showPw ? 'text' : 'password'"
          autocomplete="new-password"
          :placeholder="t('vault.password2Ph')"
          @keydown.enter="onCreate"
        />
        <label class="tog">
          <input v-model="showPw" type="checkbox" />
          {{ t('vault.showPassword') }}
        </label>
        <p v-if="error" class="err">{{ error }}</p>
        <button
          type="button"
          class="btn btn-primary btn-block"
          :disabled="vault.busy"
          @click="onCreate"
        >
          {{ vault.busy ? t('common.loading') : t('vault.create') }}
        </button>
        <p class="warn">{{ t('vault.recoveryWarn') }}</p>
      </template>

      <template v-else-if="isLocked">
        <p class="hint">{{ t('vault.unlockHint') }}</p>
        <p class="hint sm">{{ t('vault.rememberSessionHint') }}</p>
        <button type="button" class="linkish" @click="useRecovery = !useRecovery">
          {{ useRecovery ? t('vault.usePassword') : t('vault.useRecovery') }}
        </button>
        <template v-if="!useRecovery">
          <label class="label">{{ t('vault.password') }}</label>
          <input
            v-model="password"
            class="input"
            :type="showPw ? 'text' : 'password'"
            autocomplete="current-password"
            :placeholder="t('vault.passwordPh')"
            @keydown.enter="onUnlock"
          />
          <label class="tog">
            <input v-model="showPw" type="checkbox" />
            {{ t('vault.showPassword') }}
          </label>
        </template>
        <template v-else>
          <label class="label">{{ t('vault.recoveryKey') }}</label>
          <input
            v-model="recoveryInput"
            class="input mono"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :placeholder="t('vault.recoveryKeyPh')"
            @keydown.enter="onUnlock"
          />
        </template>
        <p v-if="error" class="err">{{ error }}</p>
        <button
          type="button"
          class="btn btn-primary btn-block"
          :disabled="vault.busy"
          @click="onUnlock"
        >
          {{ vault.busy ? t('common.loading') : t('vault.unlock') }}
        </button>

        <div class="reset-zone">
          <button type="button" class="linkish danger" @click="showReset = !showReset">
            {{ t('vault.factoryResetLink') }}
          </button>
          <div v-if="showReset" class="reset-box">
            <p class="hint">{{ t('vault.factoryResetHint') }}</p>
            <p class="explain-warn">{{ t('vault.factoryResetWarn') }}</p>
            <label class="label">{{ t('vault.resetTypeLabel', { word: t('vault.resetConfirmWord') }) }}</label>
            <input
              v-model="resetConfirm"
              class="input mono"
              type="text"
              autocomplete="off"
              spellcheck="false"
              :placeholder="t('vault.resetConfirmWord')"
              @keydown.enter="onFactoryReset"
            />
            <button type="button" class="btn btn-danger btn-block" @click="onFactoryReset">
              {{ t('vault.factoryResetAction') }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.vault-gate {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--bg, #eef1f8);
}
.panel {
  width: min(440px, 100%);
  padding: 28px 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.explain {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--panel-soft, #f5f7fc);
  border: 1px solid var(--border, rgba(15, 23, 42, 0.08));
  margin: 4px 0 6px;
}
.explain-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text, #0b1220);
}
.explain-row strong {
  display: block;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 2px;
  color: var(--accent, #4f46e5);
}
.explain-row p {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--muted, #647089);
}
.explain-warn {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--danger, #e11d48);
}
.explain-mini {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  line-height: 1.45;
  color: var(--muted, #647089);
}
.explain-mini strong {
  color: var(--text, #0b1220);
  font-size: 12px;
}
.linkish {
  appearance: none;
  border: 0;
  background: none;
  color: var(--accent, #4f46e5);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.hint.sm {
  font-size: 11px;
}
.brand {
  font-weight: 800;
  letter-spacing: 0.04em;
  color: var(--accent, #4f46e5);
  font-size: 13px;
}
.title {
  margin: 0;
  font-size: 22px;
  font-weight: 750;
}
.sub-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}
.desc {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--muted, #647089);
  line-height: 1.5;
}
.hint {
  font-size: 12px;
  color: var(--muted, #647089);
  margin: 0;
}
.label {
  font-size: 12px;
  font-weight: 600;
  margin-top: 4px;
}
.input {
  width: 100%;
}
.mono {
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 12px;
  letter-spacing: 0.04em;
}
.tog {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted, #647089);
}
.btn-block {
  width: 100%;
  margin-top: 8px;
}
.btn-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.err {
  color: var(--danger, #e11d48);
  font-size: 12px;
  margin: 0;
}
.warn {
  font-size: 11px;
  color: var(--muted, #647089);
  line-height: 1.45;
  margin: 8px 0 0;
}
.recovery-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-radius: 12px;
  background: rgba(79, 70, 229, 0.08);
  border: 1px solid rgba(79, 70, 229, 0.25);
}
.recovery-key {
  display: block;
  padding: 14px 12px;
  border-radius: 8px;
  background: #0a0a0a;
  color: #fafafa;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 13px;
  letter-spacing: 0.06em;
  word-break: break-all;
  user-select: all;
  line-height: 1.5;
}
.reset-zone {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed var(--border, rgba(15, 23, 42, 0.12));
}
.linkish.danger {
  color: var(--danger, #e11d48);
}
.reset-box {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  background: rgba(225, 29, 72, 0.06);
  border: 1px solid rgba(225, 29, 72, 0.2);
}
.btn-danger {
  background: var(--danger, #e11d48);
  color: #fff;
  border: none;
}
.btn-danger:hover {
  filter: brightness(0.95);
}
</style>
