import { parseMessageDateMs } from '@/stores/mailCache'

export function normalizeMailFolder(folder?: string | null): 'inbox' | 'spam' | 'sent' {
  const f = String(folder || 'inbox').toLowerCase()
  if (f === 'spam' || f === 'junk' || f === 'junkemail') return 'spam'
  if (f === 'sent' || f === 'sentitems' || f === 'sent mail') return 'sent'
  return 'inbox'
}

/** Newest inbox message that already has a verification code. */
export function latestInboxVerificationCode(
  messages: Array<{
    folder?: string | null
    date?: string | number | null
    verification_code?: string | null
  }>,
): string | undefined {
  const inbox = messages.filter((m) => normalizeMailFolder(m.folder) === 'inbox')
  const ranked = inbox
    .map((m, idx) => ({
      code: String(m.verification_code || '').trim(),
      ts: parseMessageDateMs(m.date) ?? 0,
      idx,
    }))
    .filter((row) => row.code)
    .sort((a, b) => b.ts - a.ts || a.idx - b.idx)
  return ranked[0]?.code
}

/** Skip patching on failed fetch or empty inbox cache so cloud-synced codes survive. */
export function latestCodePatchForFetch(opts: {
  fetchOk: boolean
  inboxMessages: Array<{
    folder?: string | null
    date?: string | number | null
    verification_code?: string | null
  }>
}): { latestCode: string | undefined } | null {
  if (!opts.fetchOk) return null
  if (!opts.inboxMessages.length) return null
  return { latestCode: latestInboxVerificationCode(opts.inboxMessages) }
}
