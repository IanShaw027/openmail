/**
 * What counts as "the user is still here" for the vault idle timer.
 *
 * Only clicks and keystrokes counted originally, which meant reading — the one
 * thing you do in a mail client without touching anything — looked identical to
 * an unattended machine and tripped the auto-lock mid-session.
 */
export const ACTIVITY_EVENTS = [
  'pointerdown',
  'keydown',
  'wheel',
  'touchstart',
  'mousemove',
] as const

/** mousemove fires per pixel; one timer reset per interval is plenty. */
export const ACTIVITY_THROTTLE_MS = 5_000

/**
 * Call `onActive` when the user interacts, at most once per throttle window.
 * Returns a function that removes the listeners.
 */
export function trackUserActivity(
  onActive: () => void,
  target: EventTarget = window,
  now: () => number = Date.now,
): () => void {
  let last = 0
  const handle = () => {
    const t = now()
    if (last !== 0 && t - last < ACTIVITY_THROTTLE_MS) return
    last = t
    onActive()
  }

  for (const evt of ACTIVITY_EVENTS) {
    target.addEventListener(evt, handle, { passive: true })
  }
  return () => {
    for (const evt of ACTIVITY_EVENTS) {
      target.removeEventListener(evt, handle)
    }
  }
}
