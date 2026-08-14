/** Bare `c` copies the panel OTP. Cmd/Ctrl+C and a real text selection stay native. */
export function shouldHandleCodeCopyShortcut(
  e: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey'>,
  opts: { hasPanelCode: boolean; selectedText?: string },
): boolean {
  if (e.key !== 'c' && e.key !== 'C') return false
  if (e.metaKey || e.ctrlKey || e.altKey) return false
  if (!opts.hasPanelCode) return false
  if (String(opts.selectedText ?? '').trim() !== '') return false
  return true
}
