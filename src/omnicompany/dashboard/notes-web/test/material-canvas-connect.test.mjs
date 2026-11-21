import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const workspace = readFileSync(new URL('../src/regions/MaterialNotesWorkspace.tsx', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../src/regions/material-notes.css', import.meta.url), 'utf8')

function sliceBetween(source, startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle)
  assert.notEqual(start, -1, `missing ${startNeedle}`)
  const end = source.indexOf(endNeedle, start + startNeedle.length)
  assert.notEqual(end, -1, `missing ${endNeedle} after ${startNeedle}`)
  return source.slice(start, end)
}

test('onSelectionChange is memoized and bails out when counts are unchanged', () => {
  const block = sliceBetween(workspace, 'const [selection, setSelection]', 'const groupSelection')
  assert.match(block, /const onSelectionChange = useCallback/)
  assert.match(block, /setSelection\(\(current\) => \(current\.cards === cards && current\.groups === groups \? current : \{ cards, groups \}\)\)/)
  assert.match(workspace, /onSelectionChange=\{onSelectionChange\}/)
  assert.doesNotMatch(workspace, /onSelectionChange=\{\(\{ nodes: picked \}\) => setSelection\(/)
})

test('card side handles keep unique ids and do not twin source+target on the same id', () => {
  const block = sliceBetween(workspace, 'function CardSideHandles()', 'const MaterialCard')
  const ids = [...block.matchAll(/id="(left|right|top|bottom)"/g)].map((match) => match[1])
  assert.deepEqual(ids.sort(), ['bottom', 'left', 'right', 'top'])
  assert.equal(new Set(ids).size, ids.length)
  assert.doesNotMatch(block, /pointerEvents:\s*"none"/)
  assert.match(workspace, /<CardSideHandles \/>/)
  assert.equal([...workspace.matchAll(/<CardSideHandles \/>/g)].length, 2)
})

test('fitViewOptions is a stable module constant, not an inline object', () => {
  assert.match(workspace, /const FIT_VIEW_OPTIONS = \{ padding: 0\.18, maxZoom: 1 \}/)
  assert.match(workspace, /fitViewOptions=\{FIT_VIEW_OPTIONS\}/)
  assert.doesNotMatch(workspace, /fitViewOptions=\{\{ padding: 0\.18, maxZoom: 1 \}\}/)
})

test('group cards expose the same hover-connectable handles as material cards', () => {
  assert.match(styles, /\.material-group-card:hover \.material-card-handle/)
  assert.match(styles, /\.material-group-card\.selected \.material-card-handle/)
})

test('handles stay hittable while a connection is in progress', () => {
  assert.match(styles, /\.material-card-handle\.connectionindicator/)
  assert.match(styles, /\.material-card-handle\.connectingfrom/)
})

test('connection handles sit above mid-side resize handles', () => {
  const handleBlock = styles.slice(styles.indexOf('.material-card-handle {'), styles.indexOf('.material-canvas-card:hover'))
  const resizeBlock = styles.slice(styles.indexOf('.material-resize-handle {'), styles.indexOf('.material-resize-handle::after'))
  const handleZ = Number(/z-index:\s*(\d+)/.exec(handleBlock)?.[1] || 0)
  const resizeZ = Number(/z-index:\s*(\d+)/.exec(resizeBlock)?.[1] || 0)
  assert.ok(handleZ > resizeZ, `handle z-index ${handleZ} should beat resize z-index ${resizeZ}`)
})
