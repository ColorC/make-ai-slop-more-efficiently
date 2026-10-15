import fs from 'node:fs';
import path from 'node:path';

import { browserUseService } from '@/modules/browser-use/browser-use.service.js';
import { appConfigDb, closeConnection, getConnection } from '@/modules/database/index.js';

const SETTINGS_KEY = 'browser_use_settings';
const reviewBaseUrl = process.env.AGENT_OFFICE_P1_HD_REVIEW_URL
  || 'http://192.0.2.10:8210/agent-office/module-review';
const reviewLabel = process.env.AGENT_OFFICE_P1_HD_REVIEW_LABEL
  || 'high-density source-model review';
const requestedCloseupDeltaY = Number.parseInt(
  process.env.AGENT_OFFICE_P1_HD_REVIEW_CLOSEUP_DELTA_Y || '-360',
  10,
);
const closeupDeltaY = Number.isFinite(requestedCloseupDeltaY)
  ? Math.max(-2_400, Math.min(-80, requestedCloseupDeltaY))
  : -360;
const evidenceDirectory = process.env.AGENT_OFFICE_P1_HD_EVIDENCE_DIR
  || path.resolve(process.cwd(), '../../../../../agent-office/review/evidence/p1-hd-source-review');

type ReviewEntry = {
  moduleId: string;
  viewId: string;
  minimumTriangles: number;
};

const defaultReviewEntries: ReviewEntry[] = [
  { moduleId: 'p1-hd-tool-clamp-insert', viewId: 'tool-clamp-source', minimumTriangles: 1_900_000 },
  { moduleId: 'p1-hd-coil-spring-cartridge', viewId: 'coil-spring-source', minimumTriangles: 1_900_000 },
  { moduleId: 'p1-hd-hydraulic-damper', viewId: 'hydraulic-damper-source', minimumTriangles: 1_900_000 },
];
const reviewEntries = process.env.AGENT_OFFICE_P1_HD_REVIEW_ENTRIES
  ? JSON.parse(process.env.AGENT_OFFICE_P1_HD_REVIEW_ENTRIES) as ReviewEntry[]
  : defaultReviewEntries;

type ModelInspection = {
  selector: string;
  found: boolean;
  text: string;
  attributes: Record<string, string>;
};

async function waitForModelReady(sessionId: string, timeoutMs = 45_000) {
  const startedAt = Date.now();
  let latest: ModelInspection | undefined;
  while (Date.now() - startedAt < timeoutMs) {
    const inspection = await browserUseService.agentInspect(sessionId, [
      '.module-review__model[data-model-stage]',
    ]);
    latest = inspection.results[0] as ModelInspection | undefined;
    const state = latest?.attributes['data-model-load-state'];
    if (state === 'ready') {
      return { inspection: latest, loadMs: Date.now() - startedAt };
    }
    if (state === 'error') {
      throw new Error(latest?.attributes['data-model-load-error'] || 'Review model failed to load.');
    }
    await browserUseService.agentWaitFor(sessionId, { timeoutMs: 1_000 });
  }
  throw new Error(`Review model did not become ready in ${timeoutMs}ms; last state: ${latest?.attributes['data-model-load-state'] || 'missing'}`);
}

async function persistScreenshot(runId: string, fileStem: string) {
  const details = await browserUseService.getRunDetails(runId);
  const source = [...details.artifacts]
    .reverse()
    .find(({ kind }) => kind === 'screenshot')?.path;
  if (!source || !fs.existsSync(source)) {
    throw new Error(`Managed browser screenshot artifact is missing for ${fileStem}.`);
  }
  fs.mkdirSync(evidenceDirectory, { recursive: true });
  const target = path.join(evidenceDirectory, `${fileStem}.jpg`);
  fs.copyFileSync(source, target);
  return target;
}

async function main() {
  const priorSettings = appConfigDb.get(SETTINGS_KEY);
  let sessionId: string | null = null;
  try {
    appConfigDb.set(SETTINGS_KEY, JSON.stringify({ enabled: true }));
    const session = await browserUseService.createAgentSession({
      purpose: `Agent Office P1 ${reviewLabel}`,
    });
    sessionId = session.id;
    if (session.status !== 'ready') {
      throw new Error(session.message || 'Managed browser did not become ready.');
    }

    const checks = [];
    for (const { moduleId, viewId, minimumTriangles } of reviewEntries) {
      const url = new URL(reviewBaseUrl);
      url.searchParams.set('module', moduleId);
      url.searchParams.set('view', viewId);
      try {
        await browserUseService.agentNavigate(session.id, url.toString());
        const ready = await waitForModelReady(session.id);
        const inspection = await browserUseService.agentInspect(session.id, [
          '.module-review',
          `.module-review [data-view-id="${viewId}"]`,
          '.module-review__body header',
          '.module-review__stage canvas',
          '.module-review__model[data-model-stage]',
        ]);
        const [page, selectedView, header, canvas, model] = inspection.results;
        const errors: string[] = [];
        if (!page?.found || page.attributes['data-module-id'] !== moduleId) {
          errors.push(`expected module ${moduleId}`);
        }
        if (!selectedView?.found || selectedView.attributes['aria-pressed'] !== 'true') {
          errors.push(`expected selected view ${viewId}`);
        }
        if (!header?.found) errors.push('review header missing');
        if (!canvas?.found) errors.push('Three.js canvas missing');
        if (!model?.found || model.attributes['data-model-load-state'] !== 'ready') {
          errors.push(`model is ${model?.attributes['data-model-load-state'] || 'missing'}`);
        }
        if (Number(model?.attributes['data-model-triangle-count'] || 0) < minimumTriangles) {
          errors.push(`decoded triangle count is below the ${minimumTriangles.toLocaleString()} source threshold`);
        }
        const performance = await browserUseService.agentMeasurePerformance(session.id, 3_000);
        await browserUseService.agentSnapshot(session.id);
        const evidenceStem = `${moduleId}-${viewId}`;
        const screenshotPath = await persistScreenshot(session.id, evidenceStem);
        // The review surface fits the complete model on load. The environment
        // may request a deeper source-only closeup, but the bounded movement
        // stays around the model-centred orbit target and never changes runtime
        // camera behavior.
        await browserUseService.agentWheel(session.id, {
          deltaY: closeupDeltaY,
          x: 870,
          y: 345,
        });
        await browserUseService.agentWaitFor(session.id, { timeoutMs: 500 });
        await browserUseService.agentSnapshot(session.id);
        const closeupScreenshotPath = await persistScreenshot(session.id, `${evidenceStem}-closeup`);
        checks.push({
          moduleId,
          viewId,
          url: url.toString(),
          passed: errors.length === 0,
          errors,
          loadMs: ready.loadMs,
          minimumTriangles,
          model: model?.attributes,
          performance: performance.metrics,
          screenshotPath,
          closeupScreenshotPath,
        });
      } catch (error) {
        browserUseService.recordAgentToolFailure(
          session.id,
          `p1-hd-review:${moduleId}:${viewId}`,
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

    await browserUseService.stopSession(session.id);
    const details = await browserUseService.getRunDetails(session.id);
    sessionId = null;
    const failures = checks.filter(({ passed }) => !passed);
    process.stdout.write(`${JSON.stringify({
      runId: details.run.id,
      reviewBaseUrl,
      evidenceDirectory,
      checkCount: checks.length,
      failureCount: failures.length,
      checks,
      runStatus: details.run.status,
      cleanupStatus: details.run.cleanupStatus,
      cleanupReceipts: details.cleanupReceipts,
      artifacts: details.artifacts,
    }, null, 2)}\n`);
    if (failures.length > 0) process.exitCode = 1;
  } catch (error) {
    if (sessionId) {
      browserUseService.recordAgentToolFailure(sessionId, `p1-hd-review:${reviewLabel}`, error);
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
