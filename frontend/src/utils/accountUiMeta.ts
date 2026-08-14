/**
 * Browser-local UI metadata for accounts that may not live only in the vault.
 *
 * groupId / starred are not server Account fields. Local vault rows already
 * store them; cloud-only rows (no local vault twin) need a durable map so a
 * refresh does not reset grouping / stars.
 *
 * Keyed by lowercase email. No secrets — plain localStorage is fine.
 */

export interface AccountUiMeta {
  groupId?: string
  starred?: boolean
  /** Last time the user opened this mailbox (or first-seen baseline). */
  mailSeenAt?: number
}

const KEY = 'openmail.accountUiMeta.v1'

type MetaMap = Record<string, AccountUiMeta>

function loadMap(): MetaMap {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as MetaMap
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function saveMap(map: MetaMap): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(map))
  } catch (e) {
    console.warn('[openmail] accountUiMeta persist failed', e)
  }
}

export function getAccountUiMeta(email: string): AccountUiMeta | undefined {
  const e = email.toLowerCase().trim()
  if (!e) return undefined
  return loadMap()[e]
}

/** Merge patch into durable UI meta for this mailbox email. */
export function patchAccountUiMeta(
  email: string,
  patch: AccountUiMeta,
): AccountUiMeta {
  const e = email.toLowerCase().trim()
  if (!e) return patch
  const map = loadMap()
  const prev = map[e] || {}
  const next: AccountUiMeta = { ...prev }
  if (patch.groupId !== undefined) next.groupId = patch.groupId || 'default'
  if (patch.starred !== undefined) next.starred = Boolean(patch.starred)
  if (patch.mailSeenAt !== undefined) {
    next.mailSeenAt = Number.isFinite(patch.mailSeenAt) ? patch.mailSeenAt : undefined
  }
  // Drop empty entries to keep storage small
  if (
    (!next.groupId || next.groupId === 'default') &&
    !next.starred &&
    !next.mailSeenAt
  ) {
    // Keep default + unstarred as an explicit record only when user set them
    // (needed so cloud-only rows do not lose "back to default")
    map[e] = next
  } else {
    map[e] = next
  }
  saveMap(map)
  return next
}

export function removeAccountUiMeta(email: string): void {
  const e = email.toLowerCase().trim()
  if (!e) return
  const map = loadMap()
  if (!(e in map)) return
  delete map[e]
  saveMap(map)
}

/**
 * Remove UI meta only when no remaining account still uses this email.
 */
export function removeAccountUiMetaIfUnused(
  email: string,
  stillPresentEmails: Iterable<string>,
): void {
  const e = email.toLowerCase().trim()
  if (!e) return
  for (const x of stillPresentEmails) {
    if (String(x || '').toLowerCase().trim() === e) return
  }
  removeAccountUiMeta(e)
}

/**
 * Fill only truly missing fields from durable map.
 * Vault values (including explicit groupId 'default' / starred false) win —
 * never treat 'default' as missing (that resurrected old groups on hydrate).
 */
export function fillMissingAccountUiMeta<
  T extends { email: string; groupId?: string; starred?: boolean; mailSeenAt?: number },
>(acc: T): T {
  const meta = getAccountUiMeta(acc.email)
  if (!meta) return acc
  let next = acc
  if (acc.groupId === undefined || acc.groupId === null) {
    if (meta.groupId) next = { ...next, groupId: meta.groupId }
  }
  if (acc.starred === undefined && meta.starred !== undefined) {
    next = { ...next, starred: meta.starred }
  }
  if (acc.mailSeenAt === undefined && meta.mailSeenAt !== undefined) {
    next = { ...next, mailSeenAt: meta.mailSeenAt }
  }
  return next
}

/**
 * Cloud-only mapping: durable browser meta is source of truth for group/star
 * when present (API has no columns; prev is only in-memory this session).
 */
export function applyCloudAccountUiMeta<
  T extends { email: string; groupId?: string; starred?: boolean; mailSeenAt?: number },
>(acc: T): T {
  const meta = getAccountUiMeta(acc.email)
  if (!meta) return acc
  let next = acc
  if (meta.groupId !== undefined) next = { ...next, groupId: meta.groupId }
  if (meta.starred !== undefined) next = { ...next, starred: meta.starred }
  if (meta.mailSeenAt !== undefined) next = { ...next, mailSeenAt: meta.mailSeenAt }
  return next
}
