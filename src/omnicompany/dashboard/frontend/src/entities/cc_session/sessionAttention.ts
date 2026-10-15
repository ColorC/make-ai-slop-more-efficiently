import { create } from 'zustand'
import type { CcTabRunState, CcTabSessionMetaLite } from './tabStatus'

const STORAGE_KEY = 'omni.cc.seenCompletions.v1'

type SeenCompletions = Record<string, string>

function browserStorage(): Storage | undefined {
  if (typeof window === 'undefined') return undefined
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

function readSeenCompletions(storage?: Storage): SeenCompletions {
  if (!storage) return {}
  try {
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
    )
  } catch {
    return {}
  }
}

function writeSeenCompletions(seen: SeenCompletions): void {
  try {
    browserStorage()?.setItem(STORAGE_KEY, JSON.stringify(seen))
  } catch {
    // Attention remains correct for this page even when storage is unavailable.
  }
}

function compactHash(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

/** One stable identity for the latest completed turn; a later turn produces a new identity. */
export function sessionCompletionFingerprint(
  sessionId: string,
  meta: CcTabSessionMetaLite | undefined,
): string | null {
  if (!meta) return null
  if (meta.kind === 'pty' && meta.hasUserTurn !== true) return null
  if (meta.kind === 'chat' && !meta.messageCount && !meta.lastMessage) return null
  const completionVersion = meta.kind === 'pty'
    ? meta.lastSubmitAt
    : meta.messageCount ?? meta.lastActivityAt ?? compactHash(meta.lastMessage || '')
  if (completionVersion == null) return null
  return `${sessionId}:${meta.kind}:${meta.startedAt || 0}:${completionVersion}:${compactHash(meta.lastMessage || '')}`
}

function legacyPtySeenCoversCompletion(
  sessionId: string,
  meta: CcTabSessionMetaLite | undefined,
  seenFingerprint: string | undefined,
): boolean {
  if (meta?.kind !== 'pty' || meta.lastSubmitAt == null || !seenFingerprint) return false
  const prefix = `${sessionId}:pty:${meta.startedAt || 0}:`
  if (!seenFingerprint.startsWith(prefix)) return false
  const versionAndHash = seenFingerprint.slice(prefix.length)
  const separator = versionAndHash.lastIndexOf(':')
  if (separator <= 0) return false
  const seenVersion = Number(versionAndHash.slice(0, separator))
  // v1 used last_output_at. If that output was observed after this submit, the
  // turn was already viewed; preserve that acknowledgement during migration.
  return Number.isFinite(seenVersion) && seenVersion >= meta.lastSubmitAt
}

export function isSessionCompletionUnseen(
  state: CcTabRunState,
  sessionId: string,
  meta: CcTabSessionMetaLite | undefined,
  seen: SeenCompletions,
): boolean {
  if (state !== 'done') return false
  const fingerprint = sessionCompletionFingerprint(sessionId, meta)
  return Boolean(
    fingerprint
    && seen[sessionId] !== fingerprint
    && !legacyPtySeenCoversCompletion(sessionId, meta, seen[sessionId]),
  )
}

/* ── 服务端才是「已看」的真源（2026-09-05，计划 FOCUS-INBOX 阶段 2.2b）─────────
 *
 * 这份记录原先只在 localStorage 里：换台设备就丢，litework 那一端更是完全读不到 ——
 * 而「跑完一轮却还没被看的会话」正是消息队列里最重要的一类，判据不能只活在浏览器。
 *
 * 现在的分工：
 *   · 服务端存已看记录，并且**独家生成**完成指纹（后端 session_attention.py）；
 *   · 本地这份降级为**缓存 + 乐观更新**：点进会话立刻不再标记未读，不等网络往返；
 *     服务端确认后再以它为准。断网/未登录时退化成从前的纯本地行为，不报错。
 *
 * 指纹两边逐位等价（含 UTF-16 代理对，后端有参照值测试），所以本地算出来的串和
 * 服务端记下来的串能直接比较 —— 这是本地快路径成立的前提。
 */

async function postJson(path: string, body: unknown): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) return null
    return await response.json()
  } catch {
    return null  // 断网/未登录: 保持纯本地行为, 不打扰使用
  }
}

function adoptServerSeen(payload: Record<string, unknown> | null): void {
  const seen = payload?.seen
  if (!seen || typeof seen !== 'object' || Array.isArray(seen)) return
  const adopted = Object.fromEntries(
    Object.entries(seen as Record<string, unknown>)
      .filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
  )
  useSessionCompletionAttention.setState({ seen: adopted })
  writeSeenCompletions(adopted)
}

/** 开页一次：把本地这份交上去（只补不覆盖），再以服务端返回的为准。 */
async function syncWithServer(): Promise<void> {
  const local = readSeenCompletions(browserStorage())
  adoptServerSeen(await postJson('/api/inbox/sessions/seen/import', { seen: local }))
}

interface SessionCompletionAttentionState {
  seen: SeenCompletions
  markSeen: (sessionId: string, meta: CcTabSessionMetaLite | undefined) => void
}

export const useSessionCompletionAttention = create<SessionCompletionAttentionState>((set, get) => ({
  seen: readSeenCompletions(browserStorage()),
  markSeen: (sessionId, meta) => {
    const fingerprint = sessionCompletionFingerprint(sessionId, meta)
    if (!fingerprint || get().seen[sessionId] === fingerprint) return
    const seen = { ...get().seen, [sessionId]: fingerprint }
    set({ seen })
    writeSeenCompletions(seen)
    // 具体是哪一轮以服务端的投影为准(它可能比本地这份新), 所以只报 sessionId;
    // 带上本地指纹只是投影还没刷新到时的兜底。
    void postJson('/api/inbox/sessions/seen', { sessionId, fingerprint })
  },
}))

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return
    useSessionCompletionAttention.setState({ seen: readSeenCompletions(browserStorage()) })
  })
  void syncWithServer()
}
