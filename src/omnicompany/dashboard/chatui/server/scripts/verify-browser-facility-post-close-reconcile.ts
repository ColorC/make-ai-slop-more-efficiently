import { appConfigDb, closeConnection } from '@/modules/database/index.js';
import { browserUseService } from '@/modules/browser-use/browser-use.service.js';

const SETTINGS_KEY = 'browser_use_settings';

async function main() {
  const previous = appConfigDb.get(SETTINGS_KEY);
  appConfigDb.set(SETTINGS_KEY, JSON.stringify({ enabled: true }));
  try {
    const session = await browserUseService.createAgentSession({
      purpose: 'Browser Test Facility normal-close ownership reconciliation verification',
    });
    if (session.status !== 'ready') {
      throw new Error(session.message || 'Normal-close verification session did not become ready.');
    }

    const stopped = await browserUseService.agentStopSession(session.id);
    if (!stopped.stopped) {
      throw new Error('Normal-close verification session did not stop.');
    }

    const details = await browserUseService.getRunDetails(session.id);
    const receipt = details.cleanupReceipts.at(-1);
    if (
      details.run.status !== 'stopped'
      || details.run.cleanupStatus !== 'reclaimed'
      || receipt?.reason !== 'manual-stop'
      || receipt.status !== 'reclaimed'
      || !receipt?.details?.includes('post-close ownership reconciliation:')
    ) {
      throw new Error(`Normal-close ownership reconciliation was not evidenced: ${JSON.stringify({
        run: {
          status: details.run.status,
          cleanupStatus: details.run.cleanupStatus,
        },
        receipt: receipt && {
          reason: receipt.reason,
          status: receipt.status,
          details: receipt.details,
        },
      })}`);
    }

    process.stdout.write(`${JSON.stringify({
      runId: details.run.id,
      runStatus: details.run.status,
      cleanupStatus: details.run.cleanupStatus,
      receipt: {
        id: receipt.id,
        reason: receipt.reason,
        status: receipt.status,
        details: receipt.details,
      },
    }, null, 2)}\n`);
  } finally {
    if (previous) {
      appConfigDb.set(SETTINGS_KEY, previous);
    } else {
      // The service default is disabled, so preserve the pre-run semantic
      // state even though the config facade intentionally exposes no delete.
      appConfigDb.set(SETTINGS_KEY, JSON.stringify({ enabled: false }));
    }
    closeConnection();
  }
}

void main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
