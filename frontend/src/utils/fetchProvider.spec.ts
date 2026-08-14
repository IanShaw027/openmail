import { describe, expect, it } from 'vitest'
import { isCookieFetchProvider, resolveFetchProvider } from './fetchProvider'

describe('resolveFetchProvider', () => {
  it('sends Gmail through IMAP even when the saved type is unknown', () => {
    expect(
      resolveFetchProvider({ type: 'unknown', email: 'ianshaw028@gmail.com' }),
    ).toBe('imap')
  })

  it('keeps mail.com on the cookie/SSO path', () => {
    expect(resolveFetchProvider({ type: 'unknown', email: 'name@mail.com' })).toBe('cookie')
    expect(resolveFetchProvider({ type: 'cookie', email: 'name@mail.com' })).toBe('cookie')
  })

  it('does not override an explicit IMAP or OAuth type', () => {
    expect(resolveFetchProvider({ type: 'imap', email: 'a@mail.com' })).toBe('imap')
    expect(resolveFetchProvider({ type: 'oauth', email: 'a@gmail.com' })).toBe('oauth')
  })
})

describe('isCookieFetchProvider', () => {
  it('is true only for cookie/unknown', () => {
    expect(isCookieFetchProvider('cookie')).toBe(true)
    expect(isCookieFetchProvider('imap')).toBe(false)
    expect(isCookieFetchProvider(resolveFetchProvider({ type: 'unknown', email: 'a@gmail.com' }))).toBe(
      false,
    )
  })
})
