import { appConfigDb, closeConnection } from '@/modules/database/index.js';
import { browserUseService } from '@/modules/browser-use/browser-use.service.js';

const SETTINGS_KEY = 'browser_use_settings';
const mode = process.env.BROWSER_FACILITY_ORPHAN_MODE;
const runId = process.env.BROWSER_FACILITY_ORPHAN_RUN_ID;

async function createOrphan() {
  appConfigDb.set(SETTINGS_KEY, JSON.stringify({ enabled: true }));
  const session = await browserUseService.createAgentSession({
    purpose: 'Browser Test Facility controlled orphan-reclaim verification',
  });
  if (session.status !== 'ready') {
    throw new Error(session.message || 'Controlled orphan session did not become ready.');
  }
  const details = await browserUseService.getRunDetails(session.id);
  // Keep a machine-readable one-line marker even when database migration
  // logging precedes this script's own output.
  process.stdout.write(`BTF_ORPHAN_RUN=${session.id}\n`);
  process.stdout.write(`${JSON.stringify({
    mode,
    browserPid: details.run.browserPid,
    leaseExpiresAt: details.run.leaseExpiresAt,
  })}\n`);
  // This deliberately bypasses beforeExit so a separate process must prove
  // reconciliation. The run uses the exact, per-run profile marker required
  // by reclaimFacilityOwnedProcessTree().
  process.exit(0);
}

async function reconcileOrphan() {
  if (!runId) {
    throw new Error('BROWSER_FACILITY_ORPHAN_RUN_ID is required for reconcile mode.');
  }
  await browserUseService.listSessions();
  const details = await browserUseService.getRunDetails(runId);
  const receipt = details.cleanupReceipts.at(-1);
  if (
    details.run.status !== 'interrupted'
    || details.run.cleanupStatus !== 'reclaimed'
    || receipt?.reason !== 'orphaned-lease'
    || receipt.status !== 'reclaimed'
  ) {
    throw new Error(`Controlled orphan cleanup was not reclaimed: ${JSON.stringify({
      status: details.run.status,
      cleanupStatus: details.run.cleanupStatus,
      receipt,
    })}`);
  }
  process.stdout.write(`${JSON.stringify({
    mode,
    run: {
      id: details.run.id,
      status: details.run.status,
      browserPid: details.run.browserPid,
      cleanupStatus: details.run.cleanupStatus,
    },
    receipt: {
      id: receipt.id,
      reason: receipt.reason,
      status: receipt.status,
      browserPid: receipt.browserPid,
      details: receipt.details,
    },
  }, null, 2)}\n`);
  closeConnection();
}

if (mode === 'create') {
  await createOrphan();
} else if (mode === 'reconcile') {
  await reconcileOrphan();
} else {
  throw new Error('Set BROWSER_FACILITY_ORPHAN_MODE to create or reconcile.');
}
