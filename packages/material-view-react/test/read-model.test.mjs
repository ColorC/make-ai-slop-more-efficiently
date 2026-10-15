import test from 'node:test'
import assert from 'node:assert/strict'
import { buildMaterialNavigation, buildMaterialReadModel } from '../read-model.js'
import { createDefaultMaterialRenderers, selectMaterialRenderer } from '../renderers.js'

const revision = (object) => ({ provider: 'git', object, revision: 'r1' })
const representation = (id, media_type = 'text/markdown', content) => ({
  id: 'main', media_type, role: 'primary', source_ref: { kind: 'git', ref: id }, ...(content === undefined ? {} : { content }),
})
const material = (id, title, extra = {}) => ({
  id, title, labels: extra.labels ?? ['材料'], revision_ref: revision(id),
  representations: [representation(id, extra.media_type, extra.content)], ...extra,
})
const graph = {
  contract_version: '0.2.0', graph_id: 'fixture:read-model', generated_at: '2026-08-14T00:00:00Z', active_view_id: 'view:home',
  materials: [
    material('leaf:b', '第二块', { content: '# 第二块\n正文' }),
    material('leaf:a', '第一块', { content: '# 第一块\n可搜索关键字' }),
    material('view:doc', '文档', {
      media_type: 'application/vnd.omni.view+json',
      view: { members: [{ material_id: 'leaf:a', order: 0 }, { material_id: 'leaf:b', order: 1 }], presentation: { mode: 'continuous' } },
      metadata: { navigation: true, parent_view_id: 'view:home' },
    }),
    material('view:home', '首页', {
      media_type: 'application/vnd.omni.view+json',
      view: { members: [{ material_id: 'view:doc', order: 0 }], presentation: { mode: 'collection' } },
      metadata: { navigation: true },
    }),
  ],
}

test('read model is serializable and preserves canonical View hierarchy, order and revisions', () => {
  assert.deepEqual(buildMaterialNavigation(graph).map(({ view_id, depth }) => ({ view_id, depth })), [
    { view_id: 'view:home', depth: 0 }, { view_id: 'view:doc', depth: 1 },
  ])
  const model = buildMaterialReadModel(graph, 'view:doc', { mode: 'expanded', query: '关键字' })
  assert.deepEqual(model.breadcrumbs, ['view:home', 'view:doc'])
  assert.deepEqual(model.items.map((item) => item.material_id), ['leaf:a', 'leaf:b'])
  assert.deepEqual(model.outline.map(({ material_id, representation_id, anchor }) => ({ material_id, representation_id, anchor })), [
    { material_id: 'leaf:a', representation_id: 'main', anchor: 'leaf:a' },
    { material_id: 'leaf:b', representation_id: 'main', anchor: 'leaf:b' },
  ])
  assert.deepEqual(model.search_results.map((item) => item.material_id), ['leaf:a'])
  assert.equal(JSON.parse(JSON.stringify(model)).view_revision_ref.object, 'view:doc')
})

test('default registry covers markdown, json, image, text and file without product enums', () => {
  const renderers = createDefaultMaterialRenderers((type, props, ...children) => ({ type, props, children }))
  const ids = ['text/markdown', 'application/problem+json', 'image/png', 'text/plain', 'application/pdf']
    .map((media_type) => selectMaterialRenderer(renderers, material('m', 'M'), representation('m', media_type))?.id)
  assert.deepEqual(ids, [
    'material.default.markdown', 'material.default.json', 'material.default.image', 'material.default.text', 'material.default.file',
  ])
})

test('format_tag selects a specialized renderer when transport media is shared', () => {
  const renderers = createDefaultMaterialRenderers((type, props, ...children) => ({ type, props, children }))
  const tutorial = { ...representation('tutorial', 'application/json', '{}'), format_tag: 'tutorial' }
  const table = { ...representation('table', 'application/json', '{}'), format_tag: 'table' }
  assert.equal(selectMaterialRenderer(renderers, material('m', 'M'), tutorial)?.id, 'material.default.tutorial')
  assert.equal(selectMaterialRenderer(renderers, material('m', 'M'), table)?.id, undefined)
})
