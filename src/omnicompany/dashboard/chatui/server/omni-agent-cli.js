import { spawn } from 'child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import crossSpawn from 'cross-spawn';

import { sessionsService } from './modules/providers/services/sessions.service.js';
import { providerModelsService } from './modules/providers/services/provider-models.service.js';
import { notifyRunFailed, notifyRunStopped } from './services/notification-orchestrator.js';
import { createCompleteMessage, createNormalizedMessage } from './shared/utils.js';

const spawnFunction = process.platform === 'win32' ? crossSpawn : spawn;

// omni_agent is an external Python subprocess provider (spawn-CLI mode, like
// gemini/opencode). The spawned CLI lives in the omnicompany repo and is run as
// `python -m omnicompany.dashboard.ccdaemon.providers.omni_agent_cli`. It needs
// the omnicompany package importable, so PYTHONPATH must include its src dir.
// 健壮可移植解析(同目录 controller-cli.js 同款模式): 本文件在
// .../src/omnicompany/dashboard/chatui/server/, 上溯 4 级即仓内 `src` 目录。
// 不再硬编码本机 E: 盘绝对路径(换机/换盘符即坏)。OMNICOMPANY_SRC 环境变量仍可覆盖。
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_OMNICOMPANY_SRC = path.resolve(__dirname, '..', '..', '..', '..');
const OMNI_AGENT_MODULE = 'omnicompany.dashboard.ccdaemon.providers.omni_agent_cli';
const DEFAULT_OMNI_AGENT_MODEL = 'qwen3.6-plus';

const activeOmniAgentProcesses = new Map();

function resolveOmniPython() {
  return (process.env.OMNI_PYTHON || '').trim() || 'python';
}

function buildOmniAgentEnv() {
  const env = { ...process.env };
  const omniSrc = (process.env.OMNICOMPANY_SRC || '').trim() || DEFAULT_OMNICOMPANY_SRC;
  // Prepend our src so the omnicompany package resolves even when the server was
  // launched without the daemon's shell profile on PYTHONPATH.
  env.PYTHONPATH = env.PYTHONPATH
    ? `${omniSrc}${process.platform === 'win32' ? ';' : ':'}${env.PYTHONPATH}`
    : omniSrc;
  // Force UTF-8 so the NDJSON stdout protocol round-trips non-ASCII content.
  env.PYTHONIOENCODING = env.PYTHONIOENCODING || 'utf-8';
  return env;
}

function readOmniAgentSessionId(event) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  // The daemon emits `newSessionId` on session_created and `sessionId` on every
  // line; either one can carry the canonical id for a brand-new session.
  return event.newSessionId || event.sessionId || null;
}

async function spawnOmniAgent(command, options = {}, ws) {
  return new Promise((resolve, reject) => {
    const { sessionId, projectPath, cwd, model, sessionSummary } = options;
    const workingDir = cwd || projectPath || process.cwd();
    const processKey = sessionId || Date.now().toString();
    let capturedSessionId = sessionId || null;
    let sessionCreatedSent = false;
    let stdoutLineBuffer = '';
    let terminalNotificationSent = false;
    let omniAgentProcess = null;
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
          provider: 'omni_agent',
          sessionId: finalSessionId,
          sessionName: sessionSummary,
          stopReason: 'completed',
        });
        return;
      }

      notifyRunFailed({
        userId: ws?.userId || null,
        provider: 'omni_agent',
        sessionId: finalSessionId,
        sessionName: sessionSummary,
        error: error || `omni_agent exited with code ${code}`,
      });
    };

    const registerSession = (nextSessionId) => {
      if (!nextSessionId || capturedSessionId === nextSessionId) {
        return;
      }

      capturedSessionId = nextSessionId;
      if (processKey !== capturedSessionId && omniAgentProcess) {
        activeOmniAgentProcesses.delete(processKey);
        activeOmniAgentProcesses.set(capturedSessionId, omniAgentProcess);
      }
      if (omniAgentProcess) {
        omniAgentProcess.sessionId = capturedSessionId;
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
          provider: 'omni_agent',
        }));
      }
    };

    const processOmniAgentOutputLine = (line) => {
      if (!line || !line.trim()) {
        return;
      }

      let response;
      try {
        response = JSON.parse(line);
      } catch {
        // Non-JSON stdout is unexpected from the daemon, but rather than drop it
        // we surface it as a stream delta (same fallback as the opencode driver).
        ws.send(createNormalizedMessage({
          kind: 'stream_delta',
          content: line,
          sessionId: capturedSessionId || sessionId || null,
          provider: 'omni_agent',
        }));
        return;
      }

      try {
        registerSession(readOmniAgentSessionId(response));

        // The daemon already emits NormalizedMessage-shaped lines; the provider's
        // normalizeMessage aligns/passes them through into the app envelope. The
        // terminal complete is emitted from the close handler (see below), so the
        // daemon's own `complete` line is dropped here to keep exactly one.
        if (response && response.kind === 'complete') {
          return;
        }

        const normalized = sessionsService.normalizeMessage(
          'omni_agent',
          response,
          capturedSessionId || sessionId || null,
        );
        for (const msg of normalized) {
          ws.send(msg);
        }
      } catch (error) {
        const errorContent = error instanceof Error ? error.message : String(error);
        console.error('[OmniAgent] Failed to process JSON output:', errorContent);
        ws.send(createNormalizedMessage({
          kind: 'error',
          content: errorContent,
          sessionId: capturedSessionId || sessionId || null,
          provider: 'omni_agent',
        }));
      }
    };

    void providerModelsService.resolveResumeModel('omni_agent', sessionId, model).then((resolvedModel) => {
      const pythonExe = resolveOmniPython();
      const args = ['-m', OMNI_AGENT_MODULE];

      // 关键: 用**中性 cwd**(omnicompany src)起子进程, 不能用项目目录 workingDir。
      // 否则 python 进程的 sys.path[0]=项目目录、且 omnicompany 配置/LLM 客户端的
      // load_dotenv() 会就近加载到**项目自己的 .env**(覆盖 THE_COMPANY 配置)+ 项目根模块
      // 抢占 import —— 实测在大型项目(如 quant-lab, 自带 .env)下会让 qwen 调用挂死、
      // 永远不回复。项目路径仍通过 stdin payload 的 cwd 传给 agent 用作上下文。
      const omniSrc = (process.env.OMNICOMPANY_SRC || '').trim() || DEFAULT_OMNICOMPANY_SRC;
      omniAgentProcess = spawnFunction(pythonExe, args, {
        cwd: omniSrc,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: buildOmniAgentEnv(),
      });

      activeOmniAgentProcesses.set(processKey, omniAgentProcess);
      omniAgentProcess.sessionId = processKey;

      // Input protocol: a single JSON object on stdin (UTF-8), then close stdin.
      const inputPayload = {
        prompt: command || '',
        model: resolvedModel || DEFAULT_OMNI_AGENT_MODEL,
        cwd: workingDir,
        options: {
          ...(options.options ?? {}),
          sessionId: sessionId || null,
          resume: Boolean(options.resume),
        },
      };
      try {
        omniAgentProcess.stdin.write(JSON.stringify(inputPayload), 'utf8');
        omniAgentProcess.stdin.end();
      } catch (error) {
        // A failed stdin write surfaces through the 'error'/'close' handlers below.
        console.error('[OmniAgent] Failed to write prompt to stdin:', error);
      }

      omniAgentProcess.stdout.on('data', (data) => {
        stdoutLineBuffer += data.toString('utf8');
        const completeLines = stdoutLineBuffer.split(/\r?\n/);
        stdoutLineBuffer = completeLines.pop() || '';

        completeLines.forEach((line) => {
          processOmniAgentOutputLine(line.trim());
        });
      });

      omniAgentProcess.stderr.on('data', () => {
        // stderr is the daemon's log channel — ignore it so logs never leak into
        // the normalized message stream.
      });

      omniAgentProcess.on('close', async (code) => {
        const finalSessionId = capturedSessionId || sessionId || processKey;
        activeOmniAgentProcesses.delete(finalSessionId);
        activeOmniAgentProcesses.delete(processKey);

        if (stdoutLineBuffer.trim()) {
          processOmniAgentOutputLine(stdoutLineBuffer.trim());
          stdoutLineBuffer = '';
        }

        // Terminal complete — skipped for aborted runs. Per the gateway contract
        // (chat-websocket.service.ts handleChatAbort + chat-run-registry), the WS
        // layer emits the aborted `complete` itself and drops any duplicate, so
        // runtimes must NOT emit their own on abort. Normal completion emits here.
        if (!completeSent && !omniAgentProcess.aborted) {
          completeSent = true;
          ws.send(createCompleteMessage({ provider: 'omni_agent', sessionId: finalSessionId, exitCode: code }));
        }

        if (code === 0) {
          notifyTerminalState({ code });
          resolve();
          return;
        }

        notifyTerminalState({ code });
        reject(new Error(code === null ? 'omni_agent process was terminated' : `omni_agent exited with code ${code}`));
      });

      omniAgentProcess.on('error', async (error) => {
        const finalSessionId = capturedSessionId || sessionId || processKey;
        activeOmniAgentProcesses.delete(finalSessionId);
        activeOmniAgentProcesses.delete(processKey);

        ws.send(createNormalizedMessage({
          kind: 'error',
          content: error.message,
          sessionId: finalSessionId,
          provider: 'omni_agent',
        }));
        if (!completeSent && !omniAgentProcess.aborted) {
          completeSent = true;
          ws.send(createCompleteMessage({ provider: 'omni_agent', sessionId: finalSessionId, exitCode: 1 }));
        }
        notifyTerminalState({ error });
        reject(error);
      });
    }).catch(reject);
  });
}

function abortOmniAgentSession(sessionId) {
  const proc = activeOmniAgentProcesses.get(sessionId);
  if (!proc) {
    return false;
  }

  // The abort handler sends the terminal complete (aborted: true); flag the
  // process so its close handler does not emit a second one.
  proc.aborted = true;
  proc.kill('SIGTERM');
  activeOmniAgentProcesses.delete(sessionId);
  return true;
}

function isOmniAgentSessionActive(sessionId) {
  return activeOmniAgentProcesses.has(sessionId);
}

function getActiveOmniAgentSessions() {
  return Array.from(activeOmniAgentProcesses.keys());
}

export {
  spawnOmniAgent,
  abortOmniAgentSession,
  isOmniAgentSessionActive,
  getActiveOmniAgentSessions,
};
