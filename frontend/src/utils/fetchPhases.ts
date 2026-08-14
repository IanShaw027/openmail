/** First-window IMAP/OAuth uses headers then bodies; catch-up stays one request. */

export type FetchPhase = 'full' | 'headers' | 'bodies'

export function supportsHeaderBodyPhases(provider: string | null | undefined): boolean {
  const p = String(provider || '').toLowerCase()
  return p === 'imap' || p === 'oauth'
}

export function shouldUseHeaderBodyPhases(opts: {
  provider: string | null | undefined
  emptyFolder: boolean
  clearFirst?: boolean
  catchUp?: boolean
}): boolean {
  if (!supportsHeaderBodyPhases(opts.provider)) return false
  if (opts.catchUp) return false
  return Boolean(opts.emptyFolder || opts.clearFirst)
}

export function shouldRefreshOpenFolder(opts: {
  selectedAccountId: string | null | undefined
  expectedAccountId: string
  folder: string
  openFolder: string
}): boolean {
  return (
    opts.selectedAccountId === opts.expectedAccountId &&
    opts.folder === opts.openFolder
  )
}
