/** Mailbox data clock: last poll / newest cached message. Not account-row edits. */
export function mailDataUpdatedAt(
  acc: { lastSyncAt?: number; updatedAt?: number },
  newestMailMs?: number | null,
): number | undefined {
  const times: number[] = []
  if (isFinitePositive(acc.lastSyncAt)) times.push(acc.lastSyncAt)
  if (isFinitePositive(newestMailMs)) times.push(newestMailMs)
  return times.length ? Math.max(...times) : undefined
}

/** Account-row metadata clock (note, tags, secrets). */
export function accountRowUpdatedAt(acc: { updatedAt?: number }): number | undefined {
  return isFinitePositive(acc.updatedAt) ? acc.updatedAt : undefined
}

export function parseIsoToMs(value?: string | null): number | undefined {
  if (value == null || String(value).trim() === '') return undefined
  const ms = Date.parse(String(value))
  return Number.isFinite(ms) ? ms : undefined
}

function isFinitePositive(n: number | null | undefined): n is number {
  return typeof n === 'number' && Number.isFinite(n) && n > 0
}
