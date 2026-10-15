import test from 'node:test'
import assert from 'node:assert/strict'
import {
  applyCanvasMutation, canvasPatchIsEmpty, canvasStorageId, diffCanvasDocuments,
  emptyCanvasDocument, formatCanvasCardReference, freezeCanvasCard, groupCanvasCards,
  normalizeCanvasFontSize, parseCanvasDocument, parseNoteLocator, persistCite,
  projectCanvasToDocument, projectCanvasToView, projectDocumentToCanvas,
  resizeCanvasCard, setCanvasRelationPath, unfreezeCanvasCard, ungroupCanvasCards,
  workshopNoteTheme, omnicompanyNoteTheme, cssVarsFromNoteTheme, isUnreadNoteCard,
  UnifiedMaterialSurface, viewMemberIds, viewMembersForTheme, candidateNoteStorageIds,
} from '../index.ts'

const card = (id, x = 0) => ({
  id, kind: 'text', title: id, adapter: 'text',
  source: { kind: 'file', path: `${id}.md`, token: 'token' },
  x, y: 0, width: 440, height: 320, fontSize: 16,
})

test('stable workspace storage identity is shared across hosts', () => {
  assert.equal(canvasStorageId('external:bside:spark:studio'), 'session-ZXh0ZXJuYWw6YnNpZGU6c3Bhcms6c3R1ZGlv')
})

test('parse normalizes legacy geometry and relation presentation', () => {
  const parsed = parseCanvasDocument(JSON.stringify({
    version: 1, sessionId: 'ignored', revision: 3, updatedAt: '2026-08-14T00:00:00Z',
    cards: [card('a'), card('b')],
    relations: [{ id: 'e', source: 'a', target: 'b', path: 'straight', arrow: 'open' }],
  }), 'workspace:actual')
  assert.equal(parsed.sessionId, 'workspace:actual')
  assert.equal(parsed.relations[0].path, 'straight')
  assert.equal(parsed.relations[0].arrow, 'open')
})

test('entity patches merge non-overlapping card edits without snapshot replacement', () => {
  const base = { ...emptyCanvasDocument('workspace:test'), cards: [card('a'), card('b')] }
  const left = { ...base, cards: [card('a', 10), card('b')] }
  const right = { ...base, cards: [card('a'), card('b', 20)] }
  const leftPatch = diffCanvasDocuments(base, left)
  const rightPatch = diffCanvasDocuments(base, right)
  assert.equal(canvasPatchIsEmpty(leftPatch), false)
  const merged = applyCanvasMutation(applyCanvasMutation(base, leftPatch), rightPatch)
  assert.deepEqual(merged.cards.map(({ id, x }) => [id, x]), [['a', 10], ['b', 20]])
})

test('shared canvas operations keep geometry, relation style and app-addressable refs consistent', () => {
  const base = {
    ...emptyCanvasDocument('workspace:test'),
    cards: [card('a', 100), { ...card('b', 420), y: 80 }],
    relations: [{ id: 'edge', source: 'a', target: 'b', path: 'curve' }],
  }
  const grouped = groupCanvasCards(base, ['a', 'b'], 'group-1')
  assert.equal(grouped.cards[0].kind, 'group')
  assert.equal(grouped.cards.filter((item) => item.parentId === 'group-1').length, 2)
  const restored = ungroupCanvasCards(grouped, ['group-1'])
  assert.deepEqual(restored.cards.map(({ id, x, y }) => [id, x, y]), [['a', 100, 0], ['b', 420, 80]])
  assert.equal(setCanvasRelationPath(base, ['edge'], 'curve').relations[0].path, 'curve')
  assert.equal(normalizeCanvasFontSize(20.6), 21)
  assert.equal(normalizeCanvasFontSize(Number.NaN), 14)
  assert.equal(
    formatCanvasCardReference('Spark Workshop', 'session-a', 'text-b'),
    'workshop/session-a#text-b',
  )
  assert.equal(canvasStorageId('canvas:draft-ab12'), 'draft-ab12')
  assert.deepEqual(parseNoteLocator('workshop/draft-ab12#text-b'), {
    space: 'workshop', path: 'draft-ab12', card: 'text-b',
  })
  assert.deepEqual(
    parseNoteLocator('[omni-canvas-card:spark-workshop] session-a#text-b'),
    { space: 'workshop', path: 'session-a', card: 'text-b' },
  )
  const live = { ...card('web', 0), adapter: 'web', source: { kind: 'link', url: 'https://example.com' } }
  const frozen = freezeCanvasCard(live, '2026-08-15T00:00:00.000Z')
  assert.equal(frozen.frozen.from.url, 'https://example.com')
  assert.equal(unfreezeCanvasCard(frozen).adapter, 'web')
  assert.equal(resizeCanvasCard(live, 640, 400).width, 640)
  assert.equal(projectCanvasToDocument({
    ...emptyCanvasDocument('workspace:test'),
    title: '同一份',
    cards: [{ ...card('a', 0), text: '甲', y: 20 }, { ...card('b', 20), text: '乙', y: 0 }],
  }).blocks.map((block) => block.id).join(','), 'b,a')
  assert.equal(cssVarsFromNoteTheme(workshopNoteTheme)['--note-accent'], '#8a4b27')
  assert.equal(isUnreadNoteCard({ ...live, title: '【Agent 提议】', material: { metadata: { origin: 'agent' } } }), true)
})

test('wiki and canvas are two projections of the same View and share one locator', () => {
  const surface = new UnifiedMaterialSurface()
  const canvas = {
    ...emptyCanvasDocument('canvas:draft-ab12'),
    title: '同一份',
    cards: [
      { ...card('text-1', 0), text: '甲正文', title: '甲' },
      { ...card('text-2', 20), text: '乙正文', title: '乙', y: 40 },
    ],
  }
  surface.writeFromCanvas(canvas)
  const fromWiki = surface.asDocument('workshop/draft-ab12')
  assert.deepEqual(fromWiki.blocks.map((block) => [block.id, block.text]), [['text-1', '甲正文'], ['text-2', '乙正文']])
  assert.equal(persistCite('workshop/draft-ab12'), 'workshop/draft-ab12')
  assert.equal(persistCite('/bside/canvas/draft-ab12'), 'workshop/draft-ab12')
  assert.deepEqual(parseNoteLocator('/bside/canvas/draft-ab12#text-1'), {
    space: 'workshop', path: 'draft-ab12', card: 'text-1',
  })

  surface.writeFromDocument({
    locator: 'workshop/notes/draft-cd34',
    title: '文档写入',
    members: [
      { id: 'blk-a', title: '一', body: '文档甲', order: 0 },
      { id: 'blk-b', title: '二', body: '文档乙', order: 1 },
    ],
  })
  const fromCanvas = surface.asCanvas('workshop/draft-cd34')
  const back = projectCanvasToView(fromCanvas)
  assert.deepEqual(back.members.map((member) => [member.id, member.body]), [['blk-a', '文档甲'], ['blk-b', '文档乙']])

  const view = surface.open('workshop/draft-ab12')
  assert.deepEqual(viewMemberIds(view), viewMemberIds({
    ...view,
    members: viewMembersForTheme(view, omnicompanyNoteTheme),
  }))
  assert.deepEqual(viewMembersForTheme(view, workshopNoteTheme), viewMembersForTheme(view, omnicompanyNoteTheme))

  const roundTrip = projectCanvasToView(projectDocumentToCanvas(view))
  assert.deepEqual(roundTrip.members.map((member) => [member.id, member.body]), view.members.map((member) => [member.id, member.body]))

  const sid = '46fa34003c9a409a'
  const stored = canvasStorageId(`external:${sid}`)
  assert.equal(persistCite({ space: 'workshop', path: stored }), `workshop/${sid}`)
  assert.ok(candidateNoteStorageIds(sid).includes(stored))
})
