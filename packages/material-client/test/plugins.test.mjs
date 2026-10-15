import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createMaterialPluginHost, MATERIAL_PLUGIN_CONTRACT_VERSION } from '../index.js'

const plugin = (id, capabilities, requires = []) => ({
  manifest: { contract_version: MATERIAL_PLUGIN_CONTRACT_VERSION, id, version: '1', capabilities, requires },
  implementation: { id },
})

test('optional plugins enable independently and expose only versioned manifests', async () => {
  const host = createMaterialPluginHost()
    .register(plugin('note', ['note', 'write']))
    .register(plugin('task', ['task'], ['note']))
    .register(plugin('review', ['review']))
  await host.enable('task')
  assert.deepEqual(host.list().map(({ manifest, enabled }) => [manifest.id, enabled]), [
    ['note', true], ['review', false], ['task', true],
  ])
  assert.deepEqual(host.providers('write').map(({ manifest }) => manifest.id), ['note'])
  await assert.rejects(host.disable('note'), /required by enabled plugin task/)
  await host.disable('task')
  await host.disable('note')
  assert.equal(host.get('review').implementation.id, 'review')
})

test('unknown, duplicate and incomplete plugins fail explicitly', async () => {
  const host = createMaterialPluginHost().register(plugin('note', ['note']))
  assert.throws(() => host.register(plugin('note', ['note'])), /already registered/)
  assert.throws(() => host.register({ manifest: { id: 'bad' } }), /contract_version/)
  await assert.rejects(host.enable('missing'), /Unknown Material plugin/)
})

test('shared plugin manifest fixtures have exact validator outcomes', () => {
  const fixture = JSON.parse(readFileSync(new URL('../contract/plugin-manifest-fixtures.json', import.meta.url), 'utf8'))
  return import('../index.js').then(({ validateMaterialPluginManifest }) => {
    for (const item of fixture.cases) {
      const result = validateMaterialPluginManifest(item.manifest)
      assert.equal(result.ok, item.ok, item.id)
      if (item.issue_paths) assert.deepEqual(result.issues.map(({ path }) => path), item.issue_paths, item.id)
    }
  })
})
