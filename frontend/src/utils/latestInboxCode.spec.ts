import { describe, expect, it } from 'vitest'
import { latestCodePatchForFetch, latestInboxVerificationCode } from './latestInboxCode'

describe('latestInboxVerificationCode', () => {
  it('returns the newest inbox code and ignores spam/sent', () => {
    expect(
      latestInboxVerificationCode([
        { folder: 'spam', date: '2026-08-14T12:00:00Z', verification_code: '999999' },
        { folder: 'sent', date: '2026-08-14T13:00:00Z', verification_code: '888888' },
        { folder: 'inbox', date: '2026-08-14T10:00:00Z', verification_code: '111111' },
        { folder: 'inbox', date: '2026-08-14T11:00:00Z', verification_code: '222222' },
      ]),
    ).toBe('222222')
  })

  it('returns undefined when inbox has no code', () => {
    expect(
      latestInboxVerificationCode([
        { folder: 'spam', date: '2026-08-14T12:00:00Z', verification_code: '999999' },
        { folder: 'inbox', date: '2026-08-14T11:00:00Z', verification_code: '' },
      ]),
    ).toBeUndefined()
  })
})

describe('latestCodePatchForFetch', () => {
  const inboxWithCode = [
    { folder: 'inbox', date: '2026-08-14T11:00:00Z', verification_code: '222222' },
  ]
  const inboxWithoutCode = [
    { folder: 'inbox', date: '2026-08-14T11:00:00Z', verification_code: '' },
  ]

  it('does not patch a failed fetch', () => {
    expect(latestCodePatchForFetch({ fetchOk: false, inboxMessages: inboxWithCode })).toBeNull()
  })

  it('does not patch an empty inbox cache', () => {
    expect(latestCodePatchForFetch({ fetchOk: true, inboxMessages: [] })).toBeNull()
  })

  it('clears when inbox has messages but no code', () => {
    expect(latestCodePatchForFetch({ fetchOk: true, inboxMessages: inboxWithoutCode })).toEqual({
      latestCode: undefined,
    })
  })

  it('writes the newest inbox code after a successful fetch', () => {
    expect(latestCodePatchForFetch({ fetchOk: true, inboxMessages: inboxWithCode })).toEqual({
      latestCode: '222222',
    })
  })
})
