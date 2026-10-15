import test from 'node:test'
import assert from 'node:assert/strict'
import { bridgePrefix, materialRepresentationFileUrl, resolveBrowserNavigationUrl } from '../src/regions/browserNavigation.ts'

const href = 'http://192.0.2.10:8210/workshop/canvas/notes-web.html?bridge_prefix=%2Fworkshop'
const search = '?bridge_prefix=%2Fworkshop'

test('rewrites persisted direct Workshop origins and root paths through the host mount', () => {
  assert.equal(
    resolveBrowserNavigationUrl('http://127.0.0.1:5460/materials?id=x', href, search),
    '/workshop/materials?id=x',
  )
  assert.equal(resolveBrowserNavigationUrl('/materials?id=x', href, search), '/workshop/materials?id=x')
})

test('preserves an existing mount, resolves relative Workshop links and leaves external URLs alone', () => {
  assert.equal(resolveBrowserNavigationUrl('/workshop/materials?id=x', href, search), '/workshop/materials?id=x')
  assert.equal(resolveBrowserNavigationUrl('../materials?id=x', href, search), '/workshop/materials?id=x')
  assert.equal(resolveBrowserNavigationUrl('https://example.test/materials?id=x', href, search), 'https://example.test/materials?id=x')
})

test('keeps WebTwin named-state entries on the 8210 host root', () => {
  assert.equal(
    resolveBrowserNavigationUrl('/spark-webtwin/?state=guide4-step1&screen=1080x1920', href, search),
    '/spark-webtwin/?state=guide4-step1&screen=1080x1920',
  )
  assert.equal(
    resolveBrowserNavigationUrl('/spark-webtwin/?state=guide4-clean', href.replace('/workshop/', '/bside/'), '?bridge_prefix=%2Fbside'),
    '/spark-webtwin/?state=guide4-clean',
  )
})

test('rejects invalid bridge prefixes', () => {
  assert.equal(bridgePrefix('?bridge_prefix=%2Fworkshop%2F..%2Fadmin'), '')
})

test('routes binary Material representations through the mounted file endpoint', () => {
  assert.equal(
    materialRepresentationFileUrl('E:\\P4\\spark\\screen.png', href, search),
    '/workshop/api/material-workspace/representation-file?ref=E%3A%5CP4%5Cspark%5Cscreen.png',
  )
})
