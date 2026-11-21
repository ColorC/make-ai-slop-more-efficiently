import { ClaudeSessionsProvider } from '@/modules/providers/list/claude/claude-sessions.provider.js';

/**
 * Controller (总控) sessions are plain Claude Code sessions: the runtime is the
 * local Claude SDK, so the on-disk JSONL transcript and the live SDK stream are
 * byte-for-byte the same shape Claude produces. We therefore reuse Claude's
 * session normalization and history paging verbatim by subclassing
 * ClaudeSessionsProvider.
 *
 * Note: messages keep `provider: 'claude'` internally (that is how Claude tags
 * them); the chat gateway writer remaps session ids and the UI routes by the DB
 * session row's provider, so this does not affect controller routing.
 */
export class ControllerSessionsProvider extends ClaudeSessionsProvider {}
