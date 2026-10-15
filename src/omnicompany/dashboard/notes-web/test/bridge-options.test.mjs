import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveBridgeOptions, resolveBridgePath } from '../src/webNotesInvoke.ts'

test('direct Workshop canvas links infer their owning bridge prefix', () => {
  assert.deepEqual(
    resolveBridgeOptions('?session_id=spark-config-workflow-acceptance', '/bside/canvas/notes-web.html'),
    { prefix: '/bside', httpOnly: false },
  )
})

test('explicit bridge options win and regular Dashboard notes keep the root bridge', () => {
  assert.deepEqual(
    resolveBridgeOptions('?bridge_prefix=%2Fpreview&notes_transport=http', '/bside/canvas/notes-web.html'),
    { prefix: '/preview', httpOnly: true },
  )
  assert.deepEqual(resolveBridgeOptions('', '/lofa/overlay/app/notes-web.html'), { prefix: '', httpOnly: false })
})

test('routes transport diagnostics through the same mounted bridge as note traffic', () => {
  assert.equal(
    resolveBridgePath('?bridge_prefix=%2Fworkshop', '/workshop/canvas/notes-web.html', '/api/diagnostics/frontend/events'),
    '/workshop/api/diagnostics/frontend/events',
  )
  assert.equal(
    resolveBridgePath('', '/lofa/overlay/app/notes-web.html', '/api/diagnostics/frontend/events'),
    '/api/diagnostics/frontend/events',
  )
})
