import { describe, expect, it } from 'vitest'
import { sessionProjectionTitle } from './sessionProjection'

describe('sessionProjectionTitle', () => {
  it('keeps tab, monitor, and Multiagent title precedence deterministic', () => {
    expect(sessionProjectionTitle({
      provider: 'codex',
      sessionId: 'f4a9aa541df34881',
      topic: 'Confirmed Walker material pilot',
      summaryTitle: 'Companion summary',
      nativeTitle: 'First provider prompt',
      firstMessage: 'First user prompt',
    })).toBe('Confirmed Walker material pilot')

    expect(sessionProjectionTitle({
      provider: 'codex',
      sessionId: 'f4a9aa541df34881',
      summaryTitle: 'Companion summary',
      nativeTitle: 'First provider prompt',
    })).toBe('Companion summary')
  })

  it('falls through to native title and then a stable provider/id label', () => {
    expect(sessionProjectionTitle({
      provider: 'qoder',
      sessionId: 'e47da2bd60454ad1',
      nativeTitle: 'Implement the dashboard fix',
    })).toBe('Implement the dashboard fix')
    expect(sessionProjectionTitle({
      provider: 'qoder',
      sessionId: 'e47da2bd60454ad1',
    })).toBe('Qoder \u00b7 e47da2bd')
  })
})
