// Todo —— task-core 个人任务总线的人类界面（伴随会话的第四页）。
//
// 结构口径见 docs/plans/_cross/[2026-09-01]TASK-CORE-PERSONAL-TASK-BUS/spec-todo-frontend-rebuild.md
//   · 正交两轴（对齐 Plane / Focalboard / Vikunja / WeKan）：
//     分组维度（状态 / 项目 / 优先级 / 截止）× 布局（账页 / 看板），面板是同一个通用件。
//   · 新建 = 每个面板一个 `+`，点开就地展开输入行，落点即该面板（不再有页顶常驻输入条）。
//   · 拖拽跨面板 = 改该分组维度对应的字段；宿主写不了的维度（项目 / 截止）明说只读。
//   · 交互分层：L1 行/卡扫视 → L2 悬停预览 → L3 详情抽屉 → L4 键盘就地操作。
// 视觉一律走 frostpane token 与 blueprint.css 原子件，不自造按钮/输入/下拉。

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive, CalendarDays, CircleDot, Clock, Columns3, Copy, ExternalLink, Flag, FolderTree,
  Link2, ListPlus, RefreshCw, RotateCcw, Rows3, SlidersHorizontal, SquareArrowOutUpRight, Check,
  BellOff,
} from 'lucide-react'
import { PickerMenu } from '../../components/PickerMenu'
import { BlueprintSelect } from '../../components/BlueprintSelect'
import { useHoverPreview } from '../../components/HoverCard'
import { TaskMenu, type MenuAnchor, type MenuEntry } from './TaskMenu'
import { type ComposerInput } from './TaskComposer'
import { TaskSheet, type SheetPatch } from './TaskSheet'
import { TaskViews } from './TaskViews'
import { TaskPreview } from './TaskItem'
import {
  type Board, type BusTask, type DropPlacement, type Filters, type GroupBy, type GroupSeed, type LaneId,
  type Panel, type SortKey, type TaskDetail,
  EMPTY_FILTERS, GROUP_LABEL, INBOX_KEY, LANES, LANE_LABEL, LANE_WRITE_STATE, SORT_LABEL,
  STATE_LABEL, UNGROUPED, UNRATED, RETIRED_KEY,
  allProjects, archiveTask, bindConversation, buildPanels, createTask, dropWriteFor,
  fetchBoard, fetchDetail, fetchLinks, fetchProfile, hasFilters, humanError, inboxPanel, isDone, isInbox,
  isRetired, laneOf, orderKeyForIndex, reorderTasks, restoreTask, retiredPanel, setStatus,
  setTaskProfile, taskProfile, tierLabel, tiers, updateTask,
} from './taskBus'
import {
  type MsgRow, DEFER_CHOICES, actOnMessage, buildMessageList, deferIso, fetchInboxProjects,
  muteKind,
} from './messages'
import { TaskMessages } from './TaskMessages'
import { useCcTabStatusSnapshot } from '../cc_session/tabStatus'
import { useSessionCompletionAttention } from '../cc_session/sessionAttention'
import { usePanels } from '../../stores/panelsStore'
import './task.css'

const POLL_MS = 20000
const SORT_OPTIONS = (Object.keys(SORT_LABEL) as SortKey[]).map((v) => ({ value: v, label: SORT_LABEL[v] }))
const GROUP_OPTIONS = (Object.keys(GROUP_LABEL) as GroupBy[]).map((v) => ({ value: v, label: GROUP_LABEL[v] }))
const PROJECT_ORDER_STORAGE = 'omni:tbus:project-order:v1'

type PointDropTarget =
  | { kind: 'item'; task: BusTask; panel: Panel; element: HTMLElement }
  | { kind: 'panel'; panel: Panel }
  | null

function readProjectOrder(): string[] {
  try {
    const raw = window.localStorage.getItem(PROJECT_ORDER_STORAGE)
    const parsed = raw ? JSON.parse(raw) : null
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string') : []
  } catch {
    return []
  }
}

function writeProjectOrder(order: string[]): void {
  try { window.localStorage.setItem(PROJECT_ORDER_STORAGE, JSON.stringify(order)) } catch { /* private webview */ }
}

export default function TaskPanel({ sessionId }: { sessionId?: string }) {
  const [board, setBoard] = useState<Board | null>(null)
  const [fatal, setFatal] = useState<{ code?: string; message?: string } | null>(null)
  const [layout, setLayout] = useState<'list' | 'board'>('list')
  // 分组是「每种布局各记一份」的视图设置（Plane / Linear 同口径）：
  // 账页按**项目**分组，看板天然按状态推 —— 经典看板的列就是状态。
  //
  // 「按优先级排」不等于「按优先级分组」（用户 2026-09-04 更正）：**聚合仍然是项目**，
  // 只是项目之间按优先级排（见 taskBus.topOpenTier：拿这个项目里还欠着的最高一级
  // 当它的排位），项目内部再按阶梯排。跨项目混排会把归属打散，不是要的东西。
  const [groupByFor, setGroupByFor] = useState<Record<'list' | 'board', GroupBy>>({
    list: 'project',
    board: 'state',
  })
  const groupBy = groupByFor[layout]
  const setGroupBy = useCallback(
    (next: GroupBy) => setGroupByFor((prev) => ({ ...prev, [layout]: next })),
    [layout],
  )
  // 默认按优先级排 —— 手动序在存量任务上等于 createdAt，读起来就是流水账。
  const [sort, setSort] = useState<SortKey>('priority')
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  // 归档不预设收起：折叠态的面板体是 inert 的，从里面往外拖会拖不动
  // （实拍验到的回归）。它就是一个普通面板，收不收由用户自己决定。
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [rowError, setRowError] = useState<Record<string, string>>({})
  const [sheetError, setSheetError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [composingKey, setComposingKey] = useState<string | null>(null)
  const [composerSeed, setComposerSeed] = useState<GroupSeed | null>(null)
  const [menu, setMenu] = useState<{ task: BusTask; at: MenuAnchor } | null>(null)
  const [dragging, setDragging] = useState<BusTask | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const [projectOrder, setProjectOrder] = useState<string[]>(readProjectOrder)
  const [panelDragging, setPanelDragging] = useState<string | null>(null)
  const [panelDropTarget, setPanelDropTarget] = useState<string | null>(null)
  // 词表来自服务端，取到之后要重渲一次；内容存在 taskBus 单例里，这里只留一个版本号。
  const [profileV, setProfileV] = useState(0)
  const [filterOpen, setFilterOpen] = useState(false)
  const [inboxProjects, setInboxProjects] = useState<any[]>([])
  const [deferredCount, setDeferredCount] = useState(0)
  const [msgMenu, setMsgMenu] = useState<{ row: MsgRow; at: MenuAnchor } | null>(null)
  /** 动作发出后先把行摘掉 —— 宿主一次写要走 node 冷启, 等它回来再消失就是"点了没反应"。 */
  const [hiddenMsgs, setHiddenMsgs] = useState<Set<string>>(new Set())

  const dirtyRef = useRef(false)
  const draggingRef = useRef(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const preview = useHoverPreview()

  /* ── 读 ─────────────────────────────────────────────────────────────── */

  const load = useCallback(async () => {
    try {
      // 首屏只等任务本身(一次信封调用)。位置链接随后补 —— 它只影响行尾一个小标,
      // 不该让整页白着等(用户 2026-09-04: 读取任务总线的速度好慢)。
      const json = await fetchBoard(true)
      if (!json?.ok) { setFatal({ code: json?.code, message: json?.message }); return }
      setFatal(null)
      setBoard(json)
      setLoaded(true)
      const links = await fetchLinks(true)
      if (Object.keys(links).length === 0) return
      setBoard((prev) => prev && {
        ...prev,
        projects: prev.projects.map((p) => ({
          ...p,
          tasks: p.tasks.map((t) => (links[t.id] ? { ...t, locationLinks: links[t.id] } : t)),
        })),
      })
    } catch (err) {
      setFatal({ code: 'network', message: String(err) })
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const loadInbox = useCallback(async () => {
    const { projects, deferred } = await fetchInboxProjects()
    setInboxProjects(projects)
    setDeferredCount(deferred)
  }, [])

  useEffect(() => { void loadInbox() }, [loadInbox])

  // 词表只在挂载时取一次: 它是手改的 JSON, 不需要跟着轮询。取不到也不拦路 ——
  // 优先级会退化成裸数字 P<n>, 面板照常可用。
  useEffect(() => {
    let alive = true
    void fetchProfile().then((profile) => {
      if (!alive) return
      setTaskProfile(profile)
      setProfileV((v) => v + 1)
    })
    return () => { alive = false }
  }, [])

  // 轮询：页面不可见 / 抽屉有未保存草稿 / 正在拖 —— 任一成立就跳过，
  // 不在用户手底下把内容换掉。
  useEffect(() => {
    const idle = () => !document.hidden && !dirtyRef.current && !draggingRef.current
    const timer = window.setInterval(() => { if (idle()) void load() }, POLL_MS)
    const onVisible = () => { if (idle()) void load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [load])

  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    let alive = true
    setDetail(null)
    void fetchDetail(selectedId).then((d) => { if (alive) setDetail(d) })
    return () => { alive = false }
  }, [selectedId])

  /* ── 派生 ───────────────────────────────────────────────────────────── */

  const projects = useMemo(() => allProjects(board), [board])
  const { panels: allPanels, retired, inbox, active } = useMemo(
    () => buildPanels(board, filters, sort, groupBy),
    // profileV 进依赖: 按优先级分组时组头名字来自词表, 词表到位要重算。
    [board, filters, sort, groupBy, profileV],
  )

  // 项目面板可手动排序；任务状态与完成记录仍按总线原样呈现，
  // “本轮清完了”只属于消息队列的空态（由 TaskMessages 处理）。
  const visiblePanels = useMemo(() => {
    const live = allPanels
    if (groupBy !== 'project' || live.length < 2) return live
    const rank = new Map(projectOrder.map((key, index) => [key, index]))
    return live.slice().sort((a, b) => {
      const ar = rank.get(a.key)
      const br = rank.get(b.key)
      if (ar != null && br != null) return ar - br
      if (ar != null) return -1
      if (br != null) return 1
      return 0
    })
  }, [allPanels, groupBy, projectOrder])

  // 看板永远铺满所有面板（这才是看板）；账页把空面板收掉，除非它正开着新建框、
  // 或者全空 —— 全空时至少得留一个面板，否则连 `+` 都没得点。
  // 归档不是一段装饰性的折叠区，它也是一个正经面板：能收拖拽（拖进去 = 归档），
  // 从它里面往外拖 = 恢复。看板里它就是最后一道。
  const archivePanel = useMemo(() => retiredPanel(retired, sort), [retired, sort])
  const inboxPanelNow = useMemo(() => inboxPanel(inbox, sort), [inbox, sort])

  const panels = useMemo(() => {
    const base = layout === 'board'
      ? visiblePanels
      : (() => {
        const kept = visiblePanels.filter((p) => p.tasks.length > 0 || p.key === composingKey)
        return kept.length > 0 ? kept : visiblePanels.slice(0, 1)
      })()
    // 账页里没有归档件时不摆一个空壳；看板里恒显，否则没地方可拖。
    const tail = layout === 'board' || archivePanel.tasks.length > 0 || dragging
      ? [...base, archivePanel]
      : base
    // 收件箱跟归档同一套规矩: 有货就出, 没货不摆空壳; 正在记 / 正在拖时也出来
    // (拖回收件箱 = 撤销分诊)。它摆在最前 —— 未分诊的东西该先看见。
    return inboxPanelNow.tasks.length > 0 || dragging || composingKey === INBOX_KEY
      ? [inboxPanelNow, ...tail]
      : tail
  }, [visiblePanels, layout, composingKey, archivePanel, inboxPanelNow, dragging])

  const online = Boolean(board?.runtimeId) && !fatal
  const openCount = active.filter((t) => !isDone(t)).length

  const selected = useMemo(
    () => active.find((t) => t.id === selectedId)
      || inbox.find((t) => t.id === selectedId)
      || retired.find((t) => t.id === selectedId)
      || null,
    [active, inbox, retired, selectedId],
  )

  // 拖起来的时候把归档展开：收起态只剩一条组头，落点太小按不准。
  const collapsedNow = useMemo(
    () => (dragging ? { ...collapsed, [RETIRED_KEY]: false, [INBOX_KEY]: false } : collapsed),
    [collapsed, dragging],
  )

  const flatOrder = useMemo(
    () => panels.filter((p) => !collapsedNow[p.key]).flatMap((p) => p.tasks.map((t) => t.id)),
    [panels, collapsedNow],
  )

  useEffect(() => {
    if (focusId && !flatOrder.includes(focusId)) setFocusId(flatOrder[0] ?? null)
  }, [flatOrder, focusId])

  /* ── 写（一律乐观更新，失败回滚并就地报错，不弹全局横幅） ─────────────── */

  const patchLocal = useCallback((id: string, patch: Partial<BusTask>) => {
    setBoard((prev) => prev && {
      ...prev,
      projects: prev.projects.map((p) => ({
        ...p,
        tasks: p.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)),
      })),
    })
  }, [])

  const flashRowError = useCallback((id: string, text: string) => {
    setRowError((prev) => ({ ...prev, [id]: text }))
    window.setTimeout(() => {
      setRowError((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    }, 3000)
  }, [])

  // 未看的会话: 用别处同一份完成指纹判据, 不另算一套未读。
  const tabSnapshot = useCcTabStatusSnapshot()
  const seenCompletions = useSessionCompletionAttention((st) => st.seen)
  const markSeen = useSessionCompletionAttention((st) => st.markSeen)
  const openTab = usePanels((st) => st.openTab)

  // 会话那一类也由服务端出(适配器 session/session-ended), 本地不再另算一份 ——
  // 两份会重复, 而且换设备就对不上(已看记录 2026-09-05 已提到后端)。
  const msgRows = useMemo(
    () => buildMessageList(inboxProjects).filter((r) => !hiddenMsgs.has(r.id)),
    [inboxProjects, hiddenMsgs],
  )

  const openMessage = useCallback((row: MsgRow) => {
    if (row.kind === 'session' && row.sessionId) {
      // 打开就算看过了 —— 和别处的未读徽标同一条口径。
      markSeen(row.sessionId, tabSnapshot.metas[row.sessionId])
      openTab({ type: 'cc_session', id: row.sessionId }, row.title)
      return
    }
    if (row.url) window.location.href = row.url
  }, [markSeen, openTab, tabSnapshot])

  const runMsg = useCallback(async (
    row: MsgRow, fn: () => Promise<{ ok: boolean; message?: string }>,
  ) => {
    setHiddenMsgs((prev) => new Set(prev).add(row.id))   // 先消失, 别让人等宿主
    const res = await fn()
    if (!res.ok) {
      setHiddenMsgs((prev) => { const next = new Set(prev); next.delete(row.id); return next })
      flashRowError(row.id, humanError(undefined, res.message))
      return
    }
    void loadInbox()
  }, [flashRowError, loadInbox])

  const msgMenuEntries = useCallback((row: MsgRow): MenuEntry[] => {
    if (row.kind === 'session') {
      return [{
        label: '标记已看',
        icon: <Check size={13} aria-hidden />,
        testid: 'tk-msg-seen',
        onClick: () => {
          if (row.sessionId) markSeen(row.sessionId, tabSnapshot.metas[row.sessionId])
        },
      }]
    }
    const key = row.key
    return [
      {
        label: '已处理',
        icon: <Check size={13} aria-hidden />,
        testid: 'tk-msg-done',
        onClick: () => void runMsg(row, () => actOnMessage(key, 'done')),
      },
      {
        kind: 'group',
        label: '往后拖',
        icon: <Clock size={13} aria-hidden />,
        testid: 'tk-msg-defer',
        options: DEFER_CHOICES.map((c) => ({
          value: c.value,
          label: c.label,
          onClick: () => void runMsg(row, () => actOnMessage(key, 'defer', deferIso(c.value))),
        })),
      },
      {
        label: '转成待办',
        icon: <ListPlus size={13} aria-hidden />,
        testid: 'tk-msg-triage',
        onClick: () => void runMsg(row, () => actOnMessage(key, 'triage')),
      },
      { kind: 'sep' },
      {
        label: '忽略',
        icon: <Archive size={13} aria-hidden />,
        hint: '归档留痕',
        danger: true,
        testid: 'tk-msg-dismiss',
        onClick: () => void runMsg(row, () => actOnMessage(key, 'dismiss')),
      },
      {
        label: '以后不要这类',
        icon: <BellOff size={13} aria-hidden />,
        hint: `${row.rawSource}/${row.rawKind}`,
        testid: 'tk-msg-mute',
        onClick: () => void runMsg(row, async () => {
          const res = await muteKind(row.rawSource, row.rawKind)
          return res.ok ? actOnMessage(key, 'dismiss') : res
        }),
      },
    ]
  }, [runMsg, markSeen, tabSnapshot])

  const changeState = useCallback(async (task: BusTask, stateKey: string) => {
    if (task.stateKey === stateKey) return
    const before = task.stateKey
    patchLocal(task.id, { stateKey })
    const res = await setStatus(task.id, stateKey)
    if (!res.ok) {
      patchLocal(task.id, { stateKey: before })
      flashRowError(task.id, humanError(res.code, res.message))
      return
    }
    void load()
  }, [patchLocal, flashRowError, load])

  /**
   * 组内拖拽：把当前可见顺序变成手动顺序，并把稀疏 orderKey 写回总线。
   * 旧任务常没有 key；一次性规范整组后，后续拖动只会改变稳定的字典序。
   */
  const reorderWithinPanel = useCallback(async (
    panel: Panel,
    draggedTask: BusTask,
    targetTask: BusTask,
    placement: DropPlacement,
  ) => {
    if (draggedTask.id === targetTask.id) return
    const ordered = reorderTasks(panel.tasks, draggedTask.id, targetTask.id, placement)
    if (ordered.every((task, index) => task.orderKey === orderKeyForIndex(index))) {
      setSort('manual')
      return
    }
    setSort('manual')
    const desired = ordered.map((task, index) => ({ task, orderKey: orderKeyForIndex(index) }))
    const before = new Map(panel.tasks.map((task) => [task.id, task.orderKey ?? null]))
    desired.forEach(({ task, orderKey }) => patchLocal(task.id, { orderKey }))
    const results = await Promise.all(desired.map(({ task, orderKey }) => (
      updateTask({ id: task.id, revision: task.revision, title: task.title, orderKey })
    )))
    const failed = results.findIndex((result) => !result.ok)
    if (failed >= 0) {
      desired.forEach(({ task }) => patchLocal(task.id, { orderKey: before.get(task.id) ?? null }))
      const result = results[failed]
      flashRowError(desired[failed].task.id, humanError(result.code, result.message))
      return
    }
    void load()
  }, [flashRowError, load, patchLocal])

  const toggleDone = useCallback((task: BusTask) => {
    void changeState(task, isDone(task) ? 'todo' : 'completed')
  }, [changeState])

  const saveTask = useCallback(async (patch: SheetPatch) => {
    if (!selected) return false
    setBusy(true)
    setSheetError(null)
    const res = await updateTask({ id: selected.id, revision: selected.revision, ...patch })
    setBusy(false)
    if (!res.ok) {
      setSheetError(
        res.code === 'revision_mismatch' || (res.message || '').includes('revision')
          ? '已被其它端改动 · 点刷新重载后再改'
          : humanError(res.code, res.message),
      )
      return false
    }
    dirtyRef.current = false
    await load()
    return true
  }, [selected, load])

  /** 删 = cancelled + archive（总线无硬删，审计留痕）。菜单与详情抽屉共用这一条路径。 */
  // 归档慢的根子是宿主每次写都要起一次 node; 等它回来再让行消失, 手感就是"点了没反应"。
  // 所以先本地标记归档、立刻退出视野, 失败再滚回来。
  const archive = useCallback(async (task: BusTask) => {
    // 先本地归档、立刻退出视野, 失败再滚回来。宿主每次写都要起一次 node,
    // 两步(cancelled + archive)就是两次冷启 —— 等回执再让行消失, 手感就是
    // "点了没反应"(用户 2026-09-04: 为啥我点一个归档要那么久)。
    const before = { stateKey: task.stateKey, archivedAt: task.archivedAt ?? null }
    patchLocal(task.id, { stateKey: 'cancelled', archivedAt: new Date().toISOString() })
    if (selectedId === task.id) setSelectedId(null)
    const cancelled = task.stateKey === 'cancelled'
      ? { ok: true as const }
      : await setStatus(task.id, 'cancelled')
    const done = cancelled.ok ? await archiveTask(task.id) : cancelled
    if (!done.ok) {
      patchLocal(task.id, before)
      const msg = humanError((done as { code?: string }).code, (done as { message?: string }).message)
      flashRowError(task.id, msg)
      return
    }
    void load()
  }, [selectedId, patchLocal, flashRowError, load])

  const restore = useCallback(async (task: BusTask) => {
    setBusy(true)
    const res = await restoreTask(task.id)
    setBusy(false)
    if (!res.ok) { flashRowError(task.id, humanError(res.code, res.message)); return }
    await load()
  }, [flashRowError, load])

  /** priority / dueAt 走 update（要带 revision）；乐观改本地，失败回滚。 */
  const patchField = useCallback(async (
    task: BusTask,
    patch: { priority?: number | null; dueAt?: string | null; projectId?: string | null; orderKey?: string | null },
  ) => {
    const before: Partial<BusTask> = {}
    if ('priority' in patch) before.priority = task.priority ?? null
    if ('dueAt' in patch) before.dueAt = task.dueAt ?? null
    if ('projectId' in patch) before.projectId = task.projectId ?? null
    if ('orderKey' in patch) before.orderKey = task.orderKey ?? null
    patchLocal(task.id, patch)
    const res = await updateTask({ id: task.id, revision: task.revision, title: task.title, ...patch })
    if (!res.ok) {
      patchLocal(task.id, before)
      flashRowError(task.id, humanError(res.code, res.message))
      return
    }
    void load()
  }, [patchLocal, flashRowError, load])

  const bindConversationTo = useCallback(async (task: BusTask) => {
    if (!sessionId) return
    setBusy(true)
    const res = await bindConversation(task.id, sessionId)
    setBusy(false)
    if (!res.ok) { flashRowError(task.id, humanError(res.code, res.message)); return }
    if (selectedId === task.id) { setDetail(null); void fetchDetail(task.id).then(setDetail) }
    void load()
  }, [sessionId, selectedId, flashRowError, load])

  const bindHere = useCallback(async () => {
    if (!selected || !sessionId) return
    setBusy(true)
    const res = await bindConversation(selected.id, sessionId)
    setBusy(false)
    if (!res.ok) { setSheetError(humanError(res.code, res.message)); return }
    setDetail(null)
    void fetchDetail(selected.id).then(setDetail)
    void load()
  }, [selected, sessionId, load])

  const create = useCallback(async (input: ComposerInput) => {
    setBusy(true)
    const res = await createTask({
      title: input.title,
      description: input.description,
      projectId: input.projectId === UNGROUPED ? null : input.projectId,
      parentTaskId: input.parentTaskId,
      stateKey: input.stateKey,
      priority: input.priority,
      dueAt: input.dueAt,
    })
    setBusy(false)
    if (!res.ok) { setFatal({ code: res.code, message: res.message }); return false }
    setFatal(null)
    await load()
    return true
  }, [load])

  /* ── 拖放：落到哪个面板就写那个维度的字段 ───────────────────────────── */

  const onDragStart = useCallback((task: BusTask) => {
    draggingRef.current = true
    setDragging(task)
  }, [])

  const onDragEnd = useCallback(() => {
    draggingRef.current = false
    setDragging(null)
    setDropTarget(null)
  }, [])

  const onDragOverItem = useCallback((target: BusTask, panel: Panel) => {
    if (!dragging || dragging.id === target.id) return
    // 面板内落点用独立 key，避免父 section 的 dragover 把插入提示覆盖掉。
    if (panels.some((candidate) => candidate.key === panel.key && candidate.tasks.some((task) => task.id === dragging.id))) {
      setDropTarget(`item:${target.id}`)
    }
  }, [dragging, panels])

  const onDropItem = useCallback(async (
    target: BusTask,
    panel: Panel,
    placement: DropPlacement,
    sourceOverride?: BusTask,
  ) => {
    const source = sourceOverride ?? dragging
    onDragEnd()
    if (!source || source.id === target.id) return
    const sourcePanel = panels.find((candidate) => candidate.tasks.some((task) => task.id === source.id))
    if (sourcePanel?.key === panel.key) {
      await reorderWithinPanel(panel, source, target, placement)
      return
    }
    // 跨组仍沿用原来的字段变更语义；相对位置只对同组生效，避免一次拖拽
    // 同时触发两次 revision 写入而互相覆盖。
    const write = dropWriteFor(panel, source)
    if (write?.kind === 'status') await changeState(source, write.stateKey)
    else if (write?.kind === 'update') await patchField(source, write)
    else if (write?.kind === 'archive') await archive(source)
    else if (write?.kind === 'restore') await restore(source)
  }, [dragging, onDragEnd, panels, reorderWithinPanel, changeState, patchField, archive, restore])

  // 触屏拖拽使用 elementFromPoint 找到当前指针下的任务或项目面板，
  // 从而不依赖浏览器原生拖放那段不可调的长按等待。
  const dropTargetAtPoint = useCallback((clientX: number, clientY: number): PointDropTarget => {
    if (typeof document === 'undefined') return null
    const hit = document.elementFromPoint(clientX, clientY)
    if (!(hit instanceof Element) || !rootRef.current?.contains(hit)) return null
    const item = hit.closest<HTMLElement>('[data-testid^="tk-item-"]')
    if (item) {
      const testid = item.dataset.testid || ''
      const id = testid.startsWith('tk-item-') ? testid.slice('tk-item-'.length) : ''
      const panel = id ? panels.find((candidate) => candidate.tasks.some((task) => task.id === id)) : undefined
      const task = panel?.tasks.find((candidate) => candidate.id === id)
      if (panel && task) return { kind: 'item', task, panel, element: item }
    }
    const group = hit.closest<HTMLElement>('[data-testid^="tk-group-"]')
    if (group) {
      const testid = group.dataset.testid || ''
      const key = testid.startsWith('tk-group-') ? testid.slice('tk-group-'.length) : ''
      const panel = panels.find((candidate) => candidate.key === key)
      if (panel) return { kind: 'panel', panel }
    }
    return null
  }, [panels])

  const onPointerDragMove = useCallback((source: BusTask, event: React.PointerEvent<HTMLElement>) => {
    const hit = dropTargetAtPoint(event.clientX, event.clientY)
    if (!hit) {
      setDropTarget(null)
      return
    }
    const sourcePanel = panels.find((candidate) => candidate.tasks.some((task) => task.id === source.id))
    if (hit.kind === 'item' && sourcePanel?.key === hit.panel.key) setDropTarget(`item:${hit.task.id}`)
    else setDropTarget(hit.panel.key)
    setPanelDropTarget(null)
  }, [dropTargetAtPoint, panels])

  const onDropPanel = useCallback(async (panel: Panel, sourceOverride?: BusTask) => {
    const task = sourceOverride ?? dragging
    onDragEnd()
    if (!task) return
    const write = dropWriteFor(panel, task)
    if (!write) {
      if (!panel.droppable && panel.readonlyReason) flashRowError(task.id, panel.readonlyReason)
      return
    }
    if (write.kind === 'archive') { await archive(task); return }
    if (write.kind === 'restore') {
      // 从归档拖回正常面板：先恢复，再把目标面板那一维落上去。
      await restore(task)
      const revived = { ...task, archivedAt: null, stateKey: task.stateKey === 'cancelled' ? 'todo' : task.stateKey }
      const next = dropWriteFor(panel, revived)
      if (next && next.kind === 'status') await changeState(revived, next.stateKey)
      else if (next && next.kind === 'update') await patchField(revived, next)
      return
    }
    if (write.kind === 'status') { await changeState(task, write.stateKey); return }
    await patchField(task, write)
  }, [dragging, onDragEnd, changeState, patchField, archive, restore, flashRowError])

  const onPointerDrop = useCallback((source: BusTask, event: React.PointerEvent<HTMLElement>) => {
    const hit = dropTargetAtPoint(event.clientX, event.clientY)
    if (!hit) {
      onDragEnd()
      return
    }
    if (hit.kind === 'item') {
      const rect = hit.element.getBoundingClientRect()
      const placement: DropPlacement = event.clientY >= rect.top + rect.height / 2 ? 'after' : 'before'
      void onDropItem(hit.task, hit.panel, placement, source)
    } else {
      void onDropPanel(hit.panel, source)
    }
  }, [dropTargetAtPoint, onDragEnd, onDropItem, onDropPanel])

  const onPanelDragStart = useCallback((panel: Panel) => {
    if (groupBy !== 'project' || panel.key === RETIRED_KEY || panel.key === INBOX_KEY) return
    setPanelDragging(panel.key)
    setPanelDropTarget(null)
  }, [groupBy])

  const onPanelDragEnd = useCallback(() => {
    setPanelDragging(null)
    setPanelDropTarget(null)
  }, [])

  const onPanelDrop = useCallback((target: Panel, sourceOverride?: string) => {
    const source = sourceOverride ?? panelDragging
    onPanelDragEnd()
    if (!source || source === target.key || groupBy !== 'project') return
    const keys = panels
      .filter((panel) => panel.key !== RETIRED_KEY && panel.key !== INBOX_KEY)
      .map((panel) => panel.key)
    const from = keys.indexOf(source)
    const to = keys.indexOf(target.key)
    if (from < 0 || to < 0) return
    const next = keys.slice()
    next.splice(from, 1)
    next.splice(Math.max(0, next.indexOf(target.key)), 0, source)
    // 当前可能有筛选，只重排屏幕上的项目；未显示的项目继续保留原偏好。
    const merged = [...next, ...projectOrder.filter((key) => !next.includes(key))]
    setProjectOrder(merged)
    writeProjectOrder(merged)
  }, [groupBy, onPanelDragEnd, panelDragging, panels, projectOrder])

  const onPanelPointerDragMove = useCallback((_source: Panel, event: React.PointerEvent<HTMLElement>) => {
    const hit = dropTargetAtPoint(event.clientX, event.clientY)
    setPanelDropTarget(hit ? hit.panel.key : null)
    setDropTarget(null)
  }, [dropTargetAtPoint])

  const onPanelPointerDrop = useCallback((source: Panel, event: React.PointerEvent<HTMLElement>) => {
    const hit = dropTargetAtPoint(event.clientX, event.clientY)
    if (hit) onPanelDrop(hit.panel, source.key)
    else onPanelDragEnd()
  }, [dropTargetAtPoint, onPanelDragEnd, onPanelDrop])

  /* ── 键盘（只在面板内生效，输入态放行，不抢终端按键） ─────────────────── */

  const focusRow = useCallback((id: string | null) => {
    if (!id) return
    setFocusId(id)
    rootRef.current
      ?.querySelector<HTMLElement>(`[data-testid="tk-item-${CSS.escape(id)}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [])

  const move = useCallback((delta: number) => {
    if (flatOrder.length === 0) return
    const at = focusId ? flatOrder.indexOf(focusId) : -1
    const next = at < 0
      ? (delta > 0 ? 0 : flatOrder.length - 1)
      : Math.min(flatOrder.length - 1, Math.max(0, at + delta))
    focusRow(flatOrder[next])
  }, [flatOrder, focusId, focusRow])

  /** 提单子 —— 没有专门的按钮: 它就是"在收件箱面板里新建一条", 和别的面板同一条路径。
   *  留一个 `i` 是因为快捷键不占版面; 鼠标党走面板组头的 `+`, 或在新建框里把状态选成收件箱。 */
  const openIntake = useCallback(() => {
    setComposerSeed({ stateKey: 'inbox', projectId: null })
    setComposingKey(INBOX_KEY)
  }, [])

  const focusedTask = useMemo(
    () => active.find((t) => t.id === focusId) || inbox.find((t) => t.id === focusId) || null,
    [active, inbox, focusId],
  )
  const panelOfFocus = useCallback(
    () => panels.find((p) => p.tasks.some((t) => t.id === focusId))?.key ?? panels[0]?.key ?? null,
    [panels, focusId],
  )

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable

    if (e.key === 'Escape') {
      if (menu) { setMenu(null); e.preventDefault(); return }
      if (composingKey) { setComposingKey(null); setComposerSeed(null); e.preventDefault(); return }
      if (selectedId) { setSelectedId(null); e.preventDefault(); return }
      if (filters.query) { setFilters((f) => ({ ...f, query: '' })); e.preventDefault(); return }
      if (typing) (target as HTMLInputElement).blur()
      return
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return

    switch (e.key) {
      case '/':
        e.preventDefault(); searchRef.current?.focus(); break
      case 'n':
        // 在「当前焦点所在的面板」里开新建框 —— 落点跟着上下文走。
        e.preventDefault(); setComposerSeed(null); setComposingKey(panelOfFocus()); break
      case 'i':
        // 提单子 —— 体验产品时随手记, 不用先找按钮。
        e.preventDefault(); openIntake(); break
      case 'r':
        e.preventDefault(); void load(); break
      case 'j': case 'ArrowDown':
        e.preventDefault(); move(1); break
      case 'k': case 'ArrowUp':
        e.preventDefault(); move(-1); break
      case 'Enter':
        if (focusedTask) { e.preventDefault(); setSelectedId((id) => (id === focusedTask.id ? null : focusedTask.id)) }
        break
      case ' ':
        if (focusedTask) { e.preventDefault(); toggleDone(focusedTask) }
        break
      case 'e':
        if (focusedTask) {
          e.preventDefault()
          setSelectedId(focusedTask.id)
          window.setTimeout(() => rootRef.current?.querySelector<HTMLInputElement>('[data-testid="tk-sheet-title"]')?.focus(), 60)
        }
        break
      case '[': case ']': {
        if (!focusedTask) break
        e.preventDefault()
        const at = panels.findIndex((p) => p.tasks.some((t) => t.id === focusedTask.id))
        const next = panels[Math.min(panels.length - 1, Math.max(0, at + (e.key === ']' ? 1 : -1)))]
        focusRow(next?.tasks[0]?.id ?? null)
        break
      }
      default:
        if (/^[1-5]$/.test(e.key) && focusedTask) {
          e.preventDefault()
          void changeState(focusedTask, LANE_WRITE_STATE[LANES[Number(e.key) - 1]])
        }
    }
  }

  /* ── 动作菜单（右键任意一行/卡，或点行尾 ⋯）───────────────────────────
     动作必须有看得见的入口 —— 上一版把归档这类操作只放在详情抽屉里，
     用户原话「我归档不了 / 我是找不到入口」。这里把全部动作摊到菜单上。
     ────────────────────────────────────────────────────────────────── */

  const copy = useCallback((text: string, task: BusTask) => {
    const done = () => flashRowError(task.id, '已复制')
    if (navigator.clipboard?.writeText) { void navigator.clipboard.writeText(text).then(done, done); return }
    // 受限环境（部分 webview）没有 clipboard API，退回 execCommand，不静默失败。
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } finally { ta.remove() }
    done()
  }, [flashRowError])

  const dueOptions = useMemo(() => {
    const at = (days: number) => {
      const d = new Date()
      d.setDate(d.getDate() + days)
      d.setHours(23, 59, 0, 0)
      return d.toISOString()
    }
    return [
      { value: 'today', label: '今天', iso: at(0) },
      { value: 'tomorrow', label: '明天', iso: at(1) },
      { value: 'week', label: '一周后', iso: at(7) },
      { value: 'clear', label: '清除', iso: null },
    ]
  }, [])

  const menuEntries = useCallback((task: BusTask): MenuEntry[] => {
    const retiredNow = isRetired(task)
    return [
      {
        kind: 'group',
        label: '状态',
        icon: <CircleDot size={13} aria-hidden />,
        value: task.stateKey,
        testid: 'tk-menu-state',
        options: Object.keys(STATE_LABEL).map((k) => ({
          value: k, label: STATE_LABEL[k], onClick: () => void changeState(task, k),
        })),
      },
      {
        kind: 'group',
        label: '优先级',
        icon: <Flag size={13} aria-hidden />,
        value: task.priority == null ? '' : String(task.priority),
        testid: 'tk-menu-priority',
        // 阶梯名字来自个人附加层词表(data/task_core_profile.json), 不在前端写第二份。
        options: [
          ...tiers().map((t) => ({ value: String(t.priority), label: t.short || t.name })),
          { value: '', label: taskProfile()?.unratedLabel || UNRATED },
        ].map((o) => ({
          ...o, onClick: () => void patchField(task, { priority: o.value === '' ? null : Number(o.value) }),
        })),
      },
      {
        kind: 'group',
        label: '项目',
        icon: <FolderTree size={13} aria-hidden />,
        value: task.projectId ?? UNGROUPED,
        testid: 'tk-menu-project',
        options: [
          ...projects.filter((p) => p.id !== UNGROUPED).map((p) => ({ value: p.id, label: p.label })),
          { value: UNGROUPED, label: UNGROUPED },
        ].map((o) => ({
          ...o,
          onClick: () => void patchField(task, { projectId: o.value === UNGROUPED ? null : o.value }),
        })),
      },
      {
        kind: 'group',
        label: '截止',
        icon: <CalendarDays size={13} aria-hidden />,
        value: '',
        testid: 'tk-menu-due',
        options: dueOptions.map((o) => ({
          value: o.value, label: o.label, onClick: () => void patchField(task, { dueAt: o.iso }),
        })),
      },
      { kind: 'sep' },
      ...(isInbox(task)
        ? [{
          label: '分诊：转待办',
          icon: <SquareArrowOutUpRight size={13} aria-hidden />,
          hint: '出收件箱',
          testid: 'tk-menu-triage',
          onClick: () => void changeState(task, 'todo'),
        } as MenuEntry, { kind: 'sep' } as MenuEntry]
        : []),
      {
        label: '打开详情',
        icon: <ExternalLink size={13} aria-hidden />,
        hint: 'Enter',
        testid: 'tk-menu-open',
        onClick: () => { setFocusId(task.id); setSelectedId(task.id) },
      },
      {
        label: '新建子任务',
        icon: <ListPlus size={13} aria-hidden />,
        testid: 'tk-menu-subtask',
        onClick: () => {
          const key = panels.find((p) => p.tasks.some((t) => t.id === task.id))?.key ?? panels[0]?.key ?? null
          setComposerSeed({ projectId: task.projectId, parentTaskId: task.id, stateKey: 'todo' })
          setComposingKey(key)
        },
      },
      {
        label: sessionId ? '绑定当前会话' : '绑定当前会话',
        icon: <Link2 size={13} aria-hidden />,
        disabled: !sessionId,
        disabledReason: '当前没有会话可绑',
        testid: 'tk-menu-bind',
        onClick: () => { if (sessionId) void bindConversationTo(task) },
      },
      { kind: 'sep' },
      { label: '复制标题', icon: <Copy size={13} aria-hidden />, onClick: () => copy(task.title, task) },
      { label: '复制 id', icon: <Copy size={13} aria-hidden />, hint: task.id.slice(0, 6), onClick: () => copy(task.id, task) },
      { kind: 'sep' },
      retiredNow
        ? {
          label: '从归档恢复',
          icon: <RotateCcw size={13} aria-hidden />,
          testid: 'tk-menu-restore',
          onClick: () => void restore(task),
        }
        : {
          label: '归档（放弃）',
          icon: <Archive size={13} aria-hidden />,
          danger: true,
          testid: 'tk-menu-archive',
          onClick: () => void archive(task),
        },
    ]
  }, [changeState, patchField, dueOptions, panels, projects, sessionId, copy, restore, archive])

  /* ── 渲染 ───────────────────────────────────────────────────────────── */

  const laneOptions = LANES.map((lane) => ({
    value: lane,
    label: LANE_LABEL[lane],
    count: active.filter((t) => laneOf(t) === lane).length,
  }))
  const projectOptions = projects.map((p) => ({ value: p.id, label: p.label, count: p.count }))

  const itemHooks = {
    onOpen: (task: BusTask) => {
      preview.hideNow()
      setFocusId(task.id)
      setSelectedId((id) => (id === task.id ? null : task.id))
    },
    onToggleDone: toggleDone,
    onHover: (anchor: HTMLElement, task: BusTask) => {
      if (draggingRef.current) return
      preview.show(anchor, <TaskPreview task={task} all={active} />)
    },
    onHoverEnd: preview.scheduleHide,
    onMenu: (task: BusTask, at: MenuAnchor) => { preview.hideNow(); setMenu({ task, at }) },
  }

  let body: React.ReactNode
  if (fatal) {
    body = (
      <Blank
        text={humanError(fatal.code, fatal.message)}
        action={<button type="button" className="v2-btn outline sm" onClick={() => void load()}>重试</button>}
      />
    )
  } else if (!loaded) {
    body = <Blank text="读取总线…" />
  } else if (active.length === 0 && hasFilters(filters)) {
    body = (
      <Blank
        text="当前筛选下无条目"
        action={<button type="button" className="v2-btn ghost sm" onClick={() => setFilters(EMPTY_FILTERS)}>清除筛选</button>}
      />
    )
  } else {
    body = (
      <TaskViews
        panels={panels}
        layout={layout}
        sort={sort}
        collapsed={collapsedNow}
        onToggleCollapse={(key) => setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }))}
        composingKey={composingKey}
        composerSeed={composerSeed}
        onOpenComposer={(key) => { setComposerSeed(null); setComposingKey(key) }}
        onCloseComposer={() => { setComposingKey(null); setComposerSeed(null) }}
        onCreate={create}
        all={active}
        projects={projects}
        showProject={groupBy !== 'project' && projects.length > 1}
        selectedId={selectedId}
        focusId={focusId}
        rowError={rowError}
        menuTaskId={menu?.task.id ?? null}
        busy={busy}
        dragging={dragging?.id ?? null}
        dropTarget={dropTarget}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragOverItem={onDragOverItem}
        onDropItem={onDropItem}
        onPointerDragMove={onPointerDragMove}
        onPointerDrop={onPointerDrop}
        onDragOverPanel={(p) => setDropTarget(p.key)}
        onDropPanel={(p) => void onDropPanel(p)}
        panelDraggable={groupBy === 'project'}
        panelDragKey={panelDragging}
        panelDropTarget={panelDropTarget}
        onPanelDragStart={onPanelDragStart}
        onPanelDragEnd={onPanelDragEnd}
        onPanelDragOver={(p) => setPanelDropTarget(p.key)}
        onPanelDrop={onPanelDrop}
        onPanelPointerDragMove={onPanelPointerDragMove}
        onPanelPointerDrop={onPanelPointerDrop}
        {...itemHooks}
      />
    )
  }

  return (
    <div
      ref={rootRef}
      className="tk-root bp-opaque-scene"
      data-view={layout}
      data-sheet={selected ? '1' : '0'}
      data-busy={busy ? '1' : '0'}
      data-testid="tbus-panel"
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      <div className="tk-cmd">
        <span
          className={`v2-status ${online ? 'st-ok' : 'st-err'} tk-led`}
          title={online ? `总线在线 · runtime ${board?.runtimeId}` : '总线宿主未连通'}
          data-testid="tk-led"
        >
          <span className="led" />
        </span>
        <span className="v2-search tk-search">
          <input
            ref={searchRef}
            className="bp-input"
            value={filters.query}
            placeholder="搜索"
            aria-label="搜索任务"
            data-testid="tk-search"
            onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
          />
        </span>
        {/* 布局只是两个图标, 和搜索并成一行; 计数去掉 —— 数字不帮你决定看哪个。 */}
        <button
          type="button"
          className="v2-iconbtn"
          aria-label="账页"
          aria-pressed={layout === 'list'}
          data-active={layout === 'list' ? '1' : undefined}
          data-testid="tk-layout-list"
          title="账页"
          onClick={() => { setLayout('list'); setComposingKey(null); setCollapsed({}) }}
        >
          <Rows3 size={14} />
        </button>
        <button
          type="button"
          className="v2-iconbtn"
          aria-label="看板"
          aria-pressed={layout === 'board'}
          data-active={layout === 'board' ? '1' : undefined}
          data-testid="tk-layout-board"
          title="看板"
          onClick={() => { setLayout('board'); setComposingKey(null); setCollapsed({}) }}
        >
          <Columns3 size={14} />
        </button>
        {/* 筛选收成一个按钮, 点了才展开 —— 平铺一排选择器等于把每天用一次的东西
            和每天用十次的东西摆得一样大。 */}
        <button
          type="button"
          className={`v2-iconbtn ${hasFilters(filters) ? 'tk-filter-on' : ''}`}
          aria-label="筛选"
          aria-expanded={filterOpen}
          data-active={filterOpen || hasFilters(filters) ? '1' : undefined}
          data-testid="tk-filter"
          title="筛选与分组"
          onClick={() => setFilterOpen((on) => !on)}
        >
          <SlidersHorizontal size={14} />
        </button>
        <button type="button" className="v2-iconbtn tk-refresh" title="刷新（快捷键 r）" aria-label="刷新" onClick={() => void load()}>
          <RefreshCw size={14} />
        </button>
      </div>

      {filterOpen && (
        <div className="tk-filters" data-testid="tk-filters">
          <BlueprintSelect
            value={groupBy}
            options={GROUP_OPTIONS}
            label="分组"
            aria-label="分组维度"
            data-testid="tk-groupby"
            onChange={(v) => { setGroupBy(v as GroupBy); setComposingKey(null); setCollapsed({}) }}
          />
          <BlueprintSelect
            value={sort}
            options={SORT_OPTIONS}
            label="排序"
            aria-label="排序"
            onChange={(v) => setSort(v as SortKey)}
          />
          <span className="tk-projects-picker">
            <PickerMenu
              label="项目"
              options={projectOptions}
              selected={filters.projects}
              onChange={(next) => setFilters((f) => ({ ...f, projects: next }))}
            />
          </span>
          <PickerMenu
            label="状态"
            options={laneOptions}
            selected={filters.lanes}
            onChange={(next) => setFilters((f) => ({ ...f, lanes: next as LaneId[] }))}
          />
          {hasFilters(filters) && (
            <button type="button" className="v2-btn ghost sm" onClick={() => setFilters(EMPTY_FILTERS)}>
              清除
            </button>
          )}
        </div>
      )}

      <div className="tk-main">
        {!fatal && projects.length > 1 && (
          <nav className="tk-rail" aria-label="项目作用域">
            <button
              type="button"
              className="v2-checkrow"
              aria-checked={filters.projects.length === 0}
              role="checkbox"
              onClick={() => setFilters((f) => ({ ...f, projects: [] }))}
            >
              <span className="cb" aria-hidden="true" />
              <span className="cr-t">全部</span>
              <span className="cr-d">{openCount}</span>
            </button>
            {projects.map((p) => {
              const on = filters.projects.includes(p.id)
              return (
                <button
                  key={p.id}
                  type="button"
                  className="v2-checkrow"
                  role="checkbox"
                  aria-checked={on}
                  onClick={() => setFilters((f) => ({
                    ...f,
                    projects: on ? f.projects.filter((x) => x !== p.id) : [...f.projects, p.id],
                  }))}
                >
                  <span className="cb" aria-hidden="true" />
                  <span className="cr-t">{p.label}</span>
                  <span className="cr-d">{p.count}</span>
                </button>
              )
            })}
          </nav>
        )}

        <div className="tk-body" data-testid="tk-body">
          <TaskMessages
            rows={msgRows}
            deferred={deferredCount}
            menuRowId={msgMenu?.row.id ?? null}
            rowError={rowError}
            onOpen={openMessage}
            onMenu={(row, at) => setMsgMenu({ row, at })}
          />
          {body}

        </div>

        {selected && (
          <>
            <div className="tk-scrim" onClick={() => setSelectedId(null)} aria-hidden="true" />
            <TaskSheet
              key={selected.id}
              task={selected}
              detail={detail}
              projects={projects}
              sessionId={sessionId}
              busy={busy}
              error={sheetError}
              onClose={() => setSelectedId(null)}
              onDirtyChange={(d) => { dirtyRef.current = d }}
              onSave={saveTask}
              onStatus={(stateKey) => void changeState(selected, stateKey)}
              onArchive={() => void archive(selected)}
              onBindConversation={() => void bindHere()}
            />
          </>
        )}
      </div>

      {menu && (
        <TaskMenu
          anchor={menu.at}
          entries={menuEntries(menu.task)}
          onClose={() => setMenu(null)}
        />
      )}

      {msgMenu && (
        <TaskMenu
          anchor={msgMenu.at}
          entries={msgMenuEntries(msgMenu.row)}
          onClose={() => setMsgMenu(null)}
        />
      )}

      {preview.host}
    </div>
  )
}

/** 空态 = 图纸上的未晒图区：虚线框 + 制图角标 + 打字机文案，不是红横幅。 */
function Blank({ text, action }: { text: string; action?: React.ReactNode }) {
  return (
    <div className="tk-blank bp-corners" data-testid="tk-blank">
      <p>{text}</p>
      {action}
    </div>
  )
}
