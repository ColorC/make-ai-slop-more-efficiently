import React, { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { resolveView, validateMaterialGraph } from '../material-core/index.js'
import { loadMaterialGraph, loadMaterialRepresentation } from '../material-client/index.js'
import { buildMaterialNavigation, buildMaterialReadModel } from './read-model.js'
import { createDefaultMaterialRenderers, displayContextForItem, selectMaterialRenderer } from './renderers.js'

export { buildMaterialNavigation, buildMaterialReadModel, searchMaterialGraph } from './read-model.js'
export { createDefaultMaterialRenderers, displayContextForItem, selectMaterialRenderer } from './renderers.js'

const h = React.createElement

const shortLabel = (label) => label.includes(':') ? label.split(':').slice(1).join(':') : label
const revisionLabel = (revision) => `${revision.provider} · ${revision.revision}`
const presentationMode = (view) => {
  const mode = view?.view?.presentation?.mode
  return mode === 'continuous' || mode === 'document' ? 'continuous' : 'collection'
}
const parentViewId = (view) => typeof view?.metadata?.parent_view_id === 'string'
  ? view.metadata.parent_view_id : ''

function RepresentationPills({ material, onOpenRepresentation }) {
  return h('div', { className: 'mc-representations', 'aria-label': '材料载体' },
    material.representations.map((representation) => h('button', {
      type: 'button',
      key: representation.id,
      className: `mc-representation mc-representation--${representation.role}`,
      title: `${representation.media_type}\n${representation.source_ref.kind}:${representation.source_ref.ref}`,
      onClick: (event) => {
        event.stopPropagation()
        onOpenRepresentation?.(material, representation)
      },
    }, representation.title || representation.role)))
}

function MaterialCard({ item, onOpenMaterial, onOpenRepresentation }) {
  const { material, member } = item
  const isView = Boolean(material.view)
  return h('article', {
    className: `mc-material-card${isView ? ' mc-material-card--view' : ''}`,
    'data-material-id': material.id,
    onClick: () => onOpenMaterial(material),
  },
  h('div', { className: 'mc-material-card__top' },
    h('span', { className: 'mc-material__eyebrow' }, member.group || '材料')),
  h('button', {
    type: 'button', className: 'mc-material-card__title',
    onClick: (event) => { event.stopPropagation(); onOpenMaterial(material) },
  }, material.title),
  material.summary ? h('p', null, material.summary) : null,
  h('div', { className: 'mc-material-card__meta' },
    h('span', null, isView ? `${material.view.members.length} 项` : revisionLabel(material.revision_ref)),
    h(RepresentationPills, { material, onOpenRepresentation })),
  h('code', null, material.id),
  isView ? h('span', { className: 'mc-material-card__open' }, '打开文档 →') : null)
}

const representationForItem = ({ material, member }) => material.representations.find((representation) =>
  member.representation_id && representation.id === member.representation_id)
  ?? material.representations.find((representation) => representation.role === 'primary')
  ?? material.representations[0]

function RepresentationFallback({ material, representation, onOpenRepresentation }) {
  if (!representation) return null
  return h('div', { className: 'mc-material__fallback' },
    h('strong', null, representation.title || '材料载体'),
    h('span', null, representation.media_type),
    h('span', null, revisionLabel(material.revision_ref)),
    h('code', null, `${representation.source_ref.kind}:${representation.source_ref.ref}`),
    onOpenRepresentation ? h('button', {
      type: 'button', onClick: () => onOpenRepresentation(material, representation),
    }, '打开来源') : null)
}

function MaterialArticle({ item, relationships, onOpenMaterial, onOpenRepresentation, renderMarkdown, renderers, displayContext }) {
  const { material, member } = item
  const primary = representationForItem(item)
  const availableRenderers = useMemo(
    () => [...(renderers ?? []), ...createDefaultMaterialRenderers(h, { renderMarkdown })],
    [renderMarkdown, renderers],
  )
  const renderer = selectMaterialRenderer(availableRenderers, material, primary, displayContext)
  let renderedBody
  try {
    renderedBody = renderer
      ? renderer.render({
        material, representation: primary, content: primary?.content,
        onOpenMaterial, onOpenRepresentation,
      })
      : h(RepresentationFallback, { material, representation: primary, onOpenRepresentation })
  } catch (error) {
    renderedBody = h('div', { className: 'mc-material__renderer-error', role: 'alert' },
      h('strong', null, '领域渲染器无法显示此载体'),
      h('pre', null, error instanceof Error ? error.message : String(error)),
      h(RepresentationFallback, { material, representation: primary, onOpenRepresentation }))
  }
  const related = relationships.filter((relation) => relation.relationship
    && (relation.relationship.from === material.id || relation.relationship.to === material.id))
  return h('article', { id: material.id, className: 'mc-material', 'data-material-id': material.id },
    h('header', { className: 'mc-material__head' },
      h('div', { className: 'mc-material__identity' },
        h('span', { className: 'mc-material__eyebrow' }, member.group || '材料'),
        h('h2', null, material.title),
        h('button', {
          type: 'button', className: 'mc-material__id', title: material.id,
          onClick: () => onOpenMaterial(material),
        }, material.id),
      ),
      h('div', { className: 'mc-material__meta' },
        h('span', { className: 'mc-revision' }, revisionLabel(material.revision_ref)),
        h(RepresentationPills, { material, onOpenRepresentation }),
      ),
    ),
    material.summary ? h('p', { className: 'mc-material__summary' }, material.summary) : null,
    h('div', { className: 'mc-material__body wiki-content', 'data-material-representation': primary?.id }, renderedBody),
    h('footer', { className: 'mc-material__foot' },
      h('div', { className: 'mc-labels' }, material.labels.slice(0, 8).map((label) =>
        h('span', { key: label }, shortLabel(label)))),
      related.length ? h('div', { className: 'mc-relations', 'aria-label': '因果与语义关系' },
        related.slice(0, 12).map((relation) => {
          const edge = relation.relationship
          const outward = edge.from === material.id
          return h('span', { key: relation.id, title: relation.id },
            outward ? '→' : '←', ' ', edge.predicate, ' ', outward ? edge.to : edge.from)
        })) : null,
    ),
  )
}

function MaterialGraphWorkspace({
  graph, theme = 'default', title = '材料工作台', initialViewId,
  onOpenMaterial, onOpenRepresentation, onActiveViewChange, renderMarkdown, renderers,
  loadRepresentation, navigation = 'rail',
}) {
  const [loadedRepresentations, setLoadedRepresentations] = useState(() => new Map())
  const pendingRepresentations = useRef(new Set())
  const [representationError, setRepresentationError] = useState('')
  useEffect(() => {
    // Keep the initial empty Map identity stable. Replacing it on mount schedules a
    // second render, aborts the first representation request, and leaves the retry
    // blocked by pendingRepresentations until the aborted promise settles.
    setLoadedRepresentations((current) => current.size ? new Map() : current)
    pendingRepresentations.current.clear()
    setRepresentationError('')
  }, [graph.graph_id, graph.generated_at])
  const hydratedGraph = useMemo(() => ({
    ...graph,
    materials: graph.materials.map((material) => ({
      ...material,
      representations: material.representations.map((representation) => {
        const key = `${material.id}\n${material.revision_ref.provider}\n${material.revision_ref.object}\n${material.revision_ref.revision}\n${representation.id}`
        const loaded = loadedRepresentations.get(key)
        return loaded ? { ...representation, ...loaded } : representation
      }),
    })),
  }), [graph, loadedRepresentations])
  const validation = useMemo(() => validateMaterialGraph(hydratedGraph), [hydratedGraph])
  const materialsById = useMemo(() => new Map(hydratedGraph.materials.map((material) => [material.id, material])), [hydratedGraph])
  const views = useMemo(() => hydratedGraph.materials.filter((material) => material.view), [hydratedGraph])
  const navRows = useMemo(() => buildMaterialNavigation(hydratedGraph), [hydratedGraph])
  const preferred = initialViewId || hydratedGraph.active_view_id || views[0]?.id || ''
  const [activeViewId, setActiveViewId] = useState(preferred)
  const [mode, setMode] = useState(() => presentationMode(materialsById.get(preferred)) === 'continuous' ? 'expanded' : 'source')
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase())

  const openView = (viewId) => {
    const next = materialsById.get(viewId)
    if (!next?.view) return
    setActiveViewId(viewId)
    setMode(presentationMode(next) === 'continuous' ? 'expanded' : 'source')
    setQuery('')
    onActiveViewChange?.(viewId)
  }
  const openMaterial = (material) => {
    if (material.view) { openView(material.id); return }
    onOpenMaterial?.(material)
  }

  useEffect(() => {
    const next = materialsById.get(preferred)
    setActiveViewId(preferred)
    setMode(presentationMode(next) === 'continuous' ? 'expanded' : 'source')
  }, [preferred])

  useEffect(() => {
    if (typeof loadRepresentation !== 'function' || !activeViewId) return undefined
    let resolved
    try { resolved = resolveView(hydratedGraph, activeViewId, { mode, max_items: 240 }) }
    catch { return undefined }
    const controller = new AbortController()
    const targets = resolved.items.flatMap((item) => {
      if (item.material.view || item.material.relationship) return []
      const representation = representationForItem(item)
      if (!representation || typeof representation.content === 'string') return []
      const material = item.material
      const key = `${material.id}\n${material.revision_ref.provider}\n${material.revision_ref.object}\n${material.revision_ref.revision}\n${representation.id}`
      if (loadedRepresentations.has(key) || pendingRepresentations.current.has(key)) return []
      pendingRepresentations.current.add(key)
      return [{ key, material, representation }]
    })
    if (!targets.length) return () => controller.abort()
    void Promise.allSettled(targets.map(async ({ key, material, representation }) => {
      try {
        const loaded = await loadRepresentation(material, representation, { signal: controller.signal })
        const normalized = typeof loaded === 'string' ? { content: loaded } : loaded
        if (!normalized || typeof normalized.content !== 'string') throw new TypeError('载体读取器没有返回正文')
        return { key, loaded: normalized }
      } finally {
        pendingRepresentations.current.delete(key)
      }
    })).then((results) => {
      if (controller.signal.aborted) return
      const succeeded = results.filter((result) => result.status === 'fulfilled').map((result) => result.value)
      const failed = results.find((result) => result.status === 'rejected')
      if (succeeded.length) setLoadedRepresentations((current) => {
        const next = new Map(current)
        for (const item of succeeded) next.set(item.key, item.loaded)
        return next
      })
      setRepresentationError(failed ? (failed.reason instanceof Error ? failed.reason.message : String(failed.reason)) : '')
    })
    return () => controller.abort()
  }, [activeViewId, hydratedGraph, loadRepresentation, loadedRepresentations, mode])

  if (!validation.ok) {
    return h('section', { className: 'mc-workspace mc-workspace--error', 'data-material-theme': theme },
      h('strong', null, '材料图谱无法读取'),
      h('pre', null, validation.issues.map((issue) => `${issue.path}: ${issue.message}`).join('\n')))
  }
  if (!activeViewId) {
    return h('section', { className: 'mc-workspace mc-workspace--error', 'data-material-theme': theme }, '没有可阅读的文档。')
  }

  let readModel
  try { readModel = buildMaterialReadModel(hydratedGraph, activeViewId, { mode, query: deferredQuery, max_items: 240 }) }
  catch (error) {
    return h('section', { className: 'mc-workspace mc-workspace--error', 'data-material-theme': theme }, String(error))
  }
  const view = materialsById.get(readModel.view_id)
  const isCollection = presentationMode(view) === 'collection'
  const shownItems = deferredQuery
    ? readModel.search_results.map((result, order) => ({
      material: materialsById.get(result.material_id),
      member: { material_id: result.material_id, group: '搜索结果', order },
    }))
    : readModel.items.map((item) => ({ ...item, material: materialsById.get(item.material_id) }))
  const groups = []
  const groupMap = new Map()
  for (const item of shownItems) {
    const label = item.member.group || '材料'
    if (!groupMap.has(label)) {
      const group = { label, items: [] }
      groupMap.set(label, group)
      groups.push(group)
    }
    groupMap.get(label).items.push(item)
  }
  const parent = materialsById.get(parentViewId(view))
  const breadcrumbs = []
  for (const id of readModel.breadcrumbs) {
    const breadcrumb = materialsById.get(id)
    if (breadcrumb) breadcrumbs.push(breadcrumb)
  }

  return h('section', {
    className: `mc-workspace${navigation === 'hidden' ? ' mc-workspace--content' : ''}`,
    'data-material-theme': theme, 'data-material-view': activeViewId, 'data-material-navigation': navigation,
  },
    navigation === 'hidden' ? null : h('aside', { className: 'mc-rail' },
      h('div', { className: 'mc-brand' },
        h('span', { className: 'mc-brand__mark' }, 'M'),
        h('div', null, h('strong', null, title), h('small', null, hydratedGraph.contract_version))),
      h('label', { className: 'mc-search' },
        h('span', { 'aria-hidden': 'true' }, '⌕'),
        h('input', {
          type: 'search', value: query, placeholder: '搜索全部材料…', 'aria-label': '搜索全部材料',
          onChange: (event) => setQuery(event.target.value),
        }),
        query ? h('button', { type: 'button', 'aria-label': '清空搜索', onClick: () => setQuery('') }, '×') : null),
      h('div', { className: 'mc-rail__label' }, '文档'),
      h('nav', { className: 'mc-view-list' }, navRows.map((candidate) => h('button', {
        type: 'button', key: candidate.view_id,
        className: candidate.view_id === activeViewId ? 'active' : '',
        style: { paddingLeft: `${9 + candidate.depth * 14}px` },
        onClick: () => openView(candidate.view_id),
      }, h('span', null, candidate.title), h('small', null, candidate.member_count)))),
      h('div', { className: 'mc-rail__facts' },
        h('span', null, `${hydratedGraph.materials.length} 项材料`),
        h('span', null, `${views.length} 份文档 · ${navRows.length} 个入口`),
        h('span', null, revisionLabel(view.revision_ref))),
    ),
    h('main', { className: 'mc-main' },
      h('header', { className: 'mc-view-head' },
        h('div', { className: 'mc-view-head__copy' },
          h('div', { className: 'mc-breadcrumbs' }, breadcrumbs.map((crumb, index) => h(React.Fragment, { key: crumb.id },
            index ? h('span', { 'aria-hidden': 'true' }, '/') : null,
            index < breadcrumbs.length - 1
              ? h('button', { type: 'button', onClick: () => openView(crumb.id) }, crumb.title)
              : h('span', null, crumb.title)))),
          h('h1', null, deferredQuery ? `搜索：${query.trim()}` : view.title),
          deferredQuery
            ? h('p', null, `在整个工作区中命中 ${readModel.search_results.length}${readModel.search_results.length === 100 ? '+' : ''} 项。`)
            : view.summary ? h('p', null, view.summary) : null,
          h('code', null, deferredQuery ? hydratedGraph.graph_id : view.id)),
        h('div', { className: 'mc-view-actions' },
          parent ? h('button', { type: 'button', className: 'mc-back', onClick: () => openView(parent.id) }, '← 返回上层') : null,
          !deferredQuery ? h('div', { className: 'mc-mode', role: 'group', 'aria-label': '文档读取方式' },
            h('button', { type: 'button', className: mode === 'source' ? 'active' : '', onClick: () => setMode('source') }, '目录'),
            h('button', { type: 'button', className: mode === 'expanded' ? 'active' : '', onClick: () => setMode('expanded') }, '展开文档')) : null),
      ),
      representationError ? h('div', { className: 'mc-diagnostics', role: 'alert' },
        h('span', null, `载体读取失败：${representationError}`)) : null,
      !deferredQuery && readModel.diagnostics.length ? h('div', { className: 'mc-diagnostics' },
        readModel.diagnostics.map((diagnostic, index) => h('span', { key: `${diagnostic.kind}-${index}` },
          `${diagnostic.kind}: ${diagnostic.detail}`))) : null,
      h('div', { className: `mc-document-layout${isCollection || deferredQuery ? ' mc-document-layout--collection' : ''}` },
        h('div', { className: `mc-document${isCollection || deferredQuery ? ' mc-document--collection' : ''}` },
          groups.length ? groups.map((group) => h('section', { className: 'mc-group', key: group.label },
            h('div', { className: 'mc-group__head' }, h('span', null, group.label), h('small', null, group.items.length)),
            h('div', { className: isCollection || deferredQuery ? 'mc-card-grid' : 'mc-reading-flow' },
              group.items.map((item) => isCollection || deferredQuery
                ? h(MaterialCard, {
                  key: `${item.owner_view_id ?? 'search'}:${item.material.id}`,
                  item, onOpenMaterial: openMaterial, onOpenRepresentation,
                })
                : h(MaterialArticle, {
                  key: `${item.owner_view_id}:${item.material.id}`,
                  item, relationships: readModel.relationships,
                  onOpenMaterial: openMaterial, onOpenRepresentation, renderMarkdown, renderers,
                  displayContext: displayContextForItem(view, item.member),
                })))))
            : h('div', { className: 'mc-empty' }, deferredQuery ? '没有匹配的材料。' : '这份文档暂时没有内容。')),
        !isCollection && !deferredQuery && readModel.outline.length
          ? h('aside', { className: 'mc-outline', 'aria-label': '文档大纲' },
            h('strong', null, '文档大纲'),
            h('nav', null, readModel.outline.map((entry) => h('a', {
              key: `${entry.material_id}:${entry.representation_id ?? ''}`,
              href: `#${encodeURIComponent(entry.anchor)}`,
              style: { paddingLeft: `${8 + Math.min(entry.depth, 4) * 10}px` },
            }, entry.title))))
          : null),
    ),
  )
}

function ProviderWorkspace({ provider, ...props }) {
  const [graph, setGraph] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    void loadMaterialGraph(provider, { signal: controller.signal })
      .then(setGraph)
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason instanceof Error ? reason.message : String(reason))
      })
    const unsubscribe = provider.subscribe?.((event) => {
      if (event?.kind === 'graph-changed' && event.graph) setGraph(event.graph)
    })
    return () => { controller.abort(); unsubscribe?.() }
  }, [provider])
  const representationLoader = useCallback((material, representation, options) =>
    loadMaterialRepresentation(provider, material, representation, options), [provider])
  if (error) return h('section', { className: 'mc-workspace mc-workspace--error', 'data-material-theme': props.theme ?? 'default' }, error)
  if (!graph) return h('section', { className: 'mc-workspace mc-workspace--loading', 'data-material-theme': props.theme ?? 'default' }, '正在读取材料…')
  return h(MaterialGraphWorkspace, { ...props, graph, loadRepresentation: props.loadRepresentation ?? representationLoader })
}

/** Shared reader. Product shells own providers, themes and domain renderers; Material/View semantics stay here. */
export function MaterialWorkspace(props) {
  if (props.provider) return h(ProviderWorkspace, props)
  if (!props.graph) return h('section', { className: 'mc-workspace mc-workspace--error', 'data-material-theme': props.theme ?? 'default' }, '缺少 Material Graph 或 Provider。')
  return h(MaterialGraphWorkspace, props)
}
