import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

import crossSpawn from 'cross-spawn';

import { sessionsService } from './modules/providers/services/sessions.service.js';
import { providerAuthService } from './modules/providers/services/provider-auth.service.js';
import { providerModelsService } from './modules/providers/services/provider-models.service.js';
import { notifyRunFailed, notifyRunStopped } from './services/notification-orchestrator.js';
import { createCompleteMessage, createNormalizedMessage } from './shared/utils.js';

const spawnFunction = process.platform === 'win32' ? crossSpawn : spawn;

/**
 * On Windows the npm `kimi.cmd` shim re-parses argv through cmd.exe, which
 * truncates multi-line prompts at the first newline (plan/context injection
 * always contains newlines). Bypass the shim: spawn node on the real entry
 * (verified on kimi-code 0.27.0 — multi-line prompt arrives intact).
 */
function resolveKimiLaunch() {
  if (process.platform === 'win32' && process.env.APPDATA) {
    const direct = path.join(
      process.env.APPDATA, 'npm', 'node_modules', '@moonshot-ai', 'kimi-code', 'dist', 'main.mjs',
    );
    if (fs.existsSync(direct)) {
      return { command: process.execPath, argsPrefix: [direct] };
    }
  }
  return { command: 'kimi', argsPrefix: [] };
}

const activeKimiProcesses = new Map();

/**
 * The provider session id only appears on the terminal
 * `{"role":"meta","type":"session.resume_hint","session_id":"session_..."}`
 * line of the stream-json output.
 */
function readKimiSessionId(event) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  if (event.role === 'meta' && event.type === 'session.resume_hint') {
    return typeof event.session_id === 'string' ? event.session_id : null;
  }

  return null;
}

async function spawnKimi(command, options = {}, ws) {
  return new Promise((resolve, reject) => {
    const { sessionId, projectPath, cwd, model, sessionSummary } = options;
    const workingDir = cwd || projectPath || process.cwd();
    const processKey = sessionId || Date.now().toString();
    let capturedSessionId = sessionId || null;
    let sessionCreatedSent = false;
    let stdoutLineBuffer = '';
    let terminalNotificationSent = false;
    let kimiProcess = null;
    // Unified lifecycle contract: exactly one terminal `complete` per run
    // (close and error handlers can both fire for spawn failures).
    let completeSent = false;

    const notifyTerminalState = ({ code = null, error = null } = {}) => {
      if (terminalNotificationSent) {
        return;
      }

      terminalNotificationSent = true;
      const finalSessionId = capturedSessionId || sessionId || processKey;
      if (code === 0 && !error) {
        notifyRunStopped({
          userId: ws?.userId || null,
          provider: 'kimi',
          sessionId: finalSessionId,
          sessionName: sessionSummary,
          stopReason: 'completed',
        });
        return;
      }

      notifyRunFailed({
        userId: ws?.userId || null,
        provider: 'kimi',
        sessionId: finalSessionId,
        sessionName: sessionSummary,
        error: error || `Kimi CLI exited with code ${code}`,
      });
    };

    const registerSession = (nextSessionId) => {
      if (!nextSessionId || capturedSessionId === nextSessionId) {
        return;
      }

      capturedSessionId = nextSessionId;
      if (processKey !== capturedSessionId && kimiProcess) {
        activeKimiProcesses.delete(processKey);
        activeKimiProcesses.set(capturedSessionId, kimiProcess);
      }
      if (kimiProcess) {
        kimiProcess.sessionId = capturedSessionId;
      }

      if (ws.setSessionId && typeof ws.setSessionId === 'function') {
        ws.setSessionId(capturedSessionId);
      }

      if (!sessionId && !sessionCreatedSent) {
        sessionCreatedSent = true;
        ws.send(createNormalizedMessage({
          kind: 'session_created',
          newSessionId: capturedSessionId,
          sessionId: capturedSessionId,
          provider: 'kimi',
        }));
      }
    };

    const processKimiOutputLine = (line) => {
      if (!line || !line.trim()) {
        return;
      }

      let response;
      try {
        response = JSON.parse(line);
      } catch {
        ws.send(createNormalizedMessage({
          kind: 'stream_delta',
          content: line,
          sessionId: capturedSessionId || sessionId || null,
          provider: 'kimi',
        }));
        return;
      }

      try {
        registerSession(readKimiSessionId(response));
        const normalized = sessionsService.normalizeMessage(
          'kimi',
          response,
          capturedSessionId || sessionId || null,
        );
        for (const msg of normalized) {
          ws.send(msg);
        }
      } catch (error) {
        const errorContent = error instanceof Error ? error.message : String(error);
        console.error('[Kimi] Failed to process JSON output:', errorContent);
        ws.send(createNormalizedMessage({
          kind: 'error',
          content: errorContent,
          sessionId: capturedSessionId || sessionId || null,
          provider: 'kimi',
        }));
      }
    };

    void providerModelsService.resolveResumeModel('kimi', sessionId, model).then((resolvedModel) => {
      // Kimi Code non-interactive mode: `kimi -p <prompt> --output-format
      // stream-json`, resumed with `-S <session_id>`, model with `-m <alias>`.
      const args = ['--output-format', 'stream-json'];
      if (sessionId) {
        args.push('-S', sessionId);
      }
      if (resolvedModel) {
        args.push('-m', resolvedModel);
      }
      args.push('-p', command && command.trim() ? command.trim() : '');

      const launch = resolveKimiLaunch();
      kimiProcess = spawnFunction(launch.command, [...launch.argsPrefix, ...args], {
        cwd: workingDir,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env },
      });

      activeKimiProcesses.set(processKey, kimiProcess);
      kimiProcess.sessionId = processKey;
      kimiProcess.stdin.end();

      kimiProcess.stdout.on('data', (data) => {
        stdoutLineBuffer += data.toString();
        const completeLines = stdoutLineBuffer.split(/\r?\n/);
        stdoutLineBuffer = completeLines.pop() || '';

        completeLines.forEach((line) => {
          processKimiOutputLine(line.trim());
        });
      });

      kimiProcess.stderr.on('data', (data) => {
        const stderrText = data.toString();
        if (!stderrText.trim()) {
          return;
        }

        ws.send(createNormalizedMessage({
          kind: 'error',
          content: stderrText,
          sessionId: capturedSessionId || sessionId || null,
          provider: 'kimi',
        }));
      });

      kimiProcess.on('close', async (code) => {
        const finalSessionId = capturedSessionId || sessionId || processKey;
        activeKimiProcesses.delete(finalSessionId);
        activeKimiProcesses.delete(processKey);

        if (stdoutLineBuffer.trim()) {
          processKimiOutputLine(stdoutLineBuffer.trim());
          stdoutLineBuffer = '';
        }

        // Terminal complete — skipped for aborted runs (abort-session
        // already sent the aborted complete on this run's behalf).
        if (!completeSent && !kimiProcess.aborted) {
          completeSent = true;
          ws.send(createCompleteMessage({ provider: 'kimi', sessionId: finalSessionId, exitCode: code }));
        }

        if (code === 0) {
          notifyTerminalState({ code });
          resolve();
          return;
        }

        if (code === 127 || code === null) {
          const installed = await providerAuthService.isProviderInstalled('kimi');
          if (!installed) {
            ws.send(createNormalizedMessage({
              kind: 'error',
              content: 'Kimi CLI is not installed. Install it from https://moonshotai.github.io/kimi-code/',
              sessionId: finalSessionId,
              provider: 'kimi',
            }));
          }
        }

        notifyTerminalState({ code });
        reject(new Error(code === null ? 'Kimi CLI process was terminated' : `Kimi CLI exited with code ${code}`));
      });

      kimiProcess.on('error', async (error) => {
        const finalSessionId = capturedSessionId || sessionId || processKey;
        activeKimiProcesses.delete(finalSessionId);
        activeKimiProcesses.delete(processKey);

        const installed = await providerAuthService.isProviderInstalled('kimi');
        const errorContent = !installed
          ? 'Kimi CLI is not installed. Install it from https://moonshotai.github.io/kimi-code/'
          : error.message;

        ws.send(createNormalizedMessage({
          kind: 'error',
          content: errorContent,
          sessionId: finalSessionId,
          provider: 'kimi',
        }));
        if (!completeSent && !kimiProcess.aborted) {
          completeSent = true;
          ws.send(createCompleteMessage({ provider: 'kimi', sessionId: finalSessionId, exitCode: 1 }));
        }
        notifyTerminalState({ error });
        reject(error);
      });
    }).catch(reject);
  });
}

function abortKimiSession(sessionId) {
  const process = activeKimiProcesses.get(sessionId);
  if (!process) {
    return false;
  }

  // The abort handler sends the terminal complete (aborted: true); flag the
  // process so its close handler does not emit a second one.
  process.aborted = true;
  process.kill('SIGTERM');
  activeKimiProcesses.delete(sessionId);
  return true;
}

function isKimiSessionActive(sessionId) {
  return activeKimiProcesses.has(sessionId);
}

function getActiveKimiSessions() {
  return Array.from(activeKimiProcesses.keys());
}

export {
  spawnKimi,
  abortKimiSession,
  isKimiSessionActive,
  getActiveKimiSessions,
};
