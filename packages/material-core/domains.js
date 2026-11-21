const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0

/** Top-level domain of a Material id: material:walker/x → walker; material-wiki/walker → walker; walker.card.x → walker */
export function materialDomain(id) {
  if (!nonEmpty(id)) return ''
  let rest = id.replace(/^material-graph:/, '').replace(/^(?:material|view|relationship|content|value):/, '')
  if (rest.startsWith('material-wiki/')) rest = rest.slice('material-wiki/'.length)
  const slash = rest.indexOf('/')
  const head = (slash < 0 ? rest : rest.slice(0, slash)).trim()
  const dot = head.indexOf('.')
  if (dot > 0 && !head.includes(':')) return head.slice(0, dot)
  return head
}

export function belongsToDomain(id, domain) {
  if (!nonEmpty(domain)) return true
  const wanted = domain.trim().toLowerCase()
  const value = String(id ?? '')
  const lower = value.toLowerCase()
  if (materialDomain(value).toLowerCase() === wanted) return true
  if (lower.includes(`material-wiki/${wanted}/`) || lower.endsWith(`material-wiki/${wanted}`)) return true
  if (lower.startsWith(`${wanted}.`) || lower.includes(`:${wanted}.`) || lower.includes(`/${wanted}/`)) return true
  return false
}

export function parseDomainRegistry(input) {
  if (!input || typeof input !== 'object') throw new TypeError('domain registry must be an object')
  const host = String(input.host_domain ?? '').trim()
  if (!host) throw new TypeError('domain registry host_domain is required')
  const foreign = []
  for (const item of Array.isArray(input.foreign_domains) ? input.foreign_domains : []) {
    const domain = typeof item === 'string' ? item : item?.domain
    if (!nonEmpty(domain)) continue
    foreign.push({
      domain: String(domain).trim(),
      graph_entry: typeof item?.graph_entry === 'string' ? item.graph_entry : undefined,
      allow_write: Boolean(item?.allow_write),
    })
  }
  return { host_domain: host, foreign_domains: foreign }
}

export function registryAllows(registry, hostDomain, foreignDomain) {
  if (!registry || registry.host_domain !== hostDomain) return false
  return registry.foreign_domains.some((item) => item.domain === foreignDomain)
}

export function isVisibleInHost(materialId, hostDomain, registry) {
  if (belongsToDomain(materialId, hostDomain)) return true
  return registryAllows(registry, hostDomain, materialDomain(materialId))
}

/** Default listing/search/View membership. Foreign members stay out until the registry is loaded. */
export function filterMaterialsForHost(materials, hostDomain, registry) {
  return materials.filter((material) => isVisibleInHost(material.id, hostDomain, registry))
}

export function filterViewMembersForHost(members, hostDomain, registry) {
  return members.filter((member) => isVisibleInHost(member.material_id, hostDomain, registry))
}

/**
 * Compose host + foreign graphs. Without a registry the host graph is unchanged
 * and colliding ids stay two objects (foreign kept aside, not merged).
 */
export function composeDomainGraphs(hostGraph, foreignGraph, hostDomain, registry) {
  const foreignDomain = materialDomain(foreignGraph.graph_id.replace(/^material-graph:/, ''))
    || [...new Set(foreignGraph.materials.map((material) => materialDomain(material.id)))].find((item) => item && item !== hostDomain)
    || ''
  if (!registryAllows(registry, hostDomain, foreignDomain)) {
    return {
      graph: hostGraph,
      held_apart: foreignGraph.materials.map((material) => ({ origin: foreignDomain, id: material.id })),
      merged: false,
    }
  }
  const seen = new Set(hostGraph.materials.map((material) => material.id))
  const extras = []
  for (const material of foreignGraph.materials) {
    if (seen.has(material.id)) continue
    seen.add(material.id)
    extras.push(material)
  }
  return {
    graph: { ...hostGraph, materials: [...hostGraph.materials, ...extras] },
    held_apart: [],
    merged: extras.length > 0 || foreignGraph.materials.length === 0,
  }
}
