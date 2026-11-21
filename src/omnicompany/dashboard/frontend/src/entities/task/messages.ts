// 消息队列的数据层 —— 两类消息，一个队列。
//
// 用户 2026-09-04：「消息队列的关键是未回复或者说观看的会话啊，不要忘了。」
// 所以队列里最重要的一类不是提审、不是 agent 举手，是**跑完一轮却还没被看的会话**。
//
// 两类消息的存法不一样，这不是不一致，是因为它们的性质不一样：
//
//   · **落总线的**（提审 / agent 举手 / 你自己提的单）—— 需要延后、忽略要留痕，
//     所以是 stateKey=inbox 的未提升任务，状态在总线上；
//   · **不落总线的**（未看的会话）—— 点进去看一眼就消了，是瞬时状态。给它建一条
//     任务只会在总线里堆垃圾。它的"已读"沿用会话完成指纹（sessionAttention），
//     和别处的未读徽标是同一份判据，不另算一套。

import type { CcTabRunState, CcTabSessionMetaLite } from '../cc_session/tabStatus'
import { isSessionCompletionUnseen } from '../cc_session/sessionAttention'

export type MsgKind = 'bus' | 'session'

export interface MsgRow {
  /** 队列内唯一。总线消息用任务 id，会话消息用会话 id。 */
  id: string
  kind: MsgKind
  /** 来源短标：提审 / Agent / 会话 / 手记。 */
  source: string
  rawSource: string
  rawKind: string
  title: string
  projectId: string
  sessionId: string | null
  priority: number | null
  /** 点「处理」要跳的深链。 */
  url: string
  /** 动作用的键 = dedupeKey(source:remoteId)。消息不是任务, 所以不是 taskId
   *  （2026-09-05 收件箱改实时取源：列表现问适配器，只有处置写进存储）。 */
  key: string
  /** 人手排过的次序。有它就压过阶梯 —— 「精密排序」的落点，用 task-core 已有的
   *  字段，不为排序另发明东西。 */
  orderKey?: string | null
  createdAt?: string
  /** 排序用的位次：逐条优先级；会话没有逐条优先级，位次由订阅矩阵那条规则给。 */
  rank: number
}

const SOURCE_LABEL: Record<string, string> = {
  review: '提审',
  agent: 'Agent',
  session: '会话',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] || source
}

export interface InboxItem {
  key: string
  /** 原始来源标识（source 是给人看的短标，别拿它做判断）。 */
  rawSource?: string
  title: string
  priority: number | null
  projectId: string
  sessionId: string | null
  source: string
  kind: string
  url: string
  orderKey?: string | null
  createdAt?: string
}

export interface InboxProject {
  projectId: string
  top: number
  count: number
  sessions: { sessionId: string | null; items: InboxItem[] }[]
}

/** 会话在队列里的位次。它没有逐条优先级，位次由订阅矩阵那条规则给 ——
 *  嫌它太靠前就把 profile 里那个数字调低，改一行，不用改代码。 */
export function sessionRank(profile: { subscriptions?: { rules?: any[] } } | null): number {
  const rule = (profile?.subscriptions?.rules || [])
    .find((r) => r?.source === 'session')
  const value = rule?.priority
  return typeof value === 'number' ? value : 5
}

/** 服务端出的候选拍平成**一个平铺队列**，按各自位次排。
 *
 * 用户 2026-09-04：「消息不要聚合，就是按照各自优先级排序，后续允许精密排序。」
 * 聚合是给待办段用的（那里项目是归属维度）；消息是待处理流，分了组反而看不出
 * 先做哪条 —— 队列的意义就是从上往下清。
 *
 * 排序规则，两层：
 *   1. **人手排过的在最前**（`orderKey` 字典序）—— 这就是「精密排序」的落点，
 *      用 task-core 已有的字段，不为排序另发明东西；
 *   2. 其余按位次降序，同位次按先来后到。
 */
export function buildMessageList(inboxProjects: InboxProject[]): MsgRow[] {
  const rows: MsgRow[] = []
  for (const project of inboxProjects) {
    for (const session of project.sessions) {
      for (const item of session.items) {
        rows.push({
          id: item.key,
          // 判来源要用 rawSource：source 是「会话/提审」这种人读短标，
          // 拿它比对永远不等于 'session'，点击就会走错分支（2026-09-05 实拍到）。
          kind: (item.rawSource ?? item.source) === 'session' ? 'session' : 'bus',
          source: sourceLabel(item.source),
          rawSource: item.source,
          rawKind: item.kind,
          title: item.title,
          projectId: project.projectId,
          sessionId: item.sessionId,
          priority: item.priority,
          url: item.url,
          key: item.key,
          orderKey: item.orderKey ?? null,
          createdAt: item.createdAt,
          rank: item.priority ?? -1,
        })
      }
    }
  }
  return rows.sort((a, b) => {
    const ak = a.orderKey || ''
    const bk = b.orderKey || ''
    if (ak && bk) return ak < bk ? -1 : ak > bk ? 1 : 0
    if (ak !== bk) return ak ? -1 : 1
    return b.rank - a.rank || String(a.createdAt || '').localeCompare(String(b.createdAt || ''))
  })
}

/** 往后拖的几档。相对时间前端算，后端只认 ISO。 */
export const DEFER_CHOICES = [
  { value: '2h', label: '2 小时后' },
  { value: '1d', label: '明天' },
  { value: '3d', label: '3 天后' },
  { value: '1w', label: '下周' },
] as const

export function deferIso(choice: string, now: number = Date.now()): string {
  const at = new Date(now)
  if (choice === '2h') at.setHours(at.getHours() + 2)
  else if (choice === '1d') { at.setDate(at.getDate() + 1); at.setHours(9, 0, 0, 0) }
  else if (choice === '3d') at.setDate(at.getDate() + 3)
  else if (choice === '1w') { at.setDate(at.getDate() + 7); at.setHours(9, 0, 0, 0) }
  return at.toISOString()
}

/* ── API ────────────────────────────────────────────────────────────── */

async function post(url: string, body: unknown): Promise<{ ok: boolean; message?: string }> {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const json = await resp.json()
    return { ok: Boolean(json?.ok), message: json?.message }
  } catch (err) {
    return { ok: false, message: String(err) }
  }
}

export async function fetchInboxProjects(): Promise<{ projects: InboxProject[]; deferred: number }> {
  try {
    const resp = await fetch('/api/inbox/board')
    const json = await resp.json()
    return json?.ok
      ? { projects: json.projects || [], deferred: json.deferred || 0 }
      : { projects: [], deferred: 0 }
  } catch {
    return { projects: [], deferred: 0 }
  }
}

export function actOnMessage(
  key: string,
  action: 'done' | 'dismiss' | 'defer' | 'triage' | 'undo',
  until?: string,
) {
  return post('/api/inbox/act', { key, action, until })
}

/** 就地「以后不要这类」。 */
export function muteKind(source: string, kind: string) {
  return post('/api/inbox/mute', { source, kind, surface: 'off' })
}

export function pullInbox() {
  return post('/api/inbox/sync', {})
}
