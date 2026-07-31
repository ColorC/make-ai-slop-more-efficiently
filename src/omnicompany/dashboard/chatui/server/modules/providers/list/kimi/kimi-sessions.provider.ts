import fsSync from 'node:fs';
import readline from 'node:readline';

import { sessionsDb } from '@/modules/database/index.js';
import type { IProviderSessions } from '@/shared/interfaces.js';
import type { AnyRecord, FetchHistoryOptions, FetchHistoryResult, NormalizedMessage } from '@/shared/types.js';
import {
  createNormalizedMessage,
  generateMessageId,
  normalizeProviderTimestamp,
  readJsonRecord,
  readObjectRecord,
  readOptionalString,
  sliceTailPage,
} from '@/shared/utils.js';

const PROVIDER = 'kimi';

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

const extractKimiPromptText = (input: unknown): string => {
  if (typeof input === 'string') {
    return input;
  }

  if (!Array.isArray(input)) {
    return '';
  }

  return input
    .map((part) => {
      if (typeof part === 'string') {
        return part;
      }

      const record = readObjectRecord(part);
      return readOptionalString(record?.text) ?? '';
    })
    .filter(Boolean)
    .join('\n');
};

const buildKimiTokenUsage = (totals: {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}): AnyRecord | undefined => {
  const displayInputTokens = totals.inputTokens + totals.cacheReadTokens;
  const used = totals.inputTokens
    + totals.outputTokens
    + totals.cacheReadTokens
    + totals.cacheWriteTokens;

  if (used <= 0) {
    return undefined;
  }

  return {
    used,
    inputTokens: displayInputTokens,
    outputTokens: totals.outputTokens,
    breakdown: {
      input: displayInputTokens,
      output: totals.outputTokens,
    },
  };
};

export class KimiSessionsProvider implements IProviderSessions {
  /**
   * Normalizes live `kimi -p <prompt> --output-format stream-json` events.
   *
   * The CLI emits one complete JSON message per line:
   * - `{"role":"assistant","content":"..."}` — a complete assistant reply.
   * - `{"role":"assistant","tool_calls":[{"id","function":{"name","arguments"}}]}`.
   * - `{"role":"tool","tool_call_id":"...","content":"..."}` — a tool result.
   * - `{"role":"meta","type":"session.resume_hint","session_id":"session_..."}` —
   *   the terminal line of a run (server/kimi-cli.js also uses it to capture the
   *   provider session id), so it maps to `stream_end`.
   */
  normalizeMessage(rawMessage: unknown, sessionId: string | null): NormalizedMessage[] {
    const raw = readObjectRecord(rawMessage);
    if (!raw) {
      return [];
    }

    const role = readOptionalString(raw.role);
    const type = readOptionalString(raw.type);
    const timestamp = normalizeProviderTimestamp(raw.time ?? raw.timestamp);

    if (role === 'meta') {
      if (type === 'session.resume_hint') {
        return [createNormalizedMessage({
          id: generateMessageId('kimi'),
          sessionId,
          timestamp,
          provider: PROVIDER,
          kind: 'stream_end',
        })];
      }

      return [];
    }

    if (role === 'assistant') {
      const toolCalls = Array.isArray(raw.tool_calls) ? raw.tool_calls : [];
      if (toolCalls.length > 0) {
        const messages: NormalizedMessage[] = [];
        for (const toolCallRaw of toolCalls) {
          const toolCall = readObjectRecord(toolCallRaw);
          const fn = readObjectRecord(toolCall?.function);
          const toolId = readOptionalString(toolCall?.id) ?? generateMessageId('kimi_tool');
          messages.push(createNormalizedMessage({
            id: toolId,
            sessionId,
            timestamp,
            provider: PROVIDER,
            kind: 'tool_use',
            toolName: readOptionalString(fn?.name) ?? 'Tool',
            toolInput: readJsonRecord(fn?.arguments) ?? {},
            toolId,
          }));
        }
        return messages;
      }

      const content = readOptionalString(raw.content);
      if (!content?.trim()) {
        return [];
      }

      return [createNormalizedMessage({
        id: generateMessageId('kimi'),
        sessionId,
        timestamp,
        provider: PROVIDER,
        kind: 'stream_delta',
        content,
      })];
    }

    if (role === 'tool') {
      return [createNormalizedMessage({
        id: generateMessageId('kimi_tool_result'),
        sessionId,
        timestamp,
        provider: PROVIDER,
        kind: 'tool_result',
        toolId: readOptionalString(raw.tool_call_id) ?? '',
        content: formatToolContent(raw.content),
        isError: false,
      })];
    }

    if (role === 'error' || type === 'error') {
      return [createNormalizedMessage({
        id: generateMessageId('kimi'),
        sessionId,
        timestamp,
        provider: PROVIDER,
        kind: 'error',
        content: readOptionalString(raw.error)
          ?? readOptionalString(raw.message)
          ?? readOptionalString(raw.content)
          ?? 'Unknown Kimi streaming error',
      })];
    }

    return [];
  }

  /**
   * Loads Kimi history from the session's on-disk `agents/main/wire.jsonl`
   * transcript (path resolved through the sessions index).
   */
  async fetchHistory(
    sessionId: string,
    options: FetchHistoryOptions = {},
  ): Promise<FetchHistoryResult> {
    const { limit = null, offset = 0 } = options;
    const wirePath = sessionsDb.getSessionById(sessionId)?.jsonl_path;
    if (!wirePath || !fsSync.existsSync(wirePath)) {
      return { messages: [], total: 0, hasMore: false, offset: 0, limit: null };
    }

    try {
      const { normalized, tokenUsage } = await this.readWireTranscript(wirePath, sessionId);

      const normalizedOffset = Math.max(0, offset);
      const normalizedLimit = limit === null ? null : Math.max(0, limit);
      const { page, hasMore } = sliceTailPage(normalized, normalizedLimit, normalizedOffset);
      let total = 0;
      for (const msg of normalized) {
        if (msg.kind !== 'tool_result') {
          total += 1;
        }
      }

      return {
        messages: page,
        total,
        hasMore,
        offset: normalizedOffset,
        limit: normalizedLimit,
        tokenUsage,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[KimiProvider] Failed to load session ${sessionId}:`, message);
      return { messages: [], total: 0, hasMore: false, offset: 0, limit: null };
    }
  }

  /**
   * Converts one wire.jsonl transcript into normalized messages.
   *
   * Relevant wire event shapes (protocol_version 1.4):
   * - `turn.prompt` — the real user prompt (`input: [{type:"text",text}]`).
   * - `context.append_loop_event` with `event.type`:
   *   - `content.part` — assistant output (`part.type` `text`/`think`).
   *   - `tool.call` — a tool invocation (`name`, `args`, `toolCallId`).
   *   - `tool.result` — a tool result (`result.output`, `result.isError`).
   * - `usage.record` — per-turn token usage (`usage.inputOther/output/
   *   inputCacheRead/inputCacheCreation`).
   *
   * `context.append_message` rows are skipped: they duplicate `turn.prompt`
   * for real user input and also carry injected system reminders, which are
   * not chat content.
   */
  private async readWireTranscript(
    wirePath: string,
    sessionId: string,
  ): Promise<{ normalized: NormalizedMessage[]; tokenUsage?: AnyRecord }> {
    const normalized: NormalizedMessage[] = [];

    let inputTokens = 0;
    let outputTokens = 0;
    let cacheReadTokens = 0;
    let cacheWriteTokens = 0;
    let lineIndex = 0;

    const fileStream = fsSync.createReadStream(wirePath);
    const lineReader = readline.createInterface({
      input: fileStream,
      crlfDelay: Infinity,
    });

    for await (const line of lineReader) {
      const trimmed = line.trim();
      if (!trimmed) {
        continue;
      }

      let entry: AnyRecord;
      try {
        entry = JSON.parse(trimmed) as AnyRecord;
      } catch {
        continue;
      }

      lineIndex += 1;
      const timestamp = normalizeProviderTimestamp(entry.time ?? entry.created_at);
      const entryType = readOptionalString(entry.type);

      if (entryType === 'turn.prompt') {
        const content = extractKimiPromptText(entry.input);
        if (content.trim()) {
          normalized.push(createNormalizedMessage({
            id: `kimi_prompt_${lineIndex}`,
            sessionId,
            timestamp,
            provider: PROVIDER,
            kind: 'text',
            role: 'user',
            content,
          }));
        }
        continue;
      }

      if (entryType === 'usage.record') {
        const usage = readObjectRecord(entry.usage);
        if (usage) {
          inputTokens += Number(usage.inputOther ?? 0);
          outputTokens += Number(usage.output ?? 0);
          cacheReadTokens += Number(usage.inputCacheRead ?? 0);
          cacheWriteTokens += Number(usage.inputCacheCreation ?? 0);
        }
        continue;
      }

      if (entryType !== 'context.append_loop_event') {
        continue;
      }

      const event = readObjectRecord(entry.event);
      const eventType = readOptionalString(event?.type);
      if (!event || !eventType) {
        continue;
      }

      if (eventType === 'content.part') {
        const part = readObjectRecord(event.part);
        const partType = readOptionalString(part?.type);
        const baseId = readOptionalString(event.uuid) ?? `kimi_part_${lineIndex}`;

        if (partType === 'text') {
          const content = readOptionalString(part?.text) ?? '';
          if (content.trim()) {
            normalized.push(createNormalizedMessage({
              id: baseId,
              sessionId,
              timestamp,
              provider: PROVIDER,
              kind: 'text',
              role: 'assistant',
              content,
            }));
          }
        } else if (partType === 'think') {
          const content = readOptionalString(part?.think) ?? '';
          if (content.trim()) {
            normalized.push(createNormalizedMessage({
              id: baseId,
              sessionId,
              timestamp,
              provider: PROVIDER,
              kind: 'thinking',
              content,
            }));
          }
        }
        continue;
      }

      if (eventType === 'tool.call') {
        const toolId = readOptionalString(event.toolCallId)
          ?? readOptionalString(event.uuid)
          ?? `kimi_tool_${lineIndex}`;
        normalized.push(createNormalizedMessage({
          id: readOptionalString(event.uuid) ?? toolId,
          sessionId,
          timestamp,
          provider: PROVIDER,
          kind: 'tool_use',
          toolName: readOptionalString(event.name) ?? 'Tool',
          toolInput: event.args ?? {},
          toolId,
        }));
        continue;
      }

      if (eventType === 'tool.result') {
        const result = readObjectRecord(event.result);
        const toolId = readOptionalString(event.toolCallId)
          ?? readOptionalString(event.parentUuid)
          ?? '';
        normalized.push(createNormalizedMessage({
          id: `${readOptionalString(event.parentUuid) ?? `kimi_tool_result_${lineIndex}`}_result`,
          sessionId,
          timestamp,
          provider: PROVIDER,
          kind: 'tool_result',
          toolId,
          content: formatToolContent(result?.output ?? event.result),
          isError: Boolean(result?.isError),
        }));
        continue;
      }
    }

    // Attach tool results to their tool_use rows so the UI renders one row.
    const toolResultMap = new Map<string, NormalizedMessage>();
    for (const msg of normalized) {
      if (msg.kind === 'tool_result' && msg.toolId) {
        toolResultMap.set(msg.toolId, msg);
      }
    }
    for (const msg of normalized) {
      if (msg.kind === 'tool_use' && msg.toolId && toolResultMap.has(msg.toolId)) {
        const toolResult = toolResultMap.get(msg.toolId);
        if (toolResult) {
          msg.toolResult = { content: toolResult.content, isError: toolResult.isError };
        }
      }
    }

    return {
      normalized,
      tokenUsage: buildKimiTokenUsage({ inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens }),
    };
  }
}
