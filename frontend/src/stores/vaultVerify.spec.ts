/**
 * Re-auth for sensitive reveals: recovery key / 2FA export must check the
 * vault password without replacing the unlocked session.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useVaultStore } from '@/stores/vault'

const PASSWORD = 'correct-horse-battery'

describe('vault verifyPassword', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    setActivePinia(createPinia())
  })

  it('accepts the current password without locking the session', async () => {
    const vault = useVaultStore()
    await vault.createVault(PASSWORD)
    expect(vault.unlocked).toBe(true)

    await expect(vault.verifyPassword(PASSWORD)).resolves.toBe(true)
    expect(vault.unlocked).toBe(true)
  })

  it('rejects a wrong password and stays unlocked', async () => {
    const vault = useVaultStore()
    await vault.createVault(PASSWORD)

    await expect(vault.verifyPassword('wrong-password-xx')).resolves.toBe(false)
    expect(vault.unlocked).toBe(true)
  })
})
