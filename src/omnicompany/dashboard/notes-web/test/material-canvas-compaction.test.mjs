import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compactDimensionsForCard } from '../src/regions/materialCanvasCompaction.ts'

const compact = (card, width, height) => compactDimensionsForCard(card, { width, height })
const semantic = (materialKind, displayRole) => ({
  kind: 'material',
  adapter: 'web',
  material: { metadata: { material_kind: materialKind, display_role: displayRole } },
})

test('image cards shrink proportionally into a compact preview footprint', () => {
  assert.deepEqual(compact({ kind: 'material', adapter: 'image' }, 560, 420), { width: 350, height: 260 })
})

test('demo cards shrink proportionally instead of retaining their authored canvas size', () => {
  assert.deepEqual(compact(semantic('demo', 'demonstration'), 420, 280), { width: 360, height: 240 })
})

test('semantic surfaces use readable compact caps', () => {
  assert.deepEqual(compact(semantic('process', 'agent-step'), 380, 260), { width: 340, height: 220 })
  assert.deepEqual(compact(semantic('diff', 'candidate-change'), 820, 540), { width: 500, height: 320 })
  assert.deepEqual(compact(semantic('document', 'document'), 760, 670), { width: 420, height: 300 })
})

test('smart layout never enlarges deliberately small cards or resizes groups directly', () => {
  assert.deepEqual(compact({ kind: 'material', adapter: 'image' }, 300, 200), { width: 300, height: 200 })
  assert.deepEqual(compact({ kind: 'group', adapter: 'auto' }, 900, 700), { width: 900, height: 700 })
})

test('auto layout applies ELK compact dimensions to ordinary unlocked cards', () => {
  const source = readFileSync(new URL('../src/regions/MaterialNotesWorkspace.tsx', import.meta.url), 'utf8')
  const start = source.indexOf('const runAutoLayout')
  const block = source.slice(start, source.indexOf('autoLayoutRef.current', start))
  assert.match(block, /compactDimensionsForCard\(node\.data, dimensionsOf\(node\)\)/)
  // 写回仍必须用布局给出的 placement（守卫的本意），但高度要扣掉标题外置预留的净空：
  // 那条带子只是布局输入，不属于卡片自身高度，不扣的话每跑一次排布卡就被撑高一次。
  assert.match(block, /width: placement\.width, height: placement\.height - titleBandOf\(node\)/)
  assert.match(block, /const titleBandOf = \(node: CanvasNode\) =>/)
  assert.doesNotMatch(block, /node\.data\.kind === ["']group["'] \? \{\s*style:/)
})


// ── 按材料固有比例定尺（REQ-CANVAS-SLICE-PROTOCOL §4.1 / 判据 3） ──

const screenCard = (resolution) => ({
  kind: 'material',
  adapter: 'image',
  material: { metadata: { material_kind: 'image', capture_resolution: resolution } },
})

test('界面截图按 1080x1920 的自身比例定尺，不再被塞进近横向的固定框', () => {
  const fitted = compact(screenCard('1080x1920'), 560, 420)
  const deviation = Math.abs(fitted.width / fitted.height - 1080 / 1920) / (1080 / 1920)
  assert.ok(deviation <= 0.01, `比例偏差 ${deviation}：${JSON.stringify(fitted)}`)
  assert.ok(fitted.height > fitted.width, `竖屏截图被摆成横向：${JSON.stringify(fitted)}`)
  // 旧行为返回 350x260（横向），偏差超过 50%
  assert.notDeepEqual(fitted, { width: 350, height: 260 })
})

test('intrinsic_width/height 与 screen_width/height 都认，优先级高于分辨率字符串', () => {
  const card = {
    kind: 'material', adapter: 'image',
    material: { metadata: { intrinsic_width: 800, intrinsic_height: 400, capture_resolution: '1080x1920' } },
  }
  const fitted = compact(card, 900, 900)
  assert.ok(Math.abs(fitted.width / fitted.height - 2) / 2 <= 0.01, JSON.stringify(fitted))
})

test('没有固有尺寸的图片仍回落到原来的形态默认框（不改既有行为）', () => {
  assert.deepEqual(compact({ kind: 'material', adapter: 'image' }, 560, 420), { width: 350, height: 260 })
})

test('分辨率字符串写坏时不猜，回落默认框', () => {
  assert.deepEqual(compact(screenCard('宽x高'), 560, 420), { width: 350, height: 260 })
  assert.deepEqual(compact(screenCard('0x1920'), 560, 420), { width: 350, height: 260 })
})
