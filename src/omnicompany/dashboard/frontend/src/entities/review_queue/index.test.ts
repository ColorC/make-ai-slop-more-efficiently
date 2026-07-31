import { describe, expect, it } from 'vitest'
import { DEFAULT_REVIEW_STATUS_BUCKETS } from './index'

describe('review queue entry defaults', () => {
  it('starts with pending only for every entry path', () => {
    expect([...DEFAULT_REVIEW_STATUS_BUCKETS]).toEqual(['pending'])
  })
})
