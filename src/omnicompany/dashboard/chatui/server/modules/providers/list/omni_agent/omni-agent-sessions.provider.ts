import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';

import type { IProviderSessions } from '@/shared/interfaces.js';
import type { AnyRecord, FetchHistoryOptions, FetchHistoryResult, NormalizedMessage } from '@/shared/types.js';
import {
  createNormalizedMessage,
  generateMessageId,
  readObjectRecord,
  readOptionalString,
  sliceTailPage,
} from '@/shared/utils.js';

const PROVIDER = 'omni_agent';

// on-disk 历史路径 —— 与 shim omni_agent_cli.py 的 _encode_cwd / _omni_agent_projects_dir
// 逐字一致: ~/.omni_agent/projects/<encoded-cwd>/<sessionId>.jsonl。shim 写, 这里读。
// 编码: cwd 里的 / \ : 全替成 -(空则 _), 让 Windows 盘符/正反斜杠不影响命名一致性。
const encodeCwd = (cwd: string): string => ((cwd || '').trim() || '_').replace(/[/\\:]/g, '-');

const omniAgentJsonlPath = (projectPath: string, sessionId: string): string =>
  path.join(os.homedir(), '.omni_agent', 'projects', encodeCwd(projectPath), `${sessionId}.jsonl`);

const formatToolContent = (value: unknown): string => {
  if (value === undefined || value === null) {
    return '';
  }

  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

export class OmniAgentSessionsProvider implements IProviderSessions {
  /**
   * Normalizes one NDJSON line emitted by the omni_agent Python daemon into the
   * app's normalized message envelope.
   *
   * The daemon already speaks the NormalizedMessage `kind` vocabulary
   * (text/thinking/tool_use/tool_result/error), so this method mostly aligns and
   * forwards fields into `createNormalizedMessage`. Lifecycle kinds
   * (`session_created`, `complete`) are owned by the CLI driver and intentionally
   * not re-emitted here.
   */
  normalizeMessage(rawMessage: unknown, sessionId: string | null): NormalizedMessage[] {
    const raw = readObjectRecord(rawMessage);
    if (!raw) {
      return [];
    }

    const kind = readOptionalString(raw.kind);
    if (!kind) {
      return [];
    }

    const eventSessionId = readOptionalString(raw.sessionId) ?? sessionId;
    const baseId = readOptionalString(raw.id) ?? generateMessageId(PROVIDER);

    if (kind === 'user') {
      // 历史里用户那一句(实时流不发 user, 由落盘转写补)。本仓无 'user' MessageKind,
      // 渲染成 role=user 的 text 气泡(与 claude 历史里 user 文本同形)。
      const content = readOptionalString(raw.content) ?? '';
      if (!content.trim()) {
        return [];
      }
      return [createNormalizedMessage({
        id: baseId,
        sessionId: eventSessionId,
        provider: PROVIDER,
        kind: 'text',
        role: 'user',
        content,
      })];
    }

    if (kind === 'text') {
      const content = readOptionalString(raw.content) ?? '';
      if (!content.trim()) {
        return [];
      }

      return [createNormalizedMessage({
        id: baseId,
        sessionId: eventSessionId,
        provider: PROVIDER,
        kind: 'text',
        role: 'assistant',
        content,
      })];
    }

    if (kind === 'thinking') {
      const content = readOptionalString(raw.content) ?? '';
      if (!content.trim()) {
        return [];
      }

      return [createNormalizedMessage({
        id: baseId,
        sessionId: eventSessionId,
        provider: PROVIDER,
        kind: 'thinking',
        content,
      })];
    }

    if (kind === 'tool_use') {
      return [createNormalizedMessage({
        id: baseId,
        sessionId: eventSessionId,
        provider: PROVIDER,
        kind: 'tool_use',
        toolName: readOptionalString(raw.toolName) ?? 'Tool',
        toolInput: raw.input ?? {},
        toolId: readOptionalString(raw.toolId) ?? baseId,
      })];
    }

    if (kind === 'tool_result') {
      return [createNormalizedMessage({
        id: baseId,
        sessionId: eventSessionId,
        provider: PROVIDER,
        kind: 'tool_result',
        toolId: readOptionalString(raw.toolId) ?? baseId,
        toolResult: {
          content: formatToolContent(raw.result),
          isError: raw.isError === true,
        },
      })];
    }

    if (kind === 'error') {
      return [createNormalizedMessage({
        id: baseId,
        sessionId: eventSessionId,
        provider: PROVIDER,
        kind: 'error',
        content: readOptionalString(raw.error) ?? readOptionalString(raw.content) ?? 'Unknown omni_agent error',
      })];
    }

    // session_created / complete are emitted by the CLI driver, and any unknown
    // kind is dropped rather than forwarded raw.
    return [];
  }

  /**
   * D4: omni_agent 现在把对话落成 ~/.omni_agent/projects/<encoded-cwd>/<sessionId>.jsonl
   * (shim omni_agent_cli.py 写)。刷新时从该文件还原历史 —— 镜像 claude:
   * 读 JSONL → 按 providerSessionId filter → 时间排序 → normalizeMessage →
   * tool_result 并入对应 tool_use → sliceTailPage 分页。
   */
  async fetchHistory(
    sessionId: string,
    options: FetchHistoryOptions = {},
  ): Promise<FetchHistoryResult> {
    const { limit = null, offset = 0, projectPath = '' } = options;
    const providerSessionId = options.providerSessionId ?? sessionId;
    const empty: FetchHistoryResult = { messages: [], total: 0, hasMore: false, offset: 0, limit: null };
    if (!providerSessionId) {
      return empty;
    }

    const jsonlPath = omniAgentJsonlPath(projectPath, providerSessionId);
    const rawLines: AnyRecord[] = [];
    try {
      if (!fs.existsSync(jsonlPath)) {
        return empty;
      }
      const rl = readline.createInterface({
        input: fs.createReadStream(jsonlPath),
        crlfDelay: Infinity,
      });
      for await (const line of rl) {
        if (!line.trim()) {
          continue;
        }
        try {
          const entry = JSON.parse(line) as AnyRecord;
          if (entry && entry.sessionId === providerSessionId) {
            rawLines.push(entry);
          }
        } catch {
          // 跳过并发 append 时读到的半行(同 claude getSessionMessages)。
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[OmniAgent] fetchHistory read failed for ${providerSessionId}:`, message);
      return empty;
    }

    rawLines.sort(
      (a, b) =>
        new Date((a.timestamp as string) || 0).getTime() - new Date((b.timestamp as string) || 0).getTime(),
    );

    // tool_result 并入对应 tool_use(镜像 claude 历史形态), 不作为独立消息。
    const toolResultMap = new Map<string, { content: string; isError: boolean }>();
    for (const raw of rawLines) {
      if (raw.kind === 'tool_result' && raw.toolId) {
        toolResultMap.set(String(raw.toolId), {
          content: formatToolContent(raw.result),
          isError: raw.isError === true,
        });
      }
    }

    const normalized: NormalizedMessage[] = [];
    for (const raw of rawLines) {
      if (raw.kind === 'tool_result') {
        continue; // 已并入 tool_use
      }
      normalized.push(...this.normalizeMessage(raw, sessionId));
    }
    for (const msg of normalized) {
      if (msg.kind === 'tool_use' && msg.toolId && toolResultMap.has(msg.toolId)) {
        msg.toolResult = toolResultMap.get(msg.toolId);
      }
    }

    const total = normalized.length;
    const normalizedOffset = Math.max(0, offset);
    const normalizedLimit = limit === null ? null : Math.max(0, limit);
    const { page, hasMore } = sliceTailPage(normalized, normalizedLimit, normalizedOffset);
    return {
      messages: page,
      total,
      hasMore,
      offset: normalizedOffset,
      limit: normalizedLimit,
    };
  }
}
