import { describe, expect, it } from 'vitest'
import {
  shouldRefreshOpenFolder,
  shouldUseHeaderBodyPhases,
  supportsHeaderBodyPhases,
} from './fetchPhases'

describe('fetchPhases', () => {
  it('uses two-phase only for empty IMAP/OAuth first window', () => {
    expect(supportsHeaderBodyPhases('imap')).toBe(true)
    expect(supportsHeaderBodyPhases('cookie')).toBe(false)
    expect(
      shouldUseHeaderBodyPhases({ provider: 'imap', emptyFolder: true }),
    ).toBe(true)
    expect(
      shouldUseHeaderBodyPhases({ provider: 'oauth', emptyFolder: false, clearFirst: true }),
    ).toBe(true)
    expect(
      shouldUseHeaderBodyPhases({ provider: 'imap', emptyFolder: false, catchUp: true }),
    ).toBe(false)
    expect(
      shouldUseHeaderBodyPhases({ provider: 'cookie', emptyFolder: true }),
    ).toBe(false)
  })

  it('refreshes the open tab for the selected account only', () => {
    expect(
      shouldRefreshOpenFolder({
        selectedAccountId: 'a1',
        expectedAccountId: 'a1',
        folder: 'inbox',
        openFolder: 'inbox',
      }),
    ).toBe(true)
    expect(
      shouldRefreshOpenFolder({
        selectedAccountId: 'a1',
        expectedAccountId: 'a1',
        folder: 'spam',
        openFolder: 'inbox',
      }),
    ).toBe(false)
    expect(
      shouldRefreshOpenFolder({
        selectedAccountId: 'a2',
        expectedAccountId: 'a1',
        folder: 'inbox',
        openFolder: 'inbox',
      }),
    ).toBe(false)
  })
})
