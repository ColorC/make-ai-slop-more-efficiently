import assert from 'node:assert/strict';
import test from 'node:test';

process.env.DATABASE_PATH = ':memory:';

const { browserUseService } = await import('@/modules/browser-use/browser-use.service.js');

test('browser monitor returns managed sessions without leaking owner ids', async () => {
  const sessions = await browserUseService.listSessions();

  assert.ok(Array.isArray(sessions));
  assert.equal(sessions.some((session) => 'ownerId' in session), false);
});
