// task-core 个人任务总线 —— 数据层与纯选择器（无 React，可单测）。
//
// 契约: docs/plans/_cross/[2026-09-01]TASK-CORE-PERSONAL-TASK-BUS/task-core.md
// 数据面: /api/tbus/*  →  omni tbus 桥  →  LiTe CLI 信封  →  SQLite(local-tasks, 唯一真源)
//
// 本文件只做「形状 + 变换」: 泳道映射、项目分组、父子树、排序、逾期判定、速记解析。
// 一切与 DOM/React 有关的东西在 TaskPanel.tsx 及其子件里。

/* ── 契约形状（与 LiTe src/shared/local-task-types.ts 对齐，只取 UI 用得上的字段） ── */

export interface LocationLink {
  id: string
  locationKind: 'branch' | 'conversation' | 'plan-file' | 'workspace' | string
  locationId: string
  role: string
  active: boolean
}

export interface BusTask {
  id: string
  title: string
  description?: string | null
  stateKey: string
  projectId: string | null
  parentTaskId: string | null
  orderKey: string | null
  priority?: number | null
  dueAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  archivedAt: string | null
  revision: number
  locationLinks: LocationLink[]
}

export interface ProjectBucket {
  projectId: string
  title: string
  tasks: BusTask[]
}

export interface Board {
  ok: boolean
  code?: string
  message?: string
  runtimeId?: string | null
  lanes: string[]
  projects: ProjectBucket[]
}

export interface TaskNote {
  id: string
  kind: string
  body: string
  createdAt: string
}

export interface TaskBinding {
  id: string
  provider: string
  remoteKind: string
  remoteId: string
  url: string | null
  title: string | null
  cacheState: string
}

export interface TaskDetail {
  notes: TaskNote[]
  bindings: TaskBinding[]
  links: LocationLink[]
}

export const UNGROUPED = '未分组'

/* ── 个人附加层词表（task-core §10） ────────────────────────────────────────
 * 真源是服务端 data/task_core_profile.json，这里只是投影。
 * 所以它是**运行时注入的单例**而不是模块常量 —— 在前端再写一份阶梯名字，
 * 就会出现「界面说 3 级、CLI 说 P1」。取不到词表时退化成裸数字 P<n>，
 * 那不是第二套词表，只是不加解释地显示 priority 本身。 */

export interface TierDef {
  priority: number
  id: string
  name: string
  short: string
  criterion: string
  note?: string
  userOnly?: boolean
  projects: string[]
}

export interface TaskProfile {
  version: string
  priorityLadder: TierDef[]
  unratedLabel: string
  kinds: { id: string; name: string; desc: string }[]
  labels: { id: string; name: string; desc: string }[]
  states: { key: string; name: string; lane: string; primaryView: boolean }[]
  triage: { ask: string; then: number; label?: string; note?: string }[]
  triageFallback: string
}

let PROFILE: TaskProfile | null = null

export function setTaskProfile(profile: TaskProfile | null): void { PROFILE = profile }
export function taskProfile(): TaskProfile | null { return PROFILE }

const RAW_TIERS: TierDef[] = [5, 4, 3, 2, 1, 0].map((priority) => ({
  priority, id: `p${priority}`, name: `P${priority}`, short: `P${priority}`, criterion: '', projects: [],
}))

/** 优先度阶梯，高在前。词表没到位就退化成裸数字。 */
export function tiers(): TierDef[] {
  const ladder = PROFILE?.priorityLadder
  if (!ladder || ladder.length === 0) return RAW_TIERS
  return [...ladder].sort((a, b) => b.priority - a.priority)
}

export function tierOf(priority: number | null | undefined): TierDef | null {
  if (priority == null) return null
  return tiers().find((t) => t.priority === priority) || null
}

export const UNRATED = '未定级'

export function tierLabel(priority: number | null | undefined): string {
  return tierOf(priority)?.name || (priority == null ? (PROFILE?.unratedLabel || UNRATED) : `P${priority}`)
}

/** 卡片/行上的短标 —— 一格宽的地方只放得下两三个字。 */
export function tierShort(priority: number | null | undefined): string {
  const tier = tierOf(priority)
  return tier ? tier.short : (priority == null ? '' : `P${priority}`)
}

/* ── 泳道: 7 个开放状态键收敛成 5 条道 + 1 个归档区（spec §2.2） ────────────── */

export type LaneId = 'inbox' | 'todo' | 'in-progress' | 'in-review' | 'hold' | 'completed'

/** **主视野**的泳道。`inbox` 故意不在里面：提单子是"记下来别打断我"，
 *  一进主视野就变成催办了（spec §2.1 的三个"不"）。它走 {@link inboxPanel}。 */
export const LANES: readonly LaneId[] = ['todo', 'in-progress', 'in-review', 'hold', 'completed']

export const LANE_LABEL: Record<LaneId, string> = {
  inbox: '收件箱',
  todo: '待办',
  'in-progress': '进行',
  'in-review': '待审',
  hold: '挂起',
  completed: '完成',
}

/** 拖入/键入某条道时写回宿主的 stateKey（hold 道默认落 paused，blocked 需显式设置）。 */
export const LANE_WRITE_STATE: Record<LaneId, string> = {
  inbox: 'inbox',
  todo: 'todo',
  'in-progress': 'in-progress',
  'in-review': 'in-review',
  hold: 'paused',
  completed: 'completed',
}

export const STATE_LABEL: Record<string, string> = {
  inbox: '收件箱',
  todo: '待办',
  'in-progress': '进行中',
  paused: '已暂停',
  blocked: '阻塞',
  'in-review': '待审',
  completed: '完成',
  cancelled: '已放弃',
}

export function stateLabel(stateKey: string): string {
  return STATE_LABEL[stateKey] || stateKey
}

/** 未知 stateKey 一律按 todo 聚合（与后端 STATE_TO_PHASE 的 open 兜底同语义）。 */
export function laneOf(task: BusTask): LaneId {
  switch (task.stateKey) {
    case 'inbox':
      return 'inbox'
    case 'in-progress':
      return 'in-progress'
    case 'in-review':
      return 'in-review'
    case 'paused':
    case 'blocked':
      return 'hold'
    case 'completed':
      return 'completed'
    default:
      return 'todo'
  }
}

/** 收件箱判据: 未分诊的单子。归档优先 —— 归档掉的单子算归档，不算待分诊。 */
export function isInbox(task: BusTask): boolean {
  return task.stateKey === 'inbox' && !isRetired(task)
}

/** 归档区判据: 已放弃或已归档。这两类退出看板与列表主体。 */
export function isRetired(task: BusTask): boolean {
  return task.stateKey === 'cancelled' || Boolean(task.archivedAt)
}

export function isDone(task: BusTask): boolean {
  return task.stateKey === 'completed' || isRetired(task)
}

/* ── 元信号 ─────────────────────────────────────────────────────────────── */

export type DueState = 'overdue' | 'soon' | 'later' | 'none'

const DAY_MS = 24 * 60 * 60 * 1000

export function dueState(dueAt: string | null | undefined, now: number = Date.now()): DueState {
  if (!dueAt) return 'none'
  const at = Date.parse(dueAt)
  if (Number.isNaN(at)) return 'none'
  if (at < now) return 'overdue'
  if (at - now <= 3 * DAY_MS) return 'soon'
  return 'later'
}

/** 截止的短标注: 逾期 N 天 / 今天 / N 天。图纸上的标注要短。 */
export function dueLabel(dueAt: string | null | undefined, now: number = Date.now()): string {
  if (!dueAt) return ''
  const at = Date.parse(dueAt)
  if (Number.isNaN(at)) return ''
  const days = Math.round((startOfDay(at) - startOfDay(now)) / DAY_MS)
  if (days < 0) return `逾期 ${-days}d`
  if (days === 0) return '今天'
  if (days === 1) return '明天'
  return `${days}d`
}

function startOfDay(ms: number): number {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

export function conversationLinks(task: BusTask): LocationLink[] {
  return (task.locationLinks || []).filter((l) => l.locationKind === 'conversation' && l.active)
}

export function planLinks(task: BusTask): LocationLink[] {
  return (task.locationLinks || []).filter((l) => l.locationKind === 'plan-file' && l.active)
}

/* ── 排序 ───────────────────────────────────────────────────────────────── */

export type SortKey = 'manual' | 'due' | 'priority' | 'updated'

export const SORT_LABEL: Record<SortKey, string> = {
  manual: '手动',
  due: '截止',
  priority: '优先级',
  updated: '最近改动',
}

const FAR_FUTURE = 8.64e15

export function sortTasks(tasks: BusTask[], key: SortKey): BusTask[] {
  const copy = tasks.slice()
  switch (key) {
    case 'due':
      // 无截止的排最后 —— 图纸上没标注的尺寸不该抢占前排。
      return copy.sort((a, b) => dueMs(a) - dueMs(b) || manualCmp(a, b))
    case 'priority':
      // priority 数字越大越紧急; null 视作 -1 排最后。
      return copy.sort((a, b) => (b.priority ?? -1) - (a.priority ?? -1) || manualCmp(a, b))
    case 'updated':
      return copy.sort((a, b) => str(b.updatedAt).localeCompare(str(a.updatedAt)) || manualCmp(a, b))
    default:
      return copy.sort(manualCmp)
  }
}

function dueMs(task: BusTask): number {
  if (!task.dueAt) return FAR_FUTURE
  const at = Date.parse(task.dueAt)
  return Number.isNaN(at) ? FAR_FUTURE : at
}

/** 宿主同语义: orderKey 字典序在前、NULL 最后、回退 createdAt/id。 */
function manualCmp(a: BusTask, b: BusTask): number {
  const an = a.orderKey == null
  const bn = b.orderKey == null
  if (an !== bn) return an ? 1 : -1
  return (
    str(a.orderKey).localeCompare(str(b.orderKey))
    || str(a.createdAt).localeCompare(str(b.createdAt))
    || str(a.id).localeCompare(str(b.id))
  )
}

function str(v: unknown): string {
  return v == null ? '' : String(v)
}

/** 组内拖拽后的稳定顺序。返回新数组，不改动宿主快照。 */
export type DropPlacement = 'before' | 'after'

export function reorderTasks(
  tasks: BusTask[],
  draggedId: string,
  targetId: string,
  placement: DropPlacement = 'before',
): BusTask[] {
  if (draggedId === targetId) return tasks.slice()
  const dragged = tasks.find((task) => task.id === draggedId)
  if (!dragged || !tasks.some((task) => task.id === targetId)) return tasks.slice()
  const rest = tasks.filter((task) => task.id !== draggedId)
  const targetIndex = rest.findIndex((task) => task.id === targetId)
  if (targetIndex < 0) return tasks.slice()
  rest.splice(placement === 'after' ? targetIndex + 1 : targetIndex, 0, dragged)
  return rest
}

/**
 * 首次手动排序时，旧任务可能没有 orderKey。给整组分配稀疏 key，
 * 这样 NULL（宿主语义上永远排最后）也能被拖到任意位置，而不是只能拖到最前。
 */
export function orderKeyForIndex(index: number): string {
  return `ui-${String(Math.max(0, index) * 1000).padStart(8, '0')}`
}

/* ── 筛选 ───────────────────────────────────────────────────────────────── */

export interface Filters {
  query: string
  projects: string[]
  lanes: LaneId[]
}

export const EMPTY_FILTERS: Filters = { query: '', projects: [], lanes: [] }

export function hasFilters(f: Filters): boolean {
  return Boolean(f.query.trim()) || f.projects.length > 0 || f.lanes.length > 0
}

export function matchesFilters(task: BusTask, f: Filters): boolean {
  if (f.projects.length > 0 && !f.projects.includes(projectKey(task))) return false
  if (f.lanes.length > 0 && !f.lanes.includes(laneOf(task))) return false
  const q = f.query.trim().toLowerCase()
  if (!q) return true
  return (
    task.title.toLowerCase().includes(q)
    || str(task.description).toLowerCase().includes(q)
    || task.id.toLowerCase().includes(q)
  )
}

export function projectKey(task: BusTask): string {
  return task.projectId || UNGROUPED
}

/* ── 分组与树 ───────────────────────────────────────────────────────────── */

export interface TaskNode {
  task: BusTask
  depth: number
}

/** 父子树展平成带 depth 的行序列。深度上限 3；父不在结果集里的孤儿按根渲染。 */
export function buildTree(tasks: BusTask[], sort: SortKey, maxDepth = 3): TaskNode[] {
  const present = new Set(tasks.map((t) => t.id))
  const children = new Map<string, BusTask[]>()
  const roots: BusTask[] = []
  for (const task of tasks) {
    const parent = task.parentTaskId
    if (parent && parent !== task.id && present.has(parent)) {
      const bucket = children.get(parent)
      if (bucket) bucket.push(task)
      else children.set(parent, [task])
    } else {
      roots.push(task)
    }
  }
  const out: TaskNode[] = []
  const seen = new Set<string>()
  const walk = (task: BusTask, depth: number): void => {
    if (seen.has(task.id)) return // 防环（宿主保证无环，UI 也不该因脏数据死循环）
    seen.add(task.id)
    out.push({ task, depth: Math.min(depth, maxDepth) })
    for (const child of sortTasks(children.get(task.id) || [], sort)) walk(child, depth + 1)
  }
  for (const root of sortTasks(roots, sort)) walk(root, 0)
  // 兜底: 互相指认父子的脏数据会让两条都不是根, 走到这里一条都没渲染 ——
  // 任务凭空消失比缩进不对严重得多, 剩下的一律按根补上。
  const missed = tasks.filter((t) => !seen.has(t.id))
  for (const task of sortTasks(missed, sort)) walk(task, 0)
  return out
}

/** 子任务进度 = 直接子任务中已完成 / 总数；无子任务返回 null。 */
export function subtaskProgress(task: BusTask, all: BusTask[]): { done: number; total: number } | null {
  const kids = all.filter((t) => t.parentTaskId === task.id)
  if (kids.length === 0) return null
  return { done: kids.filter(isDone).length, total: kids.length }
}

/* ── 通用分组引擎 ─────────────────────────────────────────────────────────
   一套分组器，两种布局共用（账页列表 / 看板泳道都是同一个「面板」）。
   旧版把「看板 = 按状态」「列表 = 按项目」写死成两套代码，于是面板不通用、
   分组维度也换不了；成熟看板产品（Plane / Focalboard / Vikunja / WeKan）都是
   「分组维度可切 × 布局可切」的正交两轴，这里对齐那个模型。

   面板还携带 seed（在这里新建 / 拖进来该落哪个字段值）与 droppable：
   宿主 `task update` 没有 project 通道，`截止` 桶落哪一天全靠猜 —— 这两个维度
   拖拽只读，但仍可在面板里新建（`task create` 支持 --project / --due-at）。
   ────────────────────────────────────────────────────────────────────── */

export type GroupBy = 'state' | 'project' | 'priority' | 'due'

export const GROUP_LABEL: Record<GroupBy, string> = {
  state: '按状态',
  project: '按项目',
  priority: '按优先级',
  due: '按截止',
}

/** 在面板里新建 / 拖进面板时要落到的字段值。 */
export interface GroupSeed {
  projectId?: string | null
  stateKey?: string
  priority?: number | null
  dueAt?: string | null
  /** 右键「新建子任务」时带上父 id —— 复用同一个 composer，不另做一套。 */
  parentTaskId?: string | null
}

export interface Panel {
  key: string
  title: string
  tasks: BusTask[]
  /** 组头计数只报"还欠着的"，不报总量。 */
  open: number
  total: number
  seed: GroupSeed
  droppable: boolean
  /** 只读时给用户的一句话理由（拖上去会显示，不让人对着不动的东西猜）。 */
  readonlyReason?: string
  /** 能不能在这个面板里新建。归档面板不行 —— 直接建一条归档件没有意义。 */
  addable?: boolean
}

/** 归档面板的固定 key。它也是一个正经面板：能收拖拽（拖进去 = 归档）。 */
export const RETIRED_KEY = '__retired__'

export function retiredPanel(retired: BusTask[], sort: SortKey): Panel {
  const tasks = sortTasks(retired, sort)
  return {
    key: RETIRED_KEY,
    title: '归档',
    tasks,
    open: 0,
    total: tasks.length,
    seed: {},
    droppable: true,
    addable: false,
  }
}

/** 收件箱面板的固定 key。用 lane id 本名，好让 dropWriteFor 的 stateKey 分支
 *  原样复用（拖回收件箱 = 撤销分诊，是合法动作）。 */
export const INBOX_KEY: LaneId = 'inbox'

export function inboxPanel(inbox: BusTask[], sort: SortKey): Panel {
  const tasks = sortTasks(inbox, sort)
  return {
    key: INBOX_KEY,
    title: LANE_LABEL.inbox,
    tasks,
    open: tasks.length,
    total: tasks.length,
    seed: { stateKey: 'inbox' },
    droppable: true,
    addable: true,
  }
}

export interface Grouped {
  panels: Panel[]
  retired: BusTask[]
  /** 未分诊的单子。不进 panels，也不进 active —— 主视野看不到它们。 */
  inbox: BusTask[]
  /** 参与主体渲染的任务全集（供子任务进度等交叉计算）。 */
  active: BusTask[]
}

/** 优先级分组的桶 —— 名字来自个人附加层词表，不在这里写死。 */
function priorityBuckets(): { key: string; title: string; priority: number | null }[] {
  return [
    // 组头用短标 —— 长名是判据, 判据留给悬浮与详情页那行。
    ...tiers().map((t) => ({ key: t.id, title: t.short || t.name, priority: t.priority })),
    { key: 'pnone', title: PROFILE?.unratedLabel || UNRATED, priority: null },
  ]
}

export const DUE_BUCKETS = ['overdue', 'today', 'week', 'later', 'none'] as const
export type DueBucket = (typeof DUE_BUCKETS)[number]

const DUE_TITLE: Record<DueBucket, string> = {
  overdue: '逾期', today: '今天', week: '7 天内', later: '以后', none: '无截止',
}

export function dueBucketOf(task: BusTask, now: number): DueBucket {
  if (!task.dueAt) return 'none'
  const at = Date.parse(task.dueAt)
  if (Number.isNaN(at)) return 'none'
  if (at < now) return 'overdue'
  const days = Math.round((startOfDay(at) - startOfDay(now)) / DAY_MS)
  if (days <= 0) return 'today'
  if (days <= 7) return 'week'
  return 'later'
}

function endOfToday(now: number): string {
  const d = new Date(now)
  d.setHours(23, 59, 0, 0)
  return d.toISOString()
}

/** 一组任务里"还欠着的最高一级"。全做完 / 全未定级都算 -1，排最后。 */
function topOpenTier(tasks: BusTask[]): number {
  let top = -1
  for (const t of tasks) {
    if (isDone(t)) continue
    if ((t.priority ?? -1) > top) top = t.priority ?? -1
  }
  return top
}

export function buildPanels(
  board: Board | null,
  filters: Filters,
  sort: SortKey,
  groupBy: GroupBy,
  now: number = Date.now(),
): Grouped {
  const retired: BusTask[] = []
  const inbox: BusTask[] = []
  const active: BusTask[] = []
  // 项目分组要保留宿主给的项目顺序，所以先按 bucket 走一遍。
  const byProject: { id: string; title: string; tasks: BusTask[] }[] = []
  for (const bucket of board?.projects || []) {
    const kept: BusTask[] = []
    for (const task of bucket.tasks) {
      if (isRetired(task)) {
        // 归档区不吃泳道筛选 —— 否则按状态筛完归档区会莫名其妙清空。
        if (matchesFilters(task, { ...filters, lanes: [] })) retired.push(task)
        continue
      }
      if (isInbox(task)) {
        // 收件箱同样不吃泳道筛选：它压根不在泳道体系里，跟着筛只会莫名清空。
        if (matchesFilters(task, { ...filters, lanes: [] })) inbox.push(task)
        continue
      }
      if (matchesFilters(task, filters)) kept.push(task)
    }
    active.push(...kept)
    byProject.push({ id: bucket.projectId, title: bucket.title || bucket.projectId, tasks: kept })
  }

  const panel = (
    key: string, title: string, tasks: BusTask[], seed: GroupSeed,
    droppable: boolean, readonlyReason?: string,
  ): Panel => {
    const sorted = sortTasks(tasks, sort)
    return {
      key, title, tasks: sorted, seed, droppable, readonlyReason,
      open: sorted.filter((t) => !isDone(t)).length,
      total: sorted.length,
    }
  }

  let panels: Panel[]
  switch (groupBy) {
    case 'project':
      // 2026-09-01 更正：项目**是**可改的。宿主 LocalTaskPatch / PATCH_COLUMNS 一直带
      // project_id，之前挡住的只是 RPC zod 与 CLI spec 两层壳（已补）。此前 UI 上写的
      // 「宿主没有 project 通道」是错的说法。
      // 项目之间也按整体优先级排: 项目本身没有优先级, 但"这个项目里最急的那条"
      // 就是它此刻该排多前(用户 2026-09-03: 项目内容应该按照整体优先级排)。
      // 只看还欠着的条目 —— 一堆已完成的高优不该把项目顶在最上面。
      panels = [...byProject]
        .map((p, at) => ({ p, at, top: topOpenTier(p.tasks) }))
        .sort((a, b) => b.top - a.top || a.at - b.at)
        .map(({ p }) => panel(
          p.id, p.title, p.tasks,
          { projectId: p.id === UNGROUPED ? null : p.id },
          true,
        ))
      if (panels.length === 0) {
        panels = [panel(UNGROUPED, UNGROUPED, [], { projectId: null }, false)]
      }
      break
    case 'priority':
      panels = priorityBuckets().map((b) => panel(
        b.key, b.title,
        active.filter((t) => (t.priority ?? null) === b.priority),
        { priority: b.priority }, true,
      ))
      break
    case 'due':
      panels = DUE_BUCKETS.map((b) => panel(
        b, DUE_TITLE[b],
        active.filter((t) => dueBucketOf(t, now) === b),
        b === 'today' ? { dueAt: endOfToday(now) } : {},
        false, '截止分组只读：落到桶里的哪一天是猜的，改日期请开详情',
      ))
      break
    default:
      panels = LANES.map((lane) => panel(
        lane, LANE_LABEL[lane],
        active.filter((t) => laneOf(t) === lane),
        { stateKey: LANE_WRITE_STATE[lane] }, true,
      ))
  }
  return { panels, retired, inbox, active }
}

/** 把一条任务落进某个面板要写什么 —— 拖拽与就地新建共用同一份口径。 */
export type DropWrite =
  | { kind: 'status'; stateKey: string }
  | { kind: 'update'; priority?: number | null; dueAt?: string | null; projectId?: string | null }
  | { kind: 'archive' }
  | { kind: 'restore' }
  | null

export function dropWriteFor(panel: Panel, task: BusTask): DropWrite {
  if (!panel.droppable) return null
  // 拖进归档面板 = 归档；从归档拖回任何正常面板 = 先恢复，再落该面板的字段。
  if (panel.key === RETIRED_KEY) return isRetired(task) ? null : { kind: 'archive' }
  if (isRetired(task)) return { kind: 'restore' }
  if (panel.seed.stateKey != null) {
    // 已经在这条道里就别写了（blocked 拖进"挂起"道不该被降级成 paused）。
    return laneOf(task) === panel.key ? null : { kind: 'status', stateKey: panel.seed.stateKey }
  }
  if ('priority' in panel.seed) {
    return (task.priority ?? null) === (panel.seed.priority ?? null)
      ? null
      : { kind: 'update', priority: panel.seed.priority ?? null }
  }
  if ('projectId' in panel.seed) {
    return (task.projectId ?? null) === (panel.seed.projectId ?? null)
      ? null
      : { kind: 'update', projectId: panel.seed.projectId ?? null }
  }
  return null
}

export function allProjects(board: Board | null): { id: string; label: string; count: number }[] {
  return (board?.projects || []).map((p) => ({
    id: p.projectId,
    label: p.title || p.projectId,
    count: p.tasks.filter((t) => !isRetired(t)).length,
  }))
}

/* ── 速记行内联语法（spec §6） ──────────────────────────────────────────── */

export interface QuickAddParsed {
  title: string
  projectId: string | null
  /** 用户写了 #xxx 但没匹配上任何项目 —— 原样留在标题里，绝不静默吞字。 */
  unmatchedProject: string | null
  priority: number | null
  dueAt: string | null
  dueText: string | null
}

const WEEKDAYS: Record<string, number> = {
  sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6,
  日: 0, 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6,
}

/**
 * `修好登录跳转 #omnicompany !2 @明天` →
 * { title:'修好登录跳转', projectId:'omnicompany', priority:2, dueAt:'…' }
 *
 * 只吃能确定解释的 token；解释不了的一律留在标题里。
 */
export function parseQuickAdd(
  raw: string,
  projects: { id: string; label: string }[],
  now: number = Date.now(),
): QuickAddParsed {
  let projectId: string | null = null
  let unmatchedProject: string | null = null
  let priority: number | null = null
  let dueAt: string | null = null
  let dueText: string | null = null

  const kept: string[] = []
  for (const token of raw.split(/\s+/)) {
    if (!token) continue
    if (token.startsWith('#') && token.length > 1) {
      const needle = token.slice(1).toLowerCase()
      const hit = projects.find((p) => p.id.toLowerCase() === needle)
        || projects.find((p) => p.id.toLowerCase().includes(needle) || p.label.toLowerCase().includes(needle))
      if (hit) { projectId = hit.id; continue }
      unmatchedProject = token.slice(1)
      kept.push(token)
      continue
    }
    if (/^![0-5]$/.test(token)) { priority = Number(token.slice(1)); continue }
    if (token.startsWith('@') && token.length > 1) {
      const parsed = parseDueToken(token.slice(1), now)
      if (parsed) { dueAt = parsed; dueText = token.slice(1); continue }
      kept.push(token)
      continue
    }
    kept.push(token)
  }
  return { title: kept.join(' ').trim(), projectId, unmatchedProject, priority, dueAt, dueText }
}

/** 支持 today/明天/后天/mon..sun/周一../MM-DD/YYYY-MM-DD；落到当天 23:59 本地时间。 */
export function parseDueToken(token: string, now: number = Date.now()): string | null {
  const t = token.toLowerCase()
  const base = new Date(now)
  base.setHours(23, 59, 0, 0)

  if (t === 'today' || t === '今天' || t === '今日') return base.toISOString()
  if (t === 'tomorrow' || t === '明天' || t === '明日') return shiftDays(base, 1)
  if (t === '后天') return shiftDays(base, 2)

  const weekdayKey = t.replace(/^(周|星期)/, '')
  if (Object.prototype.hasOwnProperty.call(WEEKDAYS, weekdayKey)) {
    const target = WEEKDAYS[weekdayKey]
    const delta = ((target - base.getDay()) + 7) % 7 || 7 // 总是指向"下一个"，同日算下周
    return shiftDays(base, delta)
  }

  const full = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t)
  if (full) return atDate(Number(full[1]), Number(full[2]), Number(full[3]))

  const short = /^(\d{1,2})-(\d{1,2})$/.exec(t)
  if (short) {
    const month = Number(short[1])
    const day = Number(short[2])
    let year = base.getFullYear()
    const candidate = new Date(year, month - 1, day, 23, 59, 0, 0)
    if (candidate.getTime() < now) year += 1 // 已过的日期理解为明年
    return atDate(year, month, day)
  }
  return null
}

function shiftDays(base: Date, days: number): string {
  const d = new Date(base)
  d.setDate(d.getDate() + days)
  return d.toISOString()
}

function atDate(year: number, month: number, day: number): string | null {
  const d = new Date(year, month - 1, day, 23, 59, 0, 0)
  if (d.getMonth() !== month - 1 || d.getDate() !== day) return null // 2-31 之类
  return d.toISOString()
}

/* ── API 客户端 ─────────────────────────────────────────────────────────── */

export interface ApiResult<T> {
  ok: boolean
  code?: string
  message?: string
  data?: T
}

async function post(url: string, body: Record<string, unknown>): Promise<ApiResult<unknown>> {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const json = (await resp.json()) as { ok?: boolean; code?: string; message?: string }
    if (!json?.ok) return { ok: false, code: json?.code || 'error', message: json?.message || '' }
    return { ok: true, data: json }
  } catch (err) {
    return { ok: false, code: 'network', message: String(err) }
  }
}

/** 词表投影。取不到就返回 null —— 面板照常能用，只是优先级显示成裸数字。 */
export async function fetchProfile(): Promise<TaskProfile | null> {
  try {
    const resp = await fetch('/api/tbus/profile')
    const json = await resp.json()
    return json?.ok && json.profile ? (json.profile as TaskProfile) : null
  } catch {
    return null
  }
}

/** 首屏**不要**位置链接: 宿主没把「一次拿全部任务+链接」暴露到 CLI, 链接只能一条
 *  任务一次调用, 每次都是一次 node 冷启(~340ms)。35 条就是 1.4s 白屏, 而链接只
 *  用来在行尾画个"会话"小标 —— 拿首屏换一个次要标记是赔本的。
 *  链接走 {@link fetchLinks}, 首屏渲染完再合并进来。 */
export async function fetchBoard(includeArchived = true, withLinks = false): Promise<Board> {
  const resp = await fetch(
    `/api/tbus/board?archived=${includeArchived ? 'true' : 'false'}&links=${withLinks ? 'true' : 'false'}`,
  )
  return (await resp.json()) as Board
}

/** 全部任务的位置链接, 一次给完。取回来之前行尾少个小标而已, 不挡任何操作。 */
export async function fetchLinks(includeArchived = true): Promise<Record<string, LocationLink[]>> {
  try {
    const resp = await fetch(`/api/tbus/links?archived=${includeArchived ? 'true' : 'false'}`)
    const json = await resp.json()
    return json?.ok ? (json.links || {}) : {}
  } catch {
    return {}
  }
}

export async function fetchDetail(taskId: string): Promise<TaskDetail> {
  const resp = await fetch(`/api/tbus/task/${encodeURIComponent(taskId)}`)
  const json = await resp.json()
  return {
    notes: json?.notes || [],
    bindings: json?.bindings || [],
    links: json?.links || [],
  }
}

export function createTask(input: {
  title: string
  description?: string
  projectId?: string | null
  parentTaskId?: string | null
  stateKey?: string
  priority?: number | null
  dueAt?: string | null
}) {
  return post('/api/tbus/task', {
    title: input.title,
    description: input.description || undefined,
    projectId: input.projectId || undefined,
    parentTaskId: input.parentTaskId || undefined,
    stateKey: input.stateKey || undefined,
    priority: input.priority ?? undefined,
    dueAt: input.dueAt || undefined,
  })
}

export function setStatus(id: string, stateKey: string) {
  return post('/api/tbus/task/status', { id, stateKey })
}

export function updateTask(input: {
  id: string
  revision: number
  title: string
  description?: string | null
  priority?: number | null
  dueAt?: string | null
  orderKey?: string | null
  /** 传 null = 移出所有项目（桥翻成 --no-project）。不传 = 不动。 */
  projectId?: string | null
}) {
  // 只发调用方真正给了的字段。多发一个宿主不认识的键，旧 RPC 的 .strict() zod 会把
  // 整条更新拒掉 —— 实证：更新时顺手带上 projectId，连改标题都会失败。
  // 又要区分「没传」与「传了 null」（null = 显式清空），所以用 in 判断而不是 ?? undefined。
  const body: Record<string, unknown> = { id: input.id, revision: input.revision, title: input.title }
  if ('description' in input && input.description != null) body.description = input.description
  if ('priority' in input) body.priority = input.priority
  if ('dueAt' in input) body.dueAt = input.dueAt
  if ('orderKey' in input) body.orderKey = input.orderKey
  if ('projectId' in input) body.projectId = input.projectId
  return post('/api/tbus/task/update', body)
}

export function archiveTask(id: string) {
  return post('/api/tbus/task/archive', { id })
}

/** 归档的反向操作 —— 总线无硬删，归档是可逆的状态位。 */
export function restoreTask(id: string) {
  return post('/api/tbus/task/restore', { id })
}

export function bindConversation(id: string, conversationId: string) {
  return post('/api/tbus/task/bind-conversation', { id, conversationId })
}

/** 后端错误码 → 人话。离线是常态不是 bug，文案要指路。 */
export function humanError(code?: string, message?: string): string {
  if (code === 'runtime_unavailable' || (message || '').includes('runtime_unavailable')) {
    return '总线宿主未连通 · 启动 LiWork / Orca 后重试'
  }
  if (code === 'network') return '面板与 dashboard 断线 · 检查后端后重试'
  // 换项目 / 排序键要新版 RPC。web 端也是由 `electron … --serve` 那个 runtime 进程
  // 提供 RPC 的，它加载的是构建产物 out/main —— 所以要「重建 out/main + 重启 serve」，
  // 光重启不重建没用。旧版会甩一句没头没尾的 "Invalid input"，这里翻成能照做的话。
  if (code === 'invalid_argument' && /invalid input/i.test(message || '')) {
    return '宿主 runtime 还是旧版 · 重建 out/main 并重启 LiTe serve 进程后此项可改'
  }
  if (code === 'revision_conflict') return '已被其它端改动 · 点刷新重载后再改'
  return message || code || '未知错误'
}
