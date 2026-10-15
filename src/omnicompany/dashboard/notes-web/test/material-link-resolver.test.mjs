import test from 'node:test'
import assert from 'node:assert/strict'
import {
  clearMaterialLinkResolverCache,
  resolveMaterialLink,
} from '../src/regions/materialLinkResolver.ts'

const fixedNow = () => new Date('2026-08-18T08:00:00.000Z')
const attachedTo = 'material:canvas/member'
const noCanvas = async () => null

test('Canvas URL imports the focused card as an attached Canvas Material', async () => {
  const document = JSON.stringify({
    version: 1,
    sessionId: 'external:demo-flow',
    title: '流程画布',
    revision: 7,
    updatedAt: '2026-08-18T00:00:00.000Z',
    cards: [{
      id: 'card-1', kind: 'text', title: '玩法说明', text: '# 正文',
      source: { kind: 'legacy-note', id: 'card-1' }, adapter: 'text',
      material: { id: 'material:spark/card-1' },
      x: 0, y: 0, width: 440, height: 320,
    }],
    relations: [],
  })
  const calls = []
  const result = await resolveMaterialLink(
    'http://host/bside/canvas/notes-web.html?session_id=demo-flow&focus=card-1',
    async (command, args) => { calls.push({ command, args }); return document },
    attachedTo,
    { now: fixedNow },
  )
  assert.equal(result.title, '玩法说明')
  assert.equal(result.adapter, 'text')
  assert.equal(result.material.material_ref, 'material:spark/card-1')
  assert.equal(result.material.resolved_material.representations[0].source_ref.kind, 'session-canvas')
  assert.equal(result.material.attached_to.material_id, attachedTo)
  assert.equal(calls[0].command, 'notes_canvas_get')
})

test('WebTwin element snapshot URL imports one content card with its selected element as a Canvas slice', async () => {
  const snapshotUrl = 'http://host/bside/api/diagnostics/snapshots/snap-mtcw2vk9-451d1c49'
  const result = await resolveMaterialLink(snapshotUrl, noCanvas, attachedTo, {
    now: fixedNow,
    fetchImpl: async (url, init) => {
      assert.equal(url, snapshotUrl)
      assert.equal(init.credentials, 'same-origin')
      return new Response(JSON.stringify({
        id: 'snap-mtcw2vk9-451d1c49',
        kind: 'element',
        capturedAt: '2026-08-28T11:49:33.628Z',
        page: 'http://host/workshop/interface-map',
        viewport: { width: 1500, height: 980, scrollX: 0, scrollY: 0 },
        selection: {
          mode: 'element',
          rect: { top: 176, left: 8, width: 231, height: 31 },
          tag: 'button',
          text: 'Activity102State',
          selector: 'aside.interface-tree button[data-interface-material-ref]',
        },
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    },
  })
  assert.equal(result.title, '界面快照｜Activity102State')
  assert.equal(result.adapter, 'text')
  assert.equal(result.material.material_ref, 'material:workshop/snapshot/snap-mtcw2vk9-451d1c49')
  assert.equal(result.material.provenance.resolver, 'workshop-diagnostic-snapshot')
  assert.equal(result.material.metadata.capture_resolution, '1500x980')
  assert.deepEqual(result.material.metadata.canvas_slices, [{
    kind: 'element',
    ref: 'snapshot-selection-snap-mtcw2vk9-451d1c49',
    box: { x: 8 / 1500, y: 176 / 980, width: 231 / 1500, height: 31 / 980 },
    label: 'Activity102State',
    note: '从 WebTwin 快照导入的已选元素：Activity102State',
    targetPath: 'aside.interface-tree button[data-interface-material-ref]',
    emphasis: 'highlight',
    createdBy: 'workshop-page-capture',
    createdAt: '2026-08-28T11:49:33.628Z',
  }])
})

test('Material Wiki imports markdown content and caches it by material revision', async () => {
  clearMaterialLinkResolverCache()
  const requests = []
  const fetchImpl = async (url) => {
    requests.push(String(url))
    if (String(url).includes('/graph')) {
      return new Response(JSON.stringify({
        active_view_id: 'material-wiki:doc-1',
        materials: [{
          id: 'material-wiki:doc-1', title: '配置说明', labels: ['文档'],
          revision_ref: { provider: 'material-wiki', object: 'doc-1', revision: 'r3' },
          representations: [{
            id: 'main', role: 'primary', media_type: 'text/markdown',
            source_ref: { kind: 'material-wiki-api', ref: '/api/wiki/doc-1/versions' },
          }],
        }],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return new Response(JSON.stringify({ versions: [{ body: '# 配置说明\n正文' }] }), {
      status: 200, headers: { 'content-type': 'application/json' },
    })
  }
  const url = 'http://host/material-wiki/project-a/wiki/doc-1?project=project-a&material=doc-1'
  const first = await resolveMaterialLink(url, noCanvas, attachedTo, { fetchImpl, now: fixedNow })
  const second = await resolveMaterialLink(url, noCanvas, attachedTo, { fetchImpl, now: fixedNow })
  assert.equal(first.adapter, 'text')
  assert.equal(first.material.resolved_material.representations[0].content, '# 配置说明\n正文')
  assert.equal(first.material.resolved_material.representations[0].content_format, 'markdown')
  assert.equal(first.material.provenance.resolver, 'material-wiki')
  assert.equal(second.material.resolved_material.representations[0].content, '# 配置说明\n正文')
  assert.equal(requests.filter((request) => request === '/api/wiki/doc-1/versions').length, 1)
})

test('Feishu document URLs become Feishu Materials instead of generic web embeds', async () => {
  const result = await resolveMaterialLink(
    'https://example.feishu.cn/docx/AbCdEf', noCanvas, attachedTo, { now: fixedNow },
  )
  assert.equal(result.title, 'collab platform云文档')
  assert.equal(result.adapter, 'link')
  assert.equal(result.material.provenance.resolver, 'feishu-document')
  assert.equal(result.material.resolved_material.representations[0].source_ref.kind, 'feishu-document')
  assert.equal(result.material.attached_to.material_id, attachedTo)
})

test('image, PDF, audio and video URLs select the matching Material adapters', async () => {
  const cases = [
    ['https://assets.example/a.png', 'image', 'image/*'],
    ['https://assets.example/a.pdf', 'pdf', 'application/pdf'],
    ['https://assets.example/a.mp3', 'audio', 'audio/*'],
    ['https://assets.example/a.mp4', 'video', 'video/*'],
  ]
  for (const [url, adapter, mediaType] of cases) {
    const result = await resolveMaterialLink(url, noCanvas, attachedTo, { now: fixedNow })
    assert.equal(result.adapter, adapter)
    assert.equal(result.material.resolved_material.representations[0].media_type, mediaType)
    assert.equal(result.material.provenance.resolver, 'media-url')
  }
})

test('ordinary URLs fall back to a generic attached link Material', async () => {
  const result = await resolveMaterialLink(
    'https://example.com/page', noCanvas, attachedTo, { now: fixedNow },
  )
  assert.equal(result.adapter, 'link')
  assert.equal(result.material.provenance.resolver, 'generic-url')
  assert.equal(result.material.resolved_material.representations[0].renderer_hint, 'external-link')
  assert.deepEqual(result.material.attached_to, { material_id: attachedTo })
})
