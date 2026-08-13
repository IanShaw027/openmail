<script setup lang="ts">
/**
 * QR device transfer:
 * - Host: build encrypted snapshot → create package → show QR → approve when guest claims
 * - Guest: scan/enter code → wait for host approve → download → overwrite local vault data
 *
 * After overwrite, auto-detect / fetch use the new local secrets only.
 * Server-side hourly sync still cannot decrypt client-sealed rows (unchanged).
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useVaultStore } from '@/stores/vault'
import { useAccountsStore } from '@/stores/accounts'
import { useMailCacheStore } from '@/stores/mailCache'
import { useTwoFaStore } from '@/stores/twofa'
import { useSettingsStore } from '@/stores/settings'
import { getDeviceId } from '@/utils/device'
import { buildSystemSnapshot, detachSnapshotAccounts, parseSystemSnapshot, type SystemSnapshot } from '@/utils/exportImport'
import { loadGroups } from '@/utils/groups'
import {
  transferApprove,
  transferClaim,
  transferCreate,
  transferDownload,
  transferStatus,
  type TransferDirection,
} from '@/api/transfer'
import { useToast } from '@/composables/useToast'
import jsQR from 'jsqr'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const vault = useVaultStore()
const accounts = useAccountsStore()
const mailCache = useMailCacheStore()
const twofa = useTwoFaStore()
const userSettings = useSettingsStore()
const { flashMsg } = useToast()

const mode = ref<'menu' | 'host' | 'guest'>('menu')
const direction = ref<TransferDirection>('to_guest')
const busy = ref(false)
const err = ref('')
const code = ref('')
const claimToken = ref('')
const status = ref('')
const expiresAt = ref(0)
const guestHint = ref('')
const qrDataUrl = ref('')
const codeInput = ref('')
const overwriteAck = ref(false)
const transferKey = ref('')

let pollTimer: ReturnType<typeof setInterval> | null = null
let stream: MediaStream | null = null
let raf = 0
const cameraOn = ref(false)
const videoEl = ref<HTMLVideoElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)

const statusLabel = computed(() => {
  const s = status.value
  if (s === 'pending') return t('transfer.statusPending')
  if (s === 'claimed') return t('transfer.statusClaimed')
  if (s === 'approved') return t('transfer.statusApproved')
  if (s === 'consumed') return t('transfer.statusConsumed')
  if (s === 'rejected') return t('transfer.statusRejected')
  if (s === 'expired') return t('transfer.statusExpired')
  return s || '—'
})

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopCamera() {
  cameraOn.value = false
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  stream?.getTracks().forEach((tr) => tr.stop())
  stream = null
  if (videoEl.value) videoEl.value.srcObject = null
}

async function makeQrPng(text: string): Promise<string> {
  // Lightweight QR via third-party CDN-free: use API from local canvas + jsQR is decoder only.
  // Encode with a minimal online-free approach: google chart is blocked offline —
  // use `https://api.qrserver.com` only as last resort; prefer pure canvas library.
  // Implement simple external-free QR via dynamic import of already-bundled path:
  try {
    // Prefer native Barcode? No encode. Use QR from URL-encoded SVG via quickresponse:
    const { default: QRCode } = await import('qrcode')
    return await QRCode.toDataURL(text, { width: 280, margin: 2, errorCorrectionLevel: 'M' })
  } catch {
    // Fallback: show code only
    return ''
  }
}

async function buildEncryptedBlob(): Promise<{ blob: string; key: string }> {
  if (vault.status !== 'unlocked') throw new Error(t('transfer.needUnlock'))
  const snap = buildSystemSnapshot({
    accounts: accounts.accounts.map((a) => ({ ...a })),
    groups: loadGroups(),
    importGroupId: accounts.importGroupId,
    settings: {
      retentionDays: userSettings.s.retentionDays,
      lookbackDays: userSettings.s.lookbackDays,
      firstFullDone: { ...userSettings.s.firstFullDone },
      batchConcurrency: 5,
      codeMasked: true,
      denseCols: false,
    },
    deviceId: getDeviceId(),
    mailCache: { ...mailCache.byEmail },
    twofa: twofa.entries.map((e) => ({ ...e })),
  })
  // The server receives only IV + ciphertext. The one-time key travels in the
  // QR URL fragment, which browsers do not send in HTTP requests.
  const raw = JSON.stringify(snap)
  const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, [
    'encrypt',
    'decrypt',
  ])
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    new TextEncoder().encode(raw),
  )
  const rawKey = await crypto.subtle.exportKey('raw', key)
  const envelope = {
    v: 1,
    alg: 'AES-GCM-256',
    iv: b64(iv),
    ct: b64(new Uint8Array(ct)),
  }
  return {
    blob: btoa(unescape(encodeURIComponent(JSON.stringify(envelope)))),
    key: b64(new Uint8Array(rawKey)),
  }
}

function b64(u8: Uint8Array): string {
  let s = ''
  u8.forEach((b) => {
    s += String.fromCharCode(b)
  })
  return btoa(s)
}

function unb64(s: string): ArrayBuffer {
  const bin = atob(s)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out.buffer
}

async function decryptBlob(blobB64: string, rawKeyB64: string): Promise<SystemSnapshot> {
  const envelope = JSON.parse(decodeURIComponent(escape(atob(blobB64)))) as {
    v: number
    iv: string
    ct: string
  }
  if (envelope.v !== 1 || !rawKeyB64) throw new Error('missing transfer key')
  const key = await crypto.subtle.importKey(
    'raw',
    unb64(rawKeyB64),
    { name: 'AES-GCM' },
    false,
    ['decrypt'],
  )
  const pt = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: new Uint8Array(unb64(envelope.iv)) },
    key,
    unb64(envelope.ct),
  )
  const text = new TextDecoder().decode(pt)
  return parseSystemSnapshot(text)
}

async function applySnapshotOverwrite(snap: SystemSnapshot) {
  if (vault.status !== 'unlocked') {
    throw new Error(t('transfer.needUnlock'))
  }
  // Full replace of local vault data — user confirmed overwrite
  const locals = detachSnapshotAccounts(snap.accounts)
  accounts.localAccounts.splice(0, accounts.localAccounts.length, ...locals)
  if (snap.twofa && Array.isArray(snap.twofa)) {
    twofa.replaceAll(snap.twofa as never)
  }
  if (snap.mailCache && typeof snap.mailCache === 'object') {
    mailCache.replaceAll(snap.mailCache as never, snap.settings?.retentionDays)
  }
  if (snap.settings) {
    userSettings.s.retentionDays = snap.settings.retentionDays ?? 90
    userSettings.s.lookbackDays = snap.settings.lookbackDays ?? 3
    userSettings.s.firstFullDone = { ...snap.settings.firstFullDone }
  }
  // Unconditional: a snapshot carrying `settings` but no `mailCache` shrinks the
  // window and would otherwise leave the existing cache holding mail that is now
  // out of range, which the retention hint promises never happens.
  mailCache.pruneAll(userSettings.s.retentionDays)
  // Snapshot overwrite must hit vault before any refresh
  await accounts.flushPersist()
  await twofa.flushPersist()
  await mailCache.flushPersist()
  userSettings.flushPersist()
}

async function startAsHost(dir: TransferDirection) {
  err.value = ''
  busy.value = true
  mode.value = 'host'
  direction.value = dir
  try {
    const encrypted = await buildEncryptedBlob()
    transferKey.value = encrypted.key
    const out = await transferCreate({
      blob: encrypted.blob,
      host_device_id: getDeviceId(),
      direction: dir,
      label: dir === 'to_guest' ? t('transfer.labelToMobile') : t('transfer.labelToPc'),
    })
    code.value = out.code
    claimToken.value = out.claim_token
    expiresAt.value = out.expires_at
    status.value = 'pending'
    const url = `${window.location.origin}${out.qr_path}&role=guest#key=${encodeURIComponent(encrypted.key)}`
    qrDataUrl.value = await makeQrPng(url)
    stopPoll()
    pollTimer = setInterval(() => void pollHost(), 2000)
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
    mode.value = 'menu'
  } finally {
    busy.value = false
  }
}

async function pollHost() {
  if (!code.value || !claimToken.value) return
  try {
    const st = await transferStatus(code.value, claimToken.value)
    status.value = st.status
    if (st.guest_device_id) guestHint.value = st.guest_device_id.slice(0, 10) + '…'
    if (st.status === 'claimed') {
      // wait for host approve click
    }
    if (st.status === 'consumed' || st.status === 'rejected' || st.status === 'expired') {
      stopPoll()
    }
  } catch {
    /* ignore */
  }
}

async function hostApprove(ok: boolean) {
  if (!code.value || !claimToken.value) return
  if (ok && !overwriteAck.value && direction.value === 'to_host') {
    err.value = t('transfer.needOverwriteAck')
    return
  }
  if (ok && direction.value === 'to_guest' && !overwriteAck.value) {
    err.value = t('transfer.needOverwriteAck')
    return
  }
  busy.value = true
  err.value = ''
  try {
    const st = await transferApprove({
      code: code.value,
      claim_token: claimToken.value,
      host_device_id: getDeviceId(),
      approve: ok,
    })
    status.value = st.status
    flashMsg(ok ? t('transfer.approved') : t('transfer.rejected'))
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function startAsGuest(inputCode?: string) {
  err.value = ''
  const rawInput = (inputCode || codeInput.value || code.value).trim()
  if (!inputCode && rawInput.includes('://')) {
    try {
      const link = new URL(rawInput)
      transferKey.value = new URLSearchParams(link.hash.slice(1)).get('key') || ''
      codeInput.value = link.searchParams.get('code') || ''
    } catch {
      /* validation below reports the invalid value */
    }
  }
  const c = (inputCode || codeInput.value || code.value).trim().toUpperCase()
  if (c.length < 6) {
    err.value = t('transfer.needCode')
    return
  }
  if (!overwriteAck.value) {
    err.value = t('transfer.needOverwriteAck')
    return
  }
  if (vault.status !== 'unlocked') {
    err.value = t('transfer.needUnlock')
    return
  }
  if (!transferKey.value) {
    err.value = t('transfer.missingKey')
    return
  }
  busy.value = true
  mode.value = 'guest'
  code.value = c
  try {
    const claimed = await transferClaim({ code: c, guest_device_id: getDeviceId() })
    claimToken.value = claimed.claim_token
    status.value = claimed.status
    direction.value = claimed.direction
    expiresAt.value = claimed.expires_at
    stopPoll()
    pollTimer = setInterval(() => void pollGuest(), 2000)
    await pollGuest()
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function pollGuest() {
  if (!code.value) return
  try {
    const st = await transferStatus(code.value, claimToken.value)
    status.value = st.status
    if (st.status === 'approved') {
      stopPoll()
      await finishGuestDownload()
    }
    if (st.status === 'rejected' || st.status === 'expired' || st.status === 'consumed') {
      stopPoll()
    }
  } catch {
    /* ignore */
  }
}

async function finishGuestDownload() {
  busy.value = true
  err.value = ''
  try {
    const dl = await transferDownload({ code: code.value, guest_device_id: getDeviceId() })
    const snap = await decryptBlob(dl.blob, transferKey.value)
    await applySnapshotOverwrite(snap)
    status.value = 'consumed'
    flashMsg(t('transfer.importDone'))
    // Resume auto-detect with new data
    setTimeout(() => router.push('/'), 800)
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    err.value = t('transfer.cameraDenied')
    return
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false,
    })
    cameraOn.value = true
    await new Promise((r) => setTimeout(r, 50))
    const video = videoEl.value
    if (video) {
      video.srcObject = stream
      video.setAttribute('playsinline', 'true')
      await video.play().catch(() => undefined)
      scanLoop()
    }
  } catch {
    err.value = t('transfer.cameraDenied')
    cameraOn.value = false
  }
}

function scanLoop() {
  const video = videoEl.value
  const canvas = canvasEl.value
  if (!video || !canvas || !cameraOn.value) return
  const w = video.videoWidth
  const h = video.videoHeight
  if (w && h) {
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    if (ctx) {
      ctx.drawImage(video, 0, 0, w, h)
      const img = ctx.getImageData(0, 0, w, h)
      const codeHit = jsQR(img.data, w, h)
      if (codeHit?.data) {
        const m = codeHit.data.match(/code=([A-Z0-9]{6,16})/i)
        if (m) {
          try {
            const scanned = new URL(codeHit.data)
            transferKey.value = new URLSearchParams(scanned.hash.slice(1)).get('key') || ''
          } catch {
            transferKey.value = ''
          }
          codeInput.value = m[1]!.toUpperCase()
          stopCamera()
          void startAsGuest(m[1])
          return
        }
      }
    }
  }
  raf = requestAnimationFrame(scanLoop)
}

onMounted(() => {
  transferKey.value = new URLSearchParams(window.location.hash.slice(1)).get('key') || ''
  const q = String(route.query.code || '').trim().toUpperCase()
  if (q) {
    codeInput.value = q
    mode.value = 'guest'
  }
})

onUnmounted(() => {
  stopPoll()
  stopCamera()
})
</script>

<template>
  <div class="transfer-page">
    <header class="head">
      <h1>{{ t('transfer.title') }}</h1>
      <p class="hint">{{ t('transfer.desc') }}</p>
      <p class="hint warn">{{ t('transfer.overwriteWarn') }}</p>
      <p class="hint">{{ t('transfer.pollNote') }}</p>
    </header>

    <div v-if="mode === 'menu'" class="card-solid panel">
      <label class="ack">
        <input v-model="overwriteAck" type="checkbox" />
        {{ t('transfer.ackOverwrite') }}
      </label>
      <div class="acts">
        <button
          type="button"
          class="btn btn-primary"
          :disabled="busy || !overwriteAck || vault.status !== 'unlocked'"
          @click="startAsHost('to_guest')"
        >
          {{ t('transfer.hostToMobile') }}
        </button>
        <button
          type="button"
          class="btn btn-outline"
          :disabled="busy || !overwriteAck"
          @click="mode = 'guest'"
        >
          {{ t('transfer.guestScan') }}
        </button>
      </div>
      <p v-if="vault.status !== 'unlocked'" class="hint danger">{{ t('transfer.needUnlock') }}</p>
    </div>

    <div v-else-if="mode === 'host'" class="card-solid panel">
      <h2>{{ t('transfer.hostTitle') }}</h2>
      <p class="code-big">{{ code }}</p>
      <img v-if="qrDataUrl" :src="qrDataUrl" class="qr" alt="QR" />
      <p class="hint">{{ t('transfer.status') }}: {{ statusLabel }}</p>
      <p v-if="guestHint" class="hint">{{ t('transfer.guestDevice') }}: {{ guestHint }}</p>
      <div class="acts">
        <button
          type="button"
          class="btn btn-primary"
          :disabled="busy || (status !== 'claimed' && status !== 'pending')"
          @click="hostApprove(true)"
        >
          {{ t('transfer.approve') }}
        </button>
        <button type="button" class="btn btn-ghost" :disabled="busy" @click="hostApprove(false)">
          {{ t('transfer.reject') }}
        </button>
        <button
          type="button"
          class="btn btn-ghost"
          @click="
            () => {
              stopPoll()
              mode = 'menu'
            }
          "
        >
          {{ t('common.back') }}
        </button>
      </div>
    </div>

    <div v-else class="card-solid panel">
      <h2>{{ t('transfer.guestTitle') }}</h2>
      <label class="ack">
        <input v-model="overwriteAck" type="checkbox" />
        {{ t('transfer.ackOverwrite') }}
      </label>
      <input
        v-model="codeInput"
        class="input"
        type="text"
        :placeholder="t('transfer.codePh')"
        autocomplete="off"
      />
      <div class="acts">
        <button
          type="button"
          class="btn btn-primary"
          :disabled="busy || !overwriteAck"
          @click="startAsGuest()"
        >
          {{ t('transfer.connect') }}
        </button>
        <button type="button" class="btn btn-outline" @click="cameraOn ? stopCamera() : startCamera()">
          {{ cameraOn ? t('transfer.stopCamera') : t('transfer.scanQr') }}
        </button>
        <button type="button" class="btn btn-ghost" @click="mode = 'menu'">
          {{ t('common.back') }}
        </button>
      </div>
      <div v-if="cameraOn" class="cam">
        <video ref="videoEl" class="video" playsinline muted autoplay />
        <canvas ref="canvasEl" class="sr-only" />
      </div>
      <p class="hint">{{ t('transfer.status') }}: {{ statusLabel }}</p>
    </div>

    <p v-if="err" class="hint danger">{{ err }}</p>
  </div>
</template>

<style scoped>
.transfer-page {
  max-width: 520px;
  margin: 0 auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.head h1 {
  font-size: 1.25rem;
  margin: 0 0 8px;
}
.hint {
  font-size: 13px;
  color: var(--muted);
  margin: 4px 0;
}
.hint.warn {
  color: var(--warning, #b45309);
}
.hint.danger {
  color: var(--danger);
}
.panel {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.code-big {
  font-family: var(--mono);
  font-size: 28px;
  letter-spacing: 0.2em;
  font-weight: 700;
  text-align: center;
  margin: 8px 0;
}
.qr {
  width: 280px;
  height: 280px;
  margin: 0 auto;
  display: block;
  border-radius: 12px;
  background: #fff;
}
.ack {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  font-size: 13px;
  line-height: 1.4;
}
.cam {
  border-radius: 12px;
  overflow: hidden;
  background: #000;
}
.video {
  width: 100%;
  max-height: 280px;
  object-fit: cover;
  display: block;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}
</style>
