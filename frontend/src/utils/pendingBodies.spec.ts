import { describe, expect, it } from 'vitest'
import {
  clearPendingBodies,
  messagesMissingBodies,
  peekPendingBodies,
  pendingBodiesKey,
  rememberPendingBodies,
} from './pendingBodies'

describe('pendingBodies', () => {
  it('remembers ids until cleared and scopes by email+folder', () => {
    let map = rememberPendingBodies({}, 'A@x.com', 'inbox', ['11', '10'], 99)
    expect(pendingBodiesKey('a@x.com', 'INBOX')).toBe(pendingBodiesKey('A@x.com', 'inbox'))
    expect(peekPendingBodies(map, 'a@x.com', 'inbox')).toEqual({
      ids: ['11', '10'],
      uidvalidity: 99,
    })
    expect(peekPendingBodies(map, 'a@x.com', 'spam')).toBeUndefined()
    map = clearPendingBodies(map, 'a@x.com', 'inbox')
    expect(peekPendingBodies(map, 'a@x.com', 'inbox')).toBeUndefined()
  })

    it('treats preview-only rows as still missing a body', () => {
    expect(
      messagesMissingBodies(
        [
          {
            id: 'g1',
            body_text: '',
            body_html: '',
            body_preview: 'Your code is 123456',
          },
        ],
        ['g1'],
      ),
    ).toEqual(['g1'])
  })

  it('treats a short full body that matches the preview as complete', () => {
    expect(
      messagesMissingBodies(
        [
          {
            id: 'g1',
            body_text: 'Your code is 123456',
            body_html: '',
            body_preview: 'Your code is 123456',
          },
        ],
        ['g1'],
      ),
    ).toEqual([])
  })
})
