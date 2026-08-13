/**
 * The sandbox attribute is the whole point of this component: it is what
 * stands between a sanitizer bypass and an unconfirmed navigation or a script
 * that can reach the parent page. Assert on it directly so a well-meaning
 * "let's also allow X" edit does not quietly remove the containment.
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import EmailHtmlFrame from '@/components/EmailHtmlFrame.vue'

function mountFrame(html = '<p>hi</p>') {
  return mount(EmailHtmlFrame, {
    props: {
      html,
      confirmNavigate: () => true,
    },
  })
}

describe('EmailHtmlFrame sandbox', () => {
  it('allows scripts (needed for the bridge) and nothing else', () => {
    const wrapper = mountFrame()
    const iframe = wrapper.get('iframe')

    const tokens = (iframe.attributes('sandbox') || '').split(/\s+/).filter(Boolean)
    expect(tokens).toEqual(['allow-scripts'])
  })

  it('never grants allow-same-origin', () => {
    // The one flag that would let mail-origin script read the parent's vault
    // storage if the sanitizer ever failed.
    const wrapper = mountFrame()
    expect(wrapper.get('iframe').attributes('sandbox')).not.toMatch(/allow-same-origin/)
  })

  it('never grants allow-popups', () => {
    // Regression guard: allow-popups would let injected script call
    // window.open() directly, skipping the confirm-before-navigate flow that
    // is the entire reason links are routed through postMessage.
    const wrapper = mountFrame()
    expect(wrapper.get('iframe').attributes('sandbox')).not.toMatch(/allow-popups/)
  })

  it('never grants top-level navigation', () => {
    const wrapper = mountFrame()
    expect(wrapper.get('iframe').attributes('sandbox')).not.toMatch(/allow-top-navigation/)
  })

  it('sends no referrer to whatever the mail body links to', () => {
    const wrapper = mountFrame()
    expect(wrapper.get('iframe').attributes('referrerpolicy')).toBe('no-referrer')
  })

  it('renders the provided html into the srcdoc', () => {
    const wrapper = mountFrame('<p>distinctive-marker-123</p>')
    expect(wrapper.get('iframe').attributes('srcdoc')).toContain('distinctive-marker-123')
  })

  it('does not allow remote images in the default srcdoc', () => {
    const wrapper = mountFrame('<img src="https://evil.test/x.png">')
    expect(wrapper.get('iframe').attributes('srcdoc')).not.toMatch(/img-src data: https:/)
  })
})
