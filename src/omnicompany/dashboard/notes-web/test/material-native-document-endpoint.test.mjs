import test from 'node:test'
import assert from 'node:assert/strict'
import { nativeMaterialDocumentUrl } from '../src/regions/nativeMaterialDocument.ts'

const materialId = 'material:spark-workshop/business-story/step-guide/agent-processing'

test('uses the active bside mount instead of nesting a persisted workshop mount', () => {
  const endpoint = nativeMaterialDocumentUrl(
    materialId,
    'document',
    'http://192.0.2.10:8210/bside/canvas/notes-web.html?bridge_prefix=%2Fbside',
    '?bridge_prefix=%2Fbside',
  )
  assert.equal(endpoint.pathname, '/bside/api/material-workspace/document')
  assert.equal(endpoint.searchParams.get('id'), materialId)
  assert.equal(endpoint.searchParams.get('representation'), 'document')
})

test('uses the active workshop mount and leaves direct hosts at the API root', () => {
  const mounted = nativeMaterialDocumentUrl(
    materialId,
    '',
    'http://192.0.2.10:8210/workshop/canvas/notes-web.html?bridge_prefix=%2Fworkshop',
    '?bridge_prefix=%2Fworkshop',
  )
  assert.equal(mounted.pathname, '/workshop/api/material-workspace/document')

  const direct = nativeMaterialDocumentUrl(materialId, '', 'http://127.0.0.1:5460/canvas/notes-web.html', '')
  assert.equal(direct.pathname, '/api/material-workspace/document')
  assert.equal(direct.searchParams.get('mode'), 'expanded')
})
