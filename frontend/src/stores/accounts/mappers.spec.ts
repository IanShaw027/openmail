import { describe, expect, it } from 'vitest'
import type { ServerAccountOut } from '@/api/accounts'
import { mapServerToLocal } from '@/stores/accounts/mappers'

describe('mapServerToLocal', () => {
  it('maps last_sync_at onto lastSyncAt instead of using updated_at for sync age', () => {
    const row: ServerAccountOut = {
      id: 'acc-1',
      email: 'user@example.com',
      provider: 'imap',
      last_sync_at: '2026-08-14T10:00:00.000Z',
      updated_at: '2026-08-13T01:00:00.000Z',
      created_at: '2026-08-01T00:00:00.000Z',
    }
    const mapped = mapServerToLocal(row)
    expect(mapped.lastSyncAt).toBe(Date.parse('2026-08-14T10:00:00.000Z'))
    expect(mapped.updatedAt).toBe(Date.parse('2026-08-13T01:00:00.000Z'))
  })
})
