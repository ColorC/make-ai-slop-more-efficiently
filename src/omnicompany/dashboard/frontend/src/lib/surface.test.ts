import { describe, expect, it } from 'vitest'
import { readSurface } from './surface'

describe('readSurface', () => {
  it('accepts the standalone CLI session companion surface', () => {
    expect(readSurface('?surface=session-companion&id=862bb496844d41ee')).toEqual({
      surface: 'session-companion',
      id: '862bb496844d41ee',
    })
  })
})
