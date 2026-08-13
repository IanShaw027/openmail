import { describe, expect, it } from 'vitest'
import { sanitizeHtml } from '@/utils/sanitizeHtml'

describe('sanitizeHtml link attributes', () => {
  it('forces rel and target after the mail-supplied attributes are applied', () => {
    const html = sanitizeHtml(
      '<a href="https://example.com" target="_self" rel="opener">x</a>',
    )
    expect(html).toContain('target="_blank"')
    expect(html).toContain('noopener')
    expect(html).not.toContain('target="_self"')
    expect(html).not.toMatch(/rel="opener"/)
  })
})
