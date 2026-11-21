import { describe, expect, it } from 'vitest';

import { sessionStoreInternalsForTest, type NormalizedMessage } from './useSessionStore';

const msg = (
  id: string,
  timestamp: string,
  content: string,
  role: 'user' | 'assistant' = 'assistant',
): NormalizedMessage => ({
  id,
  sessionId: 'chat-1',
  timestamp,
  provider: 'claude',
  kind: 'text',
  role,
  content,
});

describe('session store message merge', () => {
  it('keeps stale realtime assistant text before a later server user message', () => {
    const server = [
      msg('server-prev-final', '2026-05-15T04:36:06Z', 'previous final answer'),
      msg('server-user-2', '2026-05-15T04:39:32Z', 'new user question', 'user'),
    ];
    const realtime = [
      msg('rt-old-mid', '2026-05-15T04:34:57Z', 'old intermediate assistant text'),
    ];

    const merged = sessionStoreInternalsForTest.computeMerged(server, realtime);

    expect(merged.map((m) => m.id)).toEqual([
      'rt-old-mid',
      'server-prev-final',
      'server-user-2',
    ]);
  });

  it('drops realtime assistant text once server has the same content', () => {
    const server = [
      msg('server-answer', '2026-05-15T04:39:43Z', 'same answer'),
    ];
    const realtime = [
      msg('rt-answer', '2026-05-15T04:39:43.100Z', 'same answer'),
    ];

    const merged = sessionStoreInternalsForTest.computeMerged(server, realtime);

    expect(merged.map((m) => m.id)).toEqual(['server-answer']);
  });

  it('drops optimistic user text when the server copy only differs by thinking prefix', () => {
    const server = [
      msg('server-user', '2026-05-23T03:05:15Z', 'ultrathink: explain context', 'user'),
    ];
    const realtime = [
      msg('local_user', '2026-05-23T03:05:15.100Z', 'explain context', 'user'),
    ];

    const merged = sessionStoreInternalsForTest.computeMerged(server, realtime);

    expect(merged.map((m) => m.id)).toEqual(['server-user']);
  });
});
