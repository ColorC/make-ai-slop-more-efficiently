import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { canonicalManifest, resolveView, sliceGraphForView, validateMaterialGraph } from '../index.js'

const fixture = JSON.parse(await readFile(new URL('../contract/fixtures/fixtures.json', import.meta.url), 'utf8'))
const graphFor = (entry) => structuredClone(entry.graph)

test('all canonical fixtures have their declared validator result and issue paths', () => {
  for (const entry of fixture.cases) {
    const result = validateMaterialGraph(graphFor(entry))
    assert.equal(result.ok, entry.expected.ok, entry.id)
    assert.deepEqual([...new Set(result.issues.map((issue) => issue.path))].sort(), [...entry.expected.issue_paths].sort(), entry.id)
  }
})

test('canonical v0.2 fixture preserves formal semantics but excludes transport-only fields and theme', () => {
  const graph = graphFor(fixture.cases.find((entry) => entry.id === 'v0.2-formal-domain-and-succeeded-formalization'))
  graph.materials[1].representations[0].content = 'transport-only'
  graph.materials[0].formal_domain.extensions = { product_only: true }
  graph.materials[1].formalization.extensions = { local_note: 'retained' }
  graph.materials[0].view = { members: [], presentation: { mode: 'continuous', theme: { name: 'dark' } } }
  const manifest = canonicalManifest(graph)
  assert.equal(manifest.materials.find((material) => material.id === 'domain').formal_domain.semantic_scope, 'demo scope')
  assert.equal(manifest.materials.find((material) => material.id === 'record').formalization.authority_effect.action, 'establish')
  assert.equal(manifest.materials.find((material) => material.id === 'domain').formal_domain.extensions.product_only, true)
  assert.equal(manifest.materials.find((material) => material.id === 'record').formalization.extensions.local_note, 'retained')
  assert.equal('content' in manifest.materials.find((material) => material.id === 'record').representations[0], false)
  assert.equal('theme' in manifest.materials.find((material) => material.id === 'domain').view.presentation, false)
})

test('fixture View expands safely and reports its cycle', () => {
  const entry = fixture.cases.find((item) => item.id === 'view-cycle-and-truncation')
  const graph = graphFor(entry)
  const resolved = resolveView(graph, entry.resolve.view_id, entry.resolve.options)
  assert.deepEqual(resolved.diagnostics.map((diagnostic) => diagnostic.kind), entry.resolve.diagnostic_kinds)
  assert.deepEqual(new Set(sliceGraphForView(graph, entry.resolve.view_id).materials.map((material) => material.id)), new Set(['view-a', 'view-b', 'leaf']))
})
