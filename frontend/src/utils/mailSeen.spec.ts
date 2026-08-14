import { describe, expect, it } from 'vitest'
import { countUnseenMessages, nextMailSeenAt } from '@/utils/mailSeen'

describe('countUnseenMessages', () => {
  it('counts inbox messages newer than the seen watermark', () => {
    expect(
      countUnseenMessages(
        [
          { date: '2026-08-14T10:00:00.000Z' },
          { date: '2026-08-14T12:00:00.000Z' },
        ],
        Date.parse('2026-08-14T11:00:00.000Z'),
      ),
    ).toBe(1)
  })

  it('is zero when the mailbox has not been baselined', () => {
    expect(
      countUnseenMessages([{ date: '2026-08-14T12:00:00.000Z' }], undefined),
    ).toBe(0)
  })
})

describe('nextMailSeenAt', () => {
  it('jumps to now when the user views the mailbox', () => {
    expect(nextMailSeenAt(1_000, { viewed: true, now: 5_000 })).toBe(5_000)
  })

  it('baselines to newest mail once, then keeps the watermark', () => {
    expect(nextMailSeenAt(undefined, { newestMailMs: 3_000, now: 9_000 })).toBe(3_000)
    expect(nextMailSeenAt(3_000, { newestMailMs: 4_000, now: 9_000 })).toBe(3_000)
  })
})
