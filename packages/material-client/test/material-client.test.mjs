import test from 'node:test'
import assert from 'node:assert/strict'
import {
  MaterialProviderError,
  MaterialWriteConflictError,
  createHttpMaterialProvider,
  createMemoryMaterialProvider,
  loadMaterialGraph,
  loadMaterialRepresentation,
  writeMaterialGraph,
} from '../index.js'

const revision = (value) => ({ provider: 'git', object: 'docs/a.md', revision: value })
const graph = (value = 'r1') => ({
  contract_version: '0.2.0', graph_id: 'test', generated_at: '2026-08-14T00:00:00Z', active_view_id: 'view',
  materials: [
    { id: 'doc', title: '文档', labels: [], revision_ref: revision(value), representations: [{ id: 'body', media_type: 'text/markdown', role: 'primary', source_ref: { kind: 'file', ref: 'docs/a.md' } }] },
    { id: 'view', title: '视图', labels: [], revision_ref: revision(value), representations: [{ id: 'definition', media_type: 'application/vnd.omni.view+json', role: 'primary', source_ref: { kind: 'file', ref: 'views.json' } }], view: { members: [{ material_id: 'doc' }] } },
  ],
})

const request = (next, base = revision('r1'), key = 'write-1') => ({
  workspace_ref: { kind: 'workspace', ref: 'test' }, actor_ref: { kind: 'agent', ref: 'test' },
  target: { material_id: 'doc' }, base_revision_ref: base, idempotency_key: key, next_graph: next,
})

test('loads and validates a provider graph', async () => {
  const provider = createMemoryMaterialProvider(graph())
  assert.equal((await loadMaterialGraph(provider)).contract_version, '0.2.0')
  await assert.rejects(() => loadMaterialGraph({ readGraph: async () => ({}) }), (error) => error instanceof MaterialProviderError && error.code === 'invalid-graph')
})

test('loads inline or provider-backed representations without changing identity', async () => {
  const current = graph()
  const material = current.materials[0]
  const representation = material.representations[0]
  const provider = createMemoryMaterialProvider(current, { representations: { 'doc\nbody': { content: '# 正文', content_format: 'markdown' } } })
  assert.deepEqual(await loadMaterialRepresentation(provider, material, representation), { content: '# 正文', content_format: 'markdown' })
  assert.equal((await loadMaterialRepresentation(provider, material, { ...representation, content: 'inline' })).content, 'inline')
})

test('memory writes require an exact base revision, are idempotent, and emit one event', async () => {
  const provider = createMemoryMaterialProvider(graph())
  const events = []
  provider.subscribe((event) => events.push(event))
  const first = await writeMaterialGraph(provider, request(graph('r2')))
  const replay = await writeMaterialGraph(provider, request(graph('r2')))
  assert.equal(first.status, 'written')
  assert.equal(replay.status, 'idempotent')
  assert.equal(events.length, 1)
  const different = graph('r2')
  different.materials[0].title = 'Different payload'
  await assert.rejects(
    () => writeMaterialGraph(provider, request(different)),
    (error) => error instanceof MaterialProviderError && error.code === 'idempotency-conflict',
  )
  await assert.rejects(
    () => writeMaterialGraph(provider, request(graph('r3'), revision('r1'), 'write-2')),
    (error) => error instanceof MaterialWriteConflictError && error.detail.expected.revision === 'r2',
  )
})

test('read-only and incomplete write requests fail explicitly', async () => {
  await assert.rejects(() => writeMaterialGraph({ readGraph: async () => graph() }, request(graph('r2'))), (error) => error.code === 'read-only')
  await assert.rejects(() => writeMaterialGraph(createMemoryMaterialProvider(graph()), {}), (error) => error.code === 'invalid-write')
})

test('http provider has product-neutral URL and decoding hooks', async () => {
  const calls = []
  const provider = createHttpMaterialProvider({
    graph_url: '/graph', representation_url: (_material, representation) => `/representation/${representation.id}`,
    fetch: async (url) => {
      calls.push(url)
      return url === '/graph'
        ? { ok: true, json: async () => graph() }
        : { ok: true, text: async () => '# HTTP' }
    },
  })
  const current = await loadMaterialGraph(provider)
  const body = await loadMaterialRepresentation(provider, current.materials[0], current.materials[0].representations[0])
  assert.equal(body.content, '# HTTP')
  assert.deepEqual(calls, ['/graph', '/representation/body'])
})

test('http provider deduplicates representation reads by exact Material revision', async () => {
  const calls = []
  const provider = createHttpMaterialProvider({
    graph_url: '/graph', representation_url: () => '/body',
    fetch: async (url) => {
      calls.push(url)
      await Promise.resolve()
      return { ok: true, text: async () => '# body' }
    },
  })
  const first = graph().materials[0]
  await Promise.all([
    loadMaterialRepresentation(provider, first, first.representations[0]),
    loadMaterialRepresentation(provider, first, first.representations[0]),
  ])
  await loadMaterialRepresentation(provider, first, first.representations[0])
  const changed = { ...first, revision_ref: revision('r2') }
  await loadMaterialRepresentation(provider, changed, changed.representations[0])
  assert.deepEqual(calls, ['/body', '/body'])
})
