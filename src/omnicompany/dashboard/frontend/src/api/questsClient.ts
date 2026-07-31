// 任务窗口 API 客户端 — 接本地目标管理系统 whatnow（Rust 服务，:8230，仿 Leantime）。
// 模型：cluster(域) → goal(主线北极星/支线主题) → task(计划) → subtask(子计划) + progress(进度历史) + focus(当前专注)。
// 数据真源是 whatnow（统一数据模型 + 统一身份：meego/multica 同一单子只一条 task）；项目工作板(tab1)管资产盘点，本视图管进度与历史。

const WHATNOW = '/api/local/progress-service'

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

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

export const questsApi = {
  board: (includeArchived = false): Promise<Board> =>
    fetch(`${WHATNOW}/api/board${includeArchived ? '?archived=1' : ''}`).then((r) => j<Board>(r)),
  /** 拉取外部渠道单子（meego = 经办人/负责人 maintainer；multica = demogame-5224 议题），统一身份去重后入库 */
  syncMeego: (): Promise<any> => fetch(`${WHATNOW}/api/sync/meego`, { method: 'POST' }).then((r) => r.json()),
  syncMultica: (): Promise<any> => fetch(`${WHATNOW}/api/sync/multica`, { method: 'POST' }).then((r) => r.json()),
  /** 置顶 / 取消置顶（任务线 goal 或具体任务 task）。pinned=false 即取消。 */
  pin: (subject_kind: 'goal' | 'task', subject_id: string, pinned = true, note = ''): Promise<any> =>
    fetch(`${WHATNOW}/api/pin`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject_kind, subject_id, pinned, note }) }).then((r) => r.json()),
  archive: (id: string, archived = true): Promise<any> =>
    fetch(`${WHATNOW}/api/task/archive`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, archived }) }).then((r) => r.json()),
  addProgress: (subject_id: string, text: string, subject_kind = 'task'): Promise<any> =>
    fetch(`${WHATNOW}/api/progress`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject_kind, subject_id, text, source: 'dashboard' }) }).then((r) => r.json()),
}
