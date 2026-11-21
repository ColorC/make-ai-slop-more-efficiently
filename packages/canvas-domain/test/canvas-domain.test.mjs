import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  DomainRegistry, formatDomainUri, parseDomainConfig, parseDomainUri,
  hasCapability, assertWritable, DomainAccessError,
} from '../index.ts'
import { DiskDomainAdapter, DiskDomainConflict } from '../disk.ts'

test('parseDomainConfig validates disk and protocol forms', () => {
  const disk = parseDomainConfig({ id: 'workshop', transport: 'disk', root: 'E:/tmp/workshop' })
  assert.equal(disk.transport, 'disk')
  assert.equal(disk.access, 'readwrite')
  assert.deepEqual(disk.capabilities, ['canvas'])

  const protocol = parseDomainConfig({
    id: 'cloud', transport: 'protocol', endpoint: 'https://workshop.example.com/',
    access: 'readonly', capabilities: ['canvas', 'wiki'],
  })
  assert.equal(protocol.transport, 'protocol')
  assert.equal(protocol.endpoint, 'https://workshop.example.com')
  assert.equal(protocol.access, 'readonly')

  assert.throws(() => parseDomainConfig({ id: 'Bad_ID!', transport: 'disk', root: '/x' }))
  assert.throws(() => parseDomainConfig({ id: 'a', transport: 'ftp' }))
  assert.throws(() => parseDomainConfig({ id: 'a', transport: 'disk' }))
  assert.throws(() => parseDomainConfig({ id: 'a', transport: 'protocol', endpoint: 'ftp://x' }))
  assert.throws(() => parseDomainConfig({ id: 'a', transport: 'disk', root: '/x', capabilities: ['bogus'] }))
})

test('domain uri round trip', () => {
  const uri = { domain: 'workshop', kind: 'canvas', ref: 'session-abc' }
  assert.deepEqual(parseDomainUri(formatDomainUri(uri)), uri)
  assert.throws(() => parseDomainUri('omni://project/workshop'))
})

test('registry keeps descriptors and capability queries', () => {
  const registry = new DomainRegistry()
  const adapter = new DiskDomainAdapter({ id: 'builtin', root: mkdtempSync(join(tmpdir(), 'cd-')), capabilities: ['canvas', 'material'] })
  registry.register(adapter)
  assert.throws(() => registry.register(adapter))
  assert.equal(registry.require('builtin'), adapter)
  assert.equal(registry.withCapability('material').length, 1)
  assert.equal(registry.withCapability('wiki').length, 0)
  assert.ok(hasCapability(adapter.descriptor(), 'canvas'))
  assert.ok(registry.unregister('builtin'))
})

test('readonly domain rejects writes', () => {
  const adapter = new DiskDomainAdapter({ id: 'ro', root: '/nowhere', access: 'readonly' })
  assert.throws(() => assertWritable(adapter.descriptor()), DomainAccessError)
})

test('disk adapter mutation lifecycle: create, CAS, idempotency, conflict', async () => {
  const root = mkdtempSync(join(tmpdir(), 'cd-domain-'))
  try {
    const adapter = new DiskDomainAdapter({ id: 't', root })
    const workspace = { kind: 'workspace', ref: 'external:test-session' }
    const actor = { kind: 'agent', ref: 'test' }
    const card = {
      id: 'card-1', kind: 'text', title: '卡一', adapter: 'text',
      source: { kind: 'file', path: 'card-1.md', token: 'token' },
      x: 80, y: 72, width: 440, height: 320, fontSize: 14,
    }

    const created = await adapter.mutateCanvas({
      document_id: 'session-test', workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'm1', patch: { upsert_cards: [card] },
    })
    assert.equal(created.revision, 1)
    assert.equal(created.duplicate, false)

    const duplicate = await adapter.mutateCanvas({
      document_id: 'session-test', workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'm1', patch: { upsert_cards: [card] },
    })
    assert.equal(duplicate.duplicate, true)
    assert.equal(duplicate.revision, 1)

    const head = await adapter.headCanvas('session-test')
    assert.equal(head.revision, 1)

    // 实体级冲突：基于旧 revision 再改同一张卡
    await adapter.mutateCanvas({
      document_id: 'session-test', workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'm2',
      patch: { upsert_cards: [{ ...card, title: '卡一改' }] },
    })
    await assert.rejects(adapter.mutateCanvas({
      document_id: 'session-test', workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'm3',
      patch: { upsert_cards: [{ ...card, title: '卡二改' }] },
    }), DiskDomainConflict)

    // 未触碰的实体不受旧 base 影响
    const merged = await adapter.mutateCanvas({
      document_id: 'session-test', workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'm4',
      patch: { upsert_cards: [{ ...card, id: 'card-2', title: '卡二' }] },
    })
    assert.equal(merged.revision, 3)
    assert.equal(merged.merged, true)

    const listed = await adapter.listCanvases()
    assert.equal(listed.length, 1)
    assert.equal(listed[0].cards, 2)

    // 只读 adapter 拒绝写
    const readOnly = new DiskDomainAdapter({ id: 'ro2', root, access: 'readonly' })
    await assert.rejects(readOnly.mutateCanvas({
      document_id: 'session-test', workspace_ref: workspace, actor_ref: actor,
      base_revision: 3, idempotency_key: 'm5', patch: { set_title: true, title: 'x' },
    }), DomainAccessError)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('disk adapter text round trip', async () => {
  const root = mkdtempSync(join(tmpdir(), 'cd-text-'))
  try {
    const adapter = new DiskDomainAdapter({ id: 't', root })
    assert.equal(await adapter.getText('review-x'), null)
    const put = await adapter.putText('review-x', '# 标题\n正文', 'agent:test')
    assert.ok(put.path.endsWith('review-x.md'))
    assert.equal(await adapter.getText('review-x'), '# 标题\n正文')
    await assert.rejects(adapter.getText('../escape'), TypeError)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('disk adapter enforces locked card position unless explicitly overridden', async () => {
  const root = mkdtempSync(join(tmpdir(), 'cd-lock-'))
  try {
    const adapter = new DiskDomainAdapter({ id: 'lock', root })
    const workspace = { kind: 'workspace', ref: 'external:lock' }
    const actor = { kind: 'agent', ref: 'test' }
    const locked = {
      id: 'locked', kind: 'text', title: '锁定卡', adapter: 'text',
      source: { kind: 'file', path: 'locked.md', token: 'locked' },
      x: 80, y: 72, width: 440, height: 320, positionLock: { locked: true },
    }
    await adapter.mutateCanvas({
      document_id: 'session-lock', workspace_ref: workspace, actor_ref: actor,
      base_revision: 0, idempotency_key: 'create', patch: { upsert_cards: [locked] },
    })
    await adapter.mutateCanvas({
      document_id: 'session-lock', workspace_ref: workspace, actor_ref: actor,
      base_revision: 1, idempotency_key: 'rename', patch: { upsert_cards: [{ ...locked, title: '改标题' }] },
    })
    await assert.rejects(adapter.mutateCanvas({
      document_id: 'session-lock', workspace_ref: workspace, actor_ref: actor,
      base_revision: 2, idempotency_key: 'move-denied', patch: { upsert_cards: [{ ...locked, title: '改标题', y: 500 }] },
    }), (error) => error instanceof DiskDomainConflict && error.conflicts.includes('card:locked:position'))
    await adapter.mutateCanvas({
      document_id: 'session-lock', workspace_ref: workspace, actor_ref: actor,
      base_revision: 2, idempotency_key: 'move-override', position_lock_override: true,
      patch: { upsert_cards: [{ ...locked, title: '改标题', y: 500 }] },
    })
    assert.equal((await adapter.getCanvas('session-lock')).cards[0].y, 500)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
