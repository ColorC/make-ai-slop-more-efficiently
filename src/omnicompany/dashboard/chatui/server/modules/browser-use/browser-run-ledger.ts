import type BetterSqlite3 from 'better-sqlite3';

import { getConnection } from '@/modules/database/index.js';

export type BrowserRunStatus =
  | 'starting'
  | 'ready'
  | 'stopped'
  | 'expired'
  | 'failed'
  | 'interrupted'
  | 'deleted';

export type BrowserActionOutcome = 'ok' | 'failed';
export type BrowserCleanupStatus = 'pending' | 'reclaimed' | 'unconfirmed' | 'not-started';

export type BrowserRunRecord = {
  id: string;
  ownerId: string;
  createdBy: string;
  purpose: string;
  runtime: string;
  status: BrowserRunStatus;
  profileName: string | null;
  createdAt: string;
  updatedAt: string;
  heartbeatAt: string;
  leaseExpiresAt: string;
  endedAt: string | null;
  browserPid: number | null;
  url: string | null;
  title: string | null;
  lastAction: string | null;
  actionCount: number;
  artifactCount: number;
  failureReason: string | null;
  cleanupStatus: BrowserCleanupStatus;
  cleanupReason: string | null;
};

export type BrowserRunAction = {
  id: number;
  runId: string;
  sequence: number;
  action: string;
  outcome: BrowserActionOutcome;
  details: Record<string, unknown>;
  createdAt: string;
};

export type BrowserRunArtifact = {
  id: number;
  runId: string;
  kind: string;
  path: string;
  createdAt: string;
};

export type BrowserCleanupReceipt = {
  id: number;
  runId: string;
  reason: string;
  status: BrowserCleanupStatus;
  browserPid: number | null;
  details: string | null;
  createdAt: string;
};

type CreateRunInput = {
  id: string;
  ownerId: string;
  createdBy: string;
  purpose: string;
  runtime: string;
  profileName: string | null;
  createdAt: string;
  leaseExpiresAt: string;
};

type RunRow = {
  id: string;
  owner_id: string;
  created_by: string;
  purpose: string;
  runtime: string;
  status: BrowserRunStatus;
  profile_name: string | null;
  created_at: string;
  updated_at: string;
  heartbeat_at: string;
  lease_expires_at: string;
  ended_at: string | null;
  browser_pid: number | null;
  url: string | null;
  title: string | null;
  last_action: string | null;
  action_count: number;
  artifact_count: number;
  failure_reason: string | null;
  cleanup_status: BrowserCleanupStatus;
  cleanup_reason: string | null;
};

type ActionRow = {
  id: number;
  run_id: string;
  sequence: number;
  action: string;
  outcome: BrowserActionOutcome;
  details_json: string;
  created_at: string;
};

type ArtifactRow = {
  id: number;
  run_id: string;
  kind: string;
  path: string;
  created_at: string;
};

type CleanupRow = {
  id: number;
  run_id: string;
  reason: string;
  status: BrowserCleanupStatus;
  browser_pid: number | null;
  details: string | null;
  created_at: string;
};

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS browser_facility_runs (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  created_by TEXT NOT NULL,
  purpose TEXT NOT NULL,
  runtime TEXT NOT NULL,
  status TEXT NOT NULL,
  profile_name TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  lease_expires_at TEXT NOT NULL,
  ended_at TEXT,
  browser_pid INTEGER,
  url TEXT,
  title TEXT,
  last_action TEXT,
  action_count INTEGER NOT NULL DEFAULT 0,
  artifact_count INTEGER NOT NULL DEFAULT 0,
  failure_reason TEXT,
  cleanup_status TEXT NOT NULL DEFAULT 'pending',
  cleanup_reason TEXT
);

CREATE TABLE IF NOT EXISTS browser_facility_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES browser_facility_runs(id),
  sequence INTEGER NOT NULL,
  action TEXT NOT NULL,
  outcome TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS browser_facility_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES browser_facility_runs(id),
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, kind, path)
);

CREATE TABLE IF NOT EXISTS browser_facility_cleanup_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES browser_facility_runs(id),
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  browser_pid INTEGER,
  details TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_browser_facility_runs_updated
  ON browser_facility_runs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_browser_facility_runs_lease
  ON browser_facility_runs(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_browser_facility_actions_run
  ON browser_facility_actions(run_id, sequence);
`;

function parseDetails(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function runFromRow(row: RunRow): BrowserRunRecord {
  return {
    id: row.id,
    ownerId: row.owner_id,
    createdBy: row.created_by,
    purpose: row.purpose,
    runtime: row.runtime,
    status: row.status,
    profileName: row.profile_name,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    heartbeatAt: row.heartbeat_at,
    leaseExpiresAt: row.lease_expires_at,
    endedAt: row.ended_at,
    browserPid: row.browser_pid,
    url: row.url,
    title: row.title,
    lastAction: row.last_action,
    actionCount: row.action_count,
    artifactCount: row.artifact_count,
    failureReason: row.failure_reason,
    cleanupStatus: row.cleanup_status,
    cleanupReason: row.cleanup_reason,
  };
}

export class BrowserRunLedger {
  constructor(private readonly db: BetterSqlite3.Database) {
    db.exec(SCHEMA_SQL);
  }

  createRun(input: CreateRunInput): BrowserRunRecord {
    this.db.prepare(`
      INSERT INTO browser_facility_runs (
        id, owner_id, created_by, purpose, runtime, status, profile_name,
        created_at, updated_at, heartbeat_at, lease_expires_at, cleanup_status
      ) VALUES (
        @id, @ownerId, @createdBy, @purpose, @runtime, 'starting', @profileName,
        @createdAt, @createdAt, @createdAt, @leaseExpiresAt, 'pending'
      )
    `).run(input);
    return this.getRun(input.id)!;
  }

  getRun(runId: string): BrowserRunRecord | null {
    const row = this.db.prepare('SELECT * FROM browser_facility_runs WHERE id = ?')
      .get(runId) as RunRow | undefined;
    return row ? runFromRow(row) : null;
  }

  listRuns(limit = 100): BrowserRunRecord[] {
    const boundedLimit = Math.max(1, Math.min(Math.trunc(limit), 500));
    const rows = this.db.prepare(`
      SELECT * FROM browser_facility_runs
      ORDER BY updated_at DESC
      LIMIT ?
    `).all(boundedLimit) as RunRow[];
    return rows.map(runFromRow);
  }

  markReady(runId: string, input: {
    now: string;
    leaseExpiresAt: string;
    browserPid: number | null;
  }): BrowserRunRecord | null {
    this.db.prepare(`
      UPDATE browser_facility_runs
      SET status = 'ready', updated_at = @now, heartbeat_at = @now,
          lease_expires_at = @leaseExpiresAt, browser_pid = @browserPid,
          cleanup_status = 'pending', cleanup_reason = NULL
      WHERE id = @runId
    `).run({ runId, ...input });
    return this.getRun(runId);
  }

  heartbeat(runId: string, input: {
    now: string;
    leaseExpiresAt: string;
    url?: string | null;
    title?: string | null;
    lastAction?: string | null;
  }): BrowserRunRecord | null {
    this.db.prepare(`
      UPDATE browser_facility_runs
      SET updated_at = @now, heartbeat_at = @now, lease_expires_at = @leaseExpiresAt,
          url = COALESCE(@url, url), title = COALESCE(@title, title),
          last_action = COALESCE(@lastAction, last_action)
      WHERE id = @runId
    `).run({
      runId,
      now: input.now,
      leaseExpiresAt: input.leaseExpiresAt,
      url: input.url ?? null,
      title: input.title ?? null,
      lastAction: input.lastAction ?? null,
    });
    return this.getRun(runId);
  }

  recordAction(runId: string, input: {
    action: string;
    outcome: BrowserActionOutcome;
    details?: Record<string, unknown>;
    now: string;
    leaseExpiresAt: string;
    failureReason?: string | null;
  }): BrowserRunAction {
    const insert = this.db.transaction(() => {
      const row = this.db.prepare(`
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM browser_facility_actions
        WHERE run_id = ?
      `).get(runId) as { next_sequence: number };
      const sequence = row.next_sequence;
      const result = this.db.prepare(`
        INSERT INTO browser_facility_actions (
          run_id, sequence, action, outcome, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        runId,
        sequence,
        input.action,
        input.outcome,
        JSON.stringify(input.details || {}),
        input.now,
      );
      this.db.prepare(`
        UPDATE browser_facility_runs
        SET updated_at = @now, heartbeat_at = @now, lease_expires_at = @leaseExpiresAt,
            last_action = @action, action_count = action_count + 1,
            failure_reason = COALESCE(@failureReason, failure_reason)
        WHERE id = @runId
      `).run({
        runId,
        now: input.now,
        leaseExpiresAt: input.leaseExpiresAt,
        action: input.action,
        failureReason: input.failureReason ?? null,
      });
      return { id: Number(result.lastInsertRowid), sequence };
    });
    const inserted = insert();
    return {
      id: inserted.id,
      runId,
      sequence: inserted.sequence,
      action: input.action,
      outcome: input.outcome,
      details: input.details || {},
      createdAt: input.now,
    };
  }

  recordArtifact(runId: string, input: {
    kind: string;
    path: string;
    now: string;
  }): void {
    const result = this.db.prepare(`
      INSERT OR IGNORE INTO browser_facility_artifacts (run_id, kind, path, created_at)
      VALUES (@runId, @kind, @path, @now)
    `).run({ runId, ...input });
    if (result.changes > 0) {
      this.db.prepare(`
        UPDATE browser_facility_runs
        SET artifact_count = artifact_count + 1, updated_at = ?
        WHERE id = ?
      `).run(input.now, runId);
    }
  }

  finishRun(runId: string, input: {
    status: Exclude<BrowserRunStatus, 'starting' | 'ready'>;
    now: string;
    reason: string;
  }): BrowserRunRecord | null {
    this.db.prepare(`
      UPDATE browser_facility_runs
      SET status = @status, ended_at = @now, updated_at = @now,
          cleanup_reason = @reason,
          failure_reason = CASE WHEN @status = 'failed' THEN @reason ELSE failure_reason END
      WHERE id = @runId
    `).run({ runId, ...input });
    return this.getRun(runId);
  }

  recordCleanup(runId: string, input: {
    reason: string;
    status: BrowserCleanupStatus;
    browserPid: number | null;
    details?: string | null;
    now: string;
  }): BrowserCleanupReceipt {
    const result = this.db.prepare(`
      INSERT INTO browser_facility_cleanup_receipts (
        run_id, reason, status, browser_pid, details, created_at
      ) VALUES (@runId, @reason, @status, @browserPid, @details, @now)
    `).run({ runId, details: input.details ?? null, ...input });
    this.db.prepare(`
      UPDATE browser_facility_runs
      SET cleanup_status = ?, cleanup_reason = ?, updated_at = ?
      WHERE id = ?
    `).run(input.status, input.reason, input.now, runId);
    return {
      id: Number(result.lastInsertRowid),
      runId,
      reason: input.reason,
      status: input.status,
      browserPid: input.browserPid,
      details: input.details ?? null,
      createdAt: input.now,
    };
  }

  listActions(runId: string): BrowserRunAction[] {
    const rows = this.db.prepare(`
      SELECT * FROM browser_facility_actions
      WHERE run_id = ?
      ORDER BY sequence ASC
    `).all(runId) as ActionRow[];
    return rows.map((row) => ({
      id: row.id,
      runId: row.run_id,
      sequence: row.sequence,
      action: row.action,
      outcome: row.outcome,
      details: parseDetails(row.details_json),
      createdAt: row.created_at,
    }));
  }

  listArtifacts(runId: string): BrowserRunArtifact[] {
    const rows = this.db.prepare(`
      SELECT * FROM browser_facility_artifacts
      WHERE run_id = ?
      ORDER BY id ASC
    `).all(runId) as ArtifactRow[];
    return rows.map((row) => ({
      id: row.id,
      runId: row.run_id,
      kind: row.kind,
      path: row.path,
      createdAt: row.created_at,
    }));
  }

  listCleanupReceipts(runId: string): BrowserCleanupReceipt[] {
    const rows = this.db.prepare(`
      SELECT * FROM browser_facility_cleanup_receipts
      WHERE run_id = ?
      ORDER BY id ASC
    `).all(runId) as CleanupRow[];
    return rows.map((row) => ({
      id: row.id,
      runId: row.run_id,
      reason: row.reason,
      status: row.status,
      browserPid: row.browser_pid,
      details: row.details,
      createdAt: row.created_at,
    }));
  }

  listExpiredReadyRuns(now: string): BrowserRunRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM browser_facility_runs
      WHERE status IN ('starting', 'ready') AND lease_expires_at < ?
      ORDER BY lease_expires_at ASC
    `).all(now) as RunRow[];
    return rows.map(runFromRow);
  }
}

let sharedLedger: BrowserRunLedger | null = null;

export function getBrowserRunLedger(): BrowserRunLedger {
  if (!sharedLedger) {
    sharedLedger = new BrowserRunLedger(getConnection());
  }
  return sharedLedger;
}
