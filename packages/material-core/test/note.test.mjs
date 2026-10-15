import test from 'node:test'
import assert from 'node:assert/strict'
import {
  isotonicPositions, placeNotesInColumns, placeNotesInGutters,
  applyCanvasMutation, canvasPatchIsEmpty, canvasStorageId, diffCanvasDocuments,
  isCanvasRelationLayer, relationLayerZIndex, resolveRelationLayer, resolveSliceEmphasis,
  shortElementName, canvasCardChrome, cardBodyRatioDeviation,
  isCanvasCommentStance, validateCanvasComment, resolveCommentPresentation,
  isCanvasRelationKind, validateCanvasRelationSemantics,
  emptyCanvasDocument, formatCanvasCardReference, freezeCanvasCard, groupCanvasCards,
  normalizeCanvasFontSize, parseCanvasDocument, parseNoteLocator, persistCite,
  projectCanvasToDocument, projectCanvasToView, projectDocumentToCanvas,
  resizeCanvasCard, setCanvasRelationPath, unfreezeCanvasCard, ungroupCanvasCards,
  workshopNoteTheme, omnicompanyNoteTheme, cssVarsFromNoteTheme, isUnreadNoteCard,
  UnifiedMaterialSurface, viewMemberIds, viewMembersForTheme, candidateNoteStorageIds,
  normalizeCanvasCatalog,
  CANVAS_SPACING, canvasSpacing, canvasSliceHandleId, canvasRelationSliceSignature,
  cardRatioDeviation, fitCardToIntrinsicRatio, normalizeCanvasRelationEndpoints,
  validateCanvasRelationSlices, validateCanvasSlice,
  canvasPresentationStyles, resolveCardPresentation, titleVisibleAtZoom, validateCanvasCardPresentation,
  validateCanvasCommentThread, validateWriteIdentity, normalizeWriteIdentity, writeIdentityActorRef, writeIdentityLabel,
} from '../note.ts'

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
  assert.equal(parsed.relations[0].path, 'curve')
  assert.equal(parsed.relations[0].arrow, 'open')
})

test('comment cards: thread validated, reply needs replyTo, semantic closed set, kept out of document projection', () => {
  const identity = {
    principal: 'human',
    machine: { id: 'tablet-1', label: '平板' },
    human: { name: 'maintainer', input: 'touch' },
    captured_by: 'browser',
    at: '2026-09-12T12:00:00Z',
  }
  const parsed = parseCanvasDocument(JSON.stringify({
    version: 1, sessionId: 's', revision: 1, updatedAt: '2026-09-12T00:00:00Z',
    cards: [
      card('a'),
      { id: 'root', kind: 'comment', title: '评论', text: '这里不对', adapter: 'text',
        commentThread: { targets: ['a', 'a'], root: 'root', semantic: 'correction' },
        provenance: { created: identity, updated: identity },
        x: 500, y: 0, width: 260, height: 180 },
      { id: 'reply', kind: 'comment', title: '接续', text: '同意', adapter: 'text',
        commentThread: { targets: ['a'], root: 'root', replyTo: 'root' }, x: 500, y: 200, width: 260, height: 180 },
      // 接续却没写 replyTo → 整张丢弃
      { id: 'bad-reply', kind: 'comment', title: 'x', text: 'x', adapter: 'text',
        commentThread: { targets: ['a'], root: 'root' }, x: 0, y: 0, width: 1, height: 1 },
      // 语义不在封闭集 → 整张丢弃
      { id: 'bad-semantic', kind: 'comment', title: 'x', text: 'x', adapter: 'text',
        commentThread: { targets: ['a'], root: 'bad-semantic', semantic: 'rant' }, x: 0, y: 0, width: 1, height: 1 },
      // 没有线程 → 整张丢弃
      { id: 'no-thread', kind: 'comment', title: 'x', text: 'x', adapter: 'text', x: 0, y: 0, width: 1, height: 1 },
    ],
    relations: [],
  }), 's')
  assert.deepEqual(parsed.cards.map((item) => item.id), ['a', 'root', 'reply'])
  assert.deepEqual(parsed.cards[1].commentThread, { targets: ['a'], root: 'root', semantic: 'correction' })
  assert.equal(parsed.cards[1].text, '这里不对')
  assert.equal(parsed.cards[1].provenance.created.human.name, 'maintainer')
  assert.equal(parsed.cards[2].commentThread.replyTo, 'root')
  assert.deepEqual(projectCanvasToDocument(parsed).blocks.map((block) => block.id), ['a'])
  assert.deepEqual(validateCanvasCommentThread({ targets: ['a'], root: 'x', replyTo: 'x' }, 'x'), ['评论不能接续自己'])
})

test('write identity: validation, normalization, actor ref and label', () => {
  const agent = {
    principal: 'agent',
    machine: { id: ' 53df1732-cda5 ', label: 'L-2026', owner: 'maintainer', os: 'windows', junk: 1 },
    runtime: { provider: 'claude_2', model: 'fable' },
    session: { native_id: '073260ab-53ae', file: 'C:/x/073260ab-53ae.jsonl' },
    purpose: '  加评论  ',
    captured_by: 'cli',
    at: '2026-09-12T12:00:00+08:00',
  }
  assert.deepEqual(validateWriteIdentity(agent), [])
  const normalized = normalizeWriteIdentity(agent)
  assert.equal(normalized.machine.id, '53df1732-cda5')
  assert.equal('junk' in normalized.machine, false)
  assert.equal(normalized.at, '2026-09-12T04:00:00.000Z')
  assert.equal(normalized.purpose, '加评论')
  assert.deepEqual(writeIdentityActorRef(normalized), { kind: 'agent', ref: 'claude_2@53df1732-cda5/073260ab-53ae' })
  assert.equal(writeIdentityLabel(normalized), 'claude_2 · 会话 073260ab · L-2026')

  const human = { principal: 'human', machine: { id: 'tablet-1' }, human: { name: 'maintainer', input: 'touch', device: '平板' }, captured_by: 'browser', at: '2026-09-12T12:00:00Z' }
  assert.equal(writeIdentityLabel(normalizeWriteIdentity(human)), 'maintainer · 触屏 · 平板')
  assert.deepEqual(writeIdentityActorRef(normalizeWriteIdentity(human)), { kind: 'human', ref: 'maintainer@tablet-1' })

  assert.ok(validateWriteIdentity({ ...human, human: undefined }).includes('principal=human 必须带 human'))
  assert.ok(validateWriteIdentity({ ...agent, runtime: undefined }).includes('principal=agent 必须带 runtime'))
  assert.ok(validateWriteIdentity({ ...human, human: { name: 'x', input: 'telepathy' } }).some((item) => item.startsWith('identity.human.input')))
  assert.ok(validateWriteIdentity({ ...agent, captured_by: 'magic' }).some((item) => item.startsWith('identity.captured_by')))
  assert.equal(normalizeWriteIdentity({ ...agent, at: 'not-a-date' }), undefined)
})

test('canvas interaction contract round-trips behavior and discards intermediate edge geometry', () => {
  const document = {
    ...emptyCanvasDocument('workspace:interaction'),
    display: { pluginId: 'canvas-board' },
    behavior: {
      performance: { virtualization: 'auto', virtualizationThreshold: 80, defaultRenderMode: 'auto' },
      workbenchLinks: [{ id: 'map', label: '游戏地图', href: '../interface-map', query: { view: 'all' } }],
      checks: [{ checker: 'graph.valid-endpoints' }],
      folding: { enabled: true },
    },
    cards: [{
      ...card('a'),
      positionLock: { locked: true, actor: 'user', reason: '人工定点', updatedAt: '2026-08-18T00:00:00Z' },
      positionSource: { kind: 'user', actor: 'browser:test', updatedAt: '2026-08-18T00:00:01Z' },
      display: { pluginId: 'comparison-card' },
      fold: { defaultCollapsed: true, memberIds: ['b'] },
    }, card('b')],
    relations: [{
      id: 'edge', source: 'a', target: 'b', path: 'orthogonal',
      programLabel: 'implements', userLabel: '用户补充',
      routePoints: [{ x: 220, y: 120 }, { x: 360, y: 120 }],
      curveControl: { x: 290, y: 88 },
      programLabelPosition: { t: 0.35, offsetY: -18 },
      userLabelPosition: { t: 0.72, offsetY: 16 },
    }],
  }
  const parsed = parseCanvasDocument(JSON.stringify(document), document.sessionId)
  assert.deepEqual(parsed.display, document.display)
  assert.deepEqual(parsed.behavior, document.behavior)
  assert.deepEqual(parsed.cards[0].positionLock, document.cards[0].positionLock)
  assert.deepEqual(parsed.cards[0].positionSource, document.cards[0].positionSource)
  assert.deepEqual(parsed.cards[0].display, document.cards[0].display)
  assert.deepEqual(parsed.cards[0].fold, document.cards[0].fold)
  assert.equal(parsed.relations[0].routePoints, undefined)
  assert.equal(parsed.relations[0].curveControl, undefined)
  assert.equal(parsed.relations[0].programLabel, 'implements')
  assert.equal(parsed.relations[0].userLabel, '用户补充')

  const changed = structuredClone(document)
  changed.relations[0].userLabelPosition.t = 2
  const normalized = parseCanvasDocument(JSON.stringify(changed), changed.sessionId)
  assert.equal(normalized.relations[0].userLabelPosition.t, 1)
  const patched = applyCanvasMutation(parsed, { upsert_relations: [document.relations[0]] })
  assert.equal(patched.relations[0].routePoints, undefined)
  assert.equal(patched.relations[0].curveControl, undefined)
})

test('same-position version containers survive canonical parsing without accepting invalid variants', () => {
  const document = {
    ...emptyCanvasDocument('workspace:versions'),
    cards: [{
      ...card('versions'), kind: 'group', source: undefined,
      samePositionContainer: {
        dimension: '版本',
        variants: [{ key: 'draft', label: '进行中' }, { key: 'cl-12', label: '已入版' }],
        activeVariant: 'draft',
      },
    }, {
      ...card('source'), parentId: 'versions',
      samePositionMember: { slotId: 'source', variantKey: 'draft' },
    }],
  }
  const parsed = parseCanvasDocument(JSON.stringify(document), document.sessionId)
  assert.deepEqual(parsed.cards[0].samePositionContainer, document.cards[0].samePositionContainer)
  assert.deepEqual(parsed.cards[1].samePositionMember, document.cards[1].samePositionMember)

  document.cards[0].samePositionContainer.activeVariant = 'missing'
  assert.equal(parseCanvasDocument(JSON.stringify(document), document.sessionId).cards[0].samePositionContainer, undefined)
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

test('canvas catalog facets stay orthogonal and round-trip through patches', () => {
  assert.deepEqual(normalizeCanvasCatalog({
    purpose: 'test', lifecycle: 'archived', domain: 'Config Tools', labels: ['smoke', 'smoke', '  acceptance  '],
  }), {
    purpose: 'test', lifecycle: 'archived', role: 'working', domain: 'config-tools', workflow_status: 'todo', labels: ['smoke', 'acceptance'],
  })
  const base = emptyCanvasDocument('workspace:catalog')
  const next = {
    ...base,
    catalog: { purpose: 'product', lifecycle: 'formal', role: 'demo', domain: 'config',
      wiki_path: ['游戏设计', '配置'], workflow_status: 'ready', labels: ['acceptance'] },
  }
  const patch = diffCanvasDocuments(base, next)
  assert.equal(patch.set_catalog, true)
  assert.deepEqual(applyCanvasMutation(base, patch).catalog, next.catalog)
  const parsed = parseCanvasDocument(JSON.stringify(next), next.sessionId)
  assert.deepEqual(parsed.catalog, next.catalog)
})

test('canvas display override round-trips through entity patches', () => {
  const base = emptyCanvasDocument('workspace:display')
  const next = { ...base, display: { pluginId: 'comparison-board' } }
  const patch = diffCanvasDocuments(base, next)
  assert.equal(patch.set_display, true)
  assert.deepEqual(patch.display, next.display)
  assert.deepEqual(applyCanvasMutation(base, patch).display, next.display)
  const removed = diffCanvasDocuments(next, base)
  assert.equal(removed.set_display, true)
  assert.equal(applyCanvasMutation(next, removed).display, undefined)
})

test('unclassified canvases default to the program-test lifecycle', () => {
  assert.equal(normalizeCanvasCatalog(undefined).lifecycle, 'test')
  assert.equal(normalizeCanvasCatalog({ lifecycle: 'test' }).lifecycle, 'test')
  assert.equal(normalizeCanvasCatalog({ lifecycle: 'development' }).lifecycle, 'development')
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

// ── 画布通用切片协议（REQ-CANVAS-SLICE-PROTOCOL §3 / §5） ──

test('切片种类封闭，element/region 必须给 box，range/step 必须不给', () => {
  assert.deepEqual(validateCanvasSlice({ kind: 'range', ref: 'a::b' }), [])
  assert.deepEqual(validateCanvasSlice({ kind: 'step', ref: 'step-3' }), [])
  assert.deepEqual(validateCanvasSlice({
    kind: 'element', ref: 'uiroot/MainCanvas/btn_menu',
    box: { x: 0.61, y: 0.07, width: 0.22, height: 0.06 },
  }), [])

  // 未知种类：拒绝，不兜底
  assert.match(validateCanvasSlice({ kind: 'pixel', ref: 'x' })[0], /值集封闭/)
  // element 缺 box：拒绝
  assert.match(validateCanvasSlice({ kind: 'element', ref: 'p' })[0], /必须给 box/)
  // range 多给 box：拒绝（它没有图内区域）
  assert.match(validateCanvasSlice({ kind: 'range', ref: 'r', box: { x: 0, y: 0, width: 1, height: 1 } })[0], /不得给 box/)
  // 把序号拼进 ref：拒绝 —— 拼进字符串就又变成不可拆的结构
  assert.match(validateCanvasSlice({ kind: 'element', ref: 'p#occurrence=1', box: { x: 0, y: 0, width: 1, height: 1 } })
    .find((item) => /occurrence/.test(item)), /occurrence/)
  // 退化 box
  assert.ok(validateCanvasSlice({ kind: 'region', ref: 'r', box: { x: 0, y: 0, width: 0, height: 0.2 } })
    .some((item) => /width 必须 > 0/.test(item)))
})

test('handle 由切片派生；存量文字锚点保持零前缀以免落库关系失配', () => {
  // 存量注释锚点早就直接把锚点 id 当 handle id 用
  assert.equal(canvasSliceHandleId({ kind: 'range', ref: '020-getlike-open::9cba3ecb7373' }),
    '020-getlike-open::9cba3ecb7373')
  assert.equal(canvasSliceHandleId({
    kind: 'element', ref: 'uiroot/MainCanvas/btn_menu', box: { x: 0, y: 0, width: 1, height: 1 },
  }), 'el:uiroot/MainCanvas/btn_menu')
  // representation 作用域 + 重复序号
  assert.equal(canvasSliceHandleId({
    kind: 'element', ref: 'p', representationId: 'shot-000-base', occurrence: 1,
    box: { x: 0, y: 0, width: 1, height: 1 },
  }), 'shot-000-base@el:p#1')
  // occurrence=0 是默认，不进 handle（否则同一元素会出现两种 handle id）
  assert.equal(canvasSliceHandleId({ kind: 'step', ref: 's', occurrence: 0 }), 'st:s')
})

test('切片是端点权威：handle 与切片不一致时被判红并纠正', () => {
  const relation = {
    id: 'r1', source: 'a', target: 'b',
    sourceSlice: { kind: 'element', ref: 'btn_menu', box: { x: 0, y: 0, width: 1, height: 1 } },
    sourceHandle: 'right',
    path: 'curve',
  }
  assert.match(validateCanvasRelationSlices([relation]).join('|'), /切片是权威/)
  assert.equal(normalizeCanvasRelationEndpoints(relation).sourceHandle, 'el:btn_menu')
  // 已经一致时返回同一个对象，调用方可据此省掉写入
  const good = normalizeCanvasRelationEndpoints(relation)
  assert.equal(normalizeCanvasRelationEndpoints(good), good)
  // 没有切片就不动四边语义
  const plain = { id: 'r2', source: 'a', target: 'b', sourceHandle: 'right', targetHandle: 'left' }
  assert.equal(normalizeCanvasRelationEndpoints(plain), plain)
})

test('切片端点经持久化与 patch 落地后一字不变（判据 2 的可执行形式）', () => {
  const cards = [
    { id: 'a', kind: 'text', title: 'a', adapter: 'text', source: { kind: 'file', path: 'a.md', token: 't' }, x: 0, y: 0, width: 300, height: 200 },
    { id: 'b', kind: 'text', title: 'b', adapter: 'text', source: { kind: 'file', path: 'b.md', token: 't' }, x: 500, y: 0, width: 300, height: 200 },
  ]
  const slice = { kind: 'element', ref: 'uiroot/MainCanvas/btn_menu', representationId: 'shot-000-base', box: { x: 0.61, y: 0.07, width: 0.22, height: 0.06 } }
  const relations = [{ id: 'r1', source: 'a', target: 'b', sourceSlice: slice, sourceHandle: canvasSliceHandleId(slice), path: 'curve', arrow: 'closed' }]
  const document = { version: 1, sessionId: 'external:s', revision: 1, updatedAt: '2026-08-20T00:00:00.000Z', spacing: 'compact', cards, relations }
  const before = canvasRelationSliceSignature(document.relations)

  const parsed = parseCanvasDocument(JSON.stringify(document), 'external:s')
  assert.equal(canvasRelationSliceSignature(parsed.relations), before)
  assert.equal(parsed.spacing, 'compact')

  // 模拟"自动布局只改坐标"：端点签名必须不变
  const moved = applyCanvasMutation(parsed, {
    upsert_cards: [{ ...cards[0], x: 12, y: 8 }, { ...cards[1], x: 324, y: 8 }],
  })
  assert.equal(canvasRelationSliceSignature(moved.relations), before)
  assert.equal(moved.relations[0].sourceHandle, 'shot-000-base@el:uiroot/MainCanvas/btn_menu')
  // 曲线还是曲线
  assert.equal(moved.relations.filter((item) => item.path === 'curve').length, 1)
})

test('只改 handle 的写入会被拉回切片；间距档非法即回落默认', () => {
  const cards = [
    { id: 'a', kind: 'text', title: 'a', adapter: 'text', source: { kind: 'file', path: 'a.md', token: 't' }, x: 0, y: 0, width: 300, height: 200 },
    { id: 'b', kind: 'text', title: 'b', adapter: 'text', source: { kind: 'file', path: 'b.md', token: 't' }, x: 500, y: 0, width: 300, height: 200 },
  ]
  const slice = { kind: 'range', ref: '020::abc' }
  const base = { version: 1, sessionId: 'external:s', revision: 1, updatedAt: '2026-08-20T00:00:00.000Z', cards, relations: [] }
  const next = applyCanvasMutation(base, {
    upsert_relations: [{ id: 'r1', source: 'a', target: 'b', sourceSlice: slice, sourceHandle: 'right', path: 'curve' }],
  })
  assert.equal(next.relations[0].sourceHandle, '020::abc')

  assert.equal(parseCanvasDocument(JSON.stringify({ ...base, spacing: 'ultra' }), 'external:s').spacing, undefined)
  assert.equal(canvasSpacing(undefined).nodeNode, CANVAS_SPACING.comfortable.nodeNode)
  assert.ok(canvasSpacing('compact').nodeNode < canvasSpacing('comfortable').nodeNode)
  assert.ok(canvasSpacing('compact').betweenLayers < canvasSpacing('comfortable').betweenLayers)
})

test('图片卡按自身比例定尺，9:16 不留缺口（判据 3）', () => {
  const intrinsic = { width: 1080, height: 1920 }
  const fitted = fitCardToIntrinsicRatio(intrinsic, 300 * 300)
  assert.ok(cardRatioDeviation(fitted, intrinsic) <= 0.01,
    `比例偏差 ${cardRatioDeviation(fitted, intrinsic)} 超过 1%：${JSON.stringify(fitted)}`)
  assert.ok(fitted.height > fitted.width, '竖屏截图不能被摆成横向卡')

  // 旧行为的对照：固定 360x260 的框把 9:16 塞成 ~1.38 的比例，偏差极大
  assert.ok(cardRatioDeviation({ width: 360, height: 260 }, intrinsic) > 0.5)

  // 横向素材同样成立，且夹取时两边同时缩放、比例不变
  const wide = { width: 1920, height: 1080 }
  assert.ok(cardRatioDeviation(fitCardToIntrinsicRatio(wide, 400 * 400), wide) <= 0.01)
  assert.ok(cardRatioDeviation(fitCardToIntrinsicRatio(wide, 40 * 40), wide) <= 0.01)
  assert.throws(() => fitCardToIntrinsicRatio({ width: 0, height: 10 }, 1000), TypeError)
})

test('按比例定尺的卡片经读盘不被下限篡改（回归：260x180 夹回 13% 偏差）', () => {
  const intrinsic = { width: 1080, height: 1920 }
  const fitted = fitCardToIntrinsicRatio(intrinsic, 360 * 260, { minSide: 120, maxSide: 1400 })
  const document = {
    version: 1, sessionId: 'external:s', revision: 1, updatedAt: '2026-08-20T00:00:00.000Z',
    cards: [{
      id: 'shot', kind: 'material', title: '界面', adapter: 'image',
      source: { kind: 'file', path: 's.png', token: 't' },
      x: 0, y: 0, width: fitted.width, height: fitted.height,
    }],
    relations: [],
  }
  const parsed = parseCanvasDocument(JSON.stringify(document), 'external:s')
  const card = parsed.cards[0]
  assert.deepEqual({ width: card.width, height: card.height }, fitted)
  assert.ok(cardRatioDeviation(card, intrinsic) <= 0.01,
    `读盘后比例偏差 ${cardRatioDeviation(card, intrinsic)}`)
  // 缺值仍回落到默认，不因为放宽下限而变成 0
  const bare = parseCanvasDocument(JSON.stringify({
    ...document, cards: [{ ...document.cards[0], width: undefined, height: undefined }],
  }), 'external:s')
  assert.deepEqual({ width: bare.cards[0].width, height: bare.cards[0].height }, { width: 440, height: 320 })
})

// ── 卡片外观：无框 / 标题外置 / 标题缩放门槛（业主 2026-08-20 参照collab platform交互画板） ──

test('外观预设给默认值，卡上显式字段永远盖过预设', () => {
  assert.deepEqual(resolveCardPresentation(undefined), { frame: 'panel', title: 'inside', emphasis: 'normal' })
  assert.deepEqual(resolveCardPresentation({ style: 'screen' }),
    { frame: 'none', title: 'above', emphasis: 'normal', titleMinZoom: 0.5 })
  assert.deepEqual(resolveCardPresentation({ style: 'annotation' }),
    { frame: 'none', title: 'none', emphasis: 'normal' })
  // 显式覆盖预设
  assert.equal(resolveCardPresentation({ style: 'screen', title: 'none' }).title, 'none')
  assert.equal(resolveCardPresentation({ style: 'annotation', emphasis: 'highlight' }).emphasis, 'highlight')
  // 未知预设不报错也不猜，退回基础默认
  assert.deepEqual(resolveCardPresentation({ style: 'unknown-preset' }),
    { frame: 'panel', title: 'inside', emphasis: 'normal' })
  assert.ok(canvasPresentationStyles().includes('screen'))
})

test('标题缩放门槛：缩小看阵列、放大才看名字', () => {
  const screen = { style: 'screen' }
  assert.equal(titleVisibleAtZoom(screen, 0.3), false)
  assert.equal(titleVisibleAtZoom(screen, 0.5), true)
  assert.equal(titleVisibleAtZoom(screen, 1.4), true)
  // 位置 none 时任何缩放都不显示
  assert.equal(titleVisibleAtZoom({ style: 'annotation' }, 4), false)
  // 没有门槛就一直显示
  assert.equal(titleVisibleAtZoom(undefined, 0.05), true)
})

test('外观取值封闭；不合契约的整条丢弃而不是留半份', () => {
  assert.deepEqual(validateCanvasCardPresentation(undefined), [])
  assert.deepEqual(validateCanvasCardPresentation({ frame: 'none', title: 'above', titleMinZoom: 0.5 }), [])
  assert.match(validateCanvasCardPresentation({ frame: 'shadowed' })[0], /frame 必须是/)
  assert.match(validateCanvasCardPresentation({ title: 'below' })[0], /title 必须是/)
  assert.match(validateCanvasCardPresentation({ emphasis: 'loud' })[0], /emphasis 必须是/)
  assert.match(validateCanvasCardPresentation({ titleMinZoom: 0 })[0], /titleMinZoom/)
  assert.match(validateCanvasCardPresentation({ style: '   ' })[0], /style/)

  const base = {
    version: 1, sessionId: 'external:s', revision: 1, updatedAt: '2026-08-20T00:00:00.000Z', relations: [],
    cards: [
      { id: 'a', kind: 'material', title: '界面', adapter: 'image', source: { kind: 'file', path: 'a.png', token: 't' }, x: 0, y: 0, width: 230, height: 408, presentation: { style: 'screen' } },
      { id: 'b', kind: 'text', title: '标注', adapter: 'text', source: { kind: 'file', path: 'b.md', token: 't' }, x: 0, y: 500, width: 300, height: 120, presentation: { frame: 'nope' } },
    ],
  }
  const parsed = parseCanvasDocument(JSON.stringify(base), 'external:s')
  assert.deepEqual(parsed.cards[0].presentation, { style: 'screen' })
  assert.equal(parsed.cards[1].presentation, undefined)
  assert.equal(resolveCardPresentation(parsed.cards[1].presentation).frame, 'panel')
})

// ── 连线层级（业主第 6 条：所有注释线都在图下面，完全看不到）──────────────────

test('层级缺省：一律画在内容上面', () => {
  assert.equal(resolveRelationLayer({ kind: 'references' }), 'over')
  assert.equal(resolveRelationLayer({ kind: 'derived' }), 'over')
  /*
   * 串联类原先默认 content。而 content 不是"和内容同层"，实际是"被内容盖住"：
   * `.react-flow__edges` 与 `.react-flow__nodes` 都没有 z-index，平票时
   * DOM 更靠后的节点赢。业主：「线们都要在画面上（至少现在是）」。
   */
  assert.equal(resolveRelationLayer({ kind: 'continues' }), 'over')
  // 连 kind 都没声明的也照样看得见 —— 看不见的线等于不存在
  assert.equal(resolveRelationLayer({}), 'over')
})

test('显式声明永远盖过派生', () => {
  assert.equal(resolveRelationLayer({ kind: 'references', layer: 'under' }), 'under')
  assert.equal(resolveRelationLayer({ kind: 'continues', layer: 'over' }), 'over')
})

test('over 必须真的能压过节点：节点在 manual 模式下拿 0', () => {
  assert.ok(relationLayerZIndex('over') > 0, 'over 的 z 必须为正，否则平票时 DOM 靠后的节点赢')
  assert.equal(relationLayerZIndex('content'), 0)
  assert.ok(relationLayerZIndex('under') < 0)
  // 留出档间距，让标签能取 z+1 而不撞进下一档
  assert.ok(relationLayerZIndex('over') - relationLayerZIndex('content') >= 2)
})

test('层级取值封闭，脏值不落库', () => {
  assert.ok(isCanvasRelationLayer('over'))
  for (const bad of ['top', 'front', 'Over', '', 0, null, undefined, {}]) {
    assert.equal(isCanvasRelationLayer(bad), false, `${JSON.stringify(bad)} 不该被当成层级`)
  }
  const parsed = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('workspace:layer'),
    cards: [card('a'), card('b')],
    relations: [
      { id: 'r1', source: 'a', target: 'b', kind: 'references', layer: 'under' },
      { id: 'r2', source: 'a', target: 'b', kind: 'references', layer: '置顶' },
    ],
  }), 'workspace:layer')
  assert.equal(parsed.relations[0].layer, 'under', '合法值要保留')
  assert.equal(parsed.relations[1].layer, undefined, '非法值整条字段丢弃，不兜底成 over')
  // 丢弃后仍能按 kind 派生出可用的层级
  assert.equal(resolveRelationLayer(parsed.relations[1]), 'over')
})

// ── 差异计算对键顺序不敏感（每打开一次板子就写 41 条关系的根因）──────────────

test('内容相同但键序不同 → 空 patch，不产生扰动写入', () => {
  const base = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('workspace:churn'),
    cards: [card('a'), card('b')],
    relations: [{ id: 'r', source: 'a', target: 'b', kind: 'references', path: 'curve', arrow: 'closed' }],
  }), 'workspace:churn')
  // 同一条关系，字段一模一样，只是键序反过来写
  const reordered = {
    ...base,
    relations: [{ arrow: 'closed', path: 'curve', kind: 'references', target: 'b', source: 'a', id: 'r' }],
  }
  const patch = diffCanvasDocuments(base, reordered)
  assert.ok(canvasPatchIsEmpty(patch),
    `键序不同不该产生 patch，实得 ${JSON.stringify(patch).slice(0, 200)}`)
})

test('真的改了一个字段仍然要被抓到（别把判据放宽成永远相等）', () => {
  const base = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('workspace:churn2'),
    cards: [card('a'), card('b')],
    relations: [{ id: 'r', source: 'a', target: 'b', kind: 'references' }],
  }), 'workspace:churn2')
  const changed = { ...base, relations: [{ ...base.relations[0], kind: 'continues' }] }
  const patch = diffCanvasDocuments(base, changed)
  assert.equal(patch.upsert_relations?.length, 1)
  assert.equal(patch.upsert_relations[0].kind, 'continues')
})

test('工作台链接顺序仍然算差异（顺序在我们的模型里有意义）', () => {
  const base = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('workspace:churn3'),
    behavior: {
      workbenchLinks: [
        { id: 'map', label: '地图', href: '/bside/interface-map' },
        { id: 'review', label: '审阅', href: '/bside/review' },
      ],
    },
  }), 'workspace:churn3')
  const swapped = {
    ...base,
    behavior: {
      ...base.behavior,
      workbenchLinks: [...base.behavior.workbenchLinks].reverse(),
    },
  }
  assert.ok(diffCanvasDocuments(base, swapped).set_behavior)
})

// ── 框选响度（业主第 7 条：保持简约、不要提前显示编号）──────────────────────

test('框选缺省安静：一屏几十个框，默认全都很响等于都不响', () => {
  assert.equal(resolveSliceEmphasis(undefined), 'quiet')
  assert.equal(resolveSliceEmphasis({}), 'quiet')
  assert.equal(resolveSliceEmphasis({ emphasis: 'highlight' }), 'highlight')
  assert.equal(resolveSliceEmphasis({ emphasis: '很亮' }), 'quiet', '非法值落回 quiet，不猜')
})

test('响度不参与身份：改 emphasis 不该让已落库的边失配', () => {
  const base = { kind: 'element', ref: 'Canvas/Btn', representationId: 'shot-1', occurrence: 2 }
  assert.equal(
    canvasSliceHandleId({ ...base, emphasis: 'highlight' }),
    canvasSliceHandleId({ ...base, emphasis: 'quiet' }),
  )
  assert.equal(canvasSliceHandleId(base), canvasSliceHandleId({ ...base, emphasis: 'normal' }))
})

test('短名跳过 Unity 的通用容器段，别把「anim」当名字', () => {
  assert.equal(
    shortElementName('PuertsDontDestroy/uiroot/MainCanvas/pbui_main_icon_newgift(Clone)/anim'),
    'pbui_main_icon_newgift(Clone)',
  )
  assert.equal(shortElementName('a/b/layout/content'), 'b')
  assert.equal(shortElementName('root/anim/layout'), 'layout', '全是通用段时退回最后一段，不编')
  assert.equal(shortElementName('x/y/#title_bg/#title'), 'title', '#title 不是通用段，就取它；# 去掉只为好读')
  assert.equal(shortElementName('btn_close'), 'btn_close')
  assert.equal(shortElementName(''), '')
})

// ── 贴合（业主第 2 条：距离图片紧密贴合还有差异）────────────────────────────

test('按比例定尺时必须扣掉卡壳，否则正文框永远差一圈', () => {
  const intrinsic = { width: 1080, height: 1920 }
  // 不给卡壳：整卡等于图片比例，正文框超标（实测 2.1% ≈ 230px 卡上 4.8px 留白，
  // 与浏览器里量到的 5.77px 同一个量级）
  const naive = fitCardToIntrinsicRatio(intrinsic, 360 * 260, { minSide: 120, maxSide: 1400 })
  assert.ok(cardBodyRatioDeviation(naive, intrinsic, 'above') > 0.01,
    `不扣卡壳时正文框偏差应超过 1%，实得 ${cardBodyRatioDeviation(naive, intrinsic, 'above')}`)
  // 给卡壳：正文框才等于图片比例
  const fitted = fitCardToIntrinsicRatio(intrinsic, 360 * 260, {
    minSide: 120, maxSide: 1400, chrome: canvasCardChrome('above'),
  })
  assert.ok(cardBodyRatioDeviation(fitted, intrinsic, 'above') <= 0.01,
    `扣了卡壳后正文框偏差应 ≤1%，实得 ${cardBodyRatioDeviation(fitted, intrinsic, 'above')}`)
})

test('卡壳按标题模式给，三种模式各不相同', () => {
  assert.deepEqual(canvasCardChrome('none'), { width: 2, height: 1 })
  assert.deepEqual(canvasCardChrome('above'), { width: 2, height: 11 })
  assert.deepEqual(canvasCardChrome('inside'), { width: 2, height: 39 })
})

// ── 备注材料（业主第 4 条）────────────────────────────────────────────────

// element 切片按契约必须带归一化 box（validateCanvasSlice 盯着）
const subject = {
  kind: 'element', ref: 'Canvas/btn_close', representationId: 'shot-020',
  box: { x: 0.1, y: 0.2, width: 0.3, height: 0.05 },
}

test('备注必须有主语和立场，缺一整块丢弃', () => {
  assert.deepEqual(validateCanvasComment({ subject, stance: 'observation' }), [])
  assert.ok(validateCanvasComment({ stance: 'observation' }).length, '没有主语的备注不是备注')
  assert.ok(validateCanvasComment({ subject }).length, '没有立场的备注不收')
  assert.ok(validateCanvasComment({ subject, stance: '大概' }).length, '立场取值封闭')
  assert.ok(validateCanvasComment(null).length)
})

test('立场四值封闭', () => {
  for (const good of ['observation', 'inference', 'gap', 'decision']) {
    assert.ok(isCanvasCommentStance(good), good)
  }
  for (const bad of ['guess', 'note', '', 0, null, {}]) assert.equal(isCanvasCommentStance(bad), false)
})

test('外观由立场派生，不由样式声明', () => {
  // 备注一律无框无标题；强调级别按立场给
  for (const stance of ['observation', 'inference', 'gap', 'decision']) {
    const presentation = resolveCommentPresentation({ subject, stance })
    assert.equal(presentation.frame, 'none')
    assert.equal(presentation.title, 'none')
  }
  assert.equal(resolveCommentPresentation({ subject, stance: 'decision' }).emphasis, 'highlight')
  assert.equal(resolveCommentPresentation({ subject, stance: 'gap' }).emphasis, 'muted')
  assert.equal(resolveCommentPresentation({ subject, stance: 'observation' }).emphasis, 'normal')
})

test('落库：合格的备注保留，脏的整块丢弃', () => {
  const parsed = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('workspace:comment'),
    cards: [
      { ...card('good'), comment: { subject, stance: 'gap', authority: 'spark-workshop/screen-annotations' } },
      { ...card('bad-stance'), comment: { subject, stance: '拍的' } },
      { ...card('no-subject'), comment: { stance: 'observation' } },
    ],
    relations: [],
  }), 'workspace:comment')
  const byId = new Map(parsed.cards.map((item) => [item.id, item]))
  assert.equal(byId.get('good').comment.stance, 'gap')
  assert.equal(byId.get('good').comment.authority, 'spark-workshop/screen-annotations')
  assert.equal(byId.get('good').comment.subject.ref, 'Canvas/btn_close')
  assert.equal(byId.get('bad-stance').comment, undefined, '立场不合契约 → 整块丢弃')
  assert.equal(byId.get('no-subject').comment, undefined, '没有主语 → 整块丢弃')
})

// ── 六种关系与跳转的 trigger（业主第 5、8 条）────────────────────────────────

test('关系类型六值封闭', () => {
  for (const good of ['continues', 'transitions-to', 'annotates', 'references', 'derived', 'contradicts']) {
    assert.ok(isCanvasRelationKind(good), good)
  }
  for (const bad of ['jump', 'flows', '', 0, null]) assert.equal(isCanvasRelationKind(bad), false)
})

test('transitions-to 没有 trigger 就不合契约', () => {
  assert.deepEqual(validateCanvasRelationSemantics({ id: 'r', kind: 'transitions-to', trigger: '点 btn_close' }), [])
  assert.equal(validateCanvasRelationSemantics({ id: 'r', kind: 'transitions-to' }).length, 1)
  assert.equal(validateCanvasRelationSemantics({ id: 'r', kind: 'transitions-to', trigger: '   ' }).length, 1)
  // 其他类型不要求 trigger
  assert.deepEqual(validateCanvasRelationSemantics({ id: 'r', kind: 'continues' }), [])
})

test('落库：缺 trigger 的 transitions-to 丢掉 kind，而不是伪造或偷偷改成 continues', () => {
  const parsed = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('workspace:kinds'),
    cards: [card('a'), card('b')],
    relations: [
      { id: 'ok', source: 'a', target: 'b', kind: 'transitions-to', trigger: '点 btn_close' },
      { id: 'bare', source: 'a', target: 'b', kind: 'transitions-to' },
      { id: 'new', source: 'a', target: 'b', kind: 'contradicts' },
    ],
  }), 'workspace:kinds')
  const byId = new Map(parsed.relations.map((relation) => [relation.id, relation]))
  assert.equal(byId.get('ok').kind, 'transitions-to')
  assert.equal(byId.get('ok').trigger, '点 btn_close')
  assert.equal(byId.get('bare').kind, undefined, '缺 trigger → kind 丢掉（未定）')
  assert.notEqual(byId.get('bare').kind, 'continues', '不许偷偷改成另一种语义')
  assert.equal(byId.get('new').kind, 'contradicts')
})

test('关系默认一律画在内容上面；要压下去必须显式声明', () => {
  assert.equal(resolveRelationLayer({ kind: 'annotates' }), 'over')
  assert.equal(resolveRelationLayer({ kind: 'contradicts' }), 'over')
  /*
   * 流程线原先默认 content，而 content 实际就是"被卡片盖住"（两个容器都没有
   * z-index，平票时 DOM 更靠后的节点赢）。业主：「线们都要在画面上」。
   */
  assert.equal(resolveRelationLayer({ kind: 'transitions-to' }), 'over')
  assert.equal(resolveRelationLayer({ kind: 'continues' }), 'over')
  // 显式声明仍然说话，包括把它压到内容之下
  assert.equal(resolveRelationLayer({ kind: 'continues', layer: 'under' }), 'under')
})

test('保序回归：每张卡停在自己锚点的高度上，且不重叠', () => {
  // 三张卡的目标高度互相不冲突 → 原样落位
  const relaxed = isotonicPositions([0, 200, 400], [50, 50, 50], 10, 0);
  assert.deepEqual(relaxed, [0, 200, 400]);
  // 目标挤在一起 → 保持顺序、按最小间距摊开，且整体重心不跑
  const packed = isotonicPositions([100, 105, 110], [50, 50, 50], 10, 0);
  assert.ok(packed[1] - packed[0] >= 60, `间距不足: ${packed}`);
  assert.ok(packed[2] - packed[1] >= 60, `间距不足: ${packed}`);
  const mean = packed.reduce((sum, value, index) => sum + value + index * 0, 0) / 3;
  assert.ok(Math.abs(mean - 105) < 61, `整体偏离目标太远: ${packed}`);
  // 下界生效
  assert.ok(Math.min(...isotonicPositions([-500, -400], [40, 40], 8, 20)) >= 20);
});

test('注释分列：按组坐标定边，右侧元素的注释不许跑到左列', () => {
  const layout = placeNotesInColumns({
    notes: [
      // 锚点在组的右半边 —— 即使它在自己那张状态卡里偏左
      { id: 'a', height: 80, anchor: { x: 900, y: 200 } },
      { id: 'b', height: 80, anchor: { x: 120, y: 300 } },
    ],
    content: { left: 100, right: 1000, top: 30 },
    columnWidth: 230,
    gap: 12,
    padding: 10,
  });
  const byId = new Map(layout.placements.map((item) => [item.id, item]));
  assert.equal(byId.get('a').side, 'right');
  assert.equal(byId.get('b').side, 'left');
  assert.equal(layout.leftWidth, 230);
  assert.equal(layout.rightWidth, 230);
});

test('注释分列：同侧按锚点纵序排，不交叉', () => {
  const layout = placeNotesInColumns({
    notes: [
      { id: 'low', height: 60, anchor: { x: 120, y: 600 } },
      { id: 'high', height: 60, anchor: { x: 130, y: 100 } },
      { id: 'mid', height: 60, anchor: { x: 110, y: 350 } },
    ],
    content: { left: 100, right: 1000, top: 30 },
    columnWidth: 230,
    gap: 10,
    padding: 10,
  });
  // 三张锚点都铁定靠左（x≈120，中线 550）→ 都留在左列，按锚点纵序排
  const left = layout.placements.filter((item) => item.side === 'left').sort((a, b) => a.y - b.y);
  assert.deepEqual(left.map((item) => item.id), ['high', 'mid', 'low']);
  // 而且各自尽量停在自己锚点的高度上（锚点相距 250，摊不开才会被推挤）
  const byId = new Map(layout.placements.map((item) => [item.id, item]));
  assert.ok(Math.abs(byId.get('high').y + 30 - 100) < 20, `high 偏离锚点太远: ${byId.get('high').y}`);
  assert.ok(Math.abs(byId.get('low').y + 30 - 600) < 20, `low 偏离锚点太远: ${byId.get('low').y}`);
});

test('槽位落位：锚点在卡内侧时用中间那条缝，不横穿整张图', () => {
  const layout = placeNotesInGutters({
    notes: [
      // 锚点贴着左卡的右边缘 —— 只有左右两列时它无论去哪都要横穿一张卡
      { id: 'innerLeft', height: 60, anchor: { x: 480, y: 200 } },
      // 锚点贴着左卡的左边缘 → 最左那条缝
      { id: 'outerLeft', height: 60, anchor: { x: 30, y: 100 } },
      // 锚点贴着右卡的右边缘 → 最右那条缝
      { id: 'outerRight', height: 60, anchor: { x: 1000, y: 300 } },
    ],
    states: [{ id: 's1', x: 10, width: 490 }, { id: 's2', x: 512, width: 490 }],
    contentTop: 30,
    columnWidth: 230,
    gap: 12,
    padding: 10,
  });
  const byId = new Map(layout.placements.map((item) => [item.id, item]));
  // 三条缝都被用到：最左、卡与卡之间、最右
  const xs = [...new Set(layout.placements.map((item) => item.x))].sort((a, b) => a - b);
  assert.equal(xs.length, 3, `应该开出三条缝: ${JSON.stringify(xs)}`);
  assert.equal(byId.get('outerLeft').x, xs[0]);
  assert.equal(byId.get('innerLeft').x, xs[1]);
  assert.equal(byId.get('outerRight').x, xs[2]);
  // 状态卡为左边那两条缝让位：第一张右移一列，第二张右移两列
  assert.equal(layout.stateShift[0], 242);
  assert.equal(layout.stateShift[1], 484);
});

test('槽位落位：没人用的缝不占宽度', () => {
  const layout = placeNotesInGutters({
    notes: [{ id: 'only', height: 60, anchor: { x: 1000, y: 100 } }],
    states: [{ id: 's1', x: 10, width: 490 }, { id: 's2', x: 512, width: 490 }],
    contentTop: 30,
    columnWidth: 230,
    gap: 12,
    padding: 10,
  });
  assert.deepEqual(layout.stateShift, [0, 0]);
  assert.equal(layout.placements[0].x, 1014);
});

test('Canvas View 只保存来源、查询选择器与软编排，不复制 Material 事实', () => {
  const base = emptyCanvasDocument('canvas:view-source')
  const next = {
    ...base,
    view: {
      source: { kind: 'material-graph', ref: 'view:spark-workshop/art/interface' },
      selector: {
        rootRefs: ['interface:spark/home', 'interface:spark/home'],
        relationKinds: ['transitions-to'], direction: 'both', maxDepth: 4,
        nodeLimit: 1200, includeFrontier: true,
      },
      narrative: {
        hiddenCardIds: ['debug-note'], chapterOrder: ['home', 'gameplay'],
        slideBreakBefore: ['state:reward'], titleOverrides: { home: '主界面' },
      },
    },
  }
  const patch = diffCanvasDocuments(base, next)
  assert.equal(patch.set_view, true)
  const applied = applyCanvasMutation(base, patch)
  assert.deepEqual(applied.view.selector.rootRefs, ['interface:spark/home'])
  const parsed = parseCanvasDocument(JSON.stringify(applied), applied.sessionId)
  assert.deepEqual(parsed.view, applied.view)
  assert.equal(parsed.cards.length, 0, 'View 元数据不应该复制来源 Material 卡片')
})

test('非法 Canvas View 来源整块拒绝，清空 patch 也可移除', () => {
  const parsed = parseCanvasDocument(JSON.stringify({
    ...emptyCanvasDocument('canvas:bad-view'),
    view: { source: { kind: 'invented', ref: '' }, selector: { nodeLimit: -2 } },
  }), 'canvas:bad-view')
  assert.equal(parsed.view, undefined)
  const withView = {
    ...emptyCanvasDocument('canvas:remove-view'),
    view: { source: { kind: 'canvas', ref: 'canvas:source' } },
  }
  assert.equal(applyCanvasMutation(withView, { set_view: true, view: null }).view, undefined)
})
