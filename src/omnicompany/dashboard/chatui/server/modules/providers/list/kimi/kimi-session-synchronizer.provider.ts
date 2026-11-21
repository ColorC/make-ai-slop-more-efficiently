import path from 'node:path';
import { readFile } from 'node:fs/promises';

import { sessionsDb } from '@/modules/database/index.js';
import { getKimiCodeHome } from '@/modules/providers/list/kimi/kimi-auth.provider.js';
import type { IProviderSessionSynchronizer } from '@/shared/interfaces.js';
import type { AnyRecord } from '@/shared/types.js';
import {
  findFilesRecursivelyCreatedAfter,
  normalizeSessionName,
  readFileTimestamps,
  readObjectRecord,
  readOptionalString,
} from '@/shared/utils.js';

const FALLBACK_TITLE = 'Untitled Kimi Session';

/**
 * Session indexer for Kimi Code on-disk sessions.
 *
 * Kimi Code stores sessions under `~/.kimi-code/sessions/wd_<hash>/session_<uuid>/`
 * with a `state.json` metadata file (`workDir`, `title`, `lastPrompt`,
 * `createdAt`, `updatedAt`) and the main transcript at
 * `agents/main/wire.jsonl`. The provider-native session id is the session
 * directory name (`session_<uuid>`), which is also what `kimi -S <id>`
 * accepts for resume.
 */
export class KimiSessionSynchronizer implements IProviderSessionSynchronizer {
  private readonly provider = 'kimi' as const;
  private readonly sessionsRoot = path.join(getKimiCodeHome(), 'sessions');

  /**
   * Scans all session `state.json` artifacts and upserts sessions into DB.
   */
  async synchronize(since?: Date): Promise<number> {
    const stateFiles = await findFilesRecursivelyCreatedAfter(
      this.sessionsRoot,
      'state.json',
      since ?? null,
    );

    let processed = 0;
    for (const filePath of stateFiles) {
      if (path.basename(filePath) !== 'state.json') {
        continue;
      }

      const sessionId = await this.upsertSessionDir(path.dirname(filePath));
      if (sessionId) {
        processed += 1;
      }
    }

    return processed;
  }

  /**
   * Handles watcher changes for one session's `state.json` or `wire.jsonl`.
   */
  async synchronizeFile(filePath: string): Promise<string | null> {
    const basename = path.basename(filePath);

    if (basename === 'state.json') {
      return this.upsertSessionDir(path.dirname(filePath));
    }

    if (basename === 'wire.jsonl') {
      // <root>/wd_<hash>/session_<uuid>/agents/<agent>/wire.jsonl — the session
      // directory is three levels up; sub-agent transcripts map to the same
      // session row.
      const sessionDir = path.dirname(path.dirname(path.dirname(filePath)));
      if (!path.basename(sessionDir).startsWith('session_')) {
        return null;
      }

      return this.upsertSessionDir(sessionDir);
    }

    return null;
  }

  private async upsertSessionDir(sessionDir: string): Promise<string | null> {
    const sessionId = path.basename(sessionDir);
    if (!sessionId.startsWith('session_')) {
      return null;
    }

    const statePath = path.join(sessionDir, 'state.json');
    let state: AnyRecord | null = null;
    try {
      state = readObjectRecord(JSON.parse(await readFile(statePath, 'utf8')));
    } catch {
      state = null;
    }

    const projectPath = readOptionalString(state?.workDir);
    if (!projectPath) {
      return null;
    }

    const pendingAppSession = sessionsDb.getSessionByProviderSessionId(sessionId)
      ?? sessionsDb.getSessionById(sessionId)
      ?? sessionsDb.findLatestPendingAppSession(this.provider, projectPath);
    if (pendingAppSession && !pendingAppSession.provider_session_id) {
      // The watcher can index state.json before the runtime reports the
      // provider id back through the websocket mapping; bind it to the fresh
      // app row first so no duplicate sidebar entry appears.
      sessionsDb.assignProviderSessionId(pendingAppSession.session_id, sessionId);
    }

    // App-created sessions are keyed by an app id, so disk-discovered provider
    // ids must be resolved through the provider-id mapping first.
    const existingSession = sessionsDb.getSessionByProviderSessionId(sessionId)
      ?? sessionsDb.getSessionById(sessionId);
    const existingName = existingSession?.custom_name;
    const nextName = existingName && existingName !== FALLBACK_TITLE
      ? existingName
      : readOptionalString(state?.title) ?? readOptionalString(state?.lastPrompt);

    const timestamps = await readFileTimestamps(statePath);
    return sessionsDb.createSession(
      sessionId,
      this.provider,
      projectPath,
      normalizeSessionName(nextName, FALLBACK_TITLE),
      readOptionalString(state?.createdAt) ?? timestamps.createdAt,
      readOptionalString(state?.updatedAt) ?? timestamps.updatedAt,
      path.join(sessionDir, 'agents', 'main', 'wire.jsonl'),
    );
  }
}
