/**
 * Controller (总控) provider runtime.
 *
 * The controller is NOT a new runtime. It is exactly the local Claude Code
 * runtime (server/claude-sdk.js, @anthropic-ai/claude-agent-sdk) with two
 * deltas:
 *   1) the BOSS SIGHT 总控 system prompt is appended on top of the claude_code
 *      preset (single source of truth lives in omnicompany — see below), and
 *   2) the model is forced to opus.
 *
 * Everything else — streaming, tools (claude_code preset, incl. Bash for the
 * `omni` CLI), permission prompts, token budget, abort — is reused verbatim by
 * delegating to queryClaudeSDK / abortClaudeSDKSession. We do NOT spawn a python
 * shim and we do NOT touch ccdaemon / boss_sight (we only READ system.md).
 */

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { queryClaudeSDK, abortClaudeSDKSession } from './claude-sdk.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Single source of truth for the 总控 system prompt is maintained by omnicompany at
// .../dashboard/boss_sight/controller/prompts/system.md. chatui lives at
// .../dashboard/chatui/server, so the default relative path climbs out of
// chatui and into boss_sight. The path can be overridden with the
// OMNI_CONTROLLER_SYSTEM_MD env var. We never copy the prompt content into
// chatui — it is read at runtime.
const DEFAULT_CONTROLLER_SYSTEM_MD = path.resolve(
  __dirname,
  '..',
  '..',
  'boss_sight',
  'controller',
  'prompts',
  'system.md',
);

// Appended after the system.md body to make the runtime contract explicit to the
// controller agent regardless of how system.md evolves.
const CONTROLLER_RUNTIME_REMINDER =
  '你以本地 Claude Code(opus)运行;调度/审阅/提议一律用 Bash 跑 `omni` CLI(已在 PATH)。';

// Fallback used only when system.md cannot be read. Intentionally minimal — the
// authoritative prompt is the omnicompany-maintained file.
const FALLBACK_CONTROLLER_SYSTEM_PROMPT =
  '你是 omnicompany BOSS SIGHT 的总控 agent。你协调 subagent 完成任务:起草/调整计划与待办、派活与调度、整理产出提交审阅、监督进程。你自己不执行任务、不写改代码。';

function resolveControllerSystemMdPath() {
  const override = (process.env.OMNI_CONTROLLER_SYSTEM_MD || '').trim();
  return override || DEFAULT_CONTROLLER_SYSTEM_MD;
}

/**
 * Builds the full system-prompt append: the omnicompany-maintained 总控 prompt
 * (read from system.md, or a minimal fallback) plus the runtime reminder.
 * @returns {Promise<string>}
 */
async function buildControllerSystemPromptAppend() {
  const systemMdPath = resolveControllerSystemMdPath();
  let body;
  try {
    body = await readFile(systemMdPath, 'utf8');
  } catch (error) {
    console.warn(
      `[Controller] Could not read 总控 system prompt at "${systemMdPath}" (${error?.message || error}). `
      + 'Falling back to minimal built-in prompt.',
    );
    body = FALLBACK_CONTROLLER_SYSTEM_PROMPT;
  }

  return `${body.trim()}\n\n${CONTROLLER_RUNTIME_REMINDER}`;
}

/**
 * Controller run entry point. Thin wrapper over queryClaudeSDK: same options,
 * plus a forced model and the 总控 system-prompt append.
 * @param {string} command - User prompt/command
 * @param {Object} options - Query options (same shape claude-sdk.js expects)
 * @param {Object} writer - Gateway writer (ws-compatible)
 * @returns {Promise<void>}
 */
async function queryController(command, options = {}, writer) {
  const systemPromptAppend = await buildControllerSystemPromptAppend();

  return queryClaudeSDK(
    command,
    {
      ...options,
      // 总控 always runs on opus. claude-sdk.js threads this through
      // resolveResumeModel → mapCliOptionsToSDK, which honors options.model.
      model: 'opus',
      systemPromptAppend,
    },
    writer,
  );
}

/**
 * Aborts an in-flight controller run. Controller sessions are plain Claude SDK
 * sessions (same activeSessions map inside claude-sdk.js), so abort is reused
 * directly.
 * @param {string} sessionId - Provider-native session id
 * @returns {Promise<boolean>}
 */
function abortControllerSession(sessionId) {
  return abortClaudeSDKSession(sessionId);
}

export { queryController, abortControllerSession };
