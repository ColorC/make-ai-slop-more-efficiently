import { createHttpMaterialProvider } from '@omni/material-client'
import type { MaterialRepresentation } from '@omni/material-core'

const normalizedBase = (base: string): string => base.replace(/\/$/, '')

/** BSide owns this transport adapter; standalone and aggregated shells consume the same factory. */
export function createBsideMaterialHttpProvider(options: { base: string; fetch?: typeof fetch }) {
  const base = normalizedBase(options.base)
  const api = `${base}/api`
  return createHttpMaterialProvider({
    id: `bside:${base || '/'}`,
    graph_url: `${api}/material/graph`,
    fetch: options.fetch,
    representation_url: (_material, representation) => {
      const source = representation.source_ref
      if (source.kind === 'bside-wiki') return `${api}/wiki/doc?path=${encodeURIComponent(source.ref)}`
      if (source.kind === 'bside-api') return source.ref.startsWith('/bside/') ? source.ref : `${base}${source.ref}`
      return undefined
    },
    decode_representation: async (response, _material, representation) => {
      const value = await response.json() as unknown
      if (representation.source_ref.kind === 'bside-wiki') {
        const document = value as { markdown?: unknown }
        if (typeof document.markdown !== 'string') throw new TypeError('BSide 文档接口没有返回 markdown')
        return { content: document.markdown, content_format: 'markdown' }
      }
      return { content: JSON.stringify(value, null, 2), content_format: 'json' }
    },
  })
}

export function bsideRepresentationHref(baseValue: string, representation: MaterialRepresentation): string | undefined {
  const base = normalizedBase(baseValue)
  const source = representation.source_ref
  if (source.kind === 'bside-wiki') return `${base}/?doc=${encodeURIComponent(source.ref)}`
  if (source.kind === 'source') return `${base}/source?ref=${encodeURIComponent(source.ref)}`
  if (source.kind === 'asset') return `${base}/api/asset?ref=${encodeURIComponent(source.ref)}`
  if (source.kind === 'bside-api') return source.ref
  return undefined
}
