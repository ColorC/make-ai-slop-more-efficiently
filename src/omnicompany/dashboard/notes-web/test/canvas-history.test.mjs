import test from 'node:test'
import assert from 'node:assert/strict'
import { canvasHistoryHref, formatCanvasUpdatedAt } from '../src/regions/canvasHistory.ts'

test('history href keeps the session window and points at the chosen canvas', () => {
  assert.equal(
    canvasHistoryHref('https://dash.test/api/embed/notes-web/notes-web.html?theme=omni&session=pty-1', 'pty-1', 'session-abc'),
    'https://dash.test/api/embed/notes-web/notes-web.html?theme=omni&session=pty-1&canvas=session-abc',
  )
  assert.equal(
    canvasHistoryHref('https://dash.test/notes-web.html?session_id=pty-1', 'pty-2', 'session-xyz'),
    'https://dash.test/notes-web.html?session_id=pty-2&canvas=session-xyz',
  )
})

test('updated-at formatter hides unknown timestamps', () => {
  assert.equal(formatCanvasUpdatedAt('unknown'), '')
  assert.equal(formatCanvasUpdatedAt(''), '')
})
