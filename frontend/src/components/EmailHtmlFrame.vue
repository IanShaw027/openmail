<script setup lang="ts">
/**
 * Render sanitized email HTML inside a sandboxed iframe.
 *
 * Same-document v-html puts mail next to the vault SPA: a sanitizer miss is a
 * full XSS against local secrets. The iframe has no allow-same-origin, so even
 * if the allowlist fails, scripts in the mail cannot read parent storage.
 * allow-scripts is only for the tiny bridge that reports height and link
 * clicks via postMessage — it never calls window.open itself, so the sandbox
 * deliberately omits allow-popups. Without that omission, a sanitizer miss
 * that lands an inline event handler or <script> could call window.open()
 * directly and skip the confirm dialog entirely, defeating the reason this
 * frame exists.
 *
 * Remote https images are off by default (tracking pixels). The user can
 * opt in per message; switching mail resets the choice.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { i18n } from '@/i18n'
import { openEmailHref, type EmailLinkClickOptions } from '@/utils/emailLinks'
import { EMAIL_FRAME_MSG, buildEmailFrameSrcdoc } from '@/utils/emailHtmlFrameDoc'

const props = defineProps<{
  html: string
  confirmNavigate: EmailLinkClickOptions['confirmNavigate']
  onBlocked?: EmailLinkClickOptions['onBlocked']
}>()

function t(key: string): string {
  return String((i18n.global as { t: (k: string) => unknown }).t(key))
}
const iframeRef = ref<HTMLIFrameElement | null>(null)
const heightPx = ref(120)
const allowRemoteImages = ref(false)
const srcdoc = computed(() =>
  buildEmailFrameSrcdoc(props.html || '', { allowRemoteImages: allowRemoteImages.value }),
)

function onMessage(ev: MessageEvent) {
  const data = ev.data
  if (!data || data.source !== EMAIL_FRAME_MSG) return
  if (!iframeRef.value || ev.source !== iframeRef.value.contentWindow) return
  if (data.type === 'resize' && typeof data.height === 'number' && Number.isFinite(data.height)) {
    heightPx.value = Math.min(Math.max(Math.ceil(data.height) + 8, 80), 12000)
    return
  }
  if (
    data.type === 'navigate' &&
    typeof data.href === 'string' &&
    data.href.length > 0 &&
    data.href.length < 4096
  ) {
    openEmailHref(data.href, {
      confirmNavigate: props.confirmNavigate,
      onBlocked: props.onBlocked,
    })
  }
}

onMounted(() => {
  window.addEventListener('message', onMessage)
})
onUnmounted(() => {
  window.removeEventListener('message', onMessage)
})

watch(
  () => props.html,
  () => {
    heightPx.value = 120
    allowRemoteImages.value = false
  },
)
</script>

<template>
  <div class="email-html-frame-wrap">
    <iframe
      ref="iframeRef"
      class="email-html-frame"
      :srcdoc="srcdoc"
      :style="{ height: heightPx + 'px' }"
      sandbox="allow-scripts"
      referrerpolicy="no-referrer"
      title="Email body"
    />
    <button
      v-if="!allowRemoteImages"
      type="button"
      class="remote-img-btn"
      @click="allowRemoteImages = true"
    >
      {{ t('console.showRemoteImages') }}
    </button>
  </div>
</template>

<style scoped>
.email-html-frame-wrap {
  position: relative;
}
.email-html-frame {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
  min-height: 80px;
}
.remote-img-btn {
  margin-top: 8px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--link, #1d4ed8);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  text-decoration: underline;
}
</style>
