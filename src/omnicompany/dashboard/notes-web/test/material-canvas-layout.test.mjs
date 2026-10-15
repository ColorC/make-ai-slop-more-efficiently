import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fallbackMaterialCanvasLayout, layoutMaterialCanvas } from '../src/regions/materialCanvasLayout.ts'

const boxes = [
  { id: 'source', kind: 'material', x: 0, y: 0, width: 300, height: 180 },
  { id: 'left-branch', kind: 'material', x: 900, y: 0, width: 300, height: 180 },
  { id: 'right-branch', kind: 'material', x: 900, y: 600, width: 300, height: 180 },
]
const links = [
  { id: 'source-left', source: 'source', target: 'left-branch', labels: ['业务处理'] },
  { id: 'source-right', source: 'source', target: 'right-branch', labels: ['硬规则检验'] },
]

test('smart layout keeps a compact layered footprint', async () => {
  const result = await layoutMaterialCanvas(boxes, links)
  const source = result.placements.get('source')
  const left = result.placements.get('left-branch')
  const right = result.placements.get('right-branch')
  assert.ok(source && left && right)
  const horizontalGap = Math.min(left.x, right.x) - (source.x + source.width)
  const verticalGap = Math.abs(left.y - right.y) - left.height
  assert.ok(horizontalGap >= 76 && horizontalGap <= 96, `horizontal gap was ${horizontalGap}`)
  assert.ok(verticalGap >= 20 && verticalGap <= 48, `vertical gap was ${verticalGap}`)
})

test('auto layout persists editable curves rather than orthogonal routes', () => {
  const source = readFileSync(new URL('../src/regions/MaterialNotesWorkspace.tsx', import.meta.url), 'utf8')
  const start = source.indexOf('const runAutoLayout')
  const block = source.slice(start, source.indexOf('autoLayoutRef.current', start))
  assert.match(block, /path:\s*"curve"/)
  assert.match(block, /routePoints:\s*\[\]/)
  assert.doesNotMatch(block, /path:\s*"orthogonal"/)
})

test('timeout fallback keeps connected roots layered and preserves group-relative children', () => {
  const fallbackBoxes = [
    { id: 'scene', kind: 'text', x: 80, y: 80, width: 320, height: 200 },
    { id: 'bundle', kind: 'group', x: 500, y: 80, width: 800, height: 600 },
    { id: 'shot', parentId: 'bundle', kind: 'material', x: 35, y: 70, width: 300, height: 500 },
    { id: 'agent', kind: 'material', x: 1400, y: 80, width: 340, height: 260 },
    { id: 'aside', kind: 'text', x: 500, y: 900, width: 500, height: 220 },
  ]
  const fallbackLinks = [
    { id: 'scene-bundle', source: 'scene', target: 'bundle' },
    { id: 'bundle-agent', source: 'bundle', target: 'agent' },
    { id: 'shot-note', source: 'shot', target: 'bundle' },
  ]
  const result = fallbackMaterialCanvasLayout(fallbackBoxes, fallbackLinks, 'compact')
  const scene = result.placements.get('scene')
  const bundle = result.placements.get('bundle')
  const agent = result.placements.get('agent')
  const shot = result.placements.get('shot')
  const aside = result.placements.get('aside')
  assert.ok(scene && bundle && agent && shot && aside)
  assert.ok(scene.x < bundle.x && bundle.x < agent.x)
  assert.deepEqual({ x: shot.x, y: shot.y }, { x: 35, y: 70 })
  assert.ok(aside.y > Math.max(scene.y + scene.height, bundle.y + bundle.height, agent.y + agent.height))
})

test('ELK positioning is spline-aware and does not force fixed left-right ports', () => {
  const source = readFileSync(new URL('../src/regions/materialCanvasLayout.ts', import.meta.url), 'utf8')
  assert.match(source, /"elk\.edgeRouting": "SPLINES"/)
  assert.match(source, /"elk\.layered\.nodePlacement\.strategy": "NETWORK_SIMPLEX"/)
  assert.doesNotMatch(source, /FIXED_SIDE|BRANDES_KOEPF/)
})

// ── 紧凑档与切片端点（REQ-CANVAS-SLICE-PROTOCOL §4 / 判据 2、4） ──

const spread = (placements) => {
  const items = [...placements.values()]
  const left = Math.min(...items.map((item) => item.x))
  const top = Math.min(...items.map((item) => item.y))
  const right = Math.max(...items.map((item) => item.x + item.width))
  const bottom = Math.max(...items.map((item) => item.y + item.height))
  return (right - left) * (bottom - top)
}

test('紧凑档的占地明显小于宽松档，且卡片一张都没少', async () => {
  const comfortable = await layoutMaterialCanvas(boxes, links, 'comfortable')
  const compactResult = await layoutMaterialCanvas(boxes, links, 'compact')
  assert.equal(compactResult.placements.size, boxes.length)
  assert.ok(spread(compactResult.placements) < spread(comfortable.placements),
    `紧凑档没有更紧：compact=${spread(compactResult.placements)} comfortable=${spread(comfortable.placements)}`)
})

test('切片钉住的端点：布局不表态，调用方得以保留"从这个按钮出发"', async () => {
  const sliceLinks = [
    { ...links[0], sliceBoundSource: true },
    { ...links[1], sliceBoundTarget: true },
  ]
  const result = await layoutMaterialCanvas(boxes, sliceLinks, 'compact')
  const left = result.routes.get('source-left')
  const right = result.routes.get('source-right')
  assert.equal(left.sourceHandle, undefined, '切片钉住的起点被布局改写了')
  assert.equal(left.targetHandle, 'left')
  assert.equal(right.sourceHandle, 'right')
  assert.equal(right.targetHandle, undefined, '切片钉住的终点被布局改写了')
})

test('未钉住的边仍拿到四边端点（不改既有行为）', async () => {
  const result = await layoutMaterialCanvas(boxes, links, 'comfortable')
  for (const link of links) {
    const route = result.routes.get(link.id)
    assert.equal(route.sourceHandle, 'right')
    assert.equal(route.targetHandle, 'left')
  }
})

test('布局后的二次推挤跟随当前档，不再用写死的 48 把结果重新撑开', async () => {
  const { avoidLockedPlacementCollisions } = await import('../src/regions/materialCanvasLayout.ts')
  const overlapping = [
    { id: 'fixed', kind: 'material', x: 0, y: 0, width: 200, height: 200, locked: true },
    { id: 'moving', kind: 'material', x: 10, y: 10, width: 200, height: 200 },
  ]
  const placements = new Map(overlapping.map((box) => [box.id, { x: box.x, y: box.y, width: box.width, height: box.height }]))
  const tight = avoidLockedPlacementCollisions(overlapping, placements, 12).get('moving')
  const loose = avoidLockedPlacementCollisions(overlapping, placements, 48).get('moving')
  const distance = (item) => Math.abs(item.x) + Math.abs(item.y)
  assert.ok(distance(tight) < distance(loose), `紧凑档的让位距离没有更小：${JSON.stringify({ tight, loose })}`)
})
