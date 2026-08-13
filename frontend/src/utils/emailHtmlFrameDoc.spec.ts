import { describe, expect, it } from 'vitest'
import { EMAIL_FRAME_MSG, buildEmailFrameSrcdoc } from '@/utils/emailHtmlFrameDoc'

describe('buildEmailFrameSrcdoc', () => {
  it('embeds the body HTML verbatim inside the document', () => {
    const doc = buildEmailFrameSrcdoc('<p>hello <b>world</b></p>')
    expect(doc).toContain('<p>hello <b>world</b></p>')
  })

  it('tags bridge messages with the shared message source constant', () => {
    const doc = buildEmailFrameSrcdoc('')
    expect(doc).toContain(JSON.stringify(EMAIL_FRAME_MSG))
  })

  it('is a full, self-contained HTML document', () => {
    const doc = buildEmailFrameSrcdoc('<p>x</p>')
    expect(doc).toMatch(/^<!DOCTYPE html>/i)
    expect(doc).toContain('<html>')
    expect(doc).toContain('</html>')
  })

  it('registers a capturing click listener that intercepts link activation', () => {
    // The bridge's entire safety property rests on catching every link click
    // before the browser's default navigation runs.
    const doc = buildEmailFrameSrcdoc('')
    expect(doc).toMatch(/addEventListener\(\s*['"]click['"]\s*,\s*onActivate\s*,\s*true\s*\)/)
    expect(doc).toMatch(/addEventListener\(\s*['"]auxclick['"]\s*,\s*onActivate\s*,\s*true\s*\)/)
    expect(doc).toContain('preventDefault')
    expect(doc).toContain('stopPropagation')
  })

  it('lets mailto/tel/# links fall through to native handling', () => {
    const doc = buildEmailFrameSrcdoc('')
    expect(doc).toContain('mailto:')
    expect(doc).toContain('tel:')
  })

  it('pins the bridge script with a matching CSP hash', async () => {
    const doc = buildEmailFrameSrcdoc('')
    const script = doc.match(/<script>([\s\S]*?)<\/script>/)
    expect(script?.[1]).toBeTruthy()
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(script![1]))
    const b64 = btoa(String.fromCharCode(...new Uint8Array(digest)))
    expect(doc).toContain(`script-src 'sha256-${b64}'`)
  })
})
