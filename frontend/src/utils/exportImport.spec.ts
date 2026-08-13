import { describe, expect, it } from 'vitest'
import {
  detachSnapshotAccounts,
  parseSystemSnapshot,
} from '@/utils/exportImport'

describe('parseSystemSnapshot', () => {
  it('rejects snapshots whose accounts are not objects with email', () => {
    expect(() => parseSystemSnapshot(JSON.stringify({ v: 1, accounts: [1, 2] }))).toThrow(
      'invalid snapshot',
    )
  })

  it('strips cloud linkage on restore', () => {
    const rows = detachSnapshotAccounts([
      {
        id: 'a1',
        email: 'a@b.com',
        type: 'imap',
        storage: 'server',
        status: 'ok',
        serverId: 'host-row',
        clientSealed: true,
        cloudSyncPending: true,
      },
    ])
    expect(rows[0]?.serverId).toBeUndefined()
    expect(rows[0]?.storage).toBe('local')
    expect(rows[0]?.clientSealed).toBe(false)
  })
})
