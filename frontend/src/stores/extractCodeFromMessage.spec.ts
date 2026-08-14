import { describe, expect, it } from 'vitest'
import { extractCodeFromMessage } from './mailCache'

describe('extractCodeFromMessage', () => {
  it('keeps real inbox OTPs', () => {
    expect(
      extractCodeFromMessage({
        subject: 'Your temporary ChatGPT login code',
        body_text: 'Your ChatGPT code is 980220. It expires in 10 minutes.',
      }),
    ).toBe('980220')
    expect(
      extractCodeFromMessage({
        subject: 'SpaceXAI confirmation code: 8IX-FGG',
        body_text: '',
      }),
    ).toBe('8IX-FGG')
    expect(
      extractCodeFromMessage({
        subject: 'M1M-J00 xAI confirmation code',
        body_text: 'Please use the code below to validate your email address. M1M-J00',
      }),
    ).toBe('M1M-J00')
  })

  it('rejects login notices and unrelated code phrases', () => {
    expect(
      extractCodeFromMessage({
        subject: 'Login notice 112233 expires soon',
        body_text: '',
      }),
    ).toBeNull()
    expect(
      extractCodeFromMessage({
        subject: '',
        body_text: 'Your postal code is 94107.',
      }),
    ).toBeNull()
    expect(
      extractCodeFromMessage({
        subject: '',
        body_text: 'Error code 500123 — please retry.',
      }),
    ).toBeNull()
    expect(
      extractCodeFromMessage({
        subject: 'Weekly digest',
        body_text: 'Use discount code 882211 at checkout.',
      }),
    ).toBeNull()
  })
})
