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
 * Remote https images are off by default (tracking pixels). The parent
 * owns the opt-in control so it can sit in the pane header, not the body.
 */
import { computed, onUnmounted, ref } from 'vue'
import { openEmailHref, type EmailLinkClickOptions } from '@/utils/emailLinks'
import { EMAIL_FRAME_MSG, buildEmailFrameSrcdoc } from '@/utils/emailHtmlFrameDoc'

const props = defineProps<{
  html: string
  confirmNavigate: EmailLinkClickOptions['confirmNavigate']
  onBlocked?: EmailLinkClickOptions['onBlocked']
  allowRemoteImages?: boolean
}>()

const iframeRef = ref<HTMLIFrameElement | null>(null)
const heightPx = ref(120)
const srcdoc = computed(() =>
  buildEmailFrameSrcdoc(props.html || '', { allowRemoteImages: Boolean(props.allowRemoteImages) }),
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

window.addEventListener('message', onMessage)
onUnmounted(() => {
  window.removeEventListener('message', onMessage)
})
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
</style>
