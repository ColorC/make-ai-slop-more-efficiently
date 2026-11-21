import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { CanvasFileStore, CanvasStoreConflict } from '../store.ts'
import { canvasStorageId, emptyCanvasDocument } from '../../material-core/note.ts'

const card = (id, title, x = 60) => ({
  id, kind: 'text', title, adapter: 'text',
  source: { kind: 'file', path: `${id}.md`, token: `token-${id}` },
  x, y: 72, width: 440, height: 320, fontSize: 14,
})

function tmpRoot() { return mkdtempSync(join(tmpdir(), 'canvas-store-')) }

test('strict store (workshop 形态): mutation lifecycle', async () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const sessionRef = 'external:test-session'
    const docId = canvasStorageId(sessionRef)
    const workspace = { kind: 'workshop-canvas', ref: sessionRef }
    const actor = { kind: 'agent', ref: 'test' }

    // workspace 不匹配 → 拒绝
    assert.throws(() => store.mutate({
      document_id: 'bogus-id', workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'x', patch: {},
    }), CanvasStoreConflict)

    const created = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'm1', patch: {
        set_catalog: true,
        catalog: { purpose: 'product', lifecycle: 'formal', role: 'working', domain: 'config' },
        set_display: true,
        display: { pluginId: 'canvas-board' },
        set_behavior: true,
        behavior: { performance: { virtualization: 'always' }, folding: { enabled: true } },
        upsert_cards: [card('c1', '卡一')],
      },
    })
    assert.equal(created.revision, 1)
    assert.equal(created.document.catalog.lifecycle, 'formal')
    assert.equal(created.document._entityRevisions.catalog, 1)
    assert.equal(created.document.display.pluginId, 'canvas-board')
    assert.equal(created.document._entityRevisions.display, 1)
    assert.equal(created.document.behavior.performance.virtualization, 'always')
    assert.equal(created.document._entityRevisions.behavior, 1)
    // 卡片 material 化命名空间 = productRef
    assert.equal(created.document.cards[0].material.id, `material:workshop/note/${docId}/c1`)
    assert.equal('material_type' in created.document.cards[0].material.metadata, false)

    const dup = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'm1', patch: { upsert_cards: [card('c1', '卡一')] },
    })
    assert.equal(dup.duplicate, true)

    // 实体级冲突
    store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'm2', patch: { upsert_cards: [card('c1', '卡一改')] },
    })
    assert.throws(() => store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'm3', patch: { upsert_cards: [card('c1', '冲突改')] },
    }), CanvasStoreConflict)

    // 未触碰实体可基于旧 base 合并
    const merged = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'm4', patch: { upsert_cards: [card('c2', '卡二', 560)] },
    })
    assert.equal(merged.revision, 3)
    assert.equal(merged.merged, true)
    assert.equal(store.get(docId).cards.length, 2)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('写入身份: mutation 记身份, 卡片盖 provenance(created 只定一次), 目的可后补, 历史可按卡查', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const sessionRef = 'external:identity-session'
    const docId = canvasStorageId(sessionRef)
    const workspace = { kind: 'workshop-canvas', ref: sessionRef }
    const agent = {
      principal: 'agent',
      machine: { id: '53df1732-cda5-4221-9e79-8d8dcf96a141', label: 'example-host', owner: 'maintainer', os: 'windows' },
      runtime: { provider: 'claude_2', model: 'fable' },
      session: { native_id: '073260ab-53ae-469e-b765-14cd2fdbb7b3', pty_id: '6dd90a98f43a4ded' },
      captured_by: 'cli',
      at: '2026-09-12T12:00:00.000Z',
    }
    const human = {
      principal: 'human',
      machine: { id: 'browser-1', label: '平板' },
      human: { name: 'maintainer', input: 'touch', device: '平板' },
      captured_by: 'browser',
      at: '2026-09-12T12:05:00.000Z',
    }

    // 不合契约的身份直接拒绝，不静默丢
    assert.throws(() => store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: { kind: 'agent', ref: 'x' },
      base_revision: 0, idempotency_key: 'bad', identity: { principal: 'agent', machine: { id: 'm' }, captured_by: 'cli', at: 'now' },
      patch: { upsert_cards: [card('c1', '卡一')] },
    }), /identity 不合契约/)

    const first = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: { kind: 'agent', ref: 'x' },
      base_revision: 0, idempotency_key: 'm1', identity: agent,
      patch: { upsert_cards: [card('c1', '卡一')] },
    })
    assert.equal(first.document._mutations[0].identity.session.native_id, agent.session.native_id)
    assert.deepEqual(first.document._mutations[0].cards, ['c1'])
    assert.equal(first.document.cards[0].provenance.created.principal, 'agent')
    assert.equal(first.document.cards[0].provenance.updated.runtime.provider, 'claude_2')

    // 人改同一张卡：created 保持 agent，updated 换成 human；客户端伪造的 provenance 不采信
    const second = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: { kind: 'browser', ref: 'tablet' },
      base_revision: 1, idempotency_key: 'm2', identity: human,
      patch: { upsert_cards: [{ ...card('c1', '卡一（人改）'), provenance: { created: human, updated: human } }] },
    })
    const c1 = second.document.cards.find((item) => item.id === 'c1')
    assert.equal(c1.provenance.created.principal, 'agent')
    assert.equal(c1.provenance.updated.principal, 'human')
    assert.equal(c1.provenance.updated.human.input, 'touch')
    assert.equal(second.document.lastMutation.identity.principal, 'human')

    // 无身份的旧式写入：不伪造，沿用卡上既有溯源
    const third = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: { kind: 'agent', ref: 'legacy' },
      base_revision: 2, idempotency_key: 'm3',
      patch: { upsert_cards: [card('c2', '卡二', 560), card('c1', '卡一（再改）')] },
    })
    assert.equal(third.document._mutations[2].identity, undefined)
    assert.equal(third.document.cards.find((item) => item.id === 'c1').provenance.updated.principal, 'human')
    assert.equal(third.document.cards.find((item) => item.id === 'c2').provenance, undefined)

    // 目的后补：不推进 revision
    const annotated = store.annotateMutations(docId, { mutation_ids: ['m1', 'm2'], purpose: '给阶梯礼包画布加评论', batch: 'batch-1' })
    assert.equal(annotated.annotated, 2)
    const reread = store.get(docId)
    assert.equal(reread.revision, 3)
    assert.equal(reread._mutations[0].purpose, '给阶梯礼包画布加评论')
    assert.equal(reread._mutations[0].identity.batch, 'batch-1')
    assert.equal(reread._mutations[2].purpose, undefined)
    assert.equal(store.annotateMutations(docId, { from_sequence: 3, to_sequence: 3, purpose: '补卡二' }).annotated, 1)

    // 按卡查历史
    assert.deepEqual(store.listMutations(docId, { card: 'c2' }).map((item) => item.id), ['m3'])
    assert.deepEqual(store.listMutations(docId, { card: 'c1' }).map((item) => item.id), ['m1', 'm2', 'm3'])
    assert.deepEqual(store.listMutations(docId, { since: 1, limit: 1 }).map((item) => item.id), ['m3'])
    assert.throws(() => store.annotateMutations(docId, { mutation_ids: ['m1'] }), /至少要给/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('评论卡: kind=comment 走 material 化并落盘, 线程不合契约的整张丢弃', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const sessionRef = 'external:comment-session'
    const docId = canvasStorageId(sessionRef)
    const workspace = { kind: 'workshop-canvas', ref: sessionRef }
    const actor = { kind: 'browser', ref: 'tablet' }
    const comment = {
      id: 'note-1', kind: 'comment', title: '评论', text: '横幅太暗了', adapter: 'text',
      commentThread: { targets: ['c1'], root: 'note-1' },
      x: 600, y: 72, width: 260, height: 180,
    }
    // 线程不合契约 → 写入边界直接拒绝
    assert.throws(() => store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'bad',
      patch: { upsert_cards: [card('c1', '横幅'), { ...comment, id: 'broken', commentThread: { targets: [] } }] },
    }), /评论卡 broken 不合契约/)
    assert.throws(() => store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'bad2',
      patch: { upsert_cards: [{ ...card('c1', '横幅'), commentThread: { targets: ['x'], root: 'c1' } }] },
    }), /只有评论卡可以带 commentThread/)
    const created = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'm1',
      patch: { upsert_cards: [card('c1', '横幅'), comment] },
    })
    const stored = created.document.cards.find((item) => item.id === 'note-1')
    assert.equal(stored.kind, 'comment')
    assert.equal(stored.text, '横幅太暗了')
    assert.deepEqual(stored.commentThread, { targets: ['c1'], root: 'note-1' })
    assert.ok(stored.material.labels.includes('评论'))
    // 读回后仍在
    assert.equal(store.get(docId).cards.find((item) => item.id === 'note-1').commentThread.root, 'note-1')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('builtin store (dashboard 形态): 宽松 workspace + materials 正文目录', async () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, {
      productRef: 'omnidashboard', textDir: 'materials', workspaceCheck: 'off',
    })
    const workspace = { kind: 'workspace', ref: 'external:inbox-session' }
    const actor = { kind: 'cli', ref: 'review-submit' }

    // 具名画布(非 session- 编码)也可写 —— Python 桥历史语义
    const created = store.mutate({
      document_id: 'review-inbox', workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'm1',
      patch: { set_title: true, title: '审阅收件箱', upsert_cards: [card('c1', '卡')] },
    })
    assert.equal(created.revision, 1)
    assert.equal(created.document.title, '审阅收件箱')
    assert.equal(created.document.cards[0].material.id, 'material:omnidashboard/note/review-inbox/c1')

    // 正文落 materials/ 目录
    const put = store.putText('review-c1', '# 正文')
    assert.ok(String(put.path).includes('materials'))
    assert.equal(store.getText('review-c1').content, '# 正文')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('list 容错: 损坏文件跳过', async () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'x', workspaceCheck: 'off' })
    store.mutate({
      document_id: 'doc-a', workspace_ref: { kind: 'workspace', ref: 'a' }, actor_ref: { kind: 't', ref: 't' },
      base_revision: 0, idempotency_key: 'm1', patch: { upsert_cards: [card('c1', '卡')] },
    })
    const { writeFileSync } = await import('node:fs')
    writeFileSync(join(root, 'canvases', 'corrupt.json'), '{bad json', 'utf8')
    const items = store.list()
    assert.equal(items.length, 1)
    assert.equal(items[0]._documentId, 'doc-a')
    assert.equal(items[0].sessionId ? true : false, true)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('preset_ref survives Canvas materialization and legacy free-text types are removed', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const sessionRef = 'external:preset'
    const presetCard = {
      ...card('planning', '规划'),
      material: {
        preset_ref: {
          profile_id: 'workshop.material-presets',
          profile_revision: 'v1',
          preset_id: 'config-planning',
        },
        metadata: {
          material_type: 'planning', material_kind: 'config-planning',
          preset_label: '规划',
        },
      },
    }
    const created = store.mutate({
      document_id: canvasStorageId(sessionRef),
      workspace_ref: { kind: 'workshop-canvas', ref: sessionRef },
      actor_ref: { kind: 'agent', ref: 'test' },
      base_revision: 0,
      idempotency_key: 'preset-create',
      patch: { upsert_cards: [presetCard] },
    })
    const material = created.document.cards[0].material
    assert.equal(material.preset_ref.preset_id, 'config-planning')
    assert.equal(material.metadata.preset_label, '规划')
    assert.equal('material_type' in material.metadata, false)
    assert.equal('material_kind' in material.metadata, false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('落盘闸门拒绝 canvas 与 note 正文中的连续问号', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'omnidashboard', workspaceCheck: 'off' })
    assert.throws(() => store.putText('broken-body', '正常前缀???损坏正文'), /疑似有损编码/)
    assert.throws(() => store.mutate({
      document_id: 'broken-canvas',
      workspace_ref: { kind: 'workspace', ref: 'external:broken-canvas' },
      actor_ref: { kind: 'agent', ref: 'negative-test' },
      base_revision: 0,
      idempotency_key: 'reject-mojibake',
      patch: { set_title: true, title: '损坏???标题' },
    }), /疑似有损编码/)
    assert.equal(store.get('broken-canvas'), null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('strict store can classify an existing migrated document with a noncanonical physical id', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const id = 'session-legacy-review'
    const workspace = { kind: 'workspace', ref: 'external:migrated-legacy-review' }
    store.atomicJson(store.canvasPath(id), {
      ...emptyCanvasDocument(workspace.ref),
      _workspaceRef: workspace,
      _entityRevisions: { title: 0, catalog: 0, display: 0, cards: {}, relations: {} },
      _mutations: [],
    })
    const result = store.mutate({
      document_id: id,
      workspace_ref: workspace,
      actor_ref: { kind: 'product', ref: 'canvas-catalog' },
      base_revision: 0,
      idempotency_key: 'classify-legacy',
      patch: { set_catalog: true, catalog: { purpose: 'product', lifecycle: 'archived', role: 'working', domain: 'review' } },
    })
    assert.equal(result.document.catalog.lifecycle, 'archived')
    assert.equal(result.document._documentId, id)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('position lock rejects ordinary moves but allows metadata edits and explicit override', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const sessionRef = 'external:locked-position'
    const docId = canvasStorageId(sessionRef)
    const workspace = { kind: 'workshop-canvas', ref: sessionRef }
    const actor = { kind: 'agent', ref: 'test' }
    const locked = { ...card('locked', '锁定卡'), positionLock: { locked: true } }
    store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'lock-create', patch: { upsert_cards: [locked] },
    })

    const metadataEdit = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'lock-title',
      patch: { upsert_cards: [{ ...locked, title: '只改标题' }] },
    })
    assert.equal(metadataEdit.document.cards[0].title, '只改标题')

    assert.throws(() => store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 2, idempotency_key: 'lock-move-denied',
      patch: { upsert_cards: [{ ...locked, title: '只改标题', x: 900 }] },
    }), (error) => error instanceof CanvasStoreConflict && error.conflicts.includes('card:locked:position'))

    const moved = store.mutate({
      document_id: docId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 2, idempotency_key: 'lock-move-override', position_lock_override: true,
      patch: { upsert_cards: [{ ...locked, title: '只改标题', x: 900 }] },
    })
    assert.equal(moved.document.cards[0].x, 900)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('canvas lifecycle separates archive, restore, impact preview and permanent deletion', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const actor = { kind: 'user', ref: 'canvas-catalog' }
    const workspace = { kind: 'workshop-canvas', ref: 'canvas:deletion-target' }
    const documentId = canvasStorageId(workspace.ref)
    store.putText('deletion-body', '# retained body')
    store.mutate({
      document_id: documentId, workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'lifecycle-create', patch: {
        set_title: true, title: 'Deletion target',
        set_catalog: true, catalog: { purpose: 'product', lifecycle: 'formal', role: 'working', domain: 'config' },
        upsert_cards: [{
          ...card('owned-body', 'Owned body'),
          source: { kind: 'file', path: 'deletion-body.md', editableId: 'deletion-body', token: 'body-token' },
        }],
      },
    })
    store.mutate({
      document_id: canvasStorageId('canvas:referrer'),
      workspace_ref: { kind: 'workshop-canvas', ref: 'canvas:referrer' }, actor_ref: actor,
      base_revision: 0, idempotency_key: 'referrer-create', patch: {
        set_title: true, title: 'Referrer',
        upsert_cards: [{ ...card('target-link', 'Target link'), source: { kind: 'link', url: `/canvas?canvas=${documentId}` } }],
      },
    })

    const archived = store.archive(documentId, actor)
    assert.equal(archived.catalog.lifecycle, 'archived')
    assert.equal(archived._archiveState.previousLifecycle, 'formal')
    const restored = store.restore(documentId, actor)
    assert.equal(restored.catalog.lifecycle, 'formal')
    assert.equal(restored._archiveState, undefined)
    store.archive(documentId, actor)

    const preview = store.deletionPreview(documentId)
    assert.equal(preview.cards, 1)
    assert.equal(preview.retained_text_ids[0], 'deletion-body')
    assert.equal(preview.referenced_by[0].title, 'Referrer')
    assert.throws(() => store.deleteArchived(documentId, 'Deletion target'), CanvasStoreConflict)
    assert.throws(() => store.deleteArchived(documentId, 'wrong title', true), /名称不匹配/)

    const deleted = store.deleteArchived(documentId, 'Deletion target', true)
    assert.equal(deleted.document_id, documentId)
    assert.equal(store.get(documentId), null)
    assert.equal(store.isDeleted(documentId), true)
    assert.equal(store.getText('deletion-body').content, '# retained body')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Canvas View participates in entity-level revision conflicts', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'workshop' })
    const sessionRef = 'canvas:view-conflict'
    const documentId = canvasStorageId(sessionRef)
    const input = {
      document_id: documentId,
      workspace_ref: { kind: 'workshop-canvas', ref: sessionRef },
      actor_ref: { kind: 'test', ref: 'view-conflict' },
    }
    const created = store.mutate({
      ...input, base_revision: 0, idempotency_key: 'view-1',
      patch: { set_view: true, view: { source: { kind: 'material-graph', ref: 'graph:one' } } },
    })
    assert.equal(created.document._entityRevisions.view, 1)
    assert.throws(() => store.mutate({
      ...input, base_revision: 0, idempotency_key: 'view-stale',
      patch: { set_view: true, view: { source: { kind: 'material-graph', ref: 'graph:stale' } } },
    }), (error) => error instanceof CanvasStoreConflict && error.conflicts.includes('view'))
    const unrelated = store.mutate({
      ...input, base_revision: 0, idempotency_key: 'view-unrelated-card',
      patch: { upsert_cards: [card('view-card', 'Unrelated card')] },
    })
    assert.equal(unrelated.merged, true)
    assert.equal(unrelated.document.view.source.ref, 'graph:one')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
