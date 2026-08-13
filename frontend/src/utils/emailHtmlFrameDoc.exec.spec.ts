/**
 * Executes the bridge script for real, inside a genuine JSDOM instance, rather
 * than mounting it as an actual iframe (vitest's jsdom environment does not
 * run srcdoc scripts inside <iframe> elements). This is the part that matters
 * most: a string-content assertion can pass while the script itself throws or
 * silently does nothing, and that gap is exactly where a regression would hide.
 */
import { describe, expect, it, vi } from 'vitest'
// jsdom is untyped in this repo; the exec harness only needs the constructor.
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-expect-error -- no @types/jsdom
import { JSDOM } from 'jsdom'
import { EMAIL_FRAME_MSG, buildEmailFrameSrcdoc } from '@/utils/emailHtmlFrameDoc'

function loadFrame(bodyHtml: string) {
  const dom = new JSDOM(buildEmailFrameSrcdoc(bodyHtml), {
    runScripts: 'dangerously',
    url: 'about:blank',
  })
  const posted: unknown[] = []
  // The bridge posts to `parent`, which in a real iframe is the host window;
  // JSDOM makes a page its own parent, so stub it to capture the calls.
  dom.window.parent.postMessage = (...args: unknown[]) => {
    posted.push(args[0])
  }
  return { dom, posted }
}

describe('email frame bridge (executed)', () => {
  it('reports a resize message shortly after load', async () => {
    const { posted } = loadFrame('<p style="height:500px">tall</p>')
    await vi.waitFor(() => {
      expect(posted.some((m) => (m as { type?: string }).type === 'resize')).toBe(true)
    })
    const resize = posted.find((m) => (m as { type?: string }).type === 'resize') as {
      source: string
      type: string
      height: number
    }
    expect(resize.source).toBe(EMAIL_FRAME_MSG)
    expect(typeof resize.height).toBe('number')
  })

  it('intercepts a link click and posts navigate instead of following it', () => {
    const { dom, posted } = loadFrame('<a id="l" href="https://example.com/x">go</a>')
    const a = dom.window.document.getElementById('l') as HTMLAnchorElement
    const evt = new dom.window.MouseEvent('click', { bubbles: true, cancelable: true })
    const prevented = !a.dispatchEvent(evt)

    expect(prevented).toBe(true)
    const nav = posted.find((m) => (m as { type?: string }).type === 'navigate') as {
      source: string
      href: string
    }
    expect(nav).toBeTruthy()
    expect(nav.source).toBe(EMAIL_FRAME_MSG)
    expect(nav.href).toBe('https://example.com/x')
  })

  it('intercepts middle-click (auxclick) the same way', () => {
    const { dom, posted } = loadFrame('<a id="l" href="https://example.com/aux">go</a>')
    const a = dom.window.document.getElementById('l') as HTMLAnchorElement
    const evt = new dom.window.MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 })
    a.dispatchEvent(evt)

    const nav = posted.find((m) => (m as { type?: string }).type === 'navigate')
    expect(nav).toBeTruthy()
  })

  it('leaves mailto/tel/# links to native handling instead of intercepting them', () => {
    const { dom, posted } = loadFrame(
      '<a id="m" href="mailto:a@b.com">mail</a><a id="t" href="tel:+1234">tel</a><a id="h" href="#top">top</a>',
    )
    for (const id of ['m', 't', 'h']) {
      const a = dom.window.document.getElementById(id) as HTMLAnchorElement
      const evt = new dom.window.MouseEvent('click', { bubbles: true, cancelable: true })
      const prevented = !a.dispatchEvent(evt)
      expect(prevented).toBe(false)
    }
    expect(posted.some((m) => (m as { type?: string }).type === 'navigate')).toBe(false)
  })

  it('does not intercept clicks that land outside any link', () => {
    const { dom, posted } = loadFrame('<p id="p">just text, no link</p>')
    const p = dom.window.document.getElementById('p') as HTMLElement
    const evt = new dom.window.MouseEvent('click', { bubbles: true, cancelable: true })
    const prevented = !p.dispatchEvent(evt)

    expect(prevented).toBe(false)
    expect(posted.some((m) => (m as { type?: string }).type === 'navigate')).toBe(false)
  })
})
