import test from 'node:test'
import assert from 'node:assert/strict'
import { displayContextForItem, selectMaterialRenderer } from '../renderers.js'

const material = { id: 'm', labels: ['domain:bside', 'kind:config'] }
const representation = { id: 'r', media_type: 'application/json', renderer_hint: 'config-table' }
const renderer = (id, match, priority) => ({ id, match, priority, render: () => id })

test('renderer hint outranks exact media type, wildcard and labels', () => {
  const selected = selectMaterialRenderer([
    renderer('label', { labels: ['kind:config'] }),
    renderer('wildcard', { media_type: 'application/*' }),
    renderer('media', { media_type: 'application/json' }),
    renderer('hint', { renderer_hint: 'config-table' }),
  ], material, representation)
  assert.equal(selected.id, 'hint')
})

test('priority resolves matches at the same semantic level', () => {
  const selected = selectMaterialRenderer([
    renderer('first', { media_type: 'application/json' }, 0),
    renderer('preferred', { media_type: 'application/json' }, 10),
  ], material, representation)
  assert.equal(selected.id, 'preferred')
})

test('all declared labels must match and predicate may further constrain', () => {
  assert.equal(selectMaterialRenderer([
    renderer('wrong', { labels: ['domain:bside', 'kind:image'] }),
    renderer('right', { labels: ['domain:bside'], when: (item) => item.id === 'm' }),
  ], material, representation).id, 'right')
})

test('returns undefined for unknown representations', () => {
  assert.equal(selectMaterialRenderer([renderer('markdown', { media_type: 'text/markdown' })], material, representation), undefined)
})

test('member, group and composition overrides follow the material-core precedence', () => {
  const renderers = [
    renderer('member', { media_type: 'text/plain' }),
    renderer('group', { media_type: 'text/plain' }),
    renderer('composition', { media_type: 'text/plain' }),
    renderer('hint', { renderer_hint: 'config-table' }),
  ]
  assert.equal(selectMaterialRenderer(renderers, material, representation, {
    memberOverride: { pluginId: 'member' },
    groupOverride: { pluginId: 'group' },
    compositionOverride: { pluginId: 'composition' },
  }).id, 'member')
  assert.equal(selectMaterialRenderer(renderers, material, representation, {
    groupOverride: { pluginId: 'group' },
    compositionOverride: { pluginId: 'composition' },
  }).id, 'group')
  assert.equal(selectMaterialRenderer(renderers, material, representation, {
    compositionOverride: { pluginId: 'composition' },
  }).id, 'composition')
})

test('view member and group definitions become renderer context', () => {
  const member = { material_id: 'm', group: 'comparison', display: { pluginId: 'member' } }
  const viewMaterial = {
    view: {
      display: { pluginId: 'composition' },
      groups: [{ id: 'comparison', display: { pluginId: 'group' } }],
    },
  }
  const context = displayContextForItem(viewMaterial, member)
  assert.equal(context.member.display.pluginId, 'member')
  assert.equal(context.group.display.pluginId, 'group')
  assert.equal(context.composition.display.pluginId, 'composition')
})
