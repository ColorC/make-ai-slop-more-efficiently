import { createRequire } from 'node:module';
import { randomBytes, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { appConfigDb } from '@/modules/database/index.js';
import {
  getBrowserRunLedger,
  type BrowserCleanupStatus,
  type BrowserRunRecord,
  type BrowserRunStatus,
} from '@/modules/browser-use/browser-run-ledger.js';
import { providerMcpService } from '@/modules/providers/index.js';
import { getModuleDir } from '@/utils/runtime-paths.js';

const require = createRequire(import.meta.url);
const __dirname = getModuleDir(import.meta.url);
const IS_PLATFORM = process.env.VITE_IS_PLATFORM === 'true';
const MAX_SESSIONS_PER_OWNER = Number.parseInt(process.env.CLOUDCLI_BROWSER_USE_MAX_SESSIONS_PER_OWNER || '3', 10);
const SESSION_TTL_MS = Number.parseInt(process.env.CLOUDCLI_BROWSER_USE_SESSION_TTL_MS || String(30 * 60 * 1000), 10);
const BROWSER_USE_SETTINGS_KEY = 'browser_use_settings';
const BROWSER_USE_MCP_TOKEN_KEY = 'browser_use_mcp_token';

type BrowserUseRuntime = 'cloud' | 'local';
type BrowserUseSessionStatus = 'ready' | 'stopped' | 'unavailable';

type BrowserUseSession = {
  id: string;
  ownerId: string;
  createdBy: 'agent';
  runtime: BrowserUseRuntime;
  status: BrowserUseSessionStatus;
  url: string | null;
  title: string | null;
  screenshotDataUrl: string | null;
  createdAt: string;
  updatedAt: string;
  lastAction: string | null;
  message: string | null;
  profileName: string | null;
  purpose: string;
  runStatus: BrowserRunStatus;
  leaseExpiresAt: string;
  actionCount: number;
  artifactCount: number;
  cleanupStatus: BrowserCleanupStatus;
  lastError: string | null;
  debugCommand: string | null;
  viewport: {
    width: number;
    height: number;
  } | null;
  cursor: {
    x: number;
    y: number;
    actor: 'agent';
  } | null;
};

type PublicBrowserUseSession = Omit<BrowserUseSession, 'ownerId'>;

type RuntimeHandle = {
  browserServer?: any;
  browser?: any;
  context?: any;
  page?: any;
  browserPid: number | null;
  tracePath: string;
  harPath: string;
  screenshotPath: string;
  tracingStarted: boolean;
};

type BrowserUseSettings = {
  enabled: boolean;
};

type RuntimeReadiness = {
  playwright: any | null;
  playwrightInstalled: boolean;
  chromiumInstalled: boolean;
  chromiumExecutablePath: string | null;
  installInProgress: boolean;
  installMessage: string | null;
};

type RuntimeProbe = Omit<RuntimeReadiness, 'installInProgress' | 'installMessage'>;

const sessions = new Map<string, BrowserUseSession>();
const handles = new Map<string, RuntimeHandle>();
let installPromise: Promise<{ success: boolean; message: string }> | null = null;
let lastInstallMessage: string | null = null;
let runtimeProbeCache: { value: RuntimeProbe; updatedAt: number } | null = null;

const DEFAULT_SETTINGS: BrowserUseSettings = {
  enabled: false,
};
const AGENT_OWNER_ID = 'agent';
const PROFILE_ROOT = path.join(os.homedir(), '.cloudcli', 'browser-use', 'profiles');
const RUN_ROOT = path.join(os.homedir(), '.cloudcli', 'browser-use', 'runs');
const MCP_SERVER_NAME = 'cloudcli-browser';
const LEGACY_MCP_SERVER_NAMES = ['cloudcli-browser-use'];
const RUNTIME_READINESS_CACHE_TTL_MS = 30_000;
const SESSION_SWEEP_INTERVAL_MS = Math.max(
  5_000,
  Math.min(
    30_000,
    Number.parseInt(
      process.env.CLOUDCLI_BROWSER_USE_SWEEP_INTERVAL_MS || '30000',
      10,
    ),
  ),
);

function runPaths(runId: string) {
  const directory = path.join(RUN_ROOT, runId);
  return {
    directory,
    tracePath: path.join(directory, 'trace.zip'),
    harPath: path.join(directory, 'network.har'),
    screenshotPath: path.join(directory, 'latest.jpg'),
    debugInputPath: path.join(directory, 'debug-input.json'),
  };
}

function ensureRunDirectory(runId: string) {
  const paths = runPaths(runId);
  fs.mkdirSync(paths.directory, { recursive: true });
  return paths;
}

function leaseExpiry(now = Date.now()) {
  return new Date(now + SESSION_TTL_MS).toISOString();
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function browserProcessId(
  browserServer: any,
  browser: any,
  context: any,
): number | null {
  try {
    return browserServer?.process?.()?.pid
      || browser?.process?.()?.pid
      || context?.browser?.()?.process?.()?.pid
      || null;
  } catch {
    return null;
  }
}

function syncSessionFacilityState(session: BrowserUseSession) {
  const run = getBrowserRunLedger().getRun(session.id);
  if (!run) return;
  session.runStatus = run.status;
  session.leaseExpiresAt = run.leaseExpiresAt;
  session.actionCount = run.actionCount;
  session.artifactCount = run.artifactCount;
  session.cleanupStatus = run.cleanupStatus;
  session.lastError = run.failureReason;
  const debugInputPath = runPaths(run.id).debugInputPath;
  session.debugCommand = run.failureReason && fs.existsSync(debugInputPath)
    ? `omni run debug --json-file "${debugInputPath}"`
    : null;
}

function historicalSession(run: BrowserRunRecord): PublicBrowserUseSession {
  const status: BrowserUseSessionStatus = run.status === 'ready'
    ? 'unavailable'
    : run.status === 'failed'
      ? 'unavailable'
      : 'stopped';
  return {
    id: run.id,
    createdBy: 'agent',
    runtime: run.runtime === 'cloud' ? 'cloud' : 'local',
    status,
    url: run.url,
    title: run.title,
    screenshotDataUrl: null,
    createdAt: run.createdAt,
    updatedAt: run.updatedAt,
    lastAction: run.lastAction,
    message: run.failureReason || run.cleanupReason,
    profileName: run.profileName,
    purpose: run.purpose,
    runStatus: run.status,
    leaseExpiresAt: run.leaseExpiresAt,
    actionCount: run.actionCount,
    artifactCount: run.artifactCount,
    cleanupStatus: run.cleanupStatus,
    lastError: run.failureReason,
    debugCommand: run.failureReason && fs.existsSync(runPaths(run.id).debugInputPath)
      ? `omni run debug --json-file "${runPaths(run.id).debugInputPath}"`
      : null,
    viewport: null,
    cursor: null,
  };
}

function writeDebugInput(runId: string, failureReason: string) {
  const paths = ensureRunDirectory(runId);
  const run = getBrowserRunLedger().getRun(runId);
  const recentActions = getBrowserRunLedger().listActions(runId).slice(-12);
  const payload = {
    error_output: [
      `Browser test run: ${runId}`,
      `Purpose: ${run?.purpose || 'Browser verification'}`,
      `URL: ${run?.url || 'not loaded'}`,
      `Failure: ${failureReason}`,
      `Actions: ${JSON.stringify(recentActions)}`,
    ].join('\n'),
    language: 'typescript',
    work_dir: process.cwd(),
  };
  fs.writeFileSync(paths.debugInputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  getBrowserRunLedger().recordArtifact(runId, {
    kind: 'debug-input',
    path: paths.debugInputPath,
    now: new Date().toISOString(),
  });
  return {
    inputPath: paths.debugInputPath,
    command: `omni run debug --json-file "${paths.debugInputPath}"`,
  };
}

function recordSessionAction(
  session: BrowserUseSession,
  action: string,
  details: Record<string, unknown> = {},
) {
  const now = new Date().toISOString();
  session.lastAction = action;
  session.updatedAt = now;
  getBrowserRunLedger().recordAction(session.id, {
    action,
    outcome: 'ok',
    details,
    now,
    leaseExpiresAt: leaseExpiry(Date.parse(now)),
  });
  getBrowserRunLedger().heartbeat(session.id, {
    now,
    leaseExpiresAt: leaseExpiry(Date.parse(now)),
    url: session.url,
    title: session.title,
    lastAction: action,
  });
  syncSessionFacilityState(session);
}

function recordSessionFailure(
  session: BrowserUseSession,
  action: string,
  error: unknown,
) {
  const message = errorMessage(error);
  const now = new Date().toISOString();
  session.lastAction = `${action}:failed`;
  session.updatedAt = now;
  session.message = message;
  session.lastError = message;
  getBrowserRunLedger().recordAction(session.id, {
    action,
    outcome: 'failed',
    details: { error: message },
    now,
    leaseExpiresAt: leaseExpiry(Date.parse(now)),
    failureReason: message,
  });
  writeDebugInput(session.id, message);
  syncSessionFacilityState(session);
}

function getRuntime(): BrowserUseRuntime {
  return IS_PLATFORM ? 'cloud' : 'local';
}

function readSettings(): BrowserUseSettings {
  try {
    const raw = appConfigDb.get(BROWSER_USE_SETTINGS_KEY);
    if (!raw) {
      return DEFAULT_SETTINGS;
    }

    const parsed = JSON.parse(raw) as Partial<BrowserUseSettings>;
    return {
      enabled: parsed.enabled === true,
    };
  } catch (error: any) {
    console.warn('[Browser] Failed to read settings:', error?.message || error);
    return DEFAULT_SETTINGS;
  }
}

function writeSettings(settings: BrowserUseSettings): BrowserUseSettings {
  const normalized = {
    enabled: settings.enabled === true,
  };

  appConfigDb.set(BROWSER_USE_SETTINGS_KEY, JSON.stringify(normalized));
  return normalized;
}

function getOrCreateMcpToken(): string {
  const existing = appConfigDb.get(BROWSER_USE_MCP_TOKEN_KEY);
  if (existing) {
    return existing;
  }
  const token = randomBytes(32).toString('hex');
  appConfigDb.set(BROWSER_USE_MCP_TOKEN_KEY, token);
  return token;
}

function getSetupMessage(settings: BrowserUseSettings, readiness: RuntimeReadiness): string {
  if (!settings.enabled) {
    return 'Browser is disabled in settings.';
  }

  if (!readiness.playwrightInstalled) {
    return 'Install Playwright and Chromium to use browser sessions.';
  }

  if (!readiness.chromiumInstalled) {
    return 'Playwright is installed, but Chromium is missing. Install the Chromium runtime to continue.';
  }

  return readiness.installMessage || 'Browser runtime is not ready.';
}

function getPlaywright(): any | null {
  try {
    return require('playwright');
  } catch {
    return null;
  }
}

function getMcpCommand(): { command: string; args: string[] } {
  const serverDir = path.resolve(__dirname, '..', '..');
  const mcpScriptPath = path.join(serverDir, 'browser-use-mcp.js');
  if (fs.existsSync(mcpScriptPath)) {
    return {
      command: process.execPath,
      args: [mcpScriptPath],
    };
  }

  return {
    command: 'cloudcli',
    args: ['browser-use-mcp'],
  };
}

function getMcpApiUrl(): string {
  const port = process.env.SERVER_PORT || process.env.PORT || '3001';
  return `http://127.0.0.1:${port}/api/browser-use-mcp`;
}

async function removeMcpServerFromAllProviders(name: string) {
  const results = await providerMcpService.removeMcpServerFromAllProviders({
    name,
    scope: 'user',
  });
  return results.map((result) => ({ ...result, name }));
}

function normalizeProfileName(profileName?: string | null): string | null {
  const normalized = String(profileName || '').trim();
  if (!normalized) {
    return null;
  }

  return normalized.slice(0, 80);
}

function getProfilePath(profileName: string): string {
  const safeName = profileName
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'default';
  return path.join(PROFILE_ROOT, safeName);
}

function probeRuntime(): RuntimeProbe {
  const playwright = getPlaywright();
  const readiness: RuntimeProbe = {
    playwright,
    playwrightInstalled: Boolean(playwright),
    chromiumInstalled: false,
    chromiumExecutablePath: null,
  };

  if (!playwright) {
    return readiness;
  }

  try {
    const executablePath = playwright.chromium.executablePath();
    readiness.chromiumExecutablePath = executablePath;
    readiness.chromiumInstalled = Boolean(executablePath && fs.existsSync(executablePath));
  } catch {
    readiness.chromiumInstalled = false;
  }

  return readiness;
}

function getRuntimeReadiness(options: { force?: boolean } = {}): RuntimeReadiness {
  const now = Date.now();
  const cachedProbe = runtimeProbeCache;
  const canUseCache = !options.force
    && !installPromise
    && cachedProbe
    && now - cachedProbe.updatedAt < RUNTIME_READINESS_CACHE_TTL_MS;
  const probe = canUseCache ? cachedProbe.value : probeRuntime();

  if (!canUseCache && !installPromise) {
    runtimeProbeCache = { value: probe, updatedAt: now };
  }

  return {
    ...probe,
    installInProgress: Boolean(installPromise),
    installMessage: lastInstallMessage,
  };
}

const INSTALL_COMMAND_TIMEOUT_MS = Number.parseInt(
  process.env.CLOUDCLI_BROWSER_USE_INSTALL_TIMEOUT_MS || String(10 * 60 * 1000),
  10,
);

function runCommand(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: process.cwd(),
      env: process.env,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const output: string[] = [];
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      fn();
    };

    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(() => reject(new Error(
        `${command} ${args.join(' ')} timed out after ${INSTALL_COMMAND_TIMEOUT_MS}ms.`,
      )));
    }, INSTALL_COMMAND_TIMEOUT_MS);
    timer.unref?.();

    child.stdout.on('data', (chunk) => output.push(String(chunk)));
    child.stderr.on('data', (chunk) => output.push(String(chunk)));
    child.on('error', (error) => finish(() => reject(error)));
    child.on('close', (code) => finish(() => {
      if (code === 0) {
        resolve();
        return;
      }

      reject(new Error(output.join('').trim() || `${command} ${args.join(' ')} exited with code ${code}`));
    }));
  });
}

function formatInstallError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes('sudo') && message.includes('password')) {
    return 'Installing Chromium system dependencies requires administrator privileges. Run `npx playwright install-deps chromium` on the machine where CloudCLI runs, then try again.';
  }
  return message || 'Failed to install Browser runtime.';
}

async function installRuntime(): Promise<{ success: boolean; message: string }> {
  if (installPromise) {
    return installPromise;
  }

  const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  runtimeProbeCache = null;
  installPromise = (async () => {
    try {
      lastInstallMessage = 'Installing Playwright package...';
      await runCommand(npmCommand, ['install', '--no-save', '--no-package-lock', 'playwright']);

      if (process.platform === 'linux') {
        lastInstallMessage = 'Installing Chromium system dependencies...';
        await runCommand(npmCommand, ['exec', '--', 'playwright', 'install-deps', 'chromium']);
      }

      lastInstallMessage = 'Installing Chromium runtime...';
      await runCommand(npmCommand, ['exec', '--', 'playwright', 'install', 'chromium']);

      lastInstallMessage = 'Browser runtime installed.';
      return { success: true, message: lastInstallMessage };
    } catch (error) {
      lastInstallMessage = formatInstallError(error);
      return { success: false, message: lastInstallMessage };
    }
  })();

  try {
    return await installPromise;
  } finally {
    installPromise = null;
    runtimeProbeCache = null;
  }
}

function normalizeUrl(rawUrl: string): string {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    throw new Error('URL is required.');
  }

  const withProtocol = /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;
  const parsed = new URL(withProtocol);
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('Only http and https URLs are supported.');
  }

  return parsed.toString();
}

function publicSession(session: BrowserUseSession): PublicBrowserUseSession {
  syncSessionFacilityState(session);
  const { ownerId: _ownerId, ...publicFields } = session;
  return publicFields;
}

function ownerSessions(ownerId: string): BrowserUseSession[] {
  return [...sessions.values()].filter((session) => session.ownerId === ownerId);
}

async function closeHandle(
  sessionId: string,
  reason: string,
): Promise<BrowserCleanupStatus> {
  const handle = handles.get(sessionId);
  handles.delete(sessionId);
  if (!handle) {
    getBrowserRunLedger().recordCleanup(sessionId, {
      reason,
      status: 'not-started',
      browserPid: null,
      details: 'No live browser handle was registered.',
      now: new Date().toISOString(),
    });
    return 'not-started';
  }

  const failures: string[] = [];
  if (handle.tracingStarted) {
    await handle.context?.tracing?.stop?.({ path: handle.tracePath })
      .catch((error: unknown) => failures.push(`trace: ${errorMessage(error)}`));
  }
  await handle.context?.close?.()
    .catch((error: unknown) => failures.push(`context: ${errorMessage(error)}`));
  if (handle.browserServer) {
    await handle.browserServer.close?.()
      .catch((error: unknown) => failures.push(`browser-server: ${errorMessage(error)}`));
  } else {
    await handle.browser?.close?.()
      .catch((error: unknown) => failures.push(`browser: ${errorMessage(error)}`));
  }

  const now = new Date().toISOString();
  for (const [kind, artifactPath] of [
    ['trace', handle.tracePath],
    ['har', handle.harPath],
    ['screenshot', handle.screenshotPath],
  ] as const) {
    if (fs.existsSync(artifactPath)) {
      getBrowserRunLedger().recordArtifact(sessionId, {
        kind,
        path: artifactPath,
        now,
      });
    }
  }
  const status: BrowserCleanupStatus = failures.length > 0
    ? 'unconfirmed'
    : 'reclaimed';
  getBrowserRunLedger().recordCleanup(sessionId, {
    reason,
    status,
    browserPid: handle.browserPid,
    details: failures.length ? failures.join('; ') : 'Context and browser closed.',
    now,
  });
  return status;
}

async function expireStaleSessions(now = Date.now()): Promise<void> {
  await Promise.all([...sessions.values()].map(async (session) => {
    if (session.status !== 'ready') {
      return;
    }

    const updatedAt = Date.parse(session.updatedAt);
    if (!Number.isFinite(updatedAt) || now - updatedAt <= SESSION_TTL_MS) {
      return;
    }

    await closeHandle(session.id, 'lease-expired');
    session.status = 'stopped';
    session.runStatus = 'expired';
    session.updatedAt = new Date(now).toISOString();
    session.lastAction = 'expire';
    session.message = 'Browser session expired after inactivity.';
    getBrowserRunLedger().finishRun(session.id, {
      status: 'expired',
      now: session.updatedAt,
      reason: session.message,
    });
    syncSessionFacilityState(session);
  }));

  const nowIso = new Date(now).toISOString();
  for (const run of getBrowserRunLedger().listExpiredReadyRuns(nowIso)) {
    if (sessions.has(run.id)) continue;
    getBrowserRunLedger().finishRun(run.id, {
      status: 'interrupted',
      now: nowIso,
      reason: 'Lease expired after the owning server stopped reporting.',
    });
    getBrowserRunLedger().recordCleanup(run.id, {
      reason: 'orphaned-lease',
      status: 'unconfirmed',
      browserPid: run.browserPid,
      details: 'No in-memory Playwright handle remained; process ownership requires host reconciliation.',
      now: nowIso,
    });
  }
}

async function captureSession(
  session: BrowserUseSession,
  page: any,
  action?: { name: string; details?: Record<string, unknown> },
): Promise<void> {
  const screenshot = await page.screenshot({ type: 'jpeg', quality: 72, fullPage: false });
  session.screenshotDataUrl = `data:image/jpeg;base64,${Buffer.from(screenshot).toString('base64')}`;
  session.title = await page.title().catch(() => null);
  session.url = page.url() || session.url;
  session.viewport = page.viewportSize?.() || session.viewport;
  session.updatedAt = new Date().toISOString();
  const paths = ensureRunDirectory(session.id);
  fs.writeFileSync(paths.screenshotPath, screenshot);
  getBrowserRunLedger().recordArtifact(session.id, {
    kind: 'screenshot',
    path: paths.screenshotPath,
    now: session.updatedAt,
  });
  if (action) {
    recordSessionAction(session, action.name, action.details);
  } else {
    getBrowserRunLedger().heartbeat(session.id, {
      now: session.updatedAt,
      leaseExpiresAt: leaseExpiry(Date.parse(session.updatedAt)),
      url: session.url,
      title: session.title,
      lastAction: session.lastAction,
    });
    syncSessionFacilityState(session);
  }
}

async function getActionPoint(page: any, input: { selector?: string; text?: string; x?: number; y?: number }) {
  if (typeof input.x === 'number' && typeof input.y === 'number') {
    return { x: input.x, y: input.y };
  }

  const locator = input.selector
    ? page.locator(input.selector).first()
    : input.text
      ? page.getByText(input.text, { exact: false }).first()
      : null;

  if (!locator) {
    return null;
  }

  const box = await locator.boundingBox().catch(() => null);
  if (!box) {
    return null;
  }

  return {
    x: Math.round(box.x + box.width / 2),
    y: Math.round(box.y + box.height / 2),
  };
}

export const browserUseService = {
  async getSettings() {
    return readSettings();
  },

  async updateSettings(settings: Partial<BrowserUseSettings>) {
    const current = readSettings();
    const nextSettings = {
      enabled: typeof settings.enabled === 'boolean' ? settings.enabled : current.enabled,
    };

    const next = writeSettings(nextSettings);
    if (next.enabled) {
      await this.registerAgentMcp();
    } else if (current.enabled) {
      await this.unregisterAgentMcp();
      await this.stopAllSessions();
    }
    return next;
  },

  async getStatus() {
    const settings = readSettings();
    const readiness = getRuntimeReadiness();
    const available = settings.enabled && readiness.playwrightInstalled && readiness.chromiumInstalled;

    const runs = getBrowserRunLedger().listRuns(500);
    return {
      enabled: settings.enabled,
      runtime: getRuntime(),
      available,
      playwrightInstalled: readiness.playwrightInstalled,
      chromiumInstalled: readiness.chromiumInstalled,
      installInProgress: readiness.installInProgress,
      sessionCount: sessions.size,
      managedRunCount: runs.length,
      activeLeaseCount: runs.filter((run) => run.status === 'ready').length,
      reclaimedRunCount: runs.filter((run) => run.cleanupStatus === 'reclaimed').length,
      unconfirmedCleanupCount: runs.filter((run) => run.cleanupStatus === 'unconfirmed').length,
      message: available
        ? 'Browser runtime is available.'
        : getSetupMessage(settings, readiness),
    };
  },

  async registerAgentMcp() {
    const { command, args } = getMcpCommand();
    await Promise.all(LEGACY_MCP_SERVER_NAMES.map((name) => removeMcpServerFromAllProviders(name)));
    const results = await providerMcpService.addMcpServerToAllProviders({
      name: MCP_SERVER_NAME,
      scope: 'user',
      transport: 'stdio',
      command,
      args,
      env: {
        CLOUDCLI_BROWSER_USE_MCP_TOKEN: getOrCreateMcpToken(),
        CLOUDCLI_BROWSER_USE_API_URL: getMcpApiUrl(),
      },
    });
    return { name: MCP_SERVER_NAME, command, args, results };
  },

  getMcpToken() {
    return getOrCreateMcpToken();
  },

  async unregisterAgentMcp() {
    const results = (await Promise.all(
      [MCP_SERVER_NAME, ...LEGACY_MCP_SERVER_NAMES].map((name) => removeMcpServerFromAllProviders(name)),
    )).flat();
    return { name: MCP_SERVER_NAME, results };
  },

  async installRuntime() {
    const result = await installRuntime();
    return {
      ...result,
      status: await this.getStatus(),
    };
  },

  async listSessions() {
    await expireStaleSessions();
    const live = [...sessions.values()]
      .filter((session) => session.ownerId === AGENT_OWNER_ID)
      .map(publicSession);
    const liveIds = new Set(live.map((session) => session.id));
    const history = getBrowserRunLedger().listRuns(50)
      .filter((run) => run.ownerId === AGENT_OWNER_ID && !liveIds.has(run.id))
      .map(historicalSession);
    return [...live, ...history].sort((left, right) => (
      Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
    ));
  },

  async createAgentSession(options?: {
    profileName?: string | null;
    purpose?: string | null;
  }) {
    const settings = readSettings();
    if (!settings.enabled) {
      throw new Error('Browser agent tools are disabled.');
    }

    await expireStaleSessions();
    const profileName = normalizeProfileName(options?.profileName);
    const purpose = String(options?.purpose || 'Agent browser verification')
      .trim()
      .slice(0, 240) || 'Agent browser verification';

    const now = new Date().toISOString();
    const session: BrowserUseSession = {
      id: randomUUID(),
      ownerId: AGENT_OWNER_ID,
      createdBy: 'agent',
      runtime: getRuntime(),
      status: 'unavailable',
      url: null,
      title: null,
      screenshotDataUrl: null,
      createdAt: now,
      updatedAt: now,
      lastAction: 'create',
      message: null,
      profileName,
      purpose,
      runStatus: 'starting',
      leaseExpiresAt: leaseExpiry(Date.parse(now)),
      actionCount: 0,
      artifactCount: 0,
      cleanupStatus: 'pending',
      lastError: null,
      debugCommand: null,
      viewport: { width: 1440, height: 900 },
      cursor: null,
    };

    const activeOwnerSessions = ownerSessions(AGENT_OWNER_ID).filter((item) => item.status === 'ready');
    if (activeOwnerSessions.length >= MAX_SESSIONS_PER_OWNER) {
      throw new Error(`Browser is limited to ${MAX_SESSIONS_PER_OWNER} active agent sessions.`);
    }

    getBrowserRunLedger().createRun({
      id: session.id,
      ownerId: session.ownerId,
      createdBy: session.createdBy,
      purpose,
      runtime: session.runtime,
      profileName,
      createdAt: now,
      leaseExpiresAt: session.leaseExpiresAt,
    });
    sessions.set(session.id, session);

    const readiness = getRuntimeReadiness();
    if (!settings.enabled || !readiness.playwrightInstalled || !readiness.chromiumInstalled || !readiness.playwright) {
      session.message = getSetupMessage(settings, readiness);
      session.runStatus = 'failed';
      getBrowserRunLedger().finishRun(session.id, {
        status: 'failed',
        now,
        reason: session.message,
      });
      getBrowserRunLedger().recordCleanup(session.id, {
        reason: 'runtime-unavailable',
        status: 'not-started',
        browserPid: null,
        details: session.message,
        now,
      });
      return publicSession(session);
    }

    let browser: any | undefined;
    let browserServer: any | undefined;
    let context: any | undefined;
    let page: any;
    const launchOptions = {
      headless: true,
      args: [
        '--disable-dev-shm-usage',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
      ],
    };
    const paths = ensureRunDirectory(session.id);
    const contextOptions = {
      viewport: { width: 1440, height: 900 },
      serviceWorkers: 'block',
      recordHar: {
        path: paths.harPath,
        content: 'omit',
        mode: 'minimal',
      },
    };

    try {
      if (profileName) {
        fs.mkdirSync(PROFILE_ROOT, { recursive: true });
        context = await readiness.playwright.chromium.launchPersistentContext(getProfilePath(profileName), {
          ...launchOptions,
          ...contextOptions,
        });
        page = context.pages()[0] || await context.newPage();
      } else {
        browserServer = await readiness.playwright.chromium.launchServer(launchOptions);
        browser = await readiness.playwright.chromium.connect(browserServer.wsEndpoint());
        context = await browser.newContext(contextOptions);
        page = await context.newPage();
      }
      await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
      const browserPid = browserProcessId(browserServer, browser, context);
      session.status = 'ready';
      session.runStatus = 'ready';
      session.message = 'Browser session is ready.';
      handles.set(session.id, {
        browserServer,
        browser,
        context,
        page,
        browserPid,
        tracePath: paths.tracePath,
        harPath: paths.harPath,
        screenshotPath: paths.screenshotPath,
        tracingStarted: true,
      });
      getBrowserRunLedger().markReady(session.id, {
        now: new Date().toISOString(),
        leaseExpiresAt: leaseExpiry(),
        browserPid,
      });
      await captureSession(session, page, {
        name: 'create',
        details: { profileName, purpose },
      });
      return publicSession(session);
    } catch (error) {
      if ((context || browser || browserServer) && !handles.has(session.id)) {
        handles.set(session.id, {
          browserServer,
          browser,
          context,
          page,
          browserPid: browserProcessId(browserServer, browser, context),
          tracePath: paths.tracePath,
          harPath: paths.harPath,
          screenshotPath: paths.screenshotPath,
          tracingStarted: false,
        });
      }
      recordSessionFailure(session, 'create', error);
      await closeHandle(session.id, 'launch-failed');
      session.status = 'unavailable';
      session.runStatus = 'failed';
      getBrowserRunLedger().finishRun(session.id, {
        status: 'failed',
        now: new Date().toISOString(),
        reason: errorMessage(error),
      });
      syncSessionFacilityState(session);
      throw error;
    }
  },

  async listAgentSessions() {
    const settings = readSettings();
    if (!settings.enabled) {
      return [];
    }
    await expireStaleSessions();
    return this.listSessions();
  },

  async getAgentSession(sessionId: string) {
    const settings = readSettings();
    if (!settings.enabled) {
      throw new Error('Browser agent tools are disabled.');
    }
    const session = sessions.get(sessionId);
    if (!session || session.ownerId !== AGENT_OWNER_ID) {
      throw new Error('Browser session not found.');
    }
    return session;
  },

  async listRuns(limit = 100) {
    await expireStaleSessions();
    return getBrowserRunLedger().listRuns(limit);
  },

  async getRunDetails(runId: string) {
    await expireStaleSessions();
    const run = getBrowserRunLedger().getRun(runId);
    if (!run) throw new Error('Browser test run not found.');
    return {
      run,
      actions: getBrowserRunLedger().listActions(runId),
      artifacts: getBrowserRunLedger().listArtifacts(runId),
      cleanupReceipts: getBrowserRunLedger().listCleanupReceipts(runId),
      debug: run.failureReason && fs.existsSync(runPaths(runId).debugInputPath)
        ? {
            pipeline: 'debug',
            inputPath: runPaths(runId).debugInputPath,
            command: `omni run debug --json-file "${runPaths(runId).debugInputPath}"`,
          }
        : null,
    };
  },

  recordAgentToolFailure(sessionId: string, action: string, error: unknown) {
    const session = sessions.get(sessionId);
    if (!session || session.ownerId !== AGENT_OWNER_ID) return;
    recordSessionFailure(session, action, error);
  },

  async agentNavigate(sessionId: string, rawUrl: string) {
    await this.getAgentSession(sessionId);
    await expireStaleSessions();

    const session = sessions.get(sessionId);
    if (!session || session.ownerId !== AGENT_OWNER_ID) {
      throw new Error('Browser session not found.');
    }

    if (session.status !== 'ready') {
      throw new Error(session.message || 'Browser session is not available.');
    }

    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }

    const url = normalizeUrl(rawUrl);
    await handle.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    session.cursor = null;
    await captureSession(session, handle.page, {
      name: 'navigate',
      details: { url },
    });
    return publicSession(session);
  },

  async agentSnapshot(sessionId: string) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    await captureSession(session, handle.page, { name: 'snapshot' });
    const text = await handle.page.locator('body').innerText({ timeout: 5_000 }).catch(() => '');
    return {
      session: publicSession(session),
      text: text.slice(0, 30_000),
    };
  },

  async agentInspect(sessionId: string, selectors: string[]) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    const normalizedSelectors = selectors
      .map((selector) => String(selector || '').trim())
      .filter(Boolean)
      .slice(0, 50);
    const results = await handle.page.evaluate((requestedSelectors: string[]) => {
      const documentRef = (globalThis as any).document;
      return requestedSelectors.map((selector) => {
        const element = documentRef.querySelector(selector);
        if (!element) return { selector, found: false, text: '', attributes: {} };
        const attributes = Object.fromEntries(
          [...element.attributes]
            .filter((attribute: any) => (
              attribute.name.startsWith('data-')
              || attribute.name.startsWith('aria-')
              || attribute.name === 'title'
              || attribute.name === 'class'
            ))
            .slice(0, 80)
            .map((attribute: any) => [attribute.name, attribute.value.slice(0, 2_000)]),
        );
        return {
          selector,
          found: true,
          text: (element.textContent || '').trim().slice(0, 10_000),
          attributes,
        };
      });
    }, normalizedSelectors);
    recordSessionAction(session, 'inspect', {
      selectors: normalizedSelectors,
      foundCount: results.filter((result: { found: boolean }) => result.found).length,
    });
    return { session: publicSession(session), results };
  },

  async agentMeasurePerformance(sessionId: string, durationMs = 3_000) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    const boundedDuration = Math.max(1_000, Math.min(durationMs, 10_000));
    const metrics = await handle.page.evaluate(async (sampleDurationMs: number) => {
      const browserGlobal = globalThis as any;
      const performanceRef = browserGlobal.performance;
      const documentRef = browserGlobal.document;
      const frameIntervals: number[] = [];
      const startedAt = performanceRef.now();
      let previousFrame = startedAt;
      let frameCount = 0;
      while (performanceRef.now() - startedAt < sampleDurationMs) {
        const timestamp = await new Promise<number>((resolve) => {
          browserGlobal.requestAnimationFrame(resolve);
        });
        if (frameCount > 0) frameIntervals.push(timestamp - previousFrame);
        previousFrame = timestamp;
        frameCount += 1;
      }
      const endedAt = performanceRef.now();
      const ordered = [...frameIntervals].sort((left, right) => left - right);
      const p95Index = Math.min(
        Math.max(0, Math.ceil(ordered.length * 0.95) - 1),
        Math.max(0, ordered.length - 1),
      );
      const memory = performanceRef.memory;
      return {
        sampleDurationMs: Math.round(endedAt - startedAt),
        frameCount,
        averageFps: Number((frameCount * 1_000 / Math.max(1, endedAt - startedAt)).toFixed(2)),
        p95FrameMs: Number((ordered[p95Index] || 0).toFixed(2)),
        maxFrameMs: Number((ordered.at(-1) || 0).toFixed(2)),
        domElementCount: documentRef.querySelectorAll('*').length,
        canvasCount: documentRef.querySelectorAll('canvas').length,
        usedJSHeapBytes: memory?.usedJSHeapSize || null,
        totalJSHeapBytes: memory?.totalJSHeapSize || null,
      };
    }, boundedDuration);
    recordSessionAction(session, 'measure_performance', metrics);
    return { session: publicSession(session), metrics };
  },

  async agentClick(sessionId: string, input: { selector?: string; text?: string; x?: number; y?: number }) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    const point = await getActionPoint(handle.page, input);

    if (input.selector) {
      await handle.page.locator(input.selector).first().click({ timeout: 10_000 });
    } else if (input.text) {
      await handle.page.getByText(input.text, { exact: false }).first().click({ timeout: 10_000 });
    } else if (typeof input.x === 'number' && typeof input.y === 'number') {
      await handle.page.mouse.click(input.x, input.y);
    } else {
      throw new Error('Provide selector, text, or x/y coordinates.');
    }

    session.cursor = point ? { ...point, actor: 'agent' } : null;
    await captureSession(session, handle.page, {
      name: 'click',
      details: {
        selector: input.selector || null,
        textLength: input.text?.length || 0,
        point,
      },
    });
    return publicSession(session);
  },

  async agentType(sessionId: string, input: { selector?: string; text: string; submit?: boolean }) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }

    if (input.selector) {
      await handle.page.locator(input.selector).first().fill(input.text, { timeout: 10_000 });
      session.cursor = await getActionPoint(handle.page, input).then((point) => (
        point ? { ...point, actor: 'agent' as const } : null
      ));
    } else {
      await handle.page.keyboard.type(input.text);
    }
    if (input.submit) {
      await handle.page.keyboard.press('Enter');
    }

    await captureSession(session, handle.page, {
      name: 'type',
      details: {
        selector: input.selector || null,
        textLength: input.text.length,
        submit: input.submit === true,
      },
    });
    return publicSession(session);
  },

  async agentFillForm(sessionId: string, fields: Array<{ selector: string; value: string }>) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    for (const field of fields) {
      await handle.page.locator(field.selector).first().fill(field.value, { timeout: 10_000 });
    }
    if (fields[0]) {
      session.cursor = await getActionPoint(handle.page, { selector: fields[0].selector }).then((point) => (
        point ? { ...point, actor: 'agent' as const } : null
      ));
    }
    await captureSession(session, handle.page, {
      name: 'fill_form',
      details: {
        fields: fields.map((field) => ({
          selector: field.selector,
          valueLength: field.value.length,
        })),
      },
    });
    return publicSession(session);
  },

  async agentPressKey(sessionId: string, key: string) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    await handle.page.keyboard.press(key);
    await captureSession(session, handle.page, {
      name: 'press_key',
      details: { key },
    });
    return publicSession(session);
  },

  async agentSelectOption(sessionId: string, selector: string, values: string[]) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    await handle.page.locator(selector).first().selectOption(values, { timeout: 10_000 });
    session.cursor = await getActionPoint(handle.page, { selector }).then((point) => (
      point ? { ...point, actor: 'agent' as const } : null
    ));
    await captureSession(session, handle.page, {
      name: 'select_option',
      details: { selector, valueCount: values.length },
    });
    return publicSession(session);
  },

  async agentWaitFor(sessionId: string, input: { text?: string; url?: string; timeoutMs?: number }) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    const timeout = Math.max(250, Math.min(input.timeoutMs || 5_000, 30_000));
    if (input.text) {
      await handle.page.getByText(input.text, { exact: false }).first().waitFor({ timeout });
    } else if (input.url) {
      await handle.page.waitForURL(input.url, { timeout });
    } else {
      await handle.page.waitForTimeout(timeout);
    }
    await captureSession(session, handle.page, {
      name: 'wait_for',
      details: {
        textLength: input.text?.length || 0,
        url: input.url || null,
        timeout,
      },
    });
    return publicSession(session);
  },

  async agentTabs(sessionId: string, input: { action?: 'list' | 'new' | 'select' | 'close'; index?: number; url?: string }) {
    const session = await this.getAgentSession(sessionId);
    const handle = handles.get(sessionId);
    if (!handle?.context || !handle?.page) {
      throw new Error('Browser runtime handle is not available.');
    }
    const action = input.action || 'list';
    if (action === 'new') {
      const page = await handle.context.newPage();
      handles.set(sessionId, { ...handle, page });
      if (input.url) {
        await this.agentNavigate(sessionId, input.url);
      }
    } else if (action === 'select') {
      const page = handle.context.pages()[input.index || 0];
      if (!page) {
        throw new Error('Tab not found.');
      }
      handles.set(sessionId, { ...handle, page });
    } else if (action === 'close') {
      const pages = handle.context.pages();
      const page = pages[input.index ?? pages.indexOf(handle.page)];
      if (!page) {
        throw new Error('Tab not found.');
      }
      await page.close();
      handles.set(sessionId, { ...handle, page: handle.context.pages()[0] || await handle.context.newPage() });
    }
    const updatedHandle = handles.get(sessionId);
    await captureSession(session, updatedHandle?.page || handle.page, {
      name: `tabs:${action}`,
      details: { index: input.index ?? null, hasUrl: Boolean(input.url) },
    });
    return {
      session: publicSession(session),
      tabs: handle.context.pages().map((page: any, index: number) => ({
        index,
        url: page.url(),
        active: page === (updatedHandle?.page || handle.page),
      })),
    };
  },

  async stopSession(sessionId: string) {
    const session = sessions.get(sessionId);
    if (!session || session.ownerId !== AGENT_OWNER_ID) {
      return { stopped: false };
    }
    if (session.status !== 'ready') {
      return { stopped: false, session: publicSession(session) };
    }

    recordSessionAction(session, 'stop');
    await closeHandle(sessionId, 'manual-stop');
    session.status = 'stopped';
    session.runStatus = 'stopped';
    session.updatedAt = new Date().toISOString();
    session.message = 'Browser session stopped. Create a new session to continue browsing.';
    getBrowserRunLedger().finishRun(sessionId, {
      status: 'stopped',
      now: session.updatedAt,
      reason: 'manual-stop',
    });
    syncSessionFacilityState(session);
    return { stopped: true, session: publicSession(session) };
  },

  async deleteSession(sessionId: string) {
    const session = sessions.get(sessionId);
    if (!session || session.ownerId !== AGENT_OWNER_ID) {
      return { deleted: false };
    }

    recordSessionAction(session, 'delete');
    await closeHandle(sessionId, 'manual-delete');
    getBrowserRunLedger().finishRun(sessionId, {
      status: 'deleted',
      now: new Date().toISOString(),
      reason: 'manual-delete',
    });
    sessions.delete(sessionId);
    return { deleted: true, sessionId };
  },

  async agentStopSession(sessionId: string) {
    await this.getAgentSession(sessionId);
    return this.stopSession(sessionId);
  },

  async stopAllSessions() {
    await Promise.all([...sessions.keys()].map(async (sessionId) => {
      const session = sessions.get(sessionId);
      if (!session || session.status !== 'ready') return;
      recordSessionAction(session, 'shutdown');
      await closeHandle(sessionId, 'server-shutdown');
      session.status = 'stopped';
      session.runStatus = 'stopped';
      session.updatedAt = new Date().toISOString();
      session.message = 'Browser session stopped during server shutdown.';
      getBrowserRunLedger().finishRun(sessionId, {
        status: 'stopped',
        now: session.updatedAt,
        reason: 'server-shutdown',
      });
      syncSessionFacilityState(session);
    }));
  },
};

const sessionSweeper = setInterval(() => {
  void expireStaleSessions().catch((error) => {
    console.warn('[Browser] Session sweeper failed:', errorMessage(error));
  });
}, SESSION_SWEEP_INTERVAL_MS);
sessionSweeper.unref?.();

process.once('beforeExit', () => {
  void browserUseService.stopAllSessions();
});
