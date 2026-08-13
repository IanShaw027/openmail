/**
 * Cloud delta ack must not outrun a durable mailCache write.
 * Hitting the page cap must still advance the consumed cursor.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getSyncAck } from '@/utils/syncAck'

const { flushPersist, mergeDeltaMails, pullSyncDelta } = vi.hoisted(() => ({
  flushPersist: vi.fn(async () => undefined),
  mergeDeltaMails: vi.fn(() => 2),
  pullSyncDelta: vi.fn(),
}))

vi.mock('@/api/sync', () => ({
  pullSyncDelta,
}))

vi.mock('@/stores/mailCache', () => ({
  useMailCacheStore: () => ({
    mergeDeltaMails,
    flushPersist,
  }),
}))

vi.mock('@/stores/accounts', () => ({
  useAccountsStore: () => ({
    localAccounts: [],
    serverAccounts: [],
  }),
}))

import { useCloudSyncStore } from '@/stores/cloudSync'

function mailRow(id: string, updatedAt: string) {
  return {
    email: 'user@example.com',
    folder: 'inbox',
    stable_id: `p:${id}`,
    id,
    subject: id,
    updated_at: updatedAt,
  }
}

describe('cloudSync delta ack', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    flushPersist.mockReset()
    flushPersist.mockResolvedValue(undefined)
    mergeDeltaMails.mockReset()
    mergeDeltaMails.mockReturnValue(2)
    pullSyncDelta.mockReset()
  })

  it('requests includeBody on every delta page', async () => {
    pullSyncDelta.mockResolvedValue({
      has_more: false,
      mails: [mailRow('m1', '2026-08-12T10:00:00+00:00')],
    })

    await useCloudSyncStore().pullCloudMailDelta()

    expect(pullSyncDelta).toHaveBeenCalledWith(
      expect.objectContaining({ includeBody: true }),
    )
  })

  it('acks only after flushPersist succeeds', async () => {
    pullSyncDelta.mockResolvedValue({
      has_more: false,
      mails: [mailRow('m1', '2026-08-12T10:00:00+00:00')],
    })
    flushPersist.mockRejectedValue(new Error('quota'))

    const store = useCloudSyncStore()
    const result = await store.pullCloudMailDelta()

    expect(result.merged).toBe(2)
    expect(flushPersist).toHaveBeenCalled()
    expect(getSyncAck()).toBeNull()
  })

  it('acks the last consumed row after a successful persist', async () => {
    pullSyncDelta.mockResolvedValue({
      has_more: false,
      mails: [mailRow('m1', '2026-08-12T10:00:00+00:00')],
      next_since_id: 'm1',
    })

    const store = useCloudSyncStore()
    await store.pullCloudMailDelta()

    expect(getSyncAck()).toBe('2026-08-12T10:00:00+00:00\tm1')
  })

  it('advances ack when the page cap is hit with has_more still true', async () => {
    pullSyncDelta.mockImplementation(async ({ sinceId }: { sinceId?: string | null }) => {
      const n = sinceId ? Number(String(sinceId).slice(1)) + 1 : 1
      return {
        has_more: true,
        mails: [mailRow(`m${n}`, `2026-08-12T10:${String(n).padStart(2, '0')}:00+00:00`)],
        next_since_id: `m${n}`,
      }
    })

    const store = useCloudSyncStore()
    const result = await store.pullCloudMailDelta()

    expect(result.done).toBe(false)
    expect(getSyncAck()).toBe('2026-08-12T10:20:00+00:00\tm20')
  })
})
