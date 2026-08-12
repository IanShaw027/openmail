/**
 * The number in the retention confirmation.
 *
 * It is the only thing standing between a routine save and irreversibly
 * deleting the user's only copy of their mail, so it has to describe the
 * consequence of *this* change — not the total damage `pruneAll` would do,
 * which also includes the per-folder and per-mailbox caps that apply either way.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useMailCacheStore } from '@/stores/mailCache'

const PER_FOLDER_CAP = 300
const DAY_MS = 86_400_000

function messagesAgedDays(ages: number[]) {
  return ages.map((days, i) => ({
    id: `m${i}`,
    uid: `u${i}`,
    folder: 'inbox',
    subject: `s${i}`,
    from: 'a@b.c',
    date: new Date(Date.now() - days * DAY_MS).toISOString(),
  }))
}

function seed(rows: ReturnType<typeof messagesAgedDays>) {
  const store = useMailCacheStore()
  store.vaultHydrated = true
  // Assign directly: every ingest path caps on the way in, which would hide the
  // over-cap shape this test is about.
  store.byEmail = { 'a@b.c': rows as never }
  return store
}

describe('countPrunedBy', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('counts the messages a smaller window actually removes', () => {
    const store = seed(messagesAgedDays([1, 5, 40, 100, 200]))

    // 40/100/200 days old are outside a 30-day window.
    expect(store.countPrunedBy(30, 365)).toBe(3)
  })

  it('does not blame the retention change for cap overflow', () => {
    // Every message is same-day, so no window removes any of them — but the
    // list is over PER_FOLDER_CAP, so a raw prune count would report the
    // overflow and warn about deleting hundreds of messages for a no-op change.
    const store = seed(messagesAgedDays(Array(PER_FOLDER_CAP + 200).fill(0.5)))

    expect(store.countPrunedBy(30, 90)).toBe(0)
  })

  it('still reports the window-driven deletions on an over-cap mailbox', () => {
    const recent = messagesAgedDays(Array(PER_FOLDER_CAP + 200).fill(0.5))
    const old = messagesAgedDays(Array(50).fill(200))
    const store = seed([...recent, ...old])

    // The old messages are already beyond the cap's reach, so shrinking the
    // window changes nothing for them either.
    expect(store.countPrunedBy(30, 365)).toBe(0)
  })

  it('reports the full prune when there is no current window to compare against', () => {
    const store = seed(messagesAgedDays([1, 40, 100]))

    expect(store.countPrunedBy(30)).toBe(2)
  })

  it('returns null while the cache is still encrypted', () => {
    const store = useMailCacheStore()
    store.vaultHydrated = false

    expect(store.countPrunedBy(30, 90)).toBeNull()
  })
})
