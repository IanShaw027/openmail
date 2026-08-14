/**
 * Cloud delta ack must not outrun a durable mailCache write.
 * Hitting the page cap must still advance the consumed cursor.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getSyncAck } from '@/utils/syncAck'

const { flushPersist, mergeDeltaMails, pullSyncDelta, accountsState } = vi.hoisted(
  () => ({
    flushPersist: vi.fn(async () => undefined),
    mergeDeltaMails: vi.fn(() => 2),
    pullSyncDelta: vi.fn(),
    accountsState: {
      localAccounts: [] as Array<Record<string, unknown>>,
      serverAccounts: [] as Array<Record<string, unknown>>,
    },
  }),
)

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
  useAccountsStore: () => accountsState,
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
    accountsState.localAccounts = []
    accountsState.serverAccounts = []
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

describe('cloudSync polling', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    flushPersist.mockReset()
    flushPersist.mockResolvedValue(undefined)
    mergeDeltaMails.mockReset()
    mergeDeltaMails.mockReturnValue(0)
    pullSyncDelta.mockReset()
    pullSyncDelta.mockResolvedValue({ has_more: false, mails: [] })
    accountsState.localAccounts = []
    accountsState.serverAccounts = []
    vi.useFakeTimers()
  })

  afterEach(() => {
    useCloudSyncStore().stopCloudDeltaPolling()
    vi.useRealTimers()
  })

  it('pulls immediately when polling starts, then every minute', async () => {
    const store = useCloudSyncStore()
    store.startCloudDeltaPolling()
    await vi.advanceTimersByTimeAsync(0)
    expect(pullSyncDelta).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(59_000)
    expect(pullSyncDelta).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(pullSyncDelta).toHaveBeenCalledTimes(2)

    store.stopCloudDeltaPolling()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(pullSyncDelta).toHaveBeenCalledTimes(2)
  })
})

describe('cloudSync account lastSyncAt', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    flushPersist.mockReset()
    flushPersist.mockResolvedValue(undefined)
    mergeDeltaMails.mockReset()
    mergeDeltaMails.mockReturnValue(0)
    pullSyncDelta.mockReset()
    accountsState.localAccounts = [
      {
        id: 'loc-1',
        email: 'user@example.com',
        serverId: 'acc-1',
        latestCode: '123456',
        updatedAt: 1_000,
      },
    ]
    accountsState.serverAccounts = [
      {
        id: 'srv_acc-1',
        email: 'user@example.com',
        serverId: 'acc-1',
        latestCode: '123456',
        updatedAt: 1_000,
      },
    ]
  })

  it('applies last_sync_at even when the OTP is unchanged', async () => {
    pullSyncDelta.mockResolvedValue({
      has_more: false,
      mails: [],
      accounts: [
        {
          id: 'acc-1',
          email: 'user@example.com',
          latest_verification_code: '123456',
          last_sync_at: '2026-08-14T12:00:00.000Z',
        },
      ],
    })

    await useCloudSyncStore().pullCloudMailDelta()

    const expected = Date.parse('2026-08-14T12:00:00.000Z')
    expect(accountsState.localAccounts[0]?.lastSyncAt).toBe(expected)
    expect(accountsState.serverAccounts[0]?.lastSyncAt).toBe(expected)
    expect(accountsState.localAccounts[0]?.updatedAt).toBe(1_000)
  })
})

