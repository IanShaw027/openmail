<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import jsQR from 'jsqr'
import { useTwoFaStore, type TwoFaEntry } from '@/stores/twofa'
import { useVaultStore } from '@/stores/vault'
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
const vault = useVaultStore()
const { flashMsg } = useToast()

const q = ref('')
/** Service logo id filter; `all` = no filter */
const serviceFilter = ref<string>('all')
const showForm = ref(false)
const editingId = ref<string | null>(null)
const importText = ref('')
const showImport = ref(false)
const exportPw = ref('')
const exportErr = ref('')
const cameraOn = ref(false)
const videoEl = ref<HTMLVideoElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)
/** 手机 / 平板：优先相机直扫；桌面：优先上传图片 */
const isMobile = ref(false)
/** Drag-reorder state */
const dragId = ref<string | null>(null)
const dropTargetId = ref<string | null>(null)
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

function serviceKey(e: TwoFaEntry): string {
  return normalizeServiceLogoId(e.logo || e.issuer)
}

/** Chips: only services that appear in the vault (plus All). */
const serviceFilterChips = computed(() => {
  const counts = new Map<string, number>()
  for (const e of twofa.entries) {
    const k = serviceKey(e)
    counts.set(k, (counts.get(k) || 0) + 1)
  }
  const chips: Array<{ id: string; name: string; count: number }> = [
    { id: 'all', name: t('twofa.filterAll'), count: twofa.entries.length },
  ]
  const presetName = (id: string) =>
    SERVICE_PRESETS.find((p) => p.id === id)?.name || id
  const ids = [...counts.keys()].sort((a, b) => {
    if (a === 'other') return 1
    if (b === 'other') return -1
    return presetName(a).localeCompare(presetName(b))
  })
  for (const id of ids) {
    chips.push({ id, name: presetName(id), count: counts.get(id) || 0 })
  }
  return chips
})

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  const brand = serviceFilter.value
  return twofa.sorted.filter((e) => {
    if (brand !== 'all' && serviceKey(e) !== brand) return false
    if (!s) return true
    return [e.issuer, e.label, e.accountEmail, e.secret]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(s)
  })
})

/** Drag when not text-searching (service filter still allows reorder within view). */
const canReorder = computed(() => !q.value.trim() && filtered.value.length > 1)

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

async function requireExportPassword(): Promise<boolean> {
  exportErr.value = ''
  if (!exportPw.value) {
    exportErr.value = t('twofa.exportNeedPassword')
    return false
  }
  const ok = await vault.verifyPassword(exportPw.value)
  if (!ok) {
    exportErr.value = t('vault.errBadPassword')
    return false
  }
  exportPw.value = ''
  return true
}

async function exportUris() {
  if (!(await requireExportPassword())) return
  const text = twofa.exportText()
  downloadBlob(text, `openmail-2fa-${Date.now()}.txt`, 'text/plain')
  flashMsg(t('twofa.exported'))
}

async function exportJson() {
  if (!(await requireExportPassword())) return
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

function entrySubTitle(e: TwoFaEntry) {
  const parts = [`${e.type.toUpperCase()} · ${e.algorithm} · ${e.digits}`]
  if (e.accountEmail) parts.push(e.accountEmail)
  return parts.join(' · ')
}

function serviceLogoKey(e: TwoFaEntry): string {
  return serviceKey(e)
}

function remainPct(e: TwoFaEntry): number {
  if (e.type === 'hotp' || !e.period) return 0
  const r = twofa.remainingFor(e)
  return Math.max(0, Math.min(100, (r / e.period) * 100))
}

function remainUrgent(e: TwoFaEntry): boolean {
  return e.type !== 'hotp' && twofa.remainingFor(e) <= 5
}

function remainWarn(e: TwoFaEntry): boolean {
  return e.type !== 'hotp' && twofa.remainingFor(e) <= 10 && !remainUrgent(e)
}

/* ── Drag & drop reorder (handle-only, HTML5, no deps) ────────── */

function onHandleDragStart(e: DragEvent, entry: TwoFaEntry) {
  if (!canReorder.value) {
    e.preventDefault()
    return
  }
  dragId.value = entry.id
  dropTargetId.value = null
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', entry.id)
    // Use card as drag image when possible
    const card = (e.currentTarget as HTMLElement).closest('.entry') as HTMLElement | null
    if (card) e.dataTransfer.setDragImage(card, 24, 24)
  }
}

function onDragOver(e: DragEvent, entry: TwoFaEntry) {
  if (!canReorder.value || !dragId.value || dragId.value === entry.id) return
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  dropTargetId.value = entry.id
}

function onDragLeave(entry: TwoFaEntry) {
  if (dropTargetId.value === entry.id) dropTargetId.value = null
}

function onDrop(e: DragEvent, entry: TwoFaEntry) {
  e.preventDefault()
  const from = dragId.value || e.dataTransfer?.getData('text/plain')
  dragId.value = null
  dropTargetId.value = null
  if (!from || from === entry.id || !canReorder.value) return
  const viewIds = filtered.value.map((x) => x.id)
  twofa.reorder(from, entry.id, viewIds)
}

function onDragEnd() {
  dragId.value = null
  dropTargetId.value = null
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
        <input
          v-model="exportPw"
          class="input export-pw"
          type="password"
          autocomplete="current-password"
          :placeholder="t('twofa.exportPassword')"
        />
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
      <p v-if="exportErr" class="hint export-err">{{ exportErr }}</p>
    </div>

    <!-- Service type filter chips -->
    <div v-if="serviceFilterChips.length > 1" class="svc-filter card-solid">
      <button
        v-for="c in serviceFilterChips"
        :key="c.id"
        type="button"
        class="svc-chip"
        :class="{ active: serviceFilter === c.id }"
        @click="serviceFilter = c.id"
      >
        <TwoFaServiceMark v-if="c.id !== 'all'" :logo="c.id" :size="18" />
        <span class="svc-chip-label">{{ c.name }}</span>
        <span class="svc-chip-count">{{ c.count }}</span>
      </button>
    </div>
    <p v-if="canReorder && filtered.length > 1" class="reorder-hint muted">
      {{ t('twofa.dragHint') }}
    </p>

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
      <div class="empty-title">
        {{ twofa.entries.length ? t('twofa.filterEmpty') : t('twofa.emptyTitle') }}
      </div>
      <div class="empty-desc">
        {{ twofa.entries.length ? t('twofa.filterEmptyDesc') : t('twofa.emptyDesc') }}
      </div>
    </div>

    <div v-else class="grid">
      <article
        v-for="e in filtered"
        :key="e.id"
        class="card-solid entry"
        :class="{
          'is-dragging': dragId === e.id,
          'is-drop-target': dropTargetId === e.id && dragId !== e.id,
          'can-drag': canReorder,
        }"
        @dragover="onDragOver($event, e)"
        @dragleave="onDragLeave(e)"
        @drop="onDrop($event, e)"
        @dragend="onDragEnd"
      >
        <div class="entry-top">
          <span
            v-if="canReorder"
            class="drag-handle"
            draggable="true"
            :title="t('twofa.dragHandle')"
            role="button"
            tabindex="0"
            aria-label="Reorder"
            @dragstart="onHandleDragStart($event, e)"
            @dragend="onDragEnd"
          >
            ⋮⋮
          </span>
          <TwoFaServiceMark :logo="serviceLogoKey(e)" :issuer="e.issuer" :size="40" />
          <div class="meta">
            <div class="name" :title="displayName(e)">{{ displayName(e) }}</div>
            <div class="sub muted" :title="entrySubTitle(e)">
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
          <span
            class="code"
            :class="{ urgent: remainUrgent(e), warn: remainWarn(e) }"
          >{{ twofa.codeFor(e) }}</span>
          <!-- Circular countdown ring (clearer than thin bar) -->
          <span
            v-if="e.type !== 'hotp'"
            class="timer-ring"
            :class="{ urgent: remainUrgent(e), warn: remainWarn(e) }"
            :title="t('twofa.secondsLeft', { n: twofa.remainingFor(e) })"
            aria-hidden="true"
          >
            <svg class="timer-svg" viewBox="0 0 36 36">
              <circle class="timer-track" cx="18" cy="18" r="15.5" />
              <circle
                class="timer-progress"
                cx="18"
                cy="18"
                r="15.5"
                :style="{
                  strokeDasharray: `${(remainPct(e) / 100) * 97.4} 97.4`,
                }"
              />
            </svg>
            <span class="timer-sec">{{ twofa.remainingFor(e) }}</span>
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
  /* Wider page so account names (issuer · email) have room */
  max-width: 1400px;
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
.export-pw {
  width: 200px;
  min-width: 140px;
}
.export-err {
  flex-basis: 100%;
  margin: 0;
  color: var(--danger);
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
  /* Wider cards so long account names are readable */
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 360px), 1fr));
  gap: 12px;
}
.meta {
  flex: 1 1 auto;
  min-width: 0;
  /* Prefer giving the name more horizontal room than action buttons */
  max-width: 100%;
}
.svc-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 12px;
  align-items: center;
}
.svc-chip {
  appearance: none;
  border: 1px solid var(--border);
  background: var(--panel, #fff);
  color: var(--text);
  border-radius: 999px;
  padding: 4px 10px 4px 6px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.12s ease,
    border-color 0.12s ease,
    color 0.12s ease;
  line-height: 1.2;
}
.svc-chip:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
  background: var(--accent-soft);
}
.svc-chip.active {
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent);
}
.svc-chip-label {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.svc-chip-count {
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  opacity: 0.65;
  min-width: 1.2em;
  text-align: center;
}
.reorder-hint {
  margin: -4px 4px 0;
  font-size: 11px;
}
.entry {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition:
    box-shadow 0.15s ease,
    opacity 0.15s ease,
    transform 0.12s ease;
  touch-action: pan-y;
}
.entry.is-dragging {
  opacity: 0.45;
  transform: scale(0.98);
}
.entry.is-drop-target {
  box-shadow: 0 0 0 2px var(--accent);
}
.entry-top {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.drag-handle {
  flex-shrink: 0;
  width: 18px;
  margin-top: 8px;
  padding: 4px 0;
  color: var(--muted);
  font-size: 13px;
  letter-spacing: -2px;
  line-height: 1;
  user-select: none;
  opacity: 0.5;
  cursor: grab;
  touch-action: none;
  text-align: center;
  border-radius: 4px;
}
.drag-handle:hover {
  opacity: 1;
  background: var(--panel-soft);
}
.drag-handle:active {
  cursor: grabbing;
}
.name {
  font-weight: 700;
  font-size: 14px;
  /* Allow long issuer · account labels to wrap instead of clipping early */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: normal;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.35;
}
.sub {
  font-size: 11px;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: normal;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.35;
}
.entry-acts {
  display: flex;
  flex-shrink: 0;
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
  gap: 12px;
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
  transition: color 0.15s ease;
}
.code.warn {
  color: #d97706;
}
.code.urgent {
  color: var(--danger, #e11d48);
}
/* Circular remaining-time ring */
.timer-ring {
  position: relative;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  --ring: var(--accent);
}
.timer-ring.warn {
  --ring: #d97706;
}
.timer-ring.urgent {
  --ring: var(--danger, #e11d48);
}
.timer-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}
.timer-track {
  fill: none;
  stroke: color-mix(in srgb, var(--ring) 18%, transparent);
  stroke-width: 3.2;
}
.timer-progress {
  fill: none;
  stroke: var(--ring);
  stroke-width: 3.2;
  stroke-linecap: round;
  transition: stroke-dasharray 0.9s linear;
}
.timer-sec {
  position: relative;
  z-index: 1;
  font-size: 12px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  color: var(--ring);
  line-height: 1;
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
