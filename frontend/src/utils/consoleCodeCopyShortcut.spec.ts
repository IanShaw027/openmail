import { describe, expect, it } from 'vitest'
import { shouldHandleCodeCopyShortcut } from '@/utils/consoleCodeCopyShortcut'

function ev(partial: Partial<KeyboardEvent>): Pick<
  KeyboardEvent,
  'key' | 'metaKey' | 'ctrlKey' | 'altKey'
> {
  return {
    key: 'c',
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    ...partial,
  }
}

describe('shouldHandleCodeCopyShortcut', () => {
  it('copies the panel code on a bare c when nothing is selected', () => {
    expect(
      shouldHandleCodeCopyShortcut(ev({ key: 'c' }), { hasPanelCode: true }),
    ).toBe(true)
  })

  it('does not hijack Cmd/Ctrl+C', () => {
    expect(
      shouldHandleCodeCopyShortcut(ev({ metaKey: true }), { hasPanelCode: true }),
    ).toBe(false)
    expect(
      shouldHandleCodeCopyShortcut(ev({ ctrlKey: true }), { hasPanelCode: true }),
    ).toBe(false)
  })

  it('does not steal a real text selection', () => {
    expect(
      shouldHandleCodeCopyShortcut(ev({}), {
        hasPanelCode: true,
        selectedText: 'other text',
      }),
    ).toBe(false)
  })
})
