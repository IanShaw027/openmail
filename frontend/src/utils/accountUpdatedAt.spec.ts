import { describe, expect, it } from 'vitest'
import {
  accountRowUpdatedAt,
  mailDataUpdatedAt,
  parseIsoToMs,
} from '@/utils/accountUpdatedAt'

describe('mailDataUpdatedAt', () => {
  it('uses last sync / newest mail and ignores account-row updatedAt', () => {
    expect(
      mailDataUpdatedAt({ lastSyncAt: 2_000, updatedAt: 9_000 }, 3_000),
    ).toBe(3_000)
    expect(mailDataUpdatedAt({ updatedAt: 9_000 })).toBeUndefined()
  })
})

describe('accountRowUpdatedAt', () => {
  it('is only the account-row clock', () => {
    expect(accountRowUpdatedAt({ lastSyncAt: 2_000, updatedAt: 9_000 })).toBe(9_000)
    expect(accountRowUpdatedAt({})).toBeUndefined()
  })
})

describe('parseIsoToMs', () => {
  it('parses ISO timestamps and ignores empties', () => {
    expect(parseIsoToMs('2026-08-14T12:00:00.000Z')).toBe(
      Date.parse('2026-08-14T12:00:00.000Z'),
    )
    expect(parseIsoToMs('')).toBeUndefined()
    expect(parseIsoToMs(null)).toBeUndefined()
  })
})
