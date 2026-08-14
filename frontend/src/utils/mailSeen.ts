import { parseMessageDateMs } from '@/stores/mailCache'

export function countUnseenMessages(
  messages: Array<{ date?: string | number | null }>,
  seenAt?: number | null,
): number {
  if (!isFinitePositive(seenAt)) return 0
  let n = 0
  for (const m of messages) {
    const t = parseMessageDateMs(m.date)
    if (t != null && t > seenAt) n += 1
  }
  return n
}

/** Next watermark: viewing jumps to now; first sighting baselines to newest mail. */
export function nextMailSeenAt(
  current: number | undefined,
  opts: { viewed?: boolean; newestMailMs?: number; now?: number } = {},
): number {
  if (opts.viewed) return opts.now ?? Date.now()
  if (isFinitePositive(current)) return current
  if (isFinitePositive(opts.newestMailMs)) return opts.newestMailMs
  return opts.now ?? Date.now()
}

function isFinitePositive(n: number | null | undefined): n is number {
  return typeof n === 'number' && Number.isFinite(n) && n > 0
}
