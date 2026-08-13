/**
 * IMAP UIDVALIDITY change invalidates prior UIDs in that folder (RFC 4549).
 * Legacy rows without uv still re-key in place — do not wipe the folder.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useMailCacheStore } from '@/stores/mailCache'

function msg(partial: Record<string, unknown>) {
  return {
    subject: 's',
    from: 'a@b.c',
    date: '2026-08-01T00:00:00Z',
    folder: 'inbox',
    ...partial,
  }
}

describe('mailCache merge UIDVALIDITY', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('drops same-folder rows whose uidvalidity differs from the incoming batch', () => {
    const store = useMailCacheStore()
    store.vaultHydrated = true
    store.merge('user@example.com', [
      msg({ id: '1', uidvalidity: 100, subject: 'old-1' }),
      msg({ id: '2', uidvalidity: 100, subject: 'old-2' }),
    ])

    store.merge('user@example.com', [
      msg({ id: '3', uidvalidity: 200, subject: 'new' }),
    ])

    const ids = store.listFor('user@example.com').map((m) => m.id)
    expect(ids).toEqual(['3'])
  })

  it('keeps other folders when only inbox uidvalidity changes', () => {
    const store = useMailCacheStore()
    store.vaultHydrated = true
    store.merge('user@example.com', [
      msg({ id: '1', folder: 'inbox', uidvalidity: 100 }),
      msg({ id: '9', folder: 'spam', uidvalidity: 100, subject: 'spam-keep' }),
    ])

    store.merge('user@example.com', [
      msg({ id: '3', folder: 'inbox', uidvalidity: 200, subject: 'inbox-new' }),
    ])

    const inbox = store.listFor('user@example.com', 'inbox').map((m) => m.id)
    const spam = store.listFor('user@example.com', 'spam').map((m) => m.id)
    expect(inbox).toEqual(['3'])
    expect(spam).toEqual(['9'])
  })

  it('rekeys legacy folder::id rows instead of wiping the folder', () => {
    const store = useMailCacheStore()
    store.vaultHydrated = true
    store.merge('user@example.com', [msg({ id: '42', subject: 'legacy-no-uv' })])

    store.merge('user@example.com', [
      msg({ id: '42', uidvalidity: 99, subject: 'rekeyed' }),
    ])

    const list = store.listFor('user@example.com')
    expect(list).toHaveLength(1)
    expect(list[0]?.id).toBe('42')
    expect(list[0]?.uidvalidity).toBe(99)
    expect(list[0]?.subject).toBe('rekeyed')
  })

  it('does not drop uv-keyed rows when the incoming batch has no uidvalidity', () => {
    const store = useMailCacheStore()
    store.vaultHydrated = true
    store.merge('user@example.com', [
      msg({ id: '1', uidvalidity: 100, subject: 'keep' }),
    ])

    store.merge('user@example.com', [msg({ id: '2', subject: 'no-uv' })])

    const ids = store.listFor('user@example.com').map((m) => m.id).sort()
    expect(ids).toEqual(['1', '2'])
  })
})
