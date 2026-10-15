// Conventional-workflow layer (M1 contract): labels, relation predicates,
// WorkflowSpec validation, run unroll (cycles live in the spec layer only;
// the run layer is always a DAG), and metadata.sync / metadata.assignment parsing.
// Philosophy inherited from material-core: no kind enums in the core schema —
// everything below is convention + helpers over labels/metadata/representations.

const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0

/** §8.1 labels/profile conventions: role is carried by labels, not by a kind enum. */
export const WORKFLOW_LABELS = Object.freeze({
  SOURCE: 'source',
  SPEC: 'spec',
  RUN: 'run',
  CHECK: 'check',
  GATE: 'gate',
  TEMPLATE: 'template',
  PLACEHOLDER: 'placeholder',
  ENV_PROFILE: 'env-profile',
  EPHEMERAL: 'ephemeral',
})

export const WORKFLOW_NODE_KINDS = Object.freeze(['anchor', 'worker', 'check', 'gate'])
export const WORKFLOW_EDGE_KINDS = Object.freeze(['sequence', 'feedback'])
export const ASSIGNMENT_ROLES = Object.freeze(['owner', 'reviewer', 'assistant'])

/** Relation predicates added on top of canvas continues/references/derived. */
export const WORKFLOW_PREDICATES = Object.freeze(['executes', 'overrides', 'instantiates', 'feedback', 'syncs-to'])

export function validateWorkflowSpec(spec) {
  const issues = []
  if (!isRecord(spec)) return { ok: false, issues: [{ path: '$', message: 'spec must be an object' }] }
  if (!nonEmpty(spec.id)) issues.push({ path: 'id', message: 'must be a non-empty string' })
  if (!nonEmpty(spec.title)) issues.push({ path: 'title', message: 'must be a non-empty string' })
  if (!Array.isArray(spec.nodes) || spec.nodes.length === 0) {
    issues.push({ path: 'nodes', message: 'must be a non-empty array' })
    return { ok: false, issues }
  }
  const nodeIds = new Set()
  spec.nodes.forEach((node, index) => {
    const path = `nodes[${index}]`
    if (!isRecord(node)) { issues.push({ path, message: 'must be an object' }); return }
    if (!nonEmpty(node.id)) issues.push({ path: `${path}.id`, message: 'must be a non-empty string' })
    else if (nodeIds.has(node.id)) issues.push({ path: `${path}.id`, message: `duplicate node id ${node.id}` })
    else nodeIds.add(node.id)
    if (!WORKFLOW_NODE_KINDS.includes(node.kind)) issues.push({ path: `${path}.kind`, message: `must be one of ${WORKFLOW_NODE_KINDS.join(', ')}` })
    if (node.kind === 'worker' && !nonEmpty(node.worker_ref)) issues.push({ path: `${path}.worker_ref`, message: 'worker nodes must declare worker_ref' })
    if (node.kind === 'check' && !nonEmpty(node.check_ref)) issues.push({ path: `${path}.check_ref`, message: 'check nodes must declare check_ref' })
    if (node.params !== undefined && !isRecord(node.params)) issues.push({ path: `${path}.params`, message: 'must be an object' })
  })
  const entryOk = nonEmpty(spec.entry) && nodeIds.has(spec.entry)
  if (!entryOk) issues.push({ path: 'entry', message: 'must reference an existing node id' })
  const edges = Array.isArray(spec.edges) ? spec.edges : []
  edges.forEach((edge, index) => {
    const path = `edges[${index}]`
    if (!isRecord(edge)) { issues.push({ path, message: 'must be an object' }); return }
    if (!nodeIds.has(edge.from)) issues.push({ path: `${path}.from`, message: 'must reference an existing node id' })
    if (!nodeIds.has(edge.to)) issues.push({ path: `${path}.to`, message: 'must reference an existing node id' })
    const kind = edge.kind ?? 'sequence'
    if (!WORKFLOW_EDGE_KINDS.includes(kind)) issues.push({ path: `${path}.kind`, message: `must be one of ${WORKFLOW_EDGE_KINDS.join(', ')}` })
    if (kind === 'feedback' && edge.max !== undefined && (!Number.isInteger(edge.max) || edge.max < 1)) {
      issues.push({ path: `${path}.max`, message: 'feedback iteration cap must be a positive integer' })
    }
  })
  // Forward (sequence) edges must stay acyclic: cycles belong in feedback edges.
  const forwardOut = new Map()
  for (const edge of edges) {
    if ((edge.kind ?? 'sequence') !== 'sequence' || !nodeIds.has(edge.from) || !nodeIds.has(edge.to)) continue
    if (!forwardOut.has(edge.from)) forwardOut.set(edge.from, [])
    forwardOut.get(edge.from).push(edge.to)
  }
  const color = new Map() // 0 white, 1 gray, 2 black
  const findCycle = (start) => {
    const stack = [{ id: start, remaining: forwardOut.get(start) ?? [] }]
    color.set(start, 1)
    while (stack.length) {
      const frame = stack[stack.length - 1]
      if (frame.remaining.length === 0) {
        color.set(frame.id, 2)
        stack.pop()
        continue
      }
      const next = frame.remaining.shift()
      const state = color.get(next) ?? 0
      if (state === 1) return [frame.id, next]
      if (state === 0) {
        color.set(next, 1)
        stack.push({ id: next, remaining: forwardOut.get(next) ?? [] })
      }
    }
    return null
  }
  for (const nodeId of nodeIds) {
    if ((color.get(nodeId) ?? 0) !== 0) continue
    const cycle = findCycle(nodeId)
    if (cycle) {
      issues.push({ path: 'edges', message: `sequence edges form a cycle (${cycle[0]} -> ${cycle[1]}); cycles must be feedback edges` })
      break
    }
  }
  return { ok: issues.length === 0, issues }
}

export function assertWorkflowSpec(spec) {
  const result = validateWorkflowSpec(spec)
  if (!result.ok) throw new TypeError(`Invalid WorkflowSpec: ${result.issues.slice(0, 8).map((item) => `${item.path}: ${item.message}`).join('; ')}`)
  return spec
}

/** Read a WorkflowSpec from a spec material's primary JSON representation. */
export function parseSpecMaterial(material) {
  const representation = material?.representations?.find((item) => item.role === 'primary') ?? material?.representations?.[0]
  if (!representation || typeof representation.content !== 'string') return null
  if (representation.content_format !== 'json') return null
  try {
    const spec = JSON.parse(representation.content)
    return isRecord(spec) ? spec : null
  } catch {
    return null
  }
}

/**
 * Unroll a spec into a run-layer DAG. Feedback edges become forward edges into
 * the next iteration copy (bounded by edge.max, default 3), so the run graph
 * itself never contains a cycle. Copy ids are `${nodeId}#it${k}`.
 */
export function unrollSpec(spec, options = {}) {
  assertWorkflowSpec(spec)
  const defaultMax = options.default_max ?? 3
  const nodesById = new Map(spec.nodes.map((node) => [node.id, node]))
  const edges = (Array.isArray(spec.edges) ? spec.edges : []).map((edge) => ({ from: edge.from, to: edge.to, kind: edge.kind ?? 'sequence', condition: edge.condition, max: edge.max }))
  const forward = edges.filter((edge) => edge.kind === 'sequence')
  const feedback = edges.filter((edge) => edge.kind === 'feedback')

  const forwardOut = new Map()
  for (const edge of forward) {
    if (!forwardOut.has(edge.from)) forwardOut.set(edge.from, [])
    forwardOut.get(edge.from).push(edge.to)
  }

  const copies = []
  const runEdges = []
  const seenCopies = new Set()
  const queue = [{ id: spec.entry, iteration: 0 }]
  while (queue.length) {
    const { id, iteration } = queue.shift()
    const copyId = `${id}#it${iteration}`
    if (seenCopies.has(copyId)) continue
    seenCopies.add(copyId)
    copies.push({ id: copyId, source_node_id: id, iteration, node: nodesById.get(id) })
    for (const next of forwardOut.get(id) ?? []) queue.push({ id: next, iteration })
    for (const edge of feedback) {
      if (edge.from !== id) continue
      const max = Number.isInteger(edge.max) ? edge.max : defaultMax
      if (iteration + 1 > max) continue
      const nextCopy = `${edge.to}#it${iteration + 1}`
      runEdges.push({ from: copyId, to: nextCopy, kind: 'feedback', condition: edge.condition, iteration_from: iteration, iteration_to: iteration + 1 })
      queue.push({ id: edge.to, iteration: iteration + 1 })
    }
    for (const edge of forward) {
      if (edge.from === id) runEdges.push({ from: copyId, to: `${edge.to}#it${iteration}`, kind: 'sequence' })
    }
  }
  return { spec_id: spec.id, nodes: copies, edges: runEdges, entry: `${spec.entry}#it0` }
}

/** metadata.sync: external platform bindings (§5.1, R3). */
export function parseSyncBindings(value) {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) throw new TypeError('metadata.sync must be an array')
  return value.map((binding, index) => {
    if (!isRecord(binding) || !nonEmpty(binding.plugin) || !nonEmpty(binding.external_ref)) {
      throw new TypeError(`metadata.sync[${index}] must declare plugin and external_ref`)
    }
    return {
      plugin: binding.plugin,
      external_ref: binding.external_ref,
      last_synced_revision: binding.last_synced_revision ?? null,
      direction: binding.direction ?? 'both',
    }
  })
}

/** metadata.assignment: ActorRef assignments with light time semantics (§6, R4). */
export function parseAssignments(value) {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) throw new TypeError('metadata.assignment must be an array')
  return value.map((assignment, index) => {
    const path = `metadata.assignment[${index}]`
    if (!isRecord(assignment) || !isRecord(assignment.actor) || !nonEmpty(assignment.actor.kind) || !nonEmpty(assignment.actor.ref)) {
      throw new TypeError(`${path}.actor must be {kind, ref}`)
    }
    const role = assignment.role ?? 'owner'
    if (!ASSIGNMENT_ROLES.includes(role)) throw new TypeError(`${path}.role must be one of ${ASSIGNMENT_ROLES.join(', ')}`)
    const time = assignment.time
    if (time !== undefined) {
      if (!isRecord(time)) throw new TypeError(`${path}.time must be an object`)
      if (time.due !== undefined && !nonEmpty(time.due)) throw new TypeError(`${path}.time.due must be a non-empty string`)
      if (time.window !== undefined && (!isRecord(time.window) || !nonEmpty(time.window.earliest) || !nonEmpty(time.window.latest))) {
        throw new TypeError(`${path}.time.window must be {earliest, latest}`)
      }
      if (time.after !== undefined && !nonEmpty(time.after)) throw new TypeError(`${path}.time.after must be a non-empty material/run node id`)
    }
    return { actor: assignment.actor, role, mode: assignment.mode ?? null, time: time ?? null }
  })
}
