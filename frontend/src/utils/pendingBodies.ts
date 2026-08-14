export type PendingBodiesEntry = {
  ids: string[]
  uidvalidity?: number | null
}

export type PendingBodiesMap = Record<string, PendingBodiesEntry>

export function pendingBodiesKey(email: string, folder: string): string {
  const addr = String(email || '').trim().toLowerCase()
  const f = String(folder || 'inbox').trim().toLowerCase()
  const norm =
    f === 'junk' || f === 'spam' || f === 'junkemail'
      ? 'spam'
      : f === 'sent' || f === 'sentitems' || f === 'sent mail'
        ? 'sent'
        : 'inbox'
  return `${addr}::${norm}`
}

export function rememberPendingBodies(
  map: PendingBodiesMap,
  email: string,
  folder: string,
  ids: string[],
  uidvalidity?: number | null,
): PendingBodiesMap {
  const key = pendingBodiesKey(email, folder)
  const clean = [...new Set(ids.map((id) => String(id || '').trim()).filter(Boolean))]
  const next = { ...map }
  if (!clean.length) {
    delete next[key]
    return next
  }
  next[key] = { ids: clean, uidvalidity: uidvalidity ?? null }
  return next
}

export function peekPendingBodies(
  map: PendingBodiesMap,
  email: string,
  folder: string,
): PendingBodiesEntry | undefined {
  return map[pendingBodiesKey(email, folder)]
}

export function clearPendingBodies(
  map: PendingBodiesMap,
  email: string,
  folder: string,
): PendingBodiesMap {
  const key = pendingBodiesKey(email, folder)
  if (!(key in map)) return map
  const next = { ...map }
  delete next[key]
  return next
}

export function messageHasFullBody(m: {
  body_text?: string | null
  body_html?: string | null
  body_preview?: string | null
}): boolean {
  const html = String(m.body_html || '').trim()
  if (html) return true
  const text = String(m.body_text || '').trim()
  return Boolean(text)
}

export function messagesMissingBodies(
  messages: Array<{
    id?: string | null
    body_text?: string | null
    body_html?: string | null
    body_preview?: string | null
  }>,
  pendingIds: string[],
): string[] {
  const have = new Set(
    messages
      .filter((m) => messageHasFullBody(m))
      .map((m) => String(m.id || '').trim())
      .filter(Boolean),
  )
  return pendingIds
    .map((id) => String(id || '').trim())
    .filter((id) => id && !have.has(id))
}
