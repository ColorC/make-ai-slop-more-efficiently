import { validateMaterialGraph } from '../material-core/index.js'

export { MATERIAL_PLUGIN_CONTRACT_VERSION, createMaterialPluginHost, validateMaterialPluginManifest } from './plugins.js'

export class MaterialProviderError extends Error {
  constructor(message, options = {}) {
    super(message, options)
    this.name = 'MaterialProviderError'
    this.code = options.code ?? 'provider-error'
    this.detail = options.detail
  }
}

export class MaterialWriteConflictError extends MaterialProviderError {
  constructor(message, detail) {
    super(message, { code: 'revision-conflict', detail })
    this.name = 'MaterialWriteConflictError'
  }
}

const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0
const clone = (value) => structuredClone(value)
const stableValue = (value) => Array.isArray(value) ? value.map(stableValue)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]))
    : value
const writeSignature = (request) => JSON.stringify(stableValue(request))
const revisionKey = (revision) => revision
  ? `${revision.provider}\n${revision.object}\n${revision.revision}` : ''

export function assertMaterialProvider(provider) {
  if (!provider || typeof provider !== 'object' || typeof provider.readGraph !== 'function') {
    throw new TypeError('Material provider must expose readGraph(options)')
  }
}

export async function loadMaterialGraph(provider, options = {}) {
  assertMaterialProvider(provider)
  const graph = await provider.readGraph({ signal: options.signal })
  const validation = validateMaterialGraph(graph)
  if (!validation.ok) {
    throw new MaterialProviderError('Material provider returned an invalid graph', {
      code: 'invalid-graph', detail: validation.issues,
    })
  }
  return graph
}

export async function loadMaterialRepresentation(provider, material, representation, options = {}) {
  assertMaterialProvider(provider)
  if (typeof representation?.content === 'string') {
    return { content: representation.content, content_format: representation.content_format }
  }
  if (typeof provider.readRepresentation !== 'function') {
    throw new MaterialProviderError(`Provider cannot load representation ${material?.id ?? '?'}:${representation?.id ?? '?'}`, {
      code: 'representation-unavailable',
    })
  }
  const loaded = await provider.readRepresentation(material, representation, { signal: options.signal })
  const normalized = typeof loaded === 'string' ? { content: loaded } : loaded
  if (!normalized || typeof normalized.content !== 'string') {
    throw new MaterialProviderError('Representation provider must return text or { content, content_format? }', {
      code: 'invalid-representation', detail: loaded,
    })
  }
  return normalized
}

export function validateMaterialWriteRequest(request) {
  const issues = []
  if (!request || typeof request !== 'object') return { ok: false, issues: [{ path: '', message: 'must be an object' }] }
  if (!request.workspace_ref || !nonEmpty(request.workspace_ref.kind) || !nonEmpty(request.workspace_ref.ref)) {
    issues.push({ path: 'workspace_ref', message: 'must be a SourceRef' })
  }
  if (!request.actor_ref || !nonEmpty(request.actor_ref.kind) || !nonEmpty(request.actor_ref.ref)) {
    issues.push({ path: 'actor_ref', message: 'must be an ActorRef' })
  }
  if (!request.target || !nonEmpty(request.target.material_id)) {
    issues.push({ path: 'target.material_id', message: 'must be a non-empty string' })
  }
  if (!nonEmpty(request.idempotency_key)) {
    issues.push({ path: 'idempotency_key', message: 'must be a non-empty string' })
  }
  if (!request.next_graph) {
    issues.push({ path: 'next_graph', message: 'is required' })
  } else {
    const graph = validateMaterialGraph(request.next_graph)
    for (const issue of graph.issues) issues.push({ path: `next_graph.${issue.path}`, message: issue.message })
  }
  return { ok: issues.length === 0, issues }
}

export async function writeMaterialGraph(provider, request) {
  assertMaterialProvider(provider)
  const validation = validateMaterialWriteRequest(request)
  if (!validation.ok) {
    throw new MaterialProviderError('Invalid Material write request', {
      code: 'invalid-write', detail: validation.issues,
    })
  }
  if (typeof provider.writeGraph !== 'function') {
    throw new MaterialProviderError('Material provider is read-only', { code: 'read-only' })
  }
  return provider.writeGraph(request)
}

export function createMemoryMaterialProvider(initialGraph, options = {}) {
  let graph = clone(initialGraph)
  const validation = validateMaterialGraph(graph)
  if (!validation.ok) throw new MaterialProviderError('Initial Material graph is invalid', { code: 'invalid-graph', detail: validation.issues })
  const listeners = new Set()
  const writes = new Map()
  const representations = new Map(Object.entries(options.representations ?? {}))

  const provider = {
    id: options.id ?? 'memory',
    async readGraph() { return clone(graph) },
    async readRepresentation(material, representation) {
      const key = `${material.id}\n${representation.id}`
      if (!representations.has(key)) throw new MaterialProviderError(`Unknown representation ${key}`, { code: 'representation-unavailable' })
      return clone(representations.get(key))
    },
    subscribe(listener) {
      if (typeof listener !== 'function') throw new TypeError('listener must be a function')
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    async writeGraph(request) {
      const validation = validateMaterialWriteRequest(request)
      if (!validation.ok) throw new MaterialProviderError('Invalid Material write request', { code: 'invalid-write', detail: validation.issues })
      if (writes.has(request.idempotency_key)) {
        const previous = writes.get(request.idempotency_key)
        if (previous.signature !== writeSignature(request)) {
          throw new MaterialProviderError('idempotency_key was already used for a different write request', {
            code: 'idempotency-conflict', detail: { idempotency_key: request.idempotency_key },
          })
        }
        return { ...clone(previous.result), status: 'idempotent' }
      }
      const current = graph.materials.find((material) => material.id === request.target.material_id)
      if (current && revisionKey(current.revision_ref) !== revisionKey(request.base_revision_ref)) {
        throw new MaterialWriteConflictError('base_revision_ref does not match the current Material revision', {
          material_id: current.id,
          expected: clone(current.revision_ref),
          received: request.base_revision_ref ? clone(request.base_revision_ref) : null,
        })
      }
      if (!current && request.base_revision_ref) {
        throw new MaterialWriteConflictError('base_revision_ref was supplied for a missing Material', {
          material_id: request.target.material_id, expected: null, received: clone(request.base_revision_ref),
        })
      }
      graph = clone(request.next_graph)
      const result = { status: 'written', graph: clone(graph), idempotency_key: request.idempotency_key }
      writes.set(request.idempotency_key, { signature: writeSignature(request), result })
      for (const listener of listeners) listener({ kind: 'graph-changed', graph: clone(graph), request: clone(request) })
      return clone(result)
    },
  }
  return provider
}

export function createHttpMaterialProvider(options) {
  if (!options || !nonEmpty(options.graph_url)) throw new TypeError('graph_url is required')
  const fetchImpl = options.fetch ?? globalThis.fetch
  if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation is required')
  const request = async (url, init) => {
    const response = await fetchImpl(url, init)
    if (!response.ok) throw new MaterialProviderError(`${response.status} ${response.statusText}`.trim(), {
      code: 'http-error', detail: { status: response.status, url },
    })
    return response
  }
  const representationCache = new Map()
  const representationPending = new Map()
  const representationKey = (material, representation) => [
    material.id,
    material.revision_ref?.provider,
    material.revision_ref?.object,
    material.revision_ref?.revision,
    representation.id,
  ].join('\n')
  return {
    id: options.id ?? options.graph_url,
    async readGraph({ signal } = {}) {
      const response = await request(options.graph_url, { signal, headers: options.headers })
      return response.json()
    },
    async readRepresentation(material, representation, { signal } = {}) {
      if (typeof options.representation_url !== 'function') {
        throw new MaterialProviderError('representation_url is not configured', { code: 'representation-unavailable' })
      }
      const url = options.representation_url(material, representation)
      if (!nonEmpty(url)) throw new MaterialProviderError('representation_url returned no URL', { code: 'representation-unavailable' })
      const key = representationKey(material, representation)
      if (representationCache.has(key)) return clone(representationCache.get(key))
      if (representationPending.has(key)) return clone(await representationPending.get(key))
      const pending = (async () => {
        const response = await request(url, { signal, headers: options.headers })
        const loaded = typeof options.decode_representation === 'function'
          ? await options.decode_representation(response, material, representation)
          : { content: await response.text(), content_format: representation.content_format }
        representationCache.set(key, clone(loaded))
        return loaded
      })()
      representationPending.set(key, pending)
      try { return clone(await pending) } finally { representationPending.delete(key) }
    },
    ...(typeof options.write === 'function' ? { writeGraph: options.write } : {}),
    ...(typeof options.subscribe === 'function' ? { subscribe: options.subscribe } : {}),
  }
}
