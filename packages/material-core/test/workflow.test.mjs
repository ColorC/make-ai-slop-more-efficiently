import test from 'node:test'
import assert from 'node:assert/strict'
import {
  ASSIGNMENT_ROLES,
  MATERIAL_SYNC_PLUGINS,
  WORKFLOW_LABELS,
  WORKFLOW_PREDICATES,
  assertWorkflowSpec,
  parseAssignments,
  parseSpecMaterial,
  parseSyncBindings,
  resolveSyncPlugin,
  unrollSpec,
  validateWorkflowSpec,
} from '../index.js'

const skinSwapSpec = {
  id: 'spec-demogame-skin-swap',
  title: '换皮配置工作流（示例）',
  entry: 'source',
  nodes: [
    { id: 'source', kind: 'anchor', title: 'collab platform排期表' },
    { id: 'generate', kind: 'worker', worker_ref: 'demogame.change', format_in: 'source-set', format_out: 'table-set' },
    { id: 'benchmark', kind: 'check', check_ref: 'demogame.benchmark' },
    { id: 'planner-gate', kind: 'gate', title: '策划确认门' },
  ],
  edges: [
    { from: 'source', to: 'generate' },
    { from: 'generate', to: 'benchmark' },
    { from: 'benchmark', to: 'planner-gate' },
    { from: 'benchmark', to: 'generate', kind: 'feedback', condition: 'FAIL', max: 2 },
  ],
}

test('workflow labels and predicates are the §8.1 convention set', () => {
  assert.deepEqual(
    Object.values(WORKFLOW_LABELS).sort(),
    ['check', 'env-profile', 'ephemeral', 'gate', 'placeholder', 'run', 'source', 'spec', 'template'],
  )
  assert.deepEqual([...WORKFLOW_PREDICATES], ['executes', 'overrides', 'instantiates', 'feedback', 'syncs-to'])
  assert.deepEqual([...ASSIGNMENT_ROLES], ['owner', 'reviewer', 'assistant'])
})

test('validateWorkflowSpec accepts a spec with a feedback cycle and rejects malformed ones', () => {
  assert.equal(validateWorkflowSpec(skinSwapSpec).ok, true)
  const noEntry = validateWorkflowSpec({ ...skinSwapSpec, entry: 'missing' })
  assert.equal(noEntry.ok, false)
  assert.ok(noEntry.issues.some((issue) => issue.path === 'entry'))
  const workerWithoutRef = validateWorkflowSpec({
    ...skinSwapSpec,
    nodes: [{ id: 'source', kind: 'anchor' }, { id: 'generate', kind: 'worker' }],
    edges: [],
    entry: 'source',
  })
  assert.ok(workerWithoutRef.issues.some((issue) => issue.path === 'nodes[1].worker_ref'))
  const badMax = validateWorkflowSpec({
    ...skinSwapSpec,
    edges: [{ from: 'benchmark', to: 'generate', kind: 'feedback', max: 0 }],
  })
  assert.ok(badMax.issues.some((issue) => issue.path === 'edges[0].max'))
  assert.throws(() => assertWorkflowSpec({}), TypeError)
})

test('unrollSpec expands feedback cycles into a cycle-free run DAG bounded by max', () => {
  const run = unrollSpec(skinSwapSpec)
  assert.equal(run.entry, 'source#it0')
  const ids = run.nodes.map((node) => node.id)
  // entry iteration plus two re-entries (max=2)
  assert.ok(ids.includes('generate#it0'))
  assert.ok(ids.includes('generate#it1'))
  assert.ok(ids.includes('generate#it2'))
  assert.ok(!ids.includes('generate#it3'))
  const feedback = run.edges.filter((edge) => edge.kind === 'feedback')
  assert.equal(feedback.length, 2)
  assert.deepEqual(feedback[0], { from: 'benchmark#it0', to: 'generate#it1', kind: 'feedback', condition: 'FAIL', iteration_from: 0, iteration_to: 1 })
  // acyclicity: topological sort must consume every node
  const indegree = new Map(ids.map((id) => [id, 0]))
  for (const edge of run.edges) indegree.set(edge.to, indegree.get(edge.to) + 1)
  const queue = ids.filter((id) => indegree.get(id) === 0)
  let visited = 0
  while (queue.length) {
    const current = queue.shift()
    visited += 1
    for (const edge of run.edges.filter((item) => item.from === current)) {
      indegree.set(edge.to, indegree.get(edge.to) - 1)
      if (indegree.get(edge.to) === 0) queue.push(edge.to)
    }
  }
  assert.equal(visited, ids.length, 'run graph must be a DAG')
})

test('sequence cycles are rejected (cycles belong in feedback edges)', () => {
  const cyclic = {
    id: 'spec-cycle', title: 'x', entry: 'a',
    nodes: [{ id: 'a', kind: 'anchor' }, { id: 'b', kind: 'anchor' }],
    edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }],
  }
  const result = validateWorkflowSpec(cyclic)
  assert.equal(result.ok, false)
  assert.ok(result.issues.some((issue) => issue.path === 'edges' && /cycle/.test(issue.message)))
  const selfLoop = { ...cyclic, edges: [{ from: 'a', to: 'a' }] }
  assert.equal(validateWorkflowSpec(selfLoop).ok, false)
})

test('parallel diamonds plus feedback unroll without duplicate edges', () => {
  const nodes = [{ id: 'root', kind: 'anchor' }]
  const edges = []
  for (let index = 0; index < 8; index += 1) {
    const left = `d${index}l`; const right = `d${index}r`; const join = `d${index}j`
    nodes.push({ id: left, kind: 'anchor' }, { id: right, kind: 'anchor' }, { id: join, kind: 'anchor' })
    edges.push({ from: index === 0 ? 'root' : `d${index - 1}j`, to: left })
    edges.push({ from: index === 0 ? 'root' : `d${index - 1}j`, to: right })
    edges.push({ from: left, to: join }, { from: right, to: join })
  }
  nodes.push({ id: 'check', kind: 'check', check_ref: 'demo' })
  edges.push({ from: 'd7j', to: 'check' }, { from: 'check', to: 'root', kind: 'feedback', condition: 'FAIL', max: 2 })
  const spec = { id: 'spec-diamonds', title: 'diamonds', entry: 'root', nodes, edges }
  assert.equal(validateWorkflowSpec(spec).ok, true)
  const run = unrollSpec(spec)
  const edgeKeys = run.edges.map((edge) => `${edge.from}->${edge.to}:${edge.kind}`)
  assert.equal(new Set(edgeKeys).size, edgeKeys.length, 'no duplicate edges')
  const nodeIds = run.nodes.map((node) => node.id)
  assert.equal(new Set(nodeIds).size, nodeIds.length, 'no duplicate node copies')
})

test('parseSpecMaterial reads a WorkflowSpec from a primary JSON representation', () => {
  const material = {
    id: 'material:omnidashboard/spec/skin-swap',
    title: '换皮配置工作流',
    labels: [WORKFLOW_LABELS.SPEC],
    representations: [{
      id: 'rep-spec',
      media_type: 'application/json',
      role: 'primary',
      content_format: 'json',
      content: JSON.stringify(skinSwapSpec),
      source_ref: { kind: 'inline', ref: 'spec' },
    }],
  }
  assert.deepEqual(parseSpecMaterial(material), skinSwapSpec)
  assert.equal(parseSpecMaterial({ representations: [{ role: 'primary', content: 'not json', content_format: 'markdown' }] }), null)
})

test('parseSyncBindings and parseAssignments enforce the metadata contract', () => {
  const bindings = parseSyncBindings([{ plugin: 'sync-feishu-project', external_ref: 'FEISHU-123' }])
  assert.deepEqual(bindings, [{ plugin: 'sync-feishu-project', external_ref: 'FEISHU-123', last_synced_revision: null, direction: 'both' }])
  assert.throws(() => parseSyncBindings([{ plugin: 'x' }]), TypeError)
  assert.throws(() => parseSyncBindings({}), TypeError)

  const assignments = parseAssignments([
    { actor: { kind: 'user', ref: 'maintainer' }, role: 'owner', time: { due: '2026-08-20T00:00:00Z' } },
    { actor: { kind: 'agent', ref: 'worker-demogame' }, time: { after: 'run-1#generate#it0' } },
  ])
  assert.equal(assignments[0].role, 'owner')
  assert.equal(assignments[1].role, 'owner')
  assert.equal(assignments[1].time.after, 'run-1#generate#it0')
  assert.throws(() => parseAssignments([{ actor: { kind: 'user', ref: 'a' }, role: 'boss' }]), TypeError)
  assert.throws(() => parseAssignments([{ actor: {}, role: 'owner' }]), TypeError)
})

test('sync plugin slot matches metadata.sync bindings', () => {
  assert.ok(MATERIAL_SYNC_PLUGINS.some((plugin) => plugin.id === 'notify-omni'))
  const material = { metadata: { sync: [{ plugin: 'notify-omni', external_ref: 'canvas://demo' }] } }
  const matched = resolveSyncPlugin(material)
  assert.equal(matched.length, 1)
  assert.equal(matched[0].plugin.id, 'notify-omni')
  assert.equal(resolveSyncPlugin({ metadata: {} }).length, 0)
})
