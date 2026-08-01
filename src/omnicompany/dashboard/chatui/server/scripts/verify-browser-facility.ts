import { appConfigDb, closeConnection, getConnection } from '@/modules/database/index.js';
import { browserUseService } from '@/modules/browser-use/browser-use.service.js';

const SETTINGS_KEY = 'browser_use_settings';
const targetUrl = process.env.BROWSER_FACILITY_VERIFY_URL || 'http://127.0.0.1:8210';
const purpose = process.env.BROWSER_FACILITY_VERIFY_PURPOSE
  || 'Browser Test Facility live verification';
const exerciseDebugHandoff = process.env.BROWSER_FACILITY_VERIFY_DEBUG === '1';
const exercisePlayback = process.env.BROWSER_FACILITY_VERIFY_PLAYBACK === '1';
const selectors = process.env.BROWSER_FACILITY_VERIFY_SELECTORS
  ? JSON.parse(process.env.BROWSER_FACILITY_VERIFY_SELECTORS) as string[]
  : ['.office-scene', '.factory-playback-status', '.factory-timeline-dock'];

async function main() {
  const priorSettings = appConfigDb.get(SETTINGS_KEY);
  let sessionId: string | null = null;
  try {
    // The live verifier enables only the local service flag. It deliberately
    // avoids updateSettings(), which would mutate provider MCP registrations.
    appConfigDb.set(SETTINGS_KEY, JSON.stringify({ enabled: true }));
    const session = await browserUseService.createAgentSession({ purpose });
    sessionId = session.id;
    if (session.status !== 'ready') {
      throw new Error(session.message || 'Browser facility did not become ready.');
    }
    await browserUseService.agentNavigate(session.id, targetUrl);
    await browserUseService.agentWaitFor(session.id, { timeoutMs: 6_000 });
    if (exercisePlayback) {
      await browserUseService.agentClick(session.id, {
        selector: '.factory-timeline-play',
      });
      await browserUseService.agentWaitFor(session.id, { timeoutMs: 12_000 });
    }
    const snapshot = await browserUseService.agentSnapshot(session.id);
    const inspection = await browserUseService.agentInspect(session.id, selectors);
    const performance = await browserUseService.agentMeasurePerformance(session.id, 3_000);

    if (exerciseDebugHandoff) {
      try {
        await browserUseService.agentClick(session.id, {
          selector: '[data-browser-facility-intentional-miss]',
        });
      } catch (error) {
        browserUseService.recordAgentToolFailure(
          session.id,
          'intentional-verifier-miss',
          error,
        );
      }
    }

    await browserUseService.stopSession(session.id);
    const details = await browserUseService.getRunDetails(session.id);
    sessionId = null;
    process.stdout.write(`${JSON.stringify({
      run: details.run,
      actionCount: details.actions.length,
      artifactKinds: details.artifacts.map((artifact) => artifact.kind),
      cleanupReceipts: details.cleanupReceipts,
      debug: details.debug,
      snapshot: {
        url: snapshot.session.url,
        title: snapshot.session.title,
        textLength: snapshot.text.length,
      },
      inspection: inspection.results,
      performance: performance.metrics,
    }, null, 2)}\n`);
  } catch (error) {
    if (sessionId) {
      browserUseService.recordAgentToolFailure(
        sessionId,
        'live-verifier',
        error,
      );
    }
    throw error;
  } finally {
    if (sessionId) {
      await browserUseService.stopSession(sessionId).catch(() => undefined);
    }
    if (priorSettings === null) {
      getConnection().prepare('DELETE FROM app_config WHERE key = ?').run(SETTINGS_KEY);
    } else {
      appConfigDb.set(SETTINGS_KEY, priorSettings);
    }
    closeConnection();
  }
}

await main();
