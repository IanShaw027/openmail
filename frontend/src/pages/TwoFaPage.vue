<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import jsQR from 'jsqr'
import { useTwoFaStore, type TwoFaEntry } from '@/stores/twofa'
import { useAccountsStore } from '@/stores/accounts'
import { copyText } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import UiSelect, { type UiSelectOption } from '@/components/UiSelect.vue'
import {
  SERVICE_PRESETS,
  type TotpAlgorithm,
  type TotpEntryDraft,
  type TotpType,
  isValidBase32Secret,
  normalizeSecret,
  parseOtpauthUri,
  parseSecretOrUri,
} from '@/utils/totp'

const { t } = useI18n()
const twofa = useTwoFaStore()
const accounts = useAccountsStore()
const { flashMsg } = useToast()

const q = ref('')
const showForm = ref(false)
const editingId = ref<string | null>(null)
const importText = ref('')
const showImport = ref(false)
const cameraOn = ref(false)
const videoEl = ref<HTMLVideoElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)
/** 手机 / 平板：优先相机直扫；桌面：优先上传图片 */
const isMobile = ref(false)
let stream: MediaStream | null = null
let raf = 0
let mediaQuery: MediaQueryList | null = null

function detectMobile() {
  if (typeof window === 'undefined') return false
  const coarse = window.matchMedia('(pointer: coarse)').matches
  const narrow = window.matchMedia('(max-width: 768px)').matches
  const ua = /Android|iPhone|iPad|iPod|Mobile|webOS|BlackBerry|IEMobile|Opera Mini/i.test(
    navigator.userAgent,
  )
  // iPadOS 桌面 UA 时仍可走触控判定
  const touchTablet = navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent)
  return coarse || narrow || ua || touchTablet
}

function syncMobileFlag() {
  isMobile.value = detectMobile()
}

const form = ref({
  issuer: '',
  label: '',
  secret: '',
  type: 'totp' as TotpType,
  algorithm: 'SHA1' as TotpAlgorithm,
  digits: 6,
  period: 30,
  counter: 0,
  serviceId: 'other',
  accountId: '',
})

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  if (!s) return twofa.sorted
  return twofa.sorted.filter((e) =>
    [e.issuer, e.label, e.accountEmail, e.secret]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(s),
  )
})

const accountOptions = computed<UiSelectOption[]>(() => [
  { value: '', label: t('twofa.noBind') },
  ...accounts.accounts
    .filter((a) => !a.isApiSource)
    .map((a) => ({
      value: a.id,
      label: a.email,
      title: a.email,
    })),
])

const serviceOptions = computed<UiSelectOption[]>(() =>
  SERVICE_PRESETS.map((p) => ({ value: p.id, label: p.name })),
)

const typeOptions = computed<UiSelectOption[]>(() => [
  { value: 'totp', label: 'TOTP' },
  { value: 'hotp', label: 'HOTP' },
])

const algoOptions = computed<UiSelectOption[]>(() => [
  { value: 'SHA1', label: 'SHA1' },
  { value: 'SHA256', label: 'SHA256' },
  { value: 'SHA512', label: 'SHA512' },
])

const digitsOptions = computed<UiSelectOption[]>(() => [
  { value: 6, label: '6' },
  { value: 8, label: '8' },
])

function resetForm() {
  form.value = {
    issuer: '',
    label: '',
    secret: '',
    type: 'totp',
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
    counter: 0,
    serviceId: 'other',
    accountId: '',
  }
  editingId.value = null
}

function openAdd() {
  resetForm()
  showForm.value = true
}

function openEdit(e: TwoFaEntry) {
  editingId.value = e.id
  form.value = {
    issuer: e.issuer,
    label: e.label,
    secret: e.secret,
    type: e.type,
    algorithm: e.algorithm,
    digits: e.digits,
    period: e.period,
    counter: e.counter,
    serviceId: SERVICE_PRESETS.find((p) => p.issuer === e.issuer)?.id || 'other',
    accountId: e.accountId || '',
  }
  showForm.value = true
}

function onServicePick(v: string | number) {
  const id = String(v)
  form.value.serviceId = id
  const p = SERVICE_PRESETS.find((x) => x.id === id)
  if (p && p.issuer) form.value.issuer = p.issuer
}

function saveForm() {
  const secret = normalizeSecret(form.value.secret)
  if (!isValidBase32Secret(secret) && !form.value.secret.toLowerCase().startsWith('otpauth://')) {
    // try parse as uri
    const fromUri = parseOtpauthUri(form.value.secret)
    if (!fromUri) {
      flashMsg(t('twofa.invalidSecret'), 'danger')
      return
    }
    applyDraft(fromUri)
    return
  }
  if (!form.value.label.trim() && !form.value.issuer.trim()) {
    flashMsg(t('twofa.needLabel'), 'danger')
    return
  }
  const draft: TotpEntryDraft = {
    issuer: form.value.issuer.trim(),
    label: form.value.label.trim() || form.value.issuer.trim() || 'Account',
    secret,
    type: form.value.type,
    algorithm: form.value.algorithm,
    digits: form.value.digits === 8 ? 8 : 6,
    period: Math.max(15, Number(form.value.period) || 30),
    counter: Math.max(0, Number(form.value.counter) || 0),
  }
  applyDraft(draft)
}

function applyDraft(draft: TotpEntryDraft) {
  const acc = form.value.accountId
    ? accounts.findById(form.value.accountId)
    : undefined
  if (editingId.value) {
    twofa.update(editingId.value, {
      ...draft,
      accountId: acc?.id,
      accountEmail: acc?.email,
      logo: form.value.serviceId,
    })
    flashMsg(t('twofa.saved'))
  } else {
    twofa.addFromDraft(draft, {
      accountId: acc?.id,
      accountEmail: acc?.email,
      logo: form.value.serviceId,
    })
    flashMsg(t('twofa.added'))
  }
  showForm.value = false
  resetForm()
}

function onPasteSecret() {
  const d = parseSecretOrUri(form.value.secret, {
    issuer: form.value.issuer,
    label: form.value.label,
  })
  if (!d) {
    flashMsg(t('twofa.invalidSecret'), 'danger')
    return
  }
  form.value.secret = d.secret
  if (d.issuer) form.value.issuer = d.issuer
  if (d.label) form.value.label = d.label
  form.value.type = d.type
  form.value.algorithm = d.algorithm
  form.value.digits = d.digits
  form.value.period = d.period
  form.value.counter = d.counter
  flashMsg(t('twofa.secretParsed'))
}

async function copyCode(e: TwoFaEntry) {
  const code = twofa.codeFor(e)
  if (await copyText(code)) flashMsg(t('common.copied'))
}

function removeEntry(e: TwoFaEntry) {
  if (!window.confirm(t('twofa.deleteConfirm', { name: e.issuer || e.label }))) return
  twofa.remove(e.id)
  flashMsg(t('twofa.deleted'))
}

function doImport() {
  const r = twofa.importText(importText.value)
  flashMsg(t('twofa.importResult', { ok: r.ok, fail: r.fail }), r.fail ? 'danger' : undefined)
  if (r.ok) {
    showImport.value = false
    importText.value = ''
  }
}

function exportUris() {
  const text = twofa.exportText()
  downloadBlob(text, `openmail-2fa-${Date.now()}.txt`, 'text/plain')
  flashMsg(t('twofa.exported'))
}

function exportJson() {
  const text = twofa.exportJson()
  downloadBlob(text, `openmail-2fa-${Date.now()}.json`, 'application/json')
  flashMsg(t('twofa.exported'))
}

function downloadBlob(text: string, name: string, type: string) {
  const blob = new Blob([text], { type: `${type};charset=utf-8` })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

function applyParsedDraft(raw: string): boolean {
  const d = parseOtpauthUri(raw) || parseSecretOrUri(raw)
  if (!d) return false
  form.value.secret = d.secret
  form.value.issuer = d.issuer || form.value.issuer
  form.value.label = d.label || form.value.label
  form.value.type = d.type
  form.value.algorithm = d.algorithm
  form.value.digits = d.digits
  form.value.period = d.period
  form.value.counter = d.counter
  showForm.value = true
  flashMsg(t('twofa.scanOk'))
  return true
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    flashMsg(t('twofa.cameraDenied'), 'danger')
    return
  }
  try {
    // 后置优先；失败再退到任意摄像头
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      })
    } catch {
      stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: false,
      })
    }
    cameraOn.value = true
    await new Promise((r) => setTimeout(r, 50))
    if (videoEl.value) {
      videoEl.value.setAttribute('playsinline', 'true')
      videoEl.value.muted = true
      videoEl.value.srcObject = stream
      await videoEl.value.play()
      scanLoop()
    }
  } catch {
    flashMsg(t('twofa.cameraDenied'), 'danger')
    cameraOn.value = false
    // 移动端相机失败时仍可落到相册选图
    if (isMobile.value) fileInputEl.value?.click()
  }
}

function stopCamera() {
  cameraOn.value = false
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
  if (videoEl.value) videoEl.value.srcObject = null
}

function scanLoop() {
  const video = videoEl.value
  const canvas = canvasEl.value
  if (!video || !canvas || !cameraOn.value) return
  const w = video.videoWidth
  const h = video.videoHeight
  if (w && h) {
    // 限制解码分辨率，兼顾移动端性能
    const maxSide = 720
    const scale = Math.min(1, maxSide / Math.max(w, h))
    const dw = Math.max(1, Math.floor(w * scale))
    const dh = Math.max(1, Math.floor(h * scale))
    canvas.width = dw
    canvas.height = dh
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (ctx) {
      ctx.drawImage(video, 0, 0, dw, dh)
      const img = ctx.getImageData(0, 0, dw, dh)
      const code = jsQR(img.data, dw, dh, { inversionAttempts: 'attemptBoth' })
      if (code?.data && applyParsedDraft(code.data)) {
        stopCamera()
        return
      }
    }
  }
  raf = requestAnimationFrame(scanLoop)
}

function decodeQrFromImage(img: HTMLImageElement | ImageBitmap): string | null {
  const w = 'naturalWidth' in img ? img.naturalWidth || img.width : img.width
  const h = 'naturalHeight' in img ? img.naturalHeight || img.height : img.height
  if (!w || !h) return null
  const maxSide = 1600
  const scale = Math.min(1, maxSide / Math.max(w, h))
  const dw = Math.max(1, Math.floor(w * scale))
  const dh = Math.max(1, Math.floor(h * scale))
  const canvas = document.createElement('canvas')
  canvas.width = dw
  canvas.height = dh
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.drawImage(img, 0, 0, dw, dh)
  const data = ctx.getImageData(0, 0, dw, dh)
  const code = jsQR(data.data, dw, dh, { inversionAttempts: 'attemptBoth' })
  return code?.data || null
}

function onFileQr(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const url = URL.createObjectURL(file)
  const img = new Image()
  img.onload = () => {
    try {
      const raw = decodeQrFromImage(img)
      if (raw && applyParsedDraft(raw)) return
      flashMsg(t('twofa.scanFail'), 'danger')
    } finally {
      URL.revokeObjectURL(url)
      input.value = ''
    }
  }
  img.onerror = () => {
    URL.revokeObjectURL(url)
    input.value = ''
    flashMsg(t('twofa.scanFail'), 'danger')
  }
  img.src = url
}

function openImagePicker() {
  fileInputEl.value?.click()
}

function displayName(e: TwoFaEntry) {
  if (e.issuer && e.label) return `${e.issuer} · ${e.label}`
  return e.issuer || e.label
}

function logoLetter(e: TwoFaEntry) {
  return (e.issuer || e.label || '?').slice(0, 1).toUpperCase()
}

onMounted(() => {
  syncMobileFlag()
  mediaQuery = window.matchMedia('(max-width: 768px)')
  mediaQuery.addEventListener('change', syncMobileFlag)
  twofa.startTicker()
})
onUnmounted(() => {
  mediaQuery?.removeEventListener('change', syncMobileFlag)
  twofa.stopTicker()
  stopCamera()
})
</script>

<template>
  <div class="twofa-page">
    <div class="toolbar card-solid">
      <input v-model="q" class="input search" type="search" :placeholder="t('twofa.search')" />
      <div class="toolbar-acts">
        <button type="button" class="btn btn-primary btn-sm" @click="openAdd">
          {{ t('twofa.add') }}
        </button>
        <button type="button" class="btn btn-outline btn-sm" @click="showImport = true">
          {{ t('twofa.import') }}
        </button>
        <button type="button" class="btn btn-outline btn-sm" @click="exportUris">
          {{ t('twofa.exportUri') }}
        </button>
        <button type="button" class="btn btn-ghost btn-sm" @click="exportJson">
          {{ t('twofa.exportJson') }}
        </button>
        <!-- 移动端：相机直扫为主；桌面：上传图片为主 -->
        <button
          v-if="isMobile"
          type="button"
          class="btn btn-outline btn-sm"
          @click="cameraOn ? stopCamera() : startCamera()"
        >
          {{ cameraOn ? t('twofa.stopScan') : t('twofa.scan') }}
        </button>
        <button
          v-else
          type="button"
          class="btn btn-outline btn-sm"
          @click="openImagePicker"
        >
          {{ t('twofa.scanFile') }}
        </button>
        <!-- 次要入口：移动端也可从相册选图；桌面仍可开摄像头（极少用） -->
        <button
          v-if="isMobile"
          type="button"
          class="btn btn-ghost btn-sm"
          @click="openImagePicker"
        >
          {{ t('twofa.scanAlbum') }}
        </button>
        <button
          v-else
          type="button"
          class="btn btn-ghost btn-sm"
          @click="cameraOn ? stopCamera() : startCamera()"
        >
          {{ cameraOn ? t('twofa.stopScan') : t('twofa.scanWebcam') }}
        </button>
        <input
          ref="fileInputEl"
          type="file"
          accept="image/*"
          class="sr-only"
          @change="onFileQr"
        />
      </div>
    </div>

    <div v-if="cameraOn" class="camera card-solid" :class="{ 'camera-mobile': isMobile }">
      <div class="video-wrap">
        <video ref="videoEl" class="video" playsinline muted autoplay />
        <div class="scan-frame" aria-hidden="true" />
      </div>
      <canvas ref="canvasEl" class="sr-only" />
      <div class="camera-bar">
        <p class="hint">{{ t('twofa.scanHint') }}</p>
        <button type="button" class="btn btn-ghost btn-sm" @click="stopCamera">
          {{ t('twofa.stopScan') }}
        </button>
      </div>
    </div>

    <div v-if="!filtered.length" class="empty card-solid">
      <div class="empty-title">{{ t('twofa.emptyTitle') }}</div>
      <div class="empty-desc">{{ t('twofa.emptyDesc') }}</div>
    </div>

    <div v-else class="grid">
      <article v-for="e in filtered" :key="e.id" class="card-solid entry">
        <div class="entry-top">
          <div class="logo" :data-logo="e.logo || 'other'">{{ logoLetter(e) }}</div>
          <div class="meta">
            <div class="name">{{ displayName(e) }}</div>
            <div class="sub muted">
              {{ e.type.toUpperCase() }} · {{ e.algorithm }} · {{ e.digits }}
              <template v-if="e.accountEmail"> · ★ {{ e.accountEmail }}</template>
            </div>
          </div>
          <div class="entry-acts">
            <button type="button" class="btn btn-ghost btn-xs" @click="openEdit(e)">
              {{ t('twofa.edit') }}
            </button>
            <button type="button" class="btn btn-ghost btn-xs act-del" @click="removeEntry(e)">
              {{ t('common.delete') }}
            </button>
          </div>
        </div>
        <button type="button" class="code-row" @click="copyCode(e)">
          <span class="code">{{ twofa.codeFor(e) }}</span>
          <span v-if="e.type === 'totp'" class="remain">
            <span class="bar" :style="{ width: `${(twofa.remainingFor(e) / e.period) * 100}%` }" />
            <span class="sec">{{ twofa.remainingFor(e) }}s</span>
          </span>
        </button>
      </article>
    </div>

    <!-- Add / edit modal -->
    <div v-if="showForm" class="modal-backdrop" @click.self="showForm = false">
      <div class="modal card-solid">
        <header class="modal-head">
          <h2>{{ editingId ? t('twofa.editTitle') : t('twofa.addTitle') }}</h2>
          <button type="button" class="btn btn-ghost btn-sm" @click="showForm = false">
            {{ t('common.close') }}
          </button>
        </header>
        <div class="modal-body">
          <div class="field">
            <label class="label">{{ t('twofa.service') }}</label>
            <UiSelect
              :model-value="form.serviceId"
              :options="serviceOptions"
              @update:model-value="onServicePick"
            />
          </div>
          <div class="field">
            <label class="label">{{ t('twofa.issuer') }}</label>
            <input v-model="form.issuer" class="input" type="text" />
          </div>
          <div class="field">
            <label class="label">{{ t('twofa.label') }}</label>
            <input v-model="form.label" class="input" type="text" :placeholder="t('twofa.labelPh')" />
          </div>
          <div class="field">
            <label class="label">{{ t('twofa.secret') }}</label>
            <textarea
              v-model="form.secret"
              class="textarea"
              rows="2"
              :placeholder="t('twofa.secretPh')"
            />
            <button type="button" class="btn btn-ghost btn-xs" style="margin-top: 6px" @click="onPasteSecret">
              {{ t('twofa.parseSecret') }}
            </button>
          </div>
          <div class="field-row">
            <div class="field">
              <label class="label">{{ t('twofa.type') }}</label>
              <UiSelect v-model="form.type" :options="typeOptions" />
            </div>
            <div class="field">
              <label class="label">{{ t('twofa.algorithm') }}</label>
              <UiSelect v-model="form.algorithm" :options="algoOptions" />
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <label class="label">{{ t('twofa.digits') }}</label>
              <UiSelect
                :model-value="form.digits"
                :options="digitsOptions"
                @update:model-value="(v) => (form.digits = Number(v))"
              />
            </div>
            <div v-if="form.type === 'totp'" class="field">
              <label class="label">{{ t('twofa.period') }}</label>
              <input v-model.number="form.period" class="input" type="number" min="15" max="120" />
            </div>
            <div v-else class="field">
              <label class="label">{{ t('twofa.counter') }}</label>
              <input v-model.number="form.counter" class="input" type="number" min="0" />
            </div>
          </div>
          <div class="field">
            <label class="label">{{ t('twofa.bindAccount') }}</label>
            <UiSelect v-model="form.accountId" :options="accountOptions" mono />
            <p class="hint">{{ t('twofa.bindHint') }}</p>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-primary" @click="saveForm">
              {{ t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Bulk import -->
    <div v-if="showImport" class="modal-backdrop" @click.self="showImport = false">
      <div class="modal card-solid">
        <header class="modal-head">
          <h2>{{ t('twofa.import') }}</h2>
          <button type="button" class="btn btn-ghost btn-sm" @click="showImport = false">
            {{ t('common.close') }}
          </button>
        </header>
        <div class="modal-body">
          <p class="hint">{{ t('twofa.importHint') }}</p>
          <textarea v-model="importText" class="textarea" rows="8" :placeholder="t('twofa.importPh')" />
          <div class="btn-row" style="margin-top: 12px">
            <button type="button" class="btn btn-primary" @click="doImport">
              {{ t('twofa.import') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.twofa-page {
  padding: 12px 16px 32px;
  max-width: 1100px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-sizing: border-box;
  min-height: calc(100vh - var(--nav-h, 56px));
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px;
  align-items: center;
}
.search {
  flex: 1 1 200px;
  min-width: 160px;
  max-width: 360px;
}
.toolbar-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.camera {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.camera-mobile {
  position: sticky;
  top: 8px;
  z-index: 5;
}
.video-wrap {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  background: #000;
}
.video {
  width: 100%;
  max-height: min(56vh, 420px);
  display: block;
  object-fit: cover;
  background: #000;
}
.scan-frame {
  pointer-events: none;
  position: absolute;
  inset: 18% 16%;
  border: 2px solid rgba(255, 255, 255, 0.85);
  border-radius: 16px;
  box-shadow: 0 0 0 999px rgba(0, 0, 0, 0.28);
}
.camera-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.camera-bar .hint {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.entry {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.entry-top {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.logo {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  display: grid;
  place-items: center;
  font-weight: 750;
  font-size: 16px;
  flex-shrink: 0;
}
.meta {
  flex: 1;
  min-width: 0;
}
.name {
  font-weight: 700;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub {
  font-size: 11px;
  margin-top: 2px;
}
.entry-acts {
  display: flex;
  gap: 2px;
}
.code-row {
  appearance: none;
  border: 0;
  background: var(--panel-soft);
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  cursor: pointer;
  width: 100%;
  text-align: left;
}
.code-row:hover {
  background: var(--accent-soft);
}
.code {
  font-family: var(--mono);
  font-size: 26px;
  font-weight: 750;
  letter-spacing: 0.12em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.remain {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  min-width: 48px;
}
.bar {
  height: 4px;
  border-radius: 4px;
  background: var(--accent);
  transition: width 0.2s linear;
  min-width: 4px;
  max-width: 48px;
  align-self: stretch;
}
.sec {
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.act-del {
  color: var(--danger) !important;
}
.muted {
  color: var(--muted);
}
@media (max-width: 600px) {
  .field-row {
    grid-template-columns: 1fr;
  }
  .twofa-page {
    padding: 10px;
  }
}
</style>
