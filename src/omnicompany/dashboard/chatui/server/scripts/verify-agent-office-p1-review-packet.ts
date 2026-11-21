import { browserUseService } from '@/modules/browser-use/browser-use.service.js';
import { appConfigDb, closeConnection, getConnection } from '@/modules/database/index.js';

const SETTINGS_KEY = 'browser_use_settings';
const reviewBaseUrl = process.env.AGENT_OFFICE_P1_REVIEW_URL
  || 'http://127.0.0.1:5175/module-review';

const reviewEntries = [
  ['portal-wall-bay', 'composition'],
  ['input-buffer', 'input-buffer-lod0'],
  ['core-machine', 'core-machine-lod0'],
  ['operator-console', 'operator-console-lod0'],
  ['output-buffer', 'output-buffer-lod0'],
  ['inspection-rig', 'inspection-rig-lod0'],
  ['overhead-hoist', 'overhead-hoist-lod0'],
  ['logistics-transfer-dock', 'logistics-transfer-dock-lod0'],
  ['owned-agent-corrected-static-assembly', 'owned-agent-corrected-kinematic-joints'],
  ['train-locomotive-running-assembly-v2', 'train-locomotive-running-assembly-lod0'],
  ['train-project-carriage-functional-assembly-v1', 'train-project-carriage-functional-assembly-lod0'],
  ['human-worker-unirig-runtime-v1', 'human-worker-production-workstation-operate'],
  ['human-worker-unirig-runtime-v1', 'human-worker-production-inspect-and-diagnose'],
  ['human-worker-unirig-runtime-v1', 'human-worker-production-payload-carry'],
  ['human-worker-unirig-runtime-v1', 'human-worker-production-payload-handoff'],
  ['human-worker-unirig-runtime-v1', 'human-worker-production-locomotion-walk'],
  ['human-worker-unirig-runtime-v1', 'human-worker-production-standing-idle'],
] as const;

async function main() {
  const priorSettings = appConfigDb.get(SETTINGS_KEY);
  let sessionId: string | null = null;
  try {
    appConfigDb.set(SETTINGS_KEY, JSON.stringify({ enabled: true }));
    const session = await browserUseService.createAgentSession({
      purpose: 'Agent Office P1 review packet deep-link verification',
    });
    sessionId = session.id;
    if (session.status !== 'ready') {
      throw new Error(session.message || 'Managed browser did not become ready.');
    }

    const checks = [];
    for (const [moduleId, viewId] of reviewEntries) {
      const url = new URL(reviewBaseUrl);
      url.searchParams.set('module', moduleId);
      url.searchParams.set('view', viewId);
      try {
        await browserUseService.agentNavigate(session.id, url.toString());
        await browserUseService.agentWaitFor(session.id, { timeoutMs: 1_000 });
        const inspection = await browserUseService.agentInspect(session.id, [
          '.module-review',
          `.module-review [data-view-id="${viewId}"]`,
          '.module-review__body header',
          '.module-review__stage canvas',
        ]);
        const [page, selectedView, header, canvas] = inspection.results;
        const errors = [];
        if (!page?.found || page.attributes['data-module-id'] !== moduleId) {
          errors.push(`expected module ${moduleId}`);
        }
        if (!selectedView?.found || selectedView.attributes['aria-pressed'] !== 'true') {
          errors.push(`expected selected view ${viewId}`);
        }
        if (!header?.found) errors.push('review header missing');
        if (!canvas?.found) errors.push('Three.js canvas missing');
        checks.push({
          moduleId,
          viewId,
          url: url.toString(),
          passed: errors.length === 0,
          errors,
        });
      } catch (error) {
        browserUseService.recordAgentToolFailure(
          session.id,
          `p1-review:${moduleId}:${viewId}`,
          error,
        );
        checks.push({
          moduleId,
          viewId,
          url: url.toString(),
          passed: false,
          errors: [error instanceof Error ? error.message : String(error)],
        });
      }
    }

    const snapshot = await browserUseService.agentSnapshot(session.id);
    await browserUseService.stopSession(session.id);
    const details = await browserUseService.getRunDetails(session.id);
    sessionId = null;
    const failures = checks.filter(({ passed }) => !passed);
    process.stdout.write(`${JSON.stringify({
      runId: details.run.id,
      reviewBaseUrl,
      checkCount: checks.length,
      failureCount: failures.length,
      checks,
      finalUrl: snapshot.session.url,
      runStatus: details.run.status,
      cleanupStatus: details.run.cleanupStatus,
      cleanupReceipts: details.cleanupReceipts,
      artifactKinds: details.artifacts.map(({ kind }) => kind),
    }, null, 2)}\n`);
    if (failures.length > 0) process.exitCode = 1;
  } catch (error) {
    if (sessionId) {
      browserUseService.recordAgentToolFailure(sessionId, 'p1-review-packet', error);
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
