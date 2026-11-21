import test from 'node:test'
import assert from 'node:assert/strict'
import {
  lintCanvasDocument,
  lintMaterialGraph,
  normalizeFormatTag,
  inferFormatTag,
} from '../index.js'

const classificationRef = {
  schema: 'omni.material-classification-ref/v1',
  profile_id: 'test.materials',
  profile_revision: 'v1',
  primary_path: ['visible-form', 'document'],
  facets: {},
}
const classificationContextRef = {
  schema: 'omni.material-classification-context-ref/v1',
  profile_id: 'test.materials',
  profile_revision: 'v1',
  project_id: 'test',
  profile_ref: 'workspace:test/material/profiles/materials.yaml',
  read_via: 'omni material profile show',
}

const graph = (representation) => ({
  contract_version: '0.3.0',
  graph_id: 'format-lint-test',
  generated_at: '2026-08-30T00:00:00.000Z',
  materials: [{
    id: 'm', title: 'Material', labels: [], revision_ref: { provider: 'test', object: 'm', revision: 'r1' },
    representations: [representation],
    metadata: { classification_ref: classificationRef, classification_context_ref: classificationContextRef },
  }],
})

test('format tags distinguish JSON table from raw JSON and renderer capability is checked', () => {
  assert.equal(normalizeFormatTag('data-table'), 'table')
  assert.equal(inferFormatTag({ media_type: 'application/json', renderer_hint: 'table' }), 'table')
  assert.equal(normalizeFormatTag('tutorial-preview'), 'tutorial')
  assert.equal(inferFormatTag({ media_type: 'application/json', renderer_hint: 'tutorial-preview' }), 'tutorial')
  const result = lintMaterialGraph(graph({
    id: 'primary', media_type: 'application/json', role: 'primary', format_tag: 'table',
    source_ref: { kind: 'test', ref: 'table.json' },
  }), { renderers: [{ id: 'table', formatTags: ['table'], fallback: false }] })
  assert.equal(result.ok, true)
  assert.equal(lintMaterialGraph(graph({
    id: 'primary', media_type: 'application/json', role: 'primary', format_tag: 'table',
    source_ref: { kind: 'test', ref: 'table.json' },
  }), { renderers: [{ id: 'json', formatTags: ['json'], fallback: false }] }).ok, false)
  assert.equal(lintMaterialGraph(graph({
    id: 'primary', media_type: 'application/json', role: 'primary', format_tag: 'table',
    source_ref: { kind: 'test', ref: 'table.json' },
  }), { renderers: [{ id: 'json', formatTags: ['json'], acceptsFormats: ['application/json'], fallback: false }] }).ok, false)
})

test('lint rejects unknown or transport-only legacy protocol in strict mode', () => {
  const legacyGraph = graph({ id: 'primary', media_type: 'text/plain', role: 'primary', renderer_hint: 'markdown', source_ref: { kind: 'test', ref: 'x' } })
  legacyGraph.materials[0].metadata = { material_kind: 'markdown' }
  const result = lintMaterialGraph(legacyGraph)
  assert.equal(result.ok, false)
  assert.ok(result.issues.some((item) => item.code === 'format-tag.missing'))
  assert.ok(result.issues.some((item) => item.code === 'protocol.legacy-material-kind'))
  const compat = lintMaterialGraph(legacyGraph, { requireFormatTag: false })
  assert.equal(compat.ok, true)
  assert.ok(compat.issues.every((item) => item.severity === 'warning'))
})

test('strict 0.3 rejects a material that has not carried the formal profile read context', () => {
  const value = graph({ id: 'primary', media_type: 'text/plain', role: 'primary', format_tag: 'markdown', source_ref: { kind: 'test', ref: 'x' } })
  delete value.materials[0].metadata.classification_ref
  delete value.materials[0].metadata.classification_context_ref
  const result = lintMaterialGraph(value)
  assert.equal(result.ok, false)
  assert.ok(result.issues.some((item) => item.code === 'classification.missing'))
  assert.ok(result.issues.some((item) => item.code === 'classification.context-missing'))
  const compat = lintMaterialGraph(value, { requireFormatTag: false })
  assert.equal(compat.ok, true)
  assert.ok(compat.issues.some((item) => item.code === 'classification.context-missing' && item.severity === 'warning'))
})

test('Canvas lint validates material binding, normalized slices and transition trigger', () => {
  const document = {
    version: 1,
    cards: [{
      id: 'screen', kind: 'material', title: 'Screen', adapter: 'image',
      material: { material_ref: 'screen-material:test/screen', representation_id: 'shot' },
      slices: [{ kind: 'region', ref: 'button', box: { x: 0.1, y: 0.1, width: 0.2, height: 0.1 } }],
    }, { id: 'step', kind: 'text', title: 'Step' }],
    relations: [{ id: 'next', source: 'screen', target: 'step', kind: 'transitions-to' }],
  }
  const result = lintCanvasDocument(document)
  assert.equal(result.ok, false)
  assert.ok(result.issues.some((item) => item.code === 'protocol.legacy-adapter'))
  assert.ok(result.issues.some((item) => item.code === 'canvas.transition-trigger'))
})

test('Canvas lint rejects an embedded Material without the formal classification read context', () => {
  const result = lintCanvasDocument({
    version: 1,
    cards: [{
      id: 'embedded', kind: 'material', title: 'Embedded',
      material: {
        id: 'm', title: 'Embedded', labels: [],
        revision_ref: { provider: 'test', object: 'm', revision: 'r1' },
        representations: [{
          id: 'primary', role: 'primary', media_type: 'text/markdown', format_tag: 'markdown',
          source_ref: { kind: 'test', ref: 'm' },
        }],
      },
      representation_id: 'primary',
    }],
    relations: [],
  })
  assert.equal(result.ok, false)
  assert.ok(result.issues.some((item) => item.code === 'classification.context-missing'))
})

test('Material and Canvas lint reject unsafe workbench routes and retired edge geometry', () => {
  const value = graph({
    id: 'primary', role: 'primary', media_type: 'text/markdown', format_tag: 'markdown',
    source_ref: { kind: 'test', ref: 'm' },
  })
  value.materials[0].workbench_links = [{ id: 'edit', label: '编辑', href: 'javascript:alert(1)' }]
  const materialResult = lintMaterialGraph(value)
  assert.equal(materialResult.ok, false)
  assert.ok(materialResult.issues.some((item) => item.code === 'workbench-links.href'))

  const canvasResult = lintCanvasDocument({
    version: 1,
    behavior: {
      performance: { virtualization: 'sometimes' },
      workbenchLinks: [{ id: 'map', label: '地图', href: 'javascript:alert(1)' }],
    },
    cards: [{ id: 'a', kind: 'text', title: 'A' }, { id: 'b', kind: 'text', title: 'B' }],
    relations: [{
      id: 'edge', source: 'a', target: 'b', path: 'orthogonal',
      routePoints: [{ x: 1, y: 2 }], curveControl: { x: 3, y: 4 },
    }],
  })
  assert.equal(canvasResult.ok, false)
  assert.ok(canvasResult.issues.some((item) => item.code === 'canvas.intermediate-geometry'))
  assert.ok(canvasResult.issues.some((item) => item.code === 'canvas.relation-path'))
  assert.ok(canvasResult.issues.some((item) => item.code === 'canvas.performance-value'))
  assert.ok(canvasResult.issues.some((item) => item.code === 'workbench-links.href'))
})
