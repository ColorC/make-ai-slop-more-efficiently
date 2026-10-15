import { resolveDisplayPlugin } from '../material-core/index.js'
import { inferFormatTag, normalizeFormatTag } from '../material-core/formats.js'

const list = (value) => value === undefined ? [] : Array.isArray(value) ? value : [value]

const mediaMatches = (pattern, actual) => {
  if (pattern === actual || pattern === '*/*') return true
  if (!pattern.includes('*')) return false
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*')
  return new RegExp(`^${escaped}$`).test(actual)
}

const scoreRenderer = (entry, material, representation) => {
  const match = entry?.match ?? {}
  const hints = list(match.renderer_hint)
  const formatTags = list(match.format_tag ?? match.formatTag).map(normalizeFormatTag).filter(Boolean)
  const explicitFormatTag = normalizeFormatTag(representation?.format_tag ?? representation?.formatTag)
  // An explicit format contract opts out of MIME-only generic renderers. A
  // host must register a renderer for that tag instead of silently showing a
  // file fallback (especially for JSON table/diff/tutorial payloads).
  if (explicitFormatTag && !formatTags.length) return -1
  const mediaTypes = list(match.media_type)
  const labels = list(match.labels)
  let score = 0
  if (formatTags.length) {
    const actual = inferFormatTag(representation)
    if (!actual || !formatTags.includes(actual)) return -1
    score += 500
  }
  if (hints.length) {
    if (!representation.renderer_hint || !hints.includes(representation.renderer_hint)) return -1
    score += 400
  }
  if (mediaTypes.length) {
    const matches = mediaTypes.filter((pattern) => mediaMatches(pattern, representation.media_type))
    if (!matches.length) return -1
    score += matches.some((pattern) => pattern === representation.media_type) ? 300 : 200
  }
  if (labels.length) {
    if (!labels.every((label) => material.labels.includes(label))) return -1
    score += 100 + labels.length
  }
  if (typeof match.when === 'function') {
    if (!match.when(material, representation)) return -1
    score += 50
  }
  if (!formatTags.length && !hints.length && !mediaTypes.length && !labels.length && typeof match.when !== 'function') return -1
  return score + (Number.isFinite(entry.priority) ? entry.priority : 0)
}

const explicitOverrideResolution = (resolution) => resolution === 'member-override'
  || resolution === 'group-override'
  || resolution === 'composition-override'

export const displayContextForItem = (viewMaterial, member) => {
  const groups = Array.isArray(viewMaterial?.view?.groups) ? viewMaterial.view.groups : []
  const group = groups.find((candidate) => candidate?.id === member?.group
    || candidate?.label === member?.group
    || candidate?.title === member?.group)
  return { member, group, composition: viewMaterial?.view }
}

export function selectMaterialRenderer(renderers, material, representation, context = {}) {
  if (!representation) return undefined
  const available = (renderers ?? []).filter((entry) => typeof entry?.render === 'function')
  const materialForResolution = {
    ...material,
    representations: [representation, ...(material.representations ?? []).filter((item) => item.id !== representation.id)],
  }
  const pluginDefinitions = available.map((entry) => ({
    id: entry.id,
    hints: list(entry.match?.renderer_hint),
    formatTags: list(entry.match?.format_tag ?? entry.match?.formatTag).map(normalizeFormatTag).filter(Boolean),
    acceptsFormatTags: list(entry.match?.format_tag ?? entry.match?.formatTag).map(normalizeFormatTag).filter(Boolean),
    mediaTypes: list(entry.match?.media_type).filter((value) => !value.includes('*')),
    labels: list(entry.match?.labels),
  }))
  const resolved = resolveDisplayPlugin(materialForResolution, pluginDefinitions, context)
  if (resolved.kind === 'plugin' && explicitOverrideResolution(resolved.resolution)) {
    const entry = available.find((candidate) => candidate.id === resolved.plugin_id)
    if (entry) return entry
  }
  return available
    .map((entry, order) => ({ entry, order, score: scoreRenderer(entry, material, representation) }))
    .filter((candidate) => candidate.score >= 0)
    .sort((left, right) => right.score - left.score || left.order - right.order)[0]?.entry
}

/** Create the product-neutral renderer set. Host entries are placed before these defaults. */
export function createDefaultMaterialRenderers(createElement, options = {}) {
  if (typeof createElement !== 'function') throw new TypeError('createElement is required')
  const h = createElement
  return [
    {
      id: 'material.default.markdown',
      match: { format_tag: ['markdown'], media_type: ['text/markdown'] },
      capabilities: ['read', 'print', 'mobile'],
      render: ({ material, representation, content }) => options.renderMarkdown
        ? options.renderMarkdown(content ?? '', material, representation)
        : h('pre', { className: 'mc-material__plain' }, content ?? ''),
    },
    {
      id: 'material.default.json',
      match: { format_tag: ['json'], media_type: ['application/json', 'application/*+json'] },
      capabilities: ['read', 'print', 'mobile'],
      render: ({ content }) => {
        if (typeof content !== 'string') return h('span', null, 'JSON 载体尚未加载。')
        let value
        try { value = JSON.parse(content) } catch { return h('pre', { className: 'mc-material__plain' }, content) }
        return h('pre', { className: 'mc-material__plain mc-material__json' }, JSON.stringify(value, null, 2))
      },
    },
    {
      id: 'material.default.image',
      match: { format_tag: ['image'], media_type: ['image/*'] },
      capabilities: ['read', 'fullscreen', 'mobile', 'source-navigation'],
      render: ({ material, representation, content, onOpenRepresentation }) => h('figure', { className: 'mc-material__image' },
        h('img', { src: content || representation.source_ref.ref, alt: representation.title || material.title }),
        h('figcaption', null,
          h('span', null, representation.title || representation.media_type),
          onOpenRepresentation ? h('button', { type: 'button', onClick: () => onOpenRepresentation(material, representation) }, '打开来源') : null)),
    },
    {
      id: 'material.default.tutorial',
      match: { format_tag: ['tutorial'] },
      capabilities: ['read', 'fullscreen', 'mobile'],
      render: ({ material, representation, content }) => options.renderTutorial
        ? options.renderTutorial(content ?? '', material, representation)
        : h('pre', { className: 'mc-material__plain mc-material__tutorial' }, content ?? ''),
    },
    {
      id: 'material.default.mermaid',
      match: { format_tag: ['mermaid'] },
      capabilities: ['read', 'print', 'mobile'],
      render: ({ material, representation, content }) => options.renderMermaid
        ? options.renderMermaid(content ?? '', material, representation)
        : h('pre', { className: 'mc-material__plain mc-material__mermaid' }, content ?? ''),
    },
    {
      id: 'material.default.latex',
      match: { format_tag: ['latex'] },
      capabilities: ['read', 'print', 'mobile'],
      render: ({ material, representation, content }) => options.renderLatex
        ? options.renderLatex(content ?? '', material, representation)
        : h('pre', { className: 'mc-material__plain mc-material__latex' }, content ?? ''),
    },
    {
      id: 'material.default.text',
      match: { media_type: ['text/*'], when: (_material, representation) => representation.media_type !== 'text/markdown' && !representation.format_tag },
      capabilities: ['read', 'print', 'mobile'],
      render: ({ content }) => h('pre', { className: 'mc-material__plain' }, content ?? '文本载体尚未加载。'),
    },
    {
      id: 'material.default.file',
      match: { media_type: ['application/*', 'audio/*', 'video/*'] },
      capabilities: ['read', 'source-navigation'],
      render: ({ material, representation, onOpenRepresentation }) => h('div', { className: 'mc-material__file' },
        h('strong', null, representation.title || '文件载体'),
        h('span', null, representation.media_type),
        h('code', null, `${representation.source_ref.kind}:${representation.source_ref.ref}`),
        onOpenRepresentation ? h('button', { type: 'button', onClick: () => onOpenRepresentation(material, representation) }, '打开来源') : null),
    },
  ]
}
