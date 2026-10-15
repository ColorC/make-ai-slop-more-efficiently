import test from 'node:test'
import assert from 'node:assert/strict'
import { usesHtmlFilePreview, usesMaterialDisplaySurface, usesNativeMaterialDocument } from '../src/regions/materialDisplayPolicy.ts'

test('local HTML report files render as web previews instead of source text', () => {
  const card = { kind: 'material', adapter: 'web', source: { kind: 'file' } }
  assert.equal(usesHtmlFilePreview(card, 'text/html'), true)
  assert.equal(usesHtmlFilePreview(card, 'text/html; charset=utf-8'), true)
  assert.equal(usesHtmlFilePreview({ ...card, adapter: 'text' }, 'text/html'), false)
  assert.equal(usesHtmlFilePreview(card, 'text/plain'), false)
})

test('canonical Material references render through their representation plugin surface', () => {
  assert.equal(usesMaterialDisplaySurface({
    kind: 'material', adapter: 'web', source: { kind: 'link', url: '../materials?id=x' },
    material: { material_ref: 'material:spark/workflow/run', representation_id: 'primary' },
  }), true)
})

test('ordinary pasted links remain links instead of being promoted to Material plugins', () => {
  assert.equal(usesMaterialDisplaySurface({
    kind: 'material', adapter: 'web', source: { kind: 'link' }, material: {},
  }), false)
})

test('only Material workspace URLs use the native document renderer', () => {
  const material = { material_ref: 'material:spark/workflow/run', representation_id: 'primary' }
  assert.equal(usesNativeMaterialDocument({
    kind: 'material', adapter: 'web', source: { kind: 'link', url: '../materials?id=x' }, material,
  }), true)
  assert.equal(usesNativeMaterialDocument({
    kind: 'material', adapter: 'web', source: { kind: 'link', url: '../api/workflow-live/run' }, material,
  }), false)
})
