import { describe, expect, it } from 'vitest'

import {
  canonicalMaterialRef,
  materialEmbedUrl,
  materialReviewSurfaceUrl,
  parseCanonicalMaterialRef,
} from './materialReference'

describe('canonical review material references', () => {
  it('round-trips ids without exposing the internal review_material entity kind', () => {
    const ref = canonicalMaterialRef('mat 1/候选')
    expect(ref).toBe('omni://review/mat%201%2F%E5%80%99%E9%80%89')
    expect(parseCanonicalMaterialRef(ref)).toBe('mat 1/候选')
  })

  it('rejects non-canonical and malformed references', () => {
    expect(parseCanonicalMaterialRef('omni://review_material/mat_1')).toBeNull()
    expect(parseCanonicalMaterialRef('omni://review/%E0%A4%A')).toBeNull()
  })

  it('provides one same-origin HTML embed surface', () => {
    expect(materialEmbedUrl('mat candidate'))
      .toBe('/?surface=material-embed&id=mat+candidate')
    expect(materialReviewSurfaceUrl('mat candidate'))
      .toBe('/?surface=material&id=mat+candidate')
  })
})
