import { computed, ref } from 'vue'

export type ToastKind = 'success' | 'danger' | 'info'

export type ToastItem = {
  id: number
  msg: string
  kind: ToastKind
}

const toasts = ref<ToastItem[]>([])
let seq = 0
const timers = new Map<number, ReturnType<typeof setTimeout>>()

const DEFAULT_MS = 3200
const MAX_VISIBLE = 4

function removeToast(id: number) {
  const t = timers.get(id)
  if (t !== undefined) {
    window.clearTimeout(t)
    timers.delete(id)
  }
  toasts.value = toasts.value.filter((x) => x.id !== id)
}

/**
 * Global toast bus — one floating host for the whole app.
 * Prefer this over inline `.notice` blocks in page layout.
 */
export function pushToast(msg: string, kind: ToastKind = 'success', durationMs = DEFAULT_MS) {
  const text = (msg || '').trim()
  if (!text) return

  const id = ++seq
  toasts.value = [...toasts.value, { id, msg: text, kind }].slice(-MAX_VISIBLE)

  if (durationMs > 0) {
    timers.set(
      id,
      window.setTimeout(() => {
        removeToast(id)
      }, durationMs),
    )
  }
  return id
}

export function dismissToast(id: number) {
  removeToast(id)
}

export function clearAllToasts() {
  for (const id of [...timers.keys()]) removeToast(id)
  toasts.value = []
}

/**
 * Page-level helper (same global bus). Keeps existing `flashMsg` call sites.
 * `flash` / `flashKind` remain for rare legacy reads but UI should use ToastHost.
 */
export function useToast(durationMs = DEFAULT_MS) {
  /** @deprecated Prefer ToastHost; always empty when host is mounted */
  const flash = computed(() => {
    const last = toasts.value[toasts.value.length - 1]
    return last?.msg ?? ''
  })
  /** @deprecated */
  const flashKind = computed<ToastKind>(() => {
    const last = toasts.value[toasts.value.length - 1]
    return last?.kind ?? 'success'
  })

  function toast(msg: string, kind: ToastKind = 'success') {
    pushToast(msg, kind, durationMs)
  }

  function flashMsg(msg: string, kind: ToastKind = 'success') {
    toast(msg, kind)
  }

  function clearToast() {
    clearAllToasts()
  }

  return {
    flash,
    flashKind,
    toasts,
    toast,
    flashMsg,
    clearToast,
    dismissToast,
    pushToast,
  }
}

/** For ToastHost — reactive list */
export function useToastList() {
  return {
    toasts,
    dismissToast,
  }
}
