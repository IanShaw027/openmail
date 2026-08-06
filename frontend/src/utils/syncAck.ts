/**
 * Client water-mark for cloud delta pull (docs/19-cloud-sync-incremental.md).
 * Persist server_time or server_seq after successful merge.
 */

const SYNC_ACK_KEY = 'openmail.syncAck.v1'

/** Last acknowledged server_time or seq from GET /api/sync/delta. */
export function getSyncAck(): string | null {
  try {
    const v = localStorage.getItem(SYNC_ACK_KEY)
    if (v == null) return null
    const s = String(v).trim()
    return s || null
  } catch {
    return null
  }
}

/** Persist ack after merge (client delta: merge mailCache then ack). */
export function setSyncAck(serverTimeOrSeq: string): void {
  const s = String(serverTimeOrSeq ?? '').trim()
  try {
    if (!s) {
      localStorage.removeItem(SYNC_ACK_KEY)
      return
    }
    localStorage.setItem(SYNC_ACK_KEY, s)
  } catch {
    /* private mode / quota */
  }
}

export function clearSyncAck(): void {
  try {
    localStorage.removeItem(SYNC_ACK_KEY)
  } catch {
    /* ignore */
  }
}
