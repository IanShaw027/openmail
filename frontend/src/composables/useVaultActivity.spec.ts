import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ACTIVITY_THROTTLE_MS, trackUserActivity } from '@/composables/useVaultActivity'

describe('user activity tracking', () => {
  let clock = 0
  const now = () => clock

  beforeEach(() => {
    clock = 1_000_000
  })

  it('counts reading, not just clicking and typing', () => {
    const onActive = vi.fn()
    const stop = trackUserActivity(onActive, window, now)

    // The regression this guards: someone scrolling through a long thread was
    // indistinguishable from an unattended machine, and got locked out.
    for (const evt of ['wheel', 'mousemove', 'touchstart', 'pointerdown', 'keydown']) {
      onActive.mockClear()
      clock += ACTIVITY_THROTTLE_MS * 2
      window.dispatchEvent(new Event(evt))
      expect(onActive, `${evt} should count as activity`).toHaveBeenCalledTimes(1)
    }

    stop()
  })

  it('throttles bursts so a mousemove storm is one reset', () => {
    const onActive = vi.fn()
    const stop = trackUserActivity(onActive, window, now)

    window.dispatchEvent(new Event('mousemove'))
    expect(onActive).toHaveBeenCalledTimes(1)

    for (let i = 0; i < 500; i += 1) {
      clock += 5
      window.dispatchEvent(new Event('mousemove'))
    }
    expect(onActive).toHaveBeenCalledTimes(1)

    clock += ACTIVITY_THROTTLE_MS
    window.dispatchEvent(new Event('mousemove'))
    expect(onActive).toHaveBeenCalledTimes(2)

    stop()
  })

  it('stops listening once released', () => {
    const onActive = vi.fn()
    const stop = trackUserActivity(onActive, window, now)
    stop()

    clock += ACTIVITY_THROTTLE_MS * 2
    window.dispatchEvent(new Event('wheel'))
    expect(onActive).not.toHaveBeenCalled()
  })
})
