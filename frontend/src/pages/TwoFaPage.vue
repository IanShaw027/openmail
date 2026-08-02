<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import jsQR from 'jsqr'
import { useTwoFaStore, type TwoFaEntry } from '@/stores/twofa'
import { copyText } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import UiSelect, { type UiSelectOption } from '@/components/UiSelect.vue'
import TwoFaServiceMark from '@/components/TwoFaServiceMark.vue'
import {
  SERVICE_PRESETS,
  TOTP_ALGORITHMS,
  type TotpAlgorithm,
  type TotpEntryDraft,
  type TotpType,
  isValidBase32Secret,
  normalizeAlgorithm,
  normalizeSecret,
  parseOtpauthUri,
  parseSecretOrUri,
} from '@/utils/totp'
import { normalizeServiceLogoId } from '@/utils/twofaServiceIcons'

const { t } = useI18n()
const twofa = useTwoFaStore()
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
  /** Preset service id; "other" → show custom name field */
  serviceId: 'google',
  /** Custom service name when serviceId === 'other' */
  customName: '',
  /** Account name (email / username) */
  label: '',
  secret: '',
  type: 'totp' as TotpType,
  algorithm: 'SHA1' as TotpAlgorithm,
  digits: 6,
  period: 30,
  counter: 0,
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

const serviceOptions = computed<UiSelectOption[]>(() =>
  SERVICE_PRESETS.map((p) => ({ value: p.id, label: p.name })),
)

const typeOptions = computed<UiSelectOption[]>(() => [
  { value: 'totp', label: 'TOTP' },
  { value: 'steam', label: 'Steam' },
  { value: 'hotp', label: 'HOTP' },
])

const algoOptions = computed<UiSelectOption[]>(() =>
  TOTP_ALGORITHMS.map((a) => ({ value: a, label: a })),
)

const isOtherService = computed(() => form.value.serviceId === 'other')
const isSteam = computed(() => form.value.type === 'steam')
const isHotp = computed(() => form.value.type === 'hotp')

function resetForm() {
  form.value = {
    serviceId: 'google',
    customName: '',
    label: '',
    secret: '',
    type: 'totp',
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
    counter: 0,
  }
  editingId.value = null
}

function openAdd() {
  resetForm()
  showForm.value = true
}

function openEdit(e: TwoFaEntry) {
  editingId.value = e.id
  const preset =
    SERVICE_PRESETS.find((p) => p.issuer && p.issuer.toLowerCase() === (e.issuer || '').toLowerCase()) ||
    SERVICE_PRESETS.find((p) => p.id === e.logo)
  form.value = {
    serviceId: preset?.id || 'other',
    customName: preset && preset.id !== 'other' ? '' : e.issuer || '',
    label: e.label,
    secret: e.secret,
    type: e.type === 'steam' || e.type === 'hotp' || e.type === 'totp' ? e.type : 'totp',
    algorithm: normalizeAlgorithm(e.algorithm),
    digits: e.digits,
    period: e.period,
    counter: e.counter,
  }
  showForm.value = true
}

function onServicePick(v: string | number) {
  const id = String(v)
  form.value.serviceId = id
  const p = SERVICE_PRESETS.find((x) => x.id === id)
  if (id === 'steam') {
    form.value.type = 'steam'
    form.value.algorithm = 'SHA1'
    form.value.digits = 5
  } else if (p?.issuer && form.value.type === 'steam') {
    form.value.type = 'totp'
    form.value.digits = 6
  }
}

function onTypePick(v: string | number) {
  const ty = String(v) as TotpType
  form.value.type = ty
  if (ty === 'steam') {
    form.value.algorithm = 'SHA1'
    form.value.digits = 5
    form.value.period = 30
    if (form.value.serviceId !== 'steam' && form.value.serviceId !== 'other') {
      form.value.serviceId = 'steam'
    }
  } else if (ty === 'totp' && form.value.digits === 5) {
    form.value.digits = 6
  }
}

function resolveIssuer(): string {
  if (form.value.type === 'steam') return 'Steam'
  if (form.value.serviceId === 'other') return form.value.customName.trim()
  return (
    SERVICE_PRESETS.find((p) => p.id === form.value.serviceId)?.issuer ||
    form.value.customName.trim()
  )
}

function saveForm() {
  // Allow raw otpauth in secret field
  let secret = form.value.secret.trim()
  let draftFromUri: TotpEntryDraft | null = null
  if (secret.toLowerCase().includes('otpauth://')) {
    draftFromUri = parseOtpauthUri(secret)
    if (draftFromUri) {
      secret = draftFromUri.secret
      if (!form.value.label) form.value.label = draftFromUri.label
      if (form.value.serviceId === 'other' && !form.value.customName && draftFromUri.issuer) {
        form.value.customName = draftFromUri.issuer
      }
      form.value.type = draftFromUri.type
      form.value.algorithm = draftFromUri.algorithm
      form.value.digits = draftFromUri.digits
      form.value.period = draftFromUri.period
      form.value.counter = draftFromUri.counter
    }
  }
  secret = normalizeSecret(secret)
  if (!isValidBase32Secret(secret)) {
    flashMsg(t('twofa.invalidSecret'), 'danger')
    return
  }
  const issuer = resolveIssuer()
  const label = form.value.label.trim()
  if (!label && !issuer) {
    flashMsg(t('twofa.needLabel'), 'danger')
    return
  }
  if (form.value.serviceId === 'other' && !issuer) {
    flashMsg(t('twofa.needServiceName'), 'danger')
    return
  }
  const type = form.value.type
  const draft: TotpEntryDraft = {
    issuer: issuer || label || 'Account',
    label: label || issuer || 'Account',
    secret,
    type,
    algorithm: type === 'steam' ? 'SHA1' : normalizeAlgorithm(form.value.algorithm),
    digits: type === 'steam' ? 5 : form.value.digits === 8 ? 8 : form.value.digits === 7 ? 7 : 6,
    period: Math.max(15, Number(form.value.period) || 30),
    counter: Math.max(0, Number(form.value.counter) || 0),
  }
  applyDraft(draft)
}

function applyDraft(draft: TotpEntryDraft) {
  const logo =
    form.value.serviceId !== 'other'
      ? form.value.serviceId
      : SERVICE_PRESETS.find((p) => p.issuer.toLowerCase() === draft.issuer.toLowerCase())?.id ||
        'other'
  if (editingId.value) {
    twofa.update(editingId.value, {
      ...draft,
      logo,
    })
    flashMsg(t('twofa.saved'))
  } else {
    twofa.addFromDraft(draft, { logo })
    flashMsg(t('twofa.added'))
  }
  showForm.value = false
  resetForm()
}

function onPasteSecret() {
  const d = parseSecretOrUri(form.value.secret, {
    issuer: resolveIssuer(),
    label: form.value.label,
    type: form.value.type,
  })
  if (!d) {
    flashMsg(t('twofa.invalidSecret'), 'danger')
    return
  }
  form.value.secret = d.secret
  if (d.label) form.value.label = d.label
  form.value.type = d.type
  form.value.algorithm = d.algorithm
  form.value.digits = d.digits
  form.value.period = d.period
  form.value.counter = d.counter
  if (d.issuer) {
    const preset = SERVICE_PRESETS.find(
      (p) => p.issuer && p.issuer.toLowerCase() === d.issuer.toLowerCase(),
    )
    if (preset) form.value.serviceId = preset.id
    else {
      form.value.serviceId = 'other'
      form.value.customName = d.issuer
    }
  }
  if (d.type === 'steam') form.value.serviceId = 'steam'
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
  const cleaned = String(raw || '')
    .replace(/^\uFEFF/, '')
    .trim()
  const d = parseOtpauthUri(cleaned) || parseSecretOrUri(cleaned)
  if (!d) return false
  form.value.secret = d.secret
  form.value.label = d.label || form.value.label
  form.value.type = d.type
  form.value.algorithm = d.algorithm
  form.value.digits = d.digits
  form.value.period = d.period
  form.value.counter = d.counter
  const iss = (d.issuer || '').toLowerCase()
  const preset =
    SERVICE_PRESETS.find((p) => p.issuer && p.issuer.toLowerCase() === iss) ||
    SERVICE_PRESETS.find((p) => p.name.toLowerCase() === iss)
  if (d.type === 'steam') {
    form.value.serviceId = 'steam'
  } else if (preset) {
    form.value.serviceId = preset.id
    form.value.customName = ''
  } else if (d.issuer) {
    form.value.serviceId = 'other'
    form.value.customName = d.issuer
  }
  showForm.value = true
  flashMsg(t('twofa.scanOk'))
  return true
}

function decodeWithJsQR(
  data: Uint8ClampedArray,
  width: number,
  height: number,
): string | null {
  try {
    const code = jsQR(data, width, height, { inversionAttempts: 'attemptBoth' })
    return code?.data || null
  } catch {
    return null
  }
}

/** Prefer native BarcodeDetector when available (Chrome/Android); fall back to jsQR. */
async function decodeQrNative(source: ImageBitmapSource): Promise<string | null> {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const BD = (window as any).BarcodeDetector
    if (!BD) return null
    const detector = new BD({ formats: ['qr_code'] })
    const codes = await detector.detect(source)
    const raw = codes?.[0]?.rawValue
    return typeof raw === 'string' && raw ? raw : null
  } catch {
    return null
  }
}

function drawScaled(
  source: CanvasImageSource,
  srcW: number,
  srcH: number,
  maxSide: number,
): { data: ImageData; w: number; h: number } | null {
  if (!srcW || !srcH) return null
  const scale = Math.min(1, maxSide / Math.max(srcW, srcH))
  const dw = Math.max(1, Math.floor(srcW * scale))
  const dh = Math.max(1, Math.floor(srcH * scale))
  const canvas = document.createElement('canvas')
  canvas.width = dw
  canvas.height = dh
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.imageSmoothingEnabled = false
  ctx.drawImage(source, 0, 0, dw, dh)
  return { data: ctx.getImageData(0, 0, dw, dh), w: dw, h: dh }
}

async function decodeQrFromImage(img: HTMLImageElement | ImageBitmap): Promise<string | null> {
  const w = 'naturalWidth' in img ? img.naturalWidth || img.width : img.width
  const h = 'naturalHeight' in img ? img.naturalHeight || img.height : img.height
  if (!w || !h) return null

  const native = await decodeQrNative(img)
  if (native) return native

  // Multi-scale jsQR (small screenshots + high-res photos)
  for (const maxSide of [480, 720, 1080, 1600, 2400]) {
    if (maxSide > Math.max(w, h) * 1.1 && maxSide !== 480) continue
    const drawn = drawScaled(img, w, h, maxSide)
    if (!drawn) continue
    const hit = decodeWithJsQR(drawn.data.data, drawn.w, drawn.h)
    if (hit) return hit
  }
  return null
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    flashMsg(t('twofa.cameraDenied'), 'danger')
    if (isMobile.value) openImagePicker()
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
    await new Promise((r) => setTimeout(r, 80))
    const video = videoEl.value
    if (video) {
      video.setAttribute('playsinline', 'true')
      video.setAttribute('webkit-playsinline', 'true')
      video.muted = true
      video.srcObject = stream
      // Wait until we actually have frames
      await new Promise<void>((resolve) => {
        const done = () => {
          video.removeEventListener('loadeddata', done)
          resolve()
        }
        if (video.readyState >= 2) resolve()
        else video.addEventListener('loadeddata', done)
        setTimeout(done, 1500)
      })
      try {
        await video.play()
      } catch {
        /* autoplay policies — user gesture already happened */
      }
      scanLoop()
    }
  } catch {
    flashMsg(t('twofa.cameraDenied'), 'danger')
    cameraOn.value = false
    // 移动端相机失败时仍可落到相册选图
    if (isMobile.value) openImagePicker()
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

let scanBusy = false
async function scanLoop() {
  const video = videoEl.value
  const canvas = canvasEl.value
  if (!video || !canvas || !cameraOn.value) return
  const w = video.videoWidth
  const h = video.videoHeight
  if (w && h && !scanBusy) {
    scanBusy = true
    try {
      // Native path on capable browsers
      const native = await decodeQrNative(video)
      if (native && applyParsedDraft(native)) {
        stopCamera()
        return
      }
      const maxSide = 720
      const scale = Math.min(1, maxSide / Math.max(w, h))
      const dw = Math.max(1, Math.floor(w * scale))
      const dh = Math.max(1, Math.floor(h * scale))
      canvas.width = dw
      canvas.height = dh
      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      if (ctx) {
        ctx.drawImage(video, 0, 0, dw, dh)
        // Center crop (scan frame region) for higher hit-rate
        const crop = 0.55
        const cw = Math.floor(dw * crop)
        const ch = Math.floor(dh * crop)
        const sx = Math.floor((dw - cw) / 2)
        const sy = Math.floor((dh - ch) / 2)
        const full = ctx.getImageData(0, 0, dw, dh)
        let hit = decodeWithJsQR(full.data, dw, dh)
        if (!hit) {
          const region = ctx.getImageData(sx, sy, cw, ch)
          hit = decodeWithJsQR(region.data, cw, ch)
        }
        if (hit && applyParsedDraft(hit)) {
          stopCamera()
          return
        }
      }
    } finally {
      scanBusy = false
    }
  }
  raf = requestAnimationFrame(() => {
    void scanLoop()
  })
}

async function onFileQr(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    // createImageBitmap handles more formats / orientations than Image()
    let source: HTMLImageElement | ImageBitmap | null = null
    try {
      source = await createImageBitmap(file)
    } catch {
      source = await new Promise<HTMLImageElement | null>((resolve) => {
        const url = URL.createObjectURL(file)
        const img = new Image()
        img.onload = () => {
          URL.revokeObjectURL(url)
          resolve(img)
        }
        img.onerror = () => {
          URL.revokeObjectURL(url)
          resolve(null)
        }
        img.src = url
      })
    }
    if (!source) {
      flashMsg(t('twofa.scanFail'), 'danger')
      return
    }
    const raw = await decodeQrFromImage(source)
    if ('close' in source && typeof source.close === 'function') source.close()
    if (raw && applyParsedDraft(raw)) return
    flashMsg(t('twofa.scanFail'), 'danger')
  } catch {
    flashMsg(t('twofa.scanFail'), 'danger')
  } finally {
    input.value = ''
  }
}

function openImagePicker() {
  fileInputEl.value?.click()
}

function displayName(e: TwoFaEntry) {
  if (e.issuer && e.label) return `${e.issuer} · ${e.label}`
  return e.issuer || e.label
}

function serviceLogoKey(e: TwoFaEntry): string {
  return normalizeServiceLogoId(e.logo || e.issuer)
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
        <!-- Web + H5: live camera + file/album both always available -->
        <button
          type="button"
          class="btn btn-outline btn-sm"
          @click="cameraOn ? stopCamera() : startCamera()"
        >
          {{ cameraOn ? t('twofa.stopScan') : t('twofa.scanWebcam') }}
        </button>
        <button type="button" class="btn btn-outline btn-sm" @click="openImagePicker">
          {{ isMobile ? t('twofa.scanAlbum') : t('twofa.scanFile') }}
        </button>
        <!-- No capture attr: album / file picker on H5 & desktop -->
        <input
          ref="fileInputEl"
          type="file"
          accept="image/*"
          class="sr-only"
          @change="onFileQr"
        />
      </div>
    </div>

    <!-- Camera panel sits above modal so form-triggered scan still works -->
    <div
      v-if="cameraOn"
      class="camera card-solid"
      :class="{ 'camera-mobile': isMobile, 'camera-overlay': showForm }"
    >
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
          <TwoFaServiceMark :logo="serviceLogoKey(e)" :issuer="e.issuer" :size="40" />
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
          <span v-if="e.type !== 'hotp'" class="remain">
            <span class="bar" :style="{ width: `${(twofa.remainingFor(e) / e.period) * 100}%` }" />
            <span class="sec">{{ twofa.remainingFor(e) }}s</span>
          </span>
        </button>
      </article>
    </div>

    <!-- Add / edit modal — simplified fields -->
    <div v-if="showForm" class="modal-backdrop" @click.self="showForm = false">
      <div class="modal card-solid twofa-modal">
        <header class="modal-head">
          <h2>{{ editingId ? t('twofa.editTitle') : t('twofa.addTitle') }}</h2>
          <button type="button" class="btn btn-ghost btn-sm" @click="showForm = false">
            {{ t('common.close') }}
          </button>
        </header>
        <div class="modal-body">
          <!-- 服务类型：仅 Other 需填名称 -->
          <div class="field">
            <label class="label">{{ t('twofa.service') }}</label>
            <div class="service-pick">
              <TwoFaServiceMark :logo="form.serviceId" :size="36" />
              <UiSelect
                :model-value="form.serviceId"
                :options="serviceOptions"
                @update:model-value="onServicePick"
              />
            </div>
          </div>
          <div v-if="isOtherService" class="field">
            <label class="label">{{ t('twofa.serviceName') }}</label>
            <input
              v-model="form.customName"
              class="input"
              type="text"
              :placeholder="t('twofa.serviceNamePh')"
            />
          </div>
          <!-- 账户名称 -->
          <div class="field">
            <label class="label">{{ t('twofa.label') }}</label>
            <input
              v-model="form.label"
              class="input"
              type="text"
              :placeholder="t('twofa.labelPh')"
              autocomplete="username"
            />
          </div>
          <!-- 密钥 + 扫码（Web / H5 均支持摄像头与选图） -->
          <div class="field">
            <label class="label">{{ t('twofa.secret') }}</label>
            <textarea
              v-model="form.secret"
              class="textarea"
              rows="2"
              :placeholder="t('twofa.secretPh')"
              autocomplete="off"
              autocapitalize="off"
              spellcheck="false"
            />
            <div class="secret-acts">
              <button type="button" class="btn btn-ghost btn-xs" @click="onPasteSecret">
                {{ t('twofa.parseSecret') }}
              </button>
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="cameraOn ? stopCamera() : startCamera()"
              >
                {{ cameraOn ? t('twofa.stopScan') : t('twofa.scanWebcam') }}
              </button>
              <button type="button" class="btn btn-ghost btn-xs" @click="openImagePicker">
                {{ isMobile ? t('twofa.scanAlbum') : t('twofa.scanFile') }}
              </button>
            </div>
          </div>
          <!-- 令牌类型 + 算法 -->
          <div class="field-row">
            <div class="field">
              <label class="label">{{ t('twofa.type') }}</label>
              <UiSelect
                :model-value="form.type"
                :options="typeOptions"
                @update:model-value="onTypePick"
              />
            </div>
            <div class="field">
              <label class="label">{{ t('twofa.algorithm') }}</label>
              <UiSelect
                :model-value="form.algorithm"
                :options="algoOptions"
                :disabled="isSteam"
                mono
                @update:model-value="(v) => (form.algorithm = normalizeAlgorithm(String(v)))"
              />
            </div>
          </div>
          <div v-if="isHotp" class="field">
            <label class="label">{{ t('twofa.counter') }}</label>
            <input v-model.number="form.counter" class="input" type="number" min="0" />
          </div>
          <p v-if="isSteam" class="hint">{{ t('twofa.steamHint') }}</p>
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
/* When add/edit modal is open, float camera above modal (z-modal=100) */
.camera-overlay {
  position: fixed;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: calc(var(--z-modal, 100) + 20);
  width: min(480px, calc(100vw - 24px));
  max-height: min(80vh, 640px);
  box-shadow: var(--shadow-lg);
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
.service-pick {
  display: flex;
  align-items: center;
  gap: 10px;
}
.service-pick :deep(.ui-select) {
  flex: 1;
  min-width: 0;
}
.secret-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.twofa-modal {
  width: min(440px, 100%);
  /* Keep dropdowns above modal chrome */
  position: relative;
  z-index: 1;
  overflow: visible;
}
.twofa-modal .modal-body {
  overflow: visible;
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
  .twofa-modal {
    width: 100%;
    max-height: min(92vh, 900px);
  }
}
</style>
