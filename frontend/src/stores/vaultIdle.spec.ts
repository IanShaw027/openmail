/**
 * Idle auto-lock behaviour.
 *
 * The lock tears down the app shell, so getting this wrong is not a cosmetic
 * bug: it silently discards whatever the user was in the middle of. These tests
 * drive the real store through a real unlock and advance fake timers, because
 * the failure mode is entirely about *when* things happen.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useVaultStore } from '@/stores/vault'

const PASSWORD = 'correct-horse-battery'
const LOCK_MINUTES_KEY = 'openmail.vault.lockMinutes'

/** setInterval tick used by the idle watcher. */
const TICK_MS = 15_000

async function unlockedVault(lockMinutes: number) {
  const vault = useVaultStore()
  // Set before unlocking: the watcher reads the timeout on every tick, but the
  // store reads localStorage once at construction.
  vault.setLockMinutes(lockMinutes)
  await vault.createVault(PASSWORD)
  expect(vault.unlocked).toBe(true)
  return vault
}

/** Advance fake timers and let the watcher's async work settle. */
async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
}

describe('vault idle auto-lock', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.removeItem(LOCK_MINUTES_KEY)
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  it('locks once the idle timeout passes', async () => {
    const vault = await unlockedVault(1)

    await advance(TICK_MS * 3)
    expect(vault.unlocked).toBe(true)

    await advance(TICK_MS * 2)
    expect(vault.unlocked).toBe(false)
  })

  it('warns before locking rather than vanishing without notice', async () => {
    const vault = await unlockedVault(2)

    // 2 minutes total, warning window is the last 60s.
    await advance(TICK_MS * 3) // t=45s, 75s remaining
    expect(vault.lockWarningSeconds).toBe(0)
    expect(vault.unlocked).toBe(true)

    await advance(TICK_MS) // t=60s, 60s remaining
    expect(vault.lockWarningSeconds).toBeGreaterThan(0)
    expect(vault.unlocked).toBe(true)
  })

  it('treats activity as a reset, and clears the warning with it', async () => {
    const vault = await unlockedVault(2)

    await advance(TICK_MS * 4)
    expect(vault.lockWarningSeconds).toBeGreaterThan(0)

    vault.touch()
    expect(vault.lockWarningSeconds).toBe(0)

    // Well past the original deadline, but the timer restarted.
    await advance(TICK_MS * 4)
    expect(vault.unlocked).toBe(true)
  })

  it('never locks while activity keeps arriving', async () => {
    const vault = await unlockedVault(1)

    for (let i = 0; i < 20; i += 1) {
      await advance(TICK_MS)
      vault.touch()
    }
    expect(vault.unlocked).toBe(true)
  })

  it('postpones the lock while unsaved work is held', async () => {
    const vault = await unlockedVault(1)
    const release = vault.holdLock('compose-draft')

    await advance(TICK_MS * 10)
    expect(vault.unlocked).toBe(true)
    expect(vault.lockHeld).toBe(true)

    // Once the draft is gone the deadline applies again, without needing a new
    // idle period — the user has been away this whole time.
    release()
    await advance(TICK_MS)
    expect(vault.unlocked).toBe(false)
  })

  it('does not lock at all when the timeout is disabled', async () => {
    const vault = await unlockedVault(0)

    await advance(TICK_MS * 200)
    expect(vault.unlocked).toBe(true)
    expect(vault.lockWarningSeconds).toBe(0)
  })

  it('releasing a hold twice does not unbalance the count', async () => {
    const vault = await unlockedVault(1)
    const release = vault.holdLock('compose-draft')
    release()
    release()
    expect(vault.lockHeld).toBe(false)
  })

  it('locks anyway after a held draft exceeds the grace window', async () => {
    const vault = await unlockedVault(1)
    vault.holdLock('compose-draft')
    // 1 minute lock + 5 minute grace + one tick
    await advance(60_000 + 5 * 60_000 + TICK_MS)
    expect(vault.unlocked).toBe(false)
  })
})
