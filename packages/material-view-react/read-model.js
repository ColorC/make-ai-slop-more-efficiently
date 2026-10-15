import { resolveView, validateMaterialGraph } from '../material-core/index.js'

const parentViewId = (view) => typeof view?.metadata?.parent_view_id === 'string'
  ? view.metadata.parent_view_id : ''
const stableCompare = (left, right) => left < right ? -1 : left > right ? 1 : 0

const searchableText = (material) => [
  material.title, material.id, material.summary, ...(material.labels ?? []),
  ...(material.metadata?.aliases ?? []),
  ...material.representations.map((representation) => representation.content),
].filter(Boolean).join('\n').toLocaleLowerCase()

export function buildMaterialNavigation(graph) {
  const validation = validateMaterialGraph(graph)
  if (!validation.ok) throw new TypeError(`Invalid Material graph: ${validation.issues.map((issue) => issue.path).join(', ')}`)
  const materialsById = new Map(graph.materials.map((material) => [material.id, material]))
  const views = graph.materials.filter((material) => material.view && material.metadata?.navigation !== false)
  const visibleIds = new Set(views.map((view) => view.id))
  const children = new Map()
  for (const view of views) {
    const declaredParent = parentViewId(view)
    const parent = visibleIds.has(declaredParent) ? declaredParent : ''
    const rows = children.get(parent) ?? []
    rows.push(view)
    children.set(parent, rows)
  }
  for (const [parentId, rows] of children) {
    const memberOrder = new Map((materialsById.get(parentId)?.view?.members ?? [])
      .map((member, index) => [member.material_id, member.order ?? index]))
    rows.sort((left, right) => (memberOrder.get(left.id) ?? Number.MAX_SAFE_INTEGER)
      - (memberOrder.get(right.id) ?? Number.MAX_SAFE_INTEGER)
      || stableCompare(left.id, right.id))
  }
  const output = []
  const visited = new Set()
  const visit = (parent, depth) => {
    for (const view of children.get(parent) ?? []) {
      if (visited.has(view.id)) continue
      visited.add(view.id)
      output.push({
        view_id: view.id,
        parent_view_id: parent || null,
        title: view.title,
        member_count: view.view.members.length,
        depth,
      })
      visit(view.id, depth + 1)
    }
  }
  visit('', 0)
  return output
}

export function searchMaterialGraph(graph, query, options = {}) {
  const terms = String(query ?? '').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return []
  const limit = Number.isFinite(options.limit) ? Math.max(0, options.limit) : 100
  return graph.materials
    .filter((material) => !material.view && !material.relationship)
    .filter((material) => {
      const text = searchableText(material)
      return terms.every((term) => text.includes(term))
    })
    .slice(0, limit)
    .map((material) => ({
      material_id: material.id,
      revision_ref: structuredClone(material.revision_ref),
      title: material.title,
    }))
}

/** Serializable, shell-neutral read model used by every host. */
export function buildMaterialReadModel(graph, viewId, options = {}) {
  const mode = options.mode === 'expanded' ? 'expanded' : 'source'
  const resolved = resolveView(graph, viewId, {
    mode,
    max_depth: options.max_depth,
    max_items: options.max_items ?? 240,
  })
  const materialsById = new Map(graph.materials.map((material) => [material.id, material]))
  const breadcrumbIds = []
  const seen = new Set()
  let cursor = resolved.view
  while (cursor?.view && !seen.has(cursor.id)) {
    breadcrumbIds.unshift(cursor.id)
    seen.add(cursor.id)
    cursor = materialsById.get(parentViewId(cursor))
  }
  return {
    contract_version: graph.contract_version,
    graph_id: graph.graph_id,
    view_id: resolved.view.id,
    view_revision_ref: structuredClone(resolved.view.revision_ref),
    mode,
    navigation: buildMaterialNavigation(graph),
    breadcrumbs: breadcrumbIds,
    items: resolved.items.map((item) => ({
      material_id: item.material.id,
      revision_ref: structuredClone(item.material.revision_ref),
      member: structuredClone(item.member),
      depth: item.depth,
      owner_view_id: item.owner_view_id,
    })),
    outline: resolved.items
      .filter((item) => !item.material.view && !item.material.relationship)
      .map((item) => ({
        material_id: item.material.id,
        representation_id: item.member.representation_id
          ?? item.material.representations.find((representation) => representation.role === 'primary')?.id
          ?? item.material.representations[0]?.id,
        title: item.material.title,
        depth: item.depth,
        anchor: item.material.id,
      })),
    relationships: resolved.relationships.map((relationship) => relationship.id),
    diagnostics: structuredClone(resolved.diagnostics),
    search_results: searchMaterialGraph(graph, options.query, { limit: options.search_limit }),
  }
}
