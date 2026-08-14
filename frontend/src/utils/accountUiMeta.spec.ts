import { beforeEach, describe, expect, it } from 'vitest'
import {
  getAccountUiMeta,
  patchAccountUiMeta,
} from '@/utils/accountUiMeta'

describe('accountUiMeta mailSeenAt', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('persists a mail-seen watermark without a group or star', () => {
    patchAccountUiMeta('User@Example.com', { mailSeenAt: 1_700 })
    expect(getAccountUiMeta('user@example.com')?.mailSeenAt).toBe(1_700)
  })
})
