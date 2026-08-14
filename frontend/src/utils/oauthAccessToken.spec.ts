import { describe, expect, it } from 'vitest'
import { withOAuthAccessToken } from './oauthAccessToken'

describe('withOAuthAccessToken', () => {
  it('adds a non-empty access_token onto the credential blob', () => {
    expect(withOAuthAccessToken({ refresh_token: 'r', client_id: 'c' }, ' at ')).toEqual({
      refresh_token: 'r',
      client_id: 'c',
      access_token: 'at',
    })
    expect(withOAuthAccessToken({ refresh_token: 'r' }, '')).toEqual({ refresh_token: 'r' })
    expect(withOAuthAccessToken(null, 'tok')).toEqual({ access_token: 'tok' })
  })
})
