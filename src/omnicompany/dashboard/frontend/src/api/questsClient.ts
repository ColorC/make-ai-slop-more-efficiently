// 任务窗口 API 客户端 — 数据真源 = plan-groups 文件模型（M3b 切换，M4 已停 :8230 归档）。
// 模型：cluster(域) → goal(主线北极星/支线主题) → task(计划) → subtask(子计划) + progress(进度历史) + focus(当前专注)。
// M4（2026-08-14）：whatnow(:8230) 已停服归档。旧代理回退链撤掉——再 fallback 到
// /api/local/progress-service 会把已停的服务重新打活。读写出错=直接报错，不再回退。

const PG = '/api/plan-groups'

/** 单链：plan-groups 端点（M4 起唯一链；旧 whatnow 代理回退已随停服撤除）。 */
async function dual<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${PG}${path}`, init)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return (await r.json()) as T
}

export interface ProgressEntry { ts: number; text: string; source?: string }
export interface TaskNode {
  id: string
  title: string
  status: string
  completion: number
  line: string
  channel: string
  external_refs: string[]
  assignee?: string | null
  due_date?: string | null
  plan_id?: string | null
  latest_progress?: string | null
  updated_at?: number
  archived?: boolean
  subtasks: TaskNode[]
  progress: ProgressEntry[]
}
export interface GoalNode {
  id: string
  title: string
  kind: string
  line: string
  status: string
  objective: string
  detail: string
  source: string
  cluster_id: string
  /** 宪章型任务线对应的计划目录（docs/plans 下相对路径），供「复制路径」 */
  plan_id?: string
  archived_count?: number
  tasks: TaskNode[]
  progress: ProgressEntry[]
}
export interface ClusterNode { id: string; title: string; note: string; goals: GoalNode[] }
/** 置顶条目（取代旧「当前专注」）：任务线 goal 或具体任务 task，后端已解析出标题/进度供直接渲染。 */
export interface PinNode {
  subject_kind: 'goal' | 'task'
  subject_id: string
  note: string
  set_at: number
  title: string | null
  missing: boolean // 主体已删 → 失效，前端给「取消置顶」
  // task 专有
  line?: string
  channel?: string
  completion?: number
  status?: string
  plan_id?: string | null
  external_refs?: string[]
  latest_progress?: string | null
  // goal 专有
  kind?: string
  task_count?: number
  done_count?: number
}
export interface Board {
  clusters: ClusterNode[]
  orphan_goals: GoalNode[]
  loose_tasks: TaskNode[]
  pins: PinNode[]
  counts: { clusters: number; goals: number; tasks: number }
  updated_at: number
}

export const questsApi = {
  board: (includeArchived = false): Promise<Board> =>
    dual<Board>(`/api/board${includeArchived ? '?archived=1' : ''}`),
  /** 外部渠道 sync（meego/multica）随 whatnow 停服一并退役（M4）：不再请求任何后端，
   *  直接返回 retired 标记，避免把已停服务打活。外部接入重写到 plan-groups 是独立工作项。 */
  syncMeego: (): Promise<any> => Promise.resolve({ ok: false, retired: true, synced: 0, reason: 'whatnow 已停服归档（M4），外部渠道同步退役' }),
  syncMultica: (): Promise<any> => Promise.resolve({ ok: false, retired: true, synced: 0, reason: 'whatnow 已停服归档（M4），外部渠道同步退役' }),
  /** 置顶 / 取消置顶（任务线 goal 或具体任务 task）。pinned=false 即取消。 */
  pin: (subject_kind: 'goal' | 'task', subject_id: string, pinned = true, note = ''): Promise<any> =>
    dual('/api/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject_kind, subject_id, pinned, note }) }),
  archive: (id: string, archived = true): Promise<any> =>
    dual('/api/task/archive', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, archived }) }),
  addProgress: (subject_id: string, text: string, subject_kind = 'task'): Promise<any> =>
    dual('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject_kind, subject_id, text, source: 'dashboard' }) }),
}
