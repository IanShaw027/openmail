/**
 * Durable cloud delta watermark for GET /api/sync/delta.
 *
 * Format (preferred): `${updated_at}\t${mail_item_id}`
 * Legacy: bare ISO server_time (still accepted as since without since_id).
 *
 * Never use wall-clock "now" alone as the only ack — race can skip a row
 * with updated_at == request time (review H3).
 */

const KEY = 'openmail.syncAck.v1'

export function getSyncAck(): string | null {
  try {
    const v = localStorage.getItem(KEY)
    return v && v.trim() ? v.trim() : null
  } catch {
    return null
  }
}

export function setSyncAck(serverTimeOrSeq: string): void {
  const s = String(serverTimeOrSeq || '').trim()
  if (!s) return
  try {
    localStorage.setItem(KEY, s)
  } catch {
    /* quota / private mode */
  }
}

export function clearSyncAck(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

/** Encode keyset watermark. */
export function formatSyncAck(updatedAt: string, id?: string | null): string {
  const t = String(updatedAt || '').trim()
  if (!t) return ''
  const i = id != null && String(id).trim() !== '' ? String(id).trim() : ''
  return i ? `${t}\t${i}` : t
}

/** Decode getSyncAck() into delta query params. */
export function parseSyncAck(raw: string | null | undefined): {
  since: string | null
  sinceId: string | null
} {
  const s = String(raw || '').trim()
  if (!s) return { since: null, sinceId: null }
  const tab = s.indexOf('\t')
  if (tab >= 0) {
    return {
      since: s.slice(0, tab).trim() || null,
      sinceId: s.slice(tab + 1).trim() || null,
    }
  }
  // Legacy: ISO only
  return { since: s, sinceId: null }
}
