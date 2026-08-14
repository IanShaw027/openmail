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

describe('sanitizeHtml email layout CSS', () => {
  it('keeps <style> blocks used by HTML mail', () => {
    const html = sanitizeHtml(
      '<style type="text/css">.otp { color: #111; font-size: 24px }</style><p class="otp">123456</p>',
    )
    expect(html).toMatch(/<style\b/i)
    expect(html).toContain('.otp')
    expect(html).toContain('font-size')
    expect(html).toContain('123456')
  })

  it('keeps <style> from a full HTML mail document head', () => {
    const html = sanitizeHtml(
      '<!DOCTYPE html><html><head><style>.otp { color: #111 }</style></head><body><p class="otp">123456</p></body></html>',
    )
    expect(html).toMatch(/<style\b/i)
    expect(html).toContain('.otp')
    expect(html).toContain('123456')
  })

  it('strips @import and url() from <style> so mail CSS cannot fetch', () => {
    const html = sanitizeHtml(
      '<style>@import url("https://evil.test/x.css"); p{background:url(https://evil.test/p.png)}</style><p>x</p>',
    )
    expect(html).not.toMatch(/@import/i)
    expect(html).not.toMatch(/url\s*\(/i)
  })
})
