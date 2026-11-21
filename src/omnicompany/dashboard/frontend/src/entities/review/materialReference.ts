export const CANONICAL_REVIEW_KIND = 'review' as const

export function canonicalMaterialRef(materialId: string): string {
  const id = materialId.trim()
  if (!id) throw new Error('material id is required')
  return `omni://${CANONICAL_REVIEW_KIND}/${encodeURIComponent(id)}`
}

export function parseCanonicalMaterialRef(value: string): string | null {
  const match = value.trim().match(/^omni:\/\/review\/([^/?#]+)$/)
  if (!match) return null
  try {
    const id = decodeURIComponent(match[1]).trim()
    return id || null
  } catch {
    return null
  }
}

export function materialEmbedUrl(materialId: string): string {
  const query = new URLSearchParams({
    surface: 'material-embed',
    id: materialId.trim(),
  })
  if (!query.get('id')) throw new Error('material id is required')
  return `/?${query.toString()}`
}

export function materialReviewSurfaceUrl(materialId: string): string {
  const query = new URLSearchParams({
    surface: 'material',
    id: materialId.trim(),
  })
  if (!query.get('id')) throw new Error('material id is required')
  return `/?${query.toString()}`
}
