import assert from 'node:assert/strict';
import test from 'node:test';
import { bootstrapBrowserSession } from '../browser-session-bootstrap.js';

test('normal session exchange shares managed context and redacts receipt', async () => {
  let options: any;
  let disposed = false;
  const context = { request: { post: async (url: string, value: unknown) => {
    assert.equal(url, 'https://workshop.example/bside/api/session');
    options = value;
    return { status: () => 200, dispose: async () => { disposed = true; } };
  } } };
  const result = await bootstrapBrowserSession(context, 'https://workshop.example/bside/login', {
    url: 'https://workshop.example/bside/api/session', bearerToken: 'private-test-credential',
  });
  assert.equal(options.headers.Authorization, 'Bearer private-test-credential');
  assert.equal(options.maxRedirects, 0);
  assert.equal(disposed, true);
  assert.deepEqual(result, { origin: 'https://workshop.example', pathname: '/bside/api/session', status: 200 });
  assert.equal(JSON.stringify(result).includes('private-test-credential'), false);
});

test('foreign origins, URL credentials, query credentials and blank pages never send', async () => {
  const context = { request: { post: async () => { assert.fail('credential must not be sent'); } } };
  for (const [currentUrl, url] of [
    ['https://workshop.example/login', 'https://foreign.example/session'],
    ['https://workshop.example/login', 'https://user:secret@workshop.example/session'],
    ['https://workshop.example/login', 'https://workshop.example/session?token=secret'],
    ['about:blank', 'https://workshop.example/session'],
  ]) await assert.rejects(bootstrapBrowserSession(context, currentUrl!, { url: url!, bearerToken: 'secret' }));
});

test('redirect and transport failures do not expose credentials or response bodies', async () => {
  for (const status of [302, 401, 503]) {
    let disposed = false;
    const context = { request: { post: async () => ({ status: () => status, dispose: async () => { disposed = true; } }) } };
    await assert.rejects(bootstrapBrowserSession(context, 'https://workshop.example/login', {
      url: 'https://workshop.example/session', bearerToken: 'secret',
    }), new RegExp(`HTTP ${status}`));
    assert.equal(disposed, true);
  }
  const context = { request: { post: async () => { throw Error('request failed Bearer private-test-credential'); } } };
  await assert.rejects(bootstrapBrowserSession(context, 'https://workshop.example/login', {
    url: 'https://workshop.example/session', bearerToken: 'private-test-credential',
  }), { message: 'Session bootstrap request failed.' });
});
