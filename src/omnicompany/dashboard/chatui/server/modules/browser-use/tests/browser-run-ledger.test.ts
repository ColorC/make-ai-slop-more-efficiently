import assert from 'node:assert/strict';
import test from 'node:test';
import Database from 'better-sqlite3';

import { BrowserRunLedger } from '@/modules/browser-use/browser-run-ledger.js';

test('browser run ledger preserves lease, action, artifact, and cleanup evidence', () => {
  const db = new Database(':memory:');
  const ledger = new BrowserRunLedger(db);
  const createdAt = '2026-08-01T08:00:00.000Z';

  ledger.createRun({
    id: 'run-1',
    ownerId: 'agent',
    createdBy: 'agent',
    purpose: 'Verify version factory playback',
    runtime: 'local',
    profileName: null,
    createdAt,
    leaseExpiresAt: '2026-08-01T08:30:00.000Z',
  });
  ledger.markReady('run-1', {
    now: '2026-08-01T08:00:01.000Z',
    leaseExpiresAt: '2026-08-01T08:30:01.000Z',
    browserPid: 1234,
  });
  ledger.recordAction('run-1', {
    action: 'navigate',
    outcome: 'ok',
    details: { url: 'http://127.0.0.1:4173' },
    now: '2026-08-01T08:00:02.000Z',
    leaseExpiresAt: '2026-08-01T08:30:02.000Z',
  });
  ledger.recordArtifact('run-1', {
    kind: 'trace',
    path: 'C:\\runs\\run-1\\trace.zip',
    now: '2026-08-01T08:00:03.000Z',
  });
  ledger.finishRun('run-1', {
    status: 'stopped',
    now: '2026-08-01T08:00:04.000Z',
    reason: 'manual-stop',
  });
  ledger.recordCleanup('run-1', {
    reason: 'manual-stop',
    status: 'reclaimed',
    browserPid: 1234,
    details: 'Context and browser closed.',
    now: '2026-08-01T08:00:05.000Z',
  });

  const run = ledger.getRun('run-1');
  assert.equal(run?.status, 'stopped');
  assert.equal(run?.actionCount, 1);
  assert.equal(run?.artifactCount, 1);
  assert.equal(run?.cleanupStatus, 'reclaimed');
  assert.deepEqual(ledger.listActions('run-1')[0]?.details, {
    url: 'http://127.0.0.1:4173',
  });
  assert.equal(ledger.listArtifacts('run-1')[0]?.kind, 'trace');
  assert.equal(ledger.listCleanupReceipts('run-1')[0]?.browserPid, 1234);
  db.close();
});

test('browser run ledger finds only active runs whose lease expired', () => {
  const db = new Database(':memory:');
  const ledger = new BrowserRunLedger(db);
  ledger.createRun({
    id: 'expired-run',
    ownerId: 'agent',
    createdBy: 'agent',
    purpose: 'Expired run',
    runtime: 'local',
    profileName: null,
    createdAt: '2026-08-01T08:00:00.000Z',
    leaseExpiresAt: '2026-08-01T08:01:00.000Z',
  });
  ledger.createRun({
    id: 'future-run',
    ownerId: 'agent',
    createdBy: 'agent',
    purpose: 'Future run',
    runtime: 'local',
    profileName: null,
    createdAt: '2026-08-01T08:00:00.000Z',
    leaseExpiresAt: '2026-08-01T09:00:00.000Z',
  });

  assert.deepEqual(
    ledger.listExpiredReadyRuns('2026-08-01T08:30:00.000Z').map((run) => run.id),
    ['expired-run'],
  );
  db.close();
});

test('successful cleanup actions do not erase an earlier failure handoff', () => {
  const db = new Database(':memory:');
  const ledger = new BrowserRunLedger(db);
  ledger.createRun({
    id: 'failed-action-run',
    ownerId: 'agent',
    createdBy: 'agent',
    purpose: 'Debug handoff',
    runtime: 'local',
    profileName: null,
    createdAt: '2026-08-01T08:00:00.000Z',
    leaseExpiresAt: '2026-08-01T08:30:00.000Z',
  });
  ledger.recordAction('failed-action-run', {
    action: 'click',
    outcome: 'failed',
    details: { selector: '#missing' },
    failureReason: 'Element not found',
    now: '2026-08-01T08:00:01.000Z',
    leaseExpiresAt: '2026-08-01T08:30:01.000Z',
  });
  ledger.recordAction('failed-action-run', {
    action: 'stop',
    outcome: 'ok',
    now: '2026-08-01T08:00:02.000Z',
    leaseExpiresAt: '2026-08-01T08:30:02.000Z',
  });

  assert.equal(ledger.getRun('failed-action-run')?.failureReason, 'Element not found');
  db.close();
});
