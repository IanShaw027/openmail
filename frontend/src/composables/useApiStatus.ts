import { ref } from 'vue'
import { getApiBase } from '@/api/client'

const DISMISS_KEY = 'openmail.apiOfflineDismissed'

const offline = ref(false)
const dismissed = ref(
  typeof sessionStorage !== 'undefined' && sessionStorage.getItem(DISMISS_KEY) === '1',
)
const probed = ref(false)
let probePromise: Promise<boolean> | null = null

/**
 * Non-blocking API reachability probe.
 * Any HTTP response (incl. 401/404) counts as online; network failure = offline.
 * Banner is shown at most once per session after dismiss.
 */
export function useApiStatus() {
  const showBanner = () => offline.value && !dismissed.value

  async function probe(force = false): Promise<boolean> {
    if (probed.value && !force) return !offline.value
    if (probePromise && !force) return probePromise

    probePromise = (async () => {
      try {
        const base = getApiBase()
        // Cheap health probe — any HTTP response means the API is reachable
        const url = `${base}/api/health`
        const controller = new AbortController()
        const timer = window.setTimeout(() => controller.abort(), 6000)
        try {
          await fetch(url, {
            method: 'GET',
            signal: controller.signal,
          })
          offline.value = false
        } finally {
          window.clearTimeout(timer)
        }
      } catch {
        // TypeError / network / abort → treat as offline
        offline.value = true
      } finally {
        probed.value = true
        probePromise = null
      }
      return !offline.value
    })()

    return probePromise
  }

  function dismiss() {
    dismissed.value = true
    try {
      sessionStorage.setItem(DISMISS_KEY, '1')
    } catch {
      /* private mode */
    }
  }

  /** Mark offline from a known network failure without re-probing */
  function markOffline() {
    offline.value = true
    probed.value = true
  }

  function markOnline() {
    offline.value = false
    probed.value = true
  }

  return {
    offline,
    dismissed,
    probed,
    showBanner,
    probe,
    dismiss,
    markOffline,
    markOnline,
  }
}
