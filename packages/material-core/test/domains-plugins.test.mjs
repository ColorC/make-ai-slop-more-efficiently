import test from 'node:test'
import assert from 'node:assert/strict'
import {
  composeDomainGraphs, filterMaterialsForHost, filterViewMembersForHost,
  materialDomain, parseDomainRegistry, resolveDisplayPlugin, resolveProcessPlugin,
  viewMembersForProcess, MATERIAL_PROCESS_PLUGINS,
} from '../index.js'

const rev = (provider, object = 'doc') => ({ provider, object, revision: '1' })
const material = (id, body = '正文', extra = {}) => ({
  id, title: id, labels: [], revision_ref: extra.revision_ref ?? rev('git'),
  representations: [{
    id: 'document', media_type: extra.media_type ?? 'text/markdown', role: 'primary',
    source_ref: { kind: 'git', ref: id }, content: body,
    renderer_hint: extra.hint,
  }],
})

const graph = (graph_id, materials) => ({
  contract_version: '0.2.0', graph_id, generated_at: '2026-08-15T00:00:00Z', materials,
})

test('materials stay split by top-level domain until a registry is loaded', () => {
  const host = graph('material-graph:spark-workshop/workspace', [
    material('material:spark-workshop/a', '本域甲'),
    material('material:walker/card', '外来卡'),
    {
      id: 'view:spark-workshop/home', title: 'home', labels: [], revision_ref: rev('git'),
      representations: [{ id: 'definition', media_type: 'application/vnd.omni.view+json', role: 'primary', source_ref: { kind: 'view', ref: 'home' } }],
      view: { members: [
        { material_id: 'material:spark-workshop/a', order: 0 },
        { material_id: 'material:walker/card', order: 1 },
      ] },
    },
  ])
  const visible = filterMaterialsForHost(host.materials, 'spark-workshop', null)
  assert.deepEqual(visible.map((item) => item.id).sort(), [
    'material:spark-workshop/a', 'view:spark-workshop/home',
  ])
  assert.deepEqual(
    filterViewMembersForHost(host.materials.find((item) => item.view).view.members, 'spark-workshop', null)
      .map((item) => item.material_id),
    ['material:spark-workshop/a'],
  )

  const foreign = graph('material-graph:walker/wiki', [material('material:walker/card', '外来卡')])
  const blocked = composeDomainGraphs(host, foreign, 'spark-workshop', null)
  assert.equal(blocked.merged, false)
  assert.deepEqual(blocked.held_apart, [{ origin: 'walker', id: 'material:walker/card' }])
  assert.equal(blocked.graph.materials.some((item) => item.id === 'material:walker/extra'), false)

  const registry = parseDomainRegistry({
    host_domain: 'spark-workshop',
    foreign_domains: [{ domain: 'walker' }],
  })
  const walkerOnly = graph('material-graph:walker/wiki', [
    material('material:walker/card', '外来卡'),
    material('material:walker/extra', '新卡'),
  ])
  const merged = composeDomainGraphs(host, walkerOnly, 'spark-workshop', registry)
  assert.equal(merged.merged, true)
  assert.ok(merged.graph.materials.some((item) => item.id === 'material:walker/extra'))
  assert.deepEqual(
    filterMaterialsForHost(merged.graph.materials, 'spark-workshop', registry)
      .map((item) => item.id).filter((id) => id.startsWith('material:walker')),
    ['material:walker/card', 'material:walker/extra'],
  )

  const collide = composeDomainGraphs(host, foreign, 'spark-workshop', null)
  assert.equal(collide.graph.materials.filter((item) => item.id === 'material:walker/card').length, 1)
  assert.equal(collide.held_apart[0].origin, 'walker')
  assert.equal(materialDomain('material:omnidashboard/wiki/x'), 'omnidashboard')
  assert.equal(materialDomain('material-graph:material-wiki/walker'), 'walker')
  assert.equal(materialDomain('view:material-wiki/omni-product-family/root'), 'omni-product-family')
  assert.equal(materialDomain('walker.card.chiyanque-bloodthirst-slash'), 'walker')
  assert.equal(materialDomain('Vilo.材料.大纲'), 'Vilo')
})

test('display plugins degrade without a match and process plugins do not change View members', () => {
  const json = material('material:spark-workshop/table', '{"a":1}', { media_type: 'application/json', hint: 'gantt' })
  const bare = resolveDisplayPlugin(json, [])
  assert.equal(bare.kind, 'generic')
  assert.equal(bare.member_id, json.id)
  assert.equal(bare.body, '{"a":1}')
  assert.equal(bare.writable, false)

  const withPlugin = resolveDisplayPlugin(json, [{ id: 'gantt', hints: ['gantt'], mediaTypes: ['application/json'] }])
  assert.equal(withPlugin.kind, 'plugin')
  assert.equal(withPlugin.plugin_id, 'gantt')
  assert.equal(withPlugin.member_id, json.id)

  const members = [{ material_id: 'material:spark-workshop/a', order: 0 }, { material_id: 'material:spark-workshop/b', order: 1 }]
  const git = resolveProcessPlugin(rev('git'), [{ id: 'git-io', provider: 'git', can_write: true }])
  const p4 = resolveProcessPlugin(rev('p4'), [{ id: 'p4-io', provider: 'p4', can_write: true }])
  assert.equal(git.plugin_id, 'git-io')
  assert.equal(p4.plugin_id, 'p4-io')
  assert.deepEqual(viewMembersForProcess({ provider: 'git' }, members), viewMembersForProcess({ provider: 'p4' }, members))
  const wikiIo = resolveProcessPlugin(rev('material-wiki'))
  assert.equal(wikiIo.plugin_id, 'material-wiki-io')
  assert.equal(wikiIo.can_write, true)
  assert.ok(MATERIAL_PROCESS_PLUGINS.some((item) => item.provider === 'material-wiki' && item.can_write))
})
