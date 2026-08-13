<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
import {
  issueAdminLicense,
  listAdminLicenses,
  revokeAdminLicense,
  type IssuedLicense,
} from '@/api/admin'

const { t, locale } = useI18n()
const settings = useSettingsStore()
const vault = useVaultStore()
const accounts = useAccountsStore()
const twofa = useTwoFaStore()
const mailCache = useMailCacheStore()
const { flashMsg } = useToast()

type DeviceRow = { public_id: string; status: string; created_at?: number }
const devices = ref<DeviceRow[]>([])
const devicesBusy = ref(false)
const devicesErr = ref('')

const licenses = ref<IssuedLicense[]>([])
const licensesBusy = ref(false)
const licensesErr = ref('')
const licenseNote = ref('')
const issuing = ref(false)

const selfDeviceId = computed(() => {
  const hex = vault.devicePublicId
  return hex ? `vk_${hex.slice(0, 40)}` : ''
})

async function copyDeviceId(id: string) {
  if (!id) return
  if (await copyText(id)) flashMsg(t('common.copied'))
}

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return t('settings.adminNever')
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function loadAdminLicenses() {
  if (!vault.isAdmin) {
    licenses.value = []
    return
  }
  licensesBusy.value = true
  licensesErr.value = ''
  try {
    licenses.value = await listAdminLicenses()
  } catch (e) {
    licensesErr.value = e instanceof Error ? e.message : String(e)
    licenses.value = []
  } finally {
    licensesBusy.value = false
  }
}

async function onIssueLicense() {
  issuing.value = true
  licensesErr.value = ''
  try {
    const row = await issueAdminLicense(licenseNote.value)
    licenseNote.value = ''
    flashMsg(t('settings.adminIssued'))
    licenses.value = [row, ...licenses.value.filter((x) => x.id !== row.id)]
    if (await copyText(row.token)) flashMsg(t('settings.adminCopiedToken'))
  } catch (e) {
    flashMsg(e instanceof Error ? e.message : String(e), 'danger')
  } finally {
    issuing.value = false
  }
}

async function onRevokeLicense(row: IssuedLicense) {
  if (!window.confirm(t('settings.adminRevokeConfirm'))) return
  try {
    const updated = await revokeAdminLicense(row.id)
    licenses.value = licenses.value.map((x) => (x.id === updated.id ? updated : x))
    flashMsg(t('settings.adminRevoked'))
  } catch (e) {
    flashMsg(e instanceof Error ? e.message : String(e), 'danger')
  }
}

async function loadDevices() {
  if (vault.deviceStatus === 'pending') {
    devices.value = []
    return
  }
  devicesBusy.value = true
  devicesErr.value = ''
  try {
    devices.value = await vault.listDevices()
  } catch (e) {
    devicesErr.value = e instanceof Error ? e.message : String(e)
    devices.value = []
  } finally {
    devicesBusy.value = false
  }
}

async function onApproveDevice(pid: string) {
  try {
    await vault.approveDevice(pid)
    flashMsg(t('vault.deviceApproved'))
    await loadDevices()
  } catch (e) {
    flashMsg(e instanceof Error ? e.message : String(e), 'danger')
  }
}

async function onRejectDevice(pid: string) {
  if (!window.confirm(t('vault.deviceRejectConfirm'))) return
  try {
    await vault.rejectDevice(pid)
    flashMsg(t('vault.deviceRejected'))
    await loadDevices()
  } catch (e) {
    flashMsg(e instanceof Error ? e.message : String(e), 'danger')
  }
}

async function onRevokeDevice(pid: string) {
  if (!window.confirm(t('vault.deviceRevokeConfirm'))) return
  try {
    await vault.revokeDevice(pid)
    flashMsg(t('vault.deviceRevoked'))
    await loadDevices()
  } catch (e) {
    flashMsg(e instanceof Error ? e.message : String(e), 'danger')
  }
}

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
const viewPw = ref('')
const viewPwErr = ref('')
const awaitingViewPw = ref(false)
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

// Fetch-policy fields are edited as drafts and only committed on save. Binding
// them straight to the store would push every intermediate keystroke through —
// and shrinking retention deletes mail irreversibly.
const lookbackDraft = ref(settings.s.lookbackDays)
const retentionDraft = ref(settings.s.retentionDays)
const concDraft = ref(settings.s.batchConcurrency)
// Drafted too, purely for consistency: it sits in the same panel behind the same
// Save button, and a checkbox that applies instantly next to three inputs that
// do not is a confusing affordance.
const precheckDraft = ref(settings.s.importPrecheck)

// Keep drafts in step with the store when it changes from elsewhere (vault
// hydration, system snapshot import) rather than stranding a stale edit.
watch(
  () =>
    [
      settings.s.lookbackDays,
      settings.s.retentionDays,
      settings.s.batchConcurrency,
      settings.s.importPrecheck,
    ] as const,
  ([lookback, retention, conc, precheck]) => {
    lookbackDraft.value = lookback
    retentionDraft.value = retention
    concDraft.value = conc
    precheckDraft.value = precheck
  },
)

function clampInt(v: unknown, lo: number, hi: number, fallback: number): number {
  // An empty field means "leave this alone". Without this, Number('') === 0
  // would clamp to the lower bound — turning a cleared retention box into a
  // drastic shrink.
  //
  // The fallback goes through the same clamp because it is read from the store,
  // and an imported snapshot can put `undefined` there. An unclamped fallback
  // then returns `undefined`, `undefined < undefined` skips the confirm, and
  // retention ends up silently disabled while the box renders empty.
  const safeFallback = Math.min(hi, Math.max(lo, Math.trunc(Number(fallback)) || lo))
  if (v === '' || v === null || v === undefined) return safeFallback
  const n = Math.trunc(Number(v))
  if (!Number.isFinite(n)) return safeFallback
  return Math.min(hi, Math.max(lo, n))
}

function saveRetention() {
  let nextRetention = clampInt(retentionDraft.value, 7, 365, settings.s.retentionDays)
  if (nextRetention < settings.s.retentionDays) {
    const doomed = mailCache.countPrunedBy(nextRetention, settings.s.retentionDays)
    // null = cache still encrypted, so the exact count is unknown. Warn anyway
    // rather than skipping the prompt for a change that does delete mail later.
    const prompt =
      doomed === null
        ? t('settings.retentionShrinkConfirmUnknown', { days: nextRetention })
        : doomed > 0
          ? t('settings.retentionShrinkConfirm', { n: doomed, days: nextRetention })
          : ''
    if (prompt && !window.confirm(prompt)) {
      // Declining applies to retention only — the other fields on this form
      // were never in question and the user did ask for them to be saved.
      nextRetention = settings.s.retentionDays
    }
  }
  settings.s.lookbackDays = clampInt(lookbackDraft.value, 1, 30, settings.s.lookbackDays)
  settings.s.batchConcurrency = clampInt(concDraft.value, 1, 32, settings.s.batchConcurrency)
  settings.s.importPrecheck = precheckDraft.value
  settings.s.retentionDays = nextRetention
  // Reflect any clamping back into the inputs
  lookbackDraft.value = settings.s.lookbackDays
  retentionDraft.value = settings.s.retentionDays
  concDraft.value = settings.s.batchConcurrency
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
  awaitingViewPw.value = false
  viewPw.value = ''
  viewPwErr.value = ''
}

async function onToggleRecovery() {
  viewPwErr.value = ''
  if (showRecovery.value) {
    showRecovery.value = false
    awaitingViewPw.value = false
    viewPw.value = ''
    return
  }
  if (!awaitingViewPw.value) {
    awaitingViewPw.value = true
    return
  }
  if (!viewPw.value) {
    viewPwErr.value = t('vault.errEmpty')
    return
  }
  const ok = await vault.verifyPassword(viewPw.value)
  if (!ok) {
    viewPwErr.value = t('vault.errBadPassword')
    return
  }
  viewPw.value = ''
  awaitingViewPw.value = false
  showRecovery.value = true
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

onMounted(async () => {
  licenseInput.value = getLicenseToken()
  // Prefer 0 (no idle lock) for existing users who still have old default 30
  // only when key was never customized? Keep stored value as-is.
  lockMin.value = vault.lockMinutes
  void loadConfig()
  await vault.refreshDeviceStatus()
  void loadDevices()
  void loadAdminLicenses()
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
          <input v-model.number="lookbackDraft" class="input" type="number" min="1" max="30" />
          <p class="hint">{{ t('settings.lookbackHint') }}</p>
        </div>
        <div class="field">
          <label class="label">{{ t('settings.retentionDays') }}</label>
          <input v-model.number="retentionDraft" class="input" type="number" min="7" max="365" />
          <p class="hint">{{ t('settings.retentionHint') }}</p>
        </div>
        <div class="field">
          <label class="label">{{ t('settings.batchConc') }}</label>
          <input v-model.number="concDraft" class="input" type="number" min="1" max="32" />
        </div>
        <div class="field">
          <label class="toggle">
            <input v-model="precheckDraft" type="checkbox" />
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

        <div class="field" style="margin-top: 18px">
          <label class="label">{{ t('vault.devicesTitle') }}</label>
          <p class="hint">{{ t('vault.devicesHint') }}</p>
          <div v-if="selfDeviceId" class="this-device">
            <label class="label">{{ t('settings.deviceId') }}</label>
            <p class="hint">{{ t('vault.deviceIdHint') }}</p>
            <div class="device-id-row">
              <code class="device-id">{{ selfDeviceId }}</code>
              <button type="button" class="btn btn-outline btn-sm" @click="copyDeviceId(selfDeviceId)">
                {{ t('common.copy') }}
              </button>
            </div>
          </div>
          <p v-if="vault.deviceStatus === 'pending'" class="hint" style="color: var(--warn, #b45309)">
            {{ t('vault.devicePendingBanner') }}
          </p>
          <template v-else>
            <div class="btn-row" style="margin-bottom: 8px">
              <button
                type="button"
                class="btn btn-outline btn-sm"
                :disabled="devicesBusy"
                @click="loadDevices"
              >
                {{ t('vault.devicesRefresh') }}
              </button>
            </div>
            <p v-if="devicesErr" class="hint" style="color: var(--danger)">{{ devicesErr }}</p>
            <ul v-if="devices.length" class="device-list">
              <li v-for="d in devices" :key="d.public_id" class="device-row">
                <div class="device-meta">
                  <code class="device-id">{{ d.public_id }}</code>
                  <button
                    type="button"
                    class="btn btn-ghost btn-sm"
                    @click="copyDeviceId(d.public_id)"
                  >
                    {{ t('common.copy') }}
                  </button>
                  <span class="device-status" :data-status="d.status">{{
                    d.status === 'pending' ? t('vault.deviceStatusPending') : t('vault.deviceStatusTrusted')
                  }}</span>
                  <span v-if="d.public_id === selfDeviceId" class="device-self">{{
                    t('vault.deviceThisDevice')
                  }}</span>
                </div>
                <div class="btn-row">
                  <button
                    v-if="d.status === 'pending'"
                    type="button"
                    class="btn btn-primary btn-sm"
                    @click="onApproveDevice(d.public_id)"
                  >
                    {{ t('vault.deviceApprove') }}
                  </button>
                  <button
                    v-if="d.status === 'pending'"
                    type="button"
                    class="btn btn-ghost btn-sm"
                    @click="onRejectDevice(d.public_id)"
                  >
                    {{ t('vault.deviceReject') }}
                  </button>
                  <button
                    v-if="d.status === 'trusted' && d.public_id !== selfDeviceId"
                    type="button"
                    class="btn btn-ghost btn-sm"
                    @click="onRevokeDevice(d.public_id)"
                  >
                    {{ t('vault.deviceRevoke') }}
                  </button>
                </div>
              </li>
            </ul>
            <p v-else-if="!devicesBusy" class="hint">{{ t('vault.devicesEmpty') }}</p>
          </template>
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
                  @click="onToggleRecovery"
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
              <div v-if="awaitingViewPw && !showRecovery" class="field" style="margin-top: 8px">
                <label class="label">{{ t('vault.viewRecoveryPassword') }}</label>
                <input
                  v-model="viewPw"
                  class="input"
                  type="password"
                  autocomplete="current-password"
                  @keydown.enter="onToggleRecovery"
                />
                <p v-if="viewPwErr" class="hint" style="color: var(--danger)">{{ viewPwErr }}</p>
                <button
                  type="button"
                  class="btn btn-outline btn-sm"
                  style="margin-top: 8px"
                  @click="onToggleRecovery"
                >
                  {{ t('vault.viewRecovery') }}
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

      <section v-if="vault.isAdmin" class="block">
        <h2>{{ t('settings.adminLicensesTitle') }}</h2>
        <p class="hint">{{ t('settings.adminLicensesHint') }}</p>
        <div class="field">
          <label class="label">{{ t('settings.adminNote') }}</label>
          <input
            v-model="licenseNote"
            class="input"
            type="text"
            maxlength="500"
            :placeholder="t('settings.adminNotePh')"
          />
        </div>
        <div class="btn-row">
          <button
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="issuing"
            @click="onIssueLicense"
          >
            {{ t('settings.adminIssue') }}
          </button>
          <button
            type="button"
            class="btn btn-outline btn-sm"
            :disabled="licensesBusy"
            @click="loadAdminLicenses"
          >
            {{ t('vault.devicesRefresh') }}
          </button>
        </div>
        <p v-if="licensesErr" class="hint" style="color: var(--danger)">{{ licensesErr }}</p>
        <ul v-if="licenses.length" class="license-list">
          <li v-for="row in licenses" :key="row.id" class="license-row">
            <div class="license-head">
              <code class="device-id">{{ row.token }}</code>
              <button type="button" class="btn btn-ghost btn-sm" @click="copyDeviceId(row.token)">
                {{ t('common.copy') }}
              </button>
              <span class="device-status" :data-status="row.revoked_at ? 'pending' : 'trusted'">
                {{ row.revoked_at ? t('settings.adminRevoked') : t('settings.adminActive') }}
              </span>
            </div>
            <p v-if="row.note" class="hint">{{ row.note }}</p>
            <p class="hint">
              {{ t('settings.adminDevices') }}: {{ row.device_count }} ·
              {{ t('settings.adminLastUsed') }}: {{ fmtTs(row.last_used_at) }}
            </p>
            <ul v-if="row.devices.length" class="use-list">
              <li v-for="u in row.devices" :key="u.device_id" class="device-id-row">
                <code class="device-id">{{ u.device_id }}</code>
                <button type="button" class="btn btn-ghost btn-sm" @click="copyDeviceId(u.device_id)">
                  {{ t('common.copy') }}
                </button>
                <span class="hint">{{ fmtTs(u.last_seen_at) }}</span>
              </li>
            </ul>
            <button
              v-if="!row.revoked_at"
              type="button"
              class="btn btn-ghost btn-sm"
              @click="onRevokeLicense(row)"
            >
              {{ t('settings.adminRevoke') }}
            </button>
          </li>
        </ul>
        <p v-else-if="!licensesBusy" class="hint">{{ t('settings.adminEmpty') }}</p>
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
.device-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.device-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.device-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.device-id {
  font-size: 12px;
  word-break: break-all;
  max-width: 100%;
}
.device-id-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;
}
.this-device {
  margin: 10px 0 14px;
}
.license-list {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.license-row {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  display: grid;
  gap: 8px;
}
.license-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.use-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.device-status[data-status='pending'] {
  color: var(--warn, #b45309);
  font-size: 12px;
  font-weight: 600;
}
.device-status[data-status='trusted'] {
  color: var(--success, #15803d);
  font-size: 12px;
  font-weight: 600;
}
.device-self {
  font-size: 11px;
  color: var(--muted);
}
</style>
