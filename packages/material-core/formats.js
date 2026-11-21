/**
 * Canonical Material representation formats.
 *
 * `media_type` describes the wire payload. `format_tag` describes the
 * frontend contract.  A MIME type is intentionally not enough to select a
 * renderer: table, table-diff and workflow data may all be JSON.
 */

const definitions = [
  { tag: 'markdown', mediaTypes: ['text/markdown', 'text/plain'], contentFormats: ['markdown', 'text'], rendererIds: ['markdown'], aliases: ['md', 'document', 'wiki-core', 'simulation-report'] },
  { tag: 'json', mediaTypes: ['application/json', 'application/*+json'], contentFormats: ['json'], rendererIds: ['json'], aliases: ['data', 'raw-json'] },
  { tag: 'table', mediaTypes: ['application/json'], contentFormats: ['json'], rendererIds: ['table'], aliases: ['config', 'data-table', 'json-table'] },
  { tag: 'table-diff', mediaTypes: ['application/json'], contentFormats: ['json'], rendererIds: ['table-diff'], aliases: ['config-preview', 'diff-table', 'actual-diff', 'p4-changelist', 'p4-shelve', 'p4-shelve-list'] },
  { tag: 'planning-form', mediaTypes: ['application/json'], contentFormats: ['json'], rendererIds: ['planning-form'], aliases: ['config-planning', 'planning-source', 'planning-forms'] },
  { tag: 'process', mediaTypes: ['application/json'], contentFormats: ['json'], rendererIds: ['process'], aliases: ['workflow-process', 'workflow-stage', 'workflow-run', 'workflow-check', 'workflow-transaction'] },
  { tag: 'image', mediaTypes: ['image/*'], contentFormats: [], rendererIds: ['image'], aliases: ['screenshot', 'raster'] },
  { tag: 'demo', mediaTypes: ['text/html'], contentFormats: ['html', 'text'], rendererIds: ['demo'], aliases: ['webtwin', 'web-demo'] },
  { tag: 'tutorial', mediaTypes: ['application/json', 'text/html'], contentFormats: ['json', 'html', 'text'], rendererIds: ['tutorial-preview'], aliases: ['tutorial-preview', 'guide-preview'] },
  { tag: 'html', mediaTypes: ['text/html'], contentFormats: ['html', 'text'], rendererIds: ['html'], aliases: ['web', 'webpage'] },
  { tag: 'latex', mediaTypes: ['text/x-latex', 'application/x-latex', 'text/latex'], contentFormats: ['latex', 'text'], rendererIds: ['latex'], aliases: ['tex'] },
  { tag: 'mermaid', mediaTypes: ['text/vnd.mermaid', 'text/plain'], contentFormats: ['mermaid', 'text'], rendererIds: ['mermaid'], aliases: ['mermaid-diagram'] },
  { tag: 'pdf', mediaTypes: ['application/pdf'], contentFormats: [], rendererIds: ['pdf'], aliases: [] },
  { tag: 'audio', mediaTypes: ['audio/*'], contentFormats: [], rendererIds: ['audio'], aliases: [] },
  { tag: 'video', mediaTypes: ['video/*'], contentFormats: [], rendererIds: ['video'], aliases: [] },
  { tag: 'material-view', mediaTypes: ['application/vnd.omni.view+json'], contentFormats: ['json'], rendererIds: ['view'], aliases: ['view'] },
  { tag: 'relationship', mediaTypes: ['application/vnd.omni.relationship+json'], contentFormats: ['json'], rendererIds: ['relation'], aliases: ['relations'] },
  { tag: 'prefab', mediaTypes: ['application/vnd.unity.prefab', 'application/json'], contentFormats: ['json', 'text'], rendererIds: ['prefab'], aliases: ['unity-prefab'] },
  { tag: 'story', mediaTypes: ['text/markdown', 'application/json'], contentFormats: ['markdown', 'json', 'text'], rendererIds: ['story'], aliases: ['story-simulator'] },
  { tag: 'screen-elements', mediaTypes: ['application/vnd.omni.screen-elements+json'], contentFormats: ['json'], rendererIds: ['screen-elements'], aliases: [] },
  { tag: 'screen-annotations', mediaTypes: ['application/vnd.omni.screen-annotations+json'], contentFormats: ['json'], rendererIds: ['screen-annotations'], aliases: [] },
  { tag: 'material-ref', mediaTypes: ['application/vnd.omni.material-ref+json'], contentFormats: ['json'], rendererIds: ['material-ref'], aliases: [] },
  { tag: 'perforce', mediaTypes: ['application/vnd.perforce'], contentFormats: ['text', 'json'], rendererIds: ['file'], aliases: ['p4'] },
]

const byTag = new Map(definitions.map((definition) => [definition.tag, Object.freeze({ ...definition, aliases: Object.freeze([...definition.aliases]) })]))
const aliases = new Map(definitions.flatMap((definition) => definition.aliases.map((alias) => [alias.trim().toLowerCase(), definition.tag])))

export const MATERIAL_FORMATS = Object.freeze(Object.fromEntries([...byTag].map(([tag, definition]) => [tag, definition])))

export function normalizeFormatTag(value) {
  if (typeof value !== 'string') return null
  const normalized = value.trim().toLowerCase()
  if (!normalized) return null
  return byTag.has(normalized) ? normalized : (aliases.get(normalized) ?? null)
}

export function materialFormat(tag) {
  const normalized = normalizeFormatTag(tag)
  return normalized ? MATERIAL_FORMATS[normalized] ?? null : null
}

function mediaMatches(pattern, actual) {
  if (pattern === actual || pattern === '*/*') return true
  if (!pattern.includes('*')) return false
  const escaped = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replaceAll('\\*', '.*')
  return new RegExp(`^${escaped}$`, 'i').test(actual)
}

export function formatSupportsMediaType(tag, mediaType) {
  const definition = materialFormat(tag)
  return Boolean(definition && typeof mediaType === 'string' && definition.mediaTypes.some((pattern) => mediaMatches(pattern, mediaType)))
}

export function formatTagsForMediaType(mediaType) {
  if (typeof mediaType !== 'string') return []
  return [...byTag.values()].filter((definition) => definition.mediaTypes.some((pattern) => mediaMatches(pattern, mediaType))).map((definition) => definition.tag)
}

/** Infer only when the representation is legacy. Ambiguous JSON defaults to raw JSON. */
export function inferFormatTag(representation = {}) {
  const explicit = normalizeFormatTag(representation.format_tag ?? representation.formatTag)
  if (explicit) return explicit
  const hint = normalizeFormatTag(representation.renderer_hint)
  if (hint) return hint
  const candidates = formatTagsForMediaType(representation.media_type)
  if (candidates.includes('json')) return 'json'
  if (candidates.includes('markdown')) return 'markdown'
  return candidates[0] ?? null
}

export function validateMaterialRepresentationFormat(representation, where = 'representation', options = {}) {
  const problems = []
  const rawTag = representation?.format_tag ?? representation?.formatTag
  const tag = normalizeFormatTag(rawTag)
  if (!tag) {
    const hasUnknownTag = typeof rawTag === 'string' && rawTag.trim()
    const severity = hasUnknownTag || options.requireFormatTag !== false ? 'error' : 'warning'
    problems.push({
      path: `${where}.format_tag`,
      code: typeof rawTag === 'string' && rawTag.trim() ? 'format-tag.unknown' : 'format-tag.missing',
      severity,
      message: typeof rawTag === 'string' && rawTag.trim()
        ? `unknown format_tag=${rawTag}`
        : 'format_tag is required; renderer_hint/media_type are compatibility hints only',
    })
    return problems
  }
  if (!formatSupportsMediaType(tag, representation?.media_type)) {
    problems.push({ path: `${where}.media_type`, code: 'format.media-mismatch', severity: 'error', message: `format_tag=${tag} does not accept media_type=${String(representation?.media_type)}` })
  }
  const contentFormat = representation?.content_format
  const definition = materialFormat(tag)
  if (contentFormat !== undefined && definition && definition.contentFormats.length > 0 && !definition.contentFormats.includes(contentFormat)) {
    problems.push({ path: `${where}.content_format`, code: 'format.content-mismatch', severity: 'error', message: `format_tag=${tag} does not accept content_format=${String(contentFormat)}` })
  }
  return problems
}

export const MATERIAL_FORMAT_TAGS = Object.freeze([...byTag.keys()])
