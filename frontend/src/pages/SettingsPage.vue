<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '@/stores/settings'
import { useVaultStore } from '@/stores/vault'
import { useAccountsStore } from '@/stores/accounts'
import { useTwoFaStore } from '@/stores/twofa'
import { useMailCacheStore } from '@/stores/mailCache'
import { getLicenseToken, setLicenseToken } from '@/utils/device'
import { useToast } from '@/composables/useToast'
import { copyText } from '@/utils/clipboard'
import UiSelect from '@/components/UiSelect.vue'
import { browserTimeZone, TIMEZONE_OPTIONS } from '@/utils/timezones'
import type { ThemeMode } from '@/utils/theme'
import type { UiSelectOption } from '@/components/UiSelect.vue'

const { t, locale } = useI18n()
const settings = useSettingsStore()
const vault = useVaultStore()
const accounts = useAccountsStore()
const twofa = useTwoFaStore()
const mailCache = useMailCacheStore()
const { flashMsg } = useToast()

const isZh = computed(() => String(locale.value).toLowerCase().startsWith('zh'))

const timeZoneOptions = computed<UiSelectOption[]>(() =>
  TIMEZONE_OPTIONS.map((o) => {
    const base = isZh.value ? o.labelZh : o.labelEn
    if (o.value === 'browser') {
      return {
        value: o.value,
        label: `${t('settings.timeZoneBrowser')} (${browserTimeZone()})`,
        title: browserTimeZone(),
      }
    }
    return { value: o.value, label: `${base} · ${o.value}`, title: o.value }
  }),
)

const themeOptions = computed<UiSelectOption[]>(() => [
  { value: 'system', label: t('settings.themeSystem') },
  { value: 'light', label: t('settings.themeLight') },
  { value: 'dark', label: t('settings.themeDark') },
])

function onTimeZoneChange(v: string | number) {
  settings.s.timeZone = String(v)
  flashMsg(t('settings.saved'))
}

function onThemeChange(v: string | number) {
  settings.s.theme = String(v) as ThemeMode
  flashMsg(t('settings.saved'))
}

const licenseInput = ref(getLicenseToken())
const lockMin = ref(vault.lockMinutes)
const enablePw = ref('')
const recoveryReveal = ref('')
const showRecovery = ref(false)
const vaultErr = ref('')
const showFactoryReset = ref(false)
const resetConfirm = ref('')
const resetErr = ref('')

const quota = computed(() => settings.quota)
const recoveryDisplay = computed(
  () => recoveryReveal.value || vault.savedRecoveryKey || vault.pendingRecoveryKey || '',
)

async function loadConfig() {
  await settings.loadPublicConfig()
  settings.applyRetentionNow()
}

function saveLicense() {
  setLicenseToken(licenseInput.value)
  settings.s.licenseToken = licenseInput.value
  flashMsg(t('settings.saved'))
  void loadConfig()
}

function saveRetention() {
  // watch on retentionDays also prunes; call explicitly for immediate feedback
  settings.applyRetentionNow()
  flashMsg(t('settings.saved'))
}

function saveVaultSettings() {
  vault.setLockMinutes(Number(lockMin.value) || 0)
  flashMsg(t('settings.saved'))
}

function lockNow() {
  accounts.clearLocalSecrets()
  twofa.clearSecrets()
  mailCache.clearSecrets()
  vault.lock()
}

async function onEnableRecovery() {
  vaultErr.value = ''
  recoveryReveal.value = ''
  if (!enablePw.value) {
    vaultErr.value = t('vault.errEmpty')
    return
  }
  try {
    const rk = await vault.enableRecovery(enablePw.value)
    enablePw.value = ''
    recoveryReveal.value = rk
    flashMsg(t('settings.saved'))
  } catch {
    vaultErr.value = t('vault.errBadPassword')
  }
}

function dismissReveal() {
  recoveryReveal.value = ''
  vault.dismissRecoveryKey()
  showRecovery.value = false
}

async function copyRecovery() {
  const k = recoveryDisplay.value
  if (!k) return
  if (await copyText(k)) flashMsg(t('common.copied'))
}

function onFactoryReset() {
  resetErr.value = ''
  const expect = t('vault.resetConfirmWord')
  if (resetConfirm.value.trim() !== expect) {
    resetErr.value = t('vault.resetConfirmMismatch')
    return
  }
  vault.factoryResetLocal()
}

onMounted(() => {
  licenseInput.value = getLicenseToken()
  // Prefer 0 (no idle lock) for existing users who still have old default 30
  // only when key was never customized? Keep stored value as-is.
  lockMin.value = vault.lockMinutes
  void loadConfig()
})
</script>

<template>
  <div class="settings-page">
    <div class="settings-card card-solid">
      <h1>{{ t('settings.title') }}</h1>
      <p class="sub">{{ t('settings.subtitle') }}</p>

      <section class="block">
        <h2>{{ t('settings.fetchTitle') }}</h2>
        <div class="field">
          <label class="label">{{ t('settings.lookbackDays') }}</label>
          <input v-model.number="settings.s.lookbackDays" class="input" type="number" min="1" max="30" />
          <p class="hint">{{ t('settings.lookbackHint') }}</p>
        </div>
        <div class="field">
          <label class="label">{{ t('settings.retentionDays') }}</label>
          <input v-model.number="settings.s.retentionDays" class="input" type="number" min="7" max="365" />
          <p class="hint">{{ t('settings.retentionHint') }}</p>
        </div>
        <div class="field">
          <label class="label">{{ t('settings.batchConc') }}</label>
          <input v-model.number="settings.s.batchConcurrency" class="input" type="number" min="1" max="32" />
        </div>
        <div class="field">
          <label class="toggle">
            <input v-model="settings.s.importPrecheck" type="checkbox" />
            <span class="toggle-track" aria-hidden="true" />
            <span>{{ t('settings.importPrecheck') }}</span>
          </label>
          <p class="hint">{{ t('settings.importPrecheckHint') }}</p>
        </div>
        <button type="button" class="btn btn-primary btn-sm" @click="saveRetention">
          {{ t('common.save') }}
        </button>
      </section>

      <section class="block">
        <h2>{{ t('settings.displayTitle') }}</h2>
        <div class="field">
          <label class="label">{{ t('settings.timeZone') }}</label>
          <UiSelect
            :model-value="settings.s.timeZone"
            :options="timeZoneOptions"
            @update:model-value="onTimeZoneChange"
          />
          <p class="hint">{{ t('settings.timeZoneHint') }}</p>
        </div>
        <div class="field">
          <label class="label">{{ t('settings.theme') }}</label>
          <UiSelect
            :model-value="settings.s.theme"
            :options="themeOptions"
            @update:model-value="onThemeChange"
          />
          <p class="hint">{{ t('settings.themeHint') }}</p>
        </div>
      </section>

      <section class="block">
        <h2>{{ t('vault.securityTitle') }}</h2>
        <p class="hint">{{ t('vault.securityHint') }}</p>
        <div class="keys-guide">
          <div class="keys-guide-title">{{ t('vault.keysGuideTitle') }}</div>
          <div class="keys-guide-row">
            <strong>{{ t('vault.explainPasswordTitle') }}</strong>
            <p>{{ t('vault.explainPasswordBody') }}</p>
          </div>
          <div class="keys-guide-row">
            <strong>{{ t('vault.explainRecoveryTitle') }}</strong>
            <p>{{ t('vault.explainRecoveryBody') }}</p>
          </div>
          <p class="keys-guide-warn">{{ t('vault.explainBothLost') }}</p>
        </div>
        <p class="hint">{{ t('vault.rememberSessionHint') }}</p>
        <div class="field">
          <label class="label">{{ t('vault.lockMinutes') }}</label>
          <input v-model.number="lockMin" class="input" type="number" min="0" max="1440" />
        </div>
        <div class="btn-row">
          <button type="button" class="btn btn-outline btn-sm" @click="saveVaultSettings">
            {{ t('common.save') }}
          </button>
          <button type="button" class="btn btn-ghost btn-sm" @click="lockNow">
            {{ t('vault.lockNow') }}
          </button>
        </div>
        <div class="field" style="margin-top: 14px">
          <p class="hint">
            {{ vault.hasRecovery ? t('vault.hasRecoveryYes') : t('vault.hasRecoveryNo') }}
          </p>
          <template v-if="!vault.hasRecovery">
            <p class="hint">{{ t('vault.enableRecoveryHint') }}</p>
            <label class="label">{{ t('vault.recoveryPasswordConfirm') }}</label>
            <input
              v-model="enablePw"
              class="input"
              type="password"
              autocomplete="current-password"
            />
            <p v-if="vaultErr" class="hint" style="color: var(--danger)">{{ vaultErr }}</p>
            <button type="button" class="btn btn-outline btn-sm" @click="onEnableRecovery">
              {{ t('vault.enableRecoveryBtn') }}
            </button>
          </template>
          <template v-else>
            <div class="field recovery-panel">
              <label class="label">{{ t('vault.recoveryKey') }}</label>
              <p class="hint">{{ t('vault.viewRecoveryHint') }}</p>
              <div class="btn-row">
                <button
                  type="button"
                  class="btn btn-outline btn-sm"
                  @click="showRecovery = !showRecovery"
                >
                  {{ showRecovery ? t('vault.hideRecovery') : t('vault.viewRecovery') }}
                </button>
                <button
                  v-if="showRecovery && recoveryDisplay"
                  type="button"
                  class="btn btn-primary btn-sm"
                  @click="copyRecovery"
                >
                  {{ t('vault.copyRecovery') }}
                </button>
              </div>
              <div v-if="showRecovery && recoveryDisplay" class="recovery-reveal">
                <code class="mono-block" data-testid="settings-recovery-key">{{ recoveryDisplay }}</code>
              </div>
              <p v-else-if="showRecovery && !recoveryDisplay" class="hint">
                {{ t('vault.recoveryNotCached') }}
              </p>
            </div>
          </template>
          <div v-if="recoveryReveal" class="recovery-reveal" style="margin-top: 10px">
            <p class="hint">{{ t('vault.recoverySaveHint') }}</p>
            <code class="mono-block">{{ recoveryReveal }}</code>
            <button type="button" class="btn btn-primary btn-sm" @click="dismissReveal">
              {{ t('vault.recoverySaved') }}
            </button>
          </div>
        </div>

        <div class="danger-zone">
          <h3 class="danger-title">{{ t('vault.factoryResetTitle') }}</h3>
          <p class="hint">{{ t('vault.factoryResetHint') }}</p>
          <p class="hint danger-text">{{ t('vault.factoryResetWarn') }}</p>
          <button
            type="button"
            class="btn btn-outline btn-sm danger-btn"
            @click="showFactoryReset = !showFactoryReset"
          >
            {{ t('vault.factoryResetLink') }}
          </button>
          <div v-if="showFactoryReset" class="reset-box">
            <label class="label">{{
              t('vault.resetTypeLabel', { word: t('vault.resetConfirmWord') })
            }}</label>
            <input
              v-model="resetConfirm"
              class="input mono"
              type="text"
              autocomplete="off"
              spellcheck="false"
              :placeholder="t('vault.resetConfirmWord')"
              @keydown.enter="onFactoryReset"
            />
            <p v-if="resetErr" class="hint danger-text">{{ resetErr }}</p>
            <button type="button" class="btn btn-sm danger-fill" @click="onFactoryReset">
              {{ t('vault.factoryResetAction') }}
            </button>
          </div>
        </div>
      </section>

      <section class="block">
        <h2>{{ t('settings.licenseTitle') }}</h2>
        <p class="hint">{{ t('settings.licenseHint') }}</p>
        <div class="field">
          <label class="label">{{ t('settings.licenseToken') }}</label>
          <input
            v-model="licenseInput"
            class="input mono"
            type="text"
            spellcheck="false"
            :placeholder="t('settings.licenseTokenPh')"
          />
        </div>
        <button type="button" class="btn btn-outline btn-sm" @click="saveLicense">
          {{ t('settings.saveLicense') }}
        </button>
        <div v-if="quota" class="quota">
          <div>
            {{ t('settings.licensed') }}:
            <strong :class="quota.licensed ? 'ok' : ''">{{
              quota.licensed ? t('common.yes') : t('common.no')
            }}</strong>
          </div>
          <div v-if="quota.licensed" class="hint">{{ t('settings.quotaUnlimited') }}</div>
          <div v-else>
            {{ t('settings.quotaLocal') }}: {{ quota.max_local_accounts ?? '—' }} ·
            {{ t('settings.quotaCloud') }}:
            {{ quota.cloud_used ?? 0 }}/{{ quota.max_cloud_accounts ?? '—' }} ·
            {{ t('settings.quotaPoll') }}: {{ quota.poll_used_hour ?? 0 }}/{{ quota.max_poll_per_hour ?? '—' }}
          </div>
          <p class="hint">{{ t('settings.quotaHint') }}</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.mono-block {
  display: block;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--panel-soft, #1a1d27);
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 12px;
  letter-spacing: 0.04em;
  word-break: break-all;
  margin: 8px 0;
  user-select: all;
}
.recovery-reveal {
  margin-top: 10px;
  padding: 12px;
  border-radius: 12px;
  background: var(--accent-soft, rgba(124, 140, 255, 0.1));
}
.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.keys-guide {
  margin: 0 0 16px;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--panel-soft, rgba(255, 255, 255, 0.04));
  border: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.keys-guide-title {
  font-size: 12px;
  font-weight: 700;
}
.keys-guide-row strong {
  display: block;
  font-size: 12px;
  color: var(--accent, #7c8cff);
  margin-bottom: 2px;
}
.keys-guide-row p {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--muted);
}
.keys-guide-warn {
  margin: 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--danger, #f07178);
}
.danger-zone {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px dashed var(--border, rgba(15, 23, 42, 0.12));
}
.danger-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--danger, #e11d48);
}
.danger-text {
  color: var(--danger, #e11d48) !important;
}
.danger-btn {
  color: var(--danger, #e11d48);
  border-color: rgba(225, 29, 72, 0.35);
}
.danger-fill {
  background: var(--danger, #e11d48);
  color: #fff;
  border: none;
}
.reset-box {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  background: rgba(225, 29, 72, 0.06);
  border: 1px solid rgba(225, 29, 72, 0.18);
}
.settings-page {
  max-width: 560px;
  margin: 0 auto;
  padding: 24px 16px 48px;
}
@media (max-width: 600px) {
  .settings-page {
    padding: 16px 12px 40px;
  }
  .settings-card {
    padding: 16px !important;
  }
}
.settings-card {
  padding: 24px;
  border-radius: var(--radius);
}
.settings-card h1 {
  font-size: 20px;
  font-weight: 750;
  letter-spacing: -0.02em;
}
.sub {
  color: var(--muted);
  font-size: 13px;
  margin: 6px 0 20px;
  line-height: 1.45;
}
.block {
  margin-bottom: 28px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.block h2 {
  font-size: 14px;
  margin-bottom: 12px;
  font-weight: 650;
}
.quota {
  margin-top: 12px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}
.quota .ok {
  color: var(--success);
}
</style>
