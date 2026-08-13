import { describe, expect, it } from 'vitest'
import { parseAccountLine } from '@/utils/importParse'

describe('importParse http_api rows', () => {
  it('does not mark a real mailbox+API URL row as an API source shell', () => {
    const parsed = parseAccountLine('user@x.com----https://worker.example/api----key')
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.account.isApiSource).toBe(false)
    expect(parsed.account.email).toBe('user@x.com')
  })
})
