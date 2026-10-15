// 审阅台(2026-08-14 视觉复位第二轮): 形态与旧审阅台一致 —— 左密集列表行 + 右详情窗口,
// 顶部紧凑筛选行(搜索 + 状态多选 PickerMenu + 图标钮)。样式**不另造一套**: 直接消费旧审阅台
// 的蓝图类(reviewFlow.css 的 .rf-tools/.rf-side/.rf-sechead/.rf-qrow/.rf-bar + blueprint.css 的
// .v2-search/.v2-iconbtn/.v2-count/.bp-dim), 本页 css 只补页面壳与 --canvas-* 变量。
// 上一轮的教训: 自造 .rb-* 类而没写 CSS, 界面裸奔 —— 现在除页面壳外零自造类。
//
// 数据读写全走 material 契约(canvas-domain 架构, 2026-08-16):
// 列表读 GET /api/review/inbox(dashboard 跨域聚合: builtin + 各注册域), 失败回退
// GET /bside/api/review/inbox(workshop 单域)。裁决按 item.domain 分派:
// builtin → invoke 桥 notes_canvas_mutate; workshop → POST /bside/api/canvases/:id/mutations
// (upsert_cards, 409/冲突重取重试一次)。
// 旧 /lofa/overlay/review-queue 读的是 dashboard 侧画布存储 —— M4 换源后恒空, 已弃。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ExternalLink, RefreshCw, Search, Image as ImageIcon, Music, Video, FileText, Globe, File as FileIcon,
  ChevronDown, ChevronRight, Archive,
} from 'lucide-react'
import type { CardSubmission, SubmissionState } from '@overlay-notes/regions/materialCanvasModel'
import {
  ReviewThumb,
  SubmissionVerdictBar,
  type ReviewItemData,
} from '@overlay-notes/regions/reviewShared'
import { webNotesInvoke } from '@overlay-notes/webNotesInvoke'
import { VSplitter } from '../../shell/Splitter'
import { SidebarToggleButton } from '../../shell/TabSidecar'
import { useNarrowPane } from '../../shell/useNarrowPane'
import { PickerMenu } from '../../components/PickerMenu'
import { DimText } from '../../components/Segmented'
import { buildRenderUnits } from '../review/shared'
import '../review/reviewFlow.css'
import './reviewBoard.css'

/** 聚合收件箱(/api/review/inbox, builtin+各域)与旧 workshop inbox 的单项形状。 */
interface InboxEntry {
  ref: string
  domain?: string
  canvas: string
  canvasTitle: string
  cardId: string
  title: string
  origin: string | null
  state: SubmissionState
  submittedAt: string
  /** 审阅库融合(2026-08-20)后随行的一等字段。旧 workshop inbox 不带，故全部可选。 */
  archived?: boolean
  tier?: string
  project?: string
  track?: string
  materialKind?: string
  reason?: string
  verdictAt?: string
  counts?: { comments?: number; annotations?: number; history?: number }
}

/** 会话画布 id = "session-" + base64("external:<ptyId>") → 还原 pty id(跳转/会话标签用)。 */
function ptyIdFromCanvas(canvas: string): string {
  if (!canvas.startsWith('session-')) return canvas
  try {
    const b64 = canvas.slice('session-'.length).replace(/-/g, '+').replace(/_/g, '/')
    const decoded = atob(b64)
    if (decoded.startsWith('external:')) return decoded.slice('external:'.length)
  } catch { /* 非标准编码, 原样返回 */ }
  return canvas
}

function toReviewItem(entry: InboxEntry): ReviewItemData {
  const sessionId = ptyIdFromCanvas(entry.canvas)
  const domain = entry.domain || 'workshop'
  return {
    key: `${domain}|${entry.canvas}|${entry.cardId}`,
    cardId: entry.cardId,
    canvasKey: entry.canvas,
    sessionId,
    sessionLabel: sessionId.slice(0, 8),
    canvasTitle: entry.canvasTitle ?? '',
    title: entry.title ?? '',
    kind: entry.materialKind ?? '',
    adapter: '',
    mime: '',
    fileToken: null,
    url: null,
    editableId: null,
    submission: {
      state: entry.state,
      submittedAt: entry.submittedAt,
      submittedBy: entry.origin ?? '',
      ...(entry.archived ? { archived: true } : {}),
      ...(entry.tier ? { tier: entry.tier } : {}),
      ...(entry.track ? { track: entry.track } : {}),
      ...(entry.project ? { project: entry.project } : {}),
      ...(entry.reason ? { reason: entry.reason } : {}),
      ...(entry.verdictAt ? { verdictAt: entry.verdictAt } : {}),
    },
    domain,
  }
}

const ACTOR = 'dashboard:review-board'
const CANVAS_URL = (canvas: string) => `/bside/api/canvases/${encodeURIComponent(canvas)}`

interface WorkshopCanvasResponse {
  document: {
    id?: string
    title?: string
    revision: number
    cards: Array<Record<string, unknown> & { id: string; kind?: string; adapter?: string; text?: string; source?: Record<string, unknown> | null }>
  }
  workspace_ref?: { kind: string; ref: string }
  revision?: number
}

/** 选中项富化: 拉画布文档取该卡的 kind/adapter/正文引用(列表阶段不拉, 全量画布太重)。
 *  按域路由: builtin → dashboard 域 API; workshop → /bside 同源代理。 */
async function enrichFromCanvas(item: ReviewItemData): Promise<Partial<ReviewItemData> | null> {
  const url = item.domain === 'builtin'
    ? `/api/domains/builtin/canvases/${encodeURIComponent(item.canvasKey)}`
    : CANVAS_URL(item.canvasKey)
  try {
    const res = await fetch(url)
    if (!res.ok) return null
    const data = await res.json() as WorkshopCanvasResponse
    const doc = data.document
    const card = doc?.cards?.find((c) => c.id === item.cardId)
    if (!card) return null
    const source = (card.source ?? null) as { kind?: string; id?: string; token?: string; mime?: string; url?: string; editableId?: string } | null
    return {
      kind: String(card.kind ?? ''),
      adapter: String(card.adapter ?? ''),
      mime: source?.mime ?? '',
      fileToken: source?.token ? String(source.token) : null,
      // web 链接卡的活 URL(结构化网页材料本体, 如 EP2 网页影片): 文件卡有 token 时优先 token。
      url: source?.url ? String(source.url) : null,
      // 正文引用: workshop 文本端点用 editableId; 提交链落 dashboard 侧 note-store 的卡
      // 只有 file token —— 两者都给正文预取(见 fetchText 的双路兜底)。
      editableId: String(source?.editableId ?? source?.id ?? ''),
      canvasTitle: item.canvasTitle || doc?.title || '',
    }
  } catch { return null }
}

const fetchText = async (id: string): Promise<string> => {
  // 双路: ① dashboard invoke 的 notes_text_get(omni review submit 的 text 正文落 overlay-note-store,
  // 走 lofa 桥按 editableId 读) —— 这是当前唯一提交源, 先走; ② workshop 文本端点(自家 note-store)兜底。
  // 2026-09-01 调序: 原先 ① 是 /bside/api/material-notes/text/<id>, bside 无此路由, 每张卡打开都先吃一个 404。
  try {
    const m = await webNotesInvoke<{ content?: string }>('notes_text_get', { id })
    if (m?.content) return m.content
  } catch { /* 落第二路 */ }
  try {
    const r = await fetch(`/bside/api/material-notes/text/${encodeURIComponent(id)}`)
    if (r.ok) {
      const m = await r.json() as { content?: string }
      if (m?.content) return m.content
    }
  } catch { /* 双路皆失败 */ }
  return ''
}

/** 裁决写回 workshop 画布(get → upsert_cards mutate; 409 冲突时重取重试一次)。 */
async function verdictOnWorkshopCanvas(item: ReviewItemData, pass: boolean, reason: string | undefined, actor: string): Promise<void> {
  const attempt = async () => {
    const res = await fetch(CANVAS_URL(item.canvasKey))
    if (!res.ok) throw new Error(`画布读取失败 ${res.status}`)
    const data = await res.json() as WorkshopCanvasResponse
    const doc = data.document
    const card = doc?.cards?.find((c) => c.id === item.cardId)
    const submission = card && (card as { submission?: Record<string, unknown> }).submission
    if (!doc || !card || !submission || typeof submission !== 'object') {
      throw new Error(`目标卡或提审记录不存在: ${item.canvasKey}/${item.cardId}`)
    }
    const next = {
      ...card,
      submission: {
        ...submission,
        state: pass ? 'fully-passed' : 'not-fully-passed',
        verdictAt: new Date().toISOString(),
        verdictBy: actor,
        ...(reason ? { reason } : {}),
      },
    }
    const body = {
      workspace_ref: data.workspace_ref ?? { kind: 'product', ref: 'workshop' },
      actor_ref: { kind: 'dashboard', ref: 'review-board' },
      base_revision: doc.revision ?? data.revision ?? 1,
      idempotency_key: `verdict-${item.cardId}-${Date.now()}`,
      patch: { upsert_cards: [next] },
    }
    const mut = await fetch(`${CANVAS_URL(item.canvasKey)}/mutations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (mut.status === 409) throw new Error('conflict')
    if (!mut.ok) throw new Error(`画布写回失败 ${mut.status} ${mut.statusText}`)
  }
  try {
    await attempt()
  } catch (e) {
    if (String(e instanceof Error ? e.message : e) !== 'conflict') throw e
    await attempt()
  }
}

/** 裁决写回 builtin 域画布(invoke 桥 notes_canvas_get/mutate; 冲突重取重试一次)。 */
async function verdictOnBuiltinCanvas(item: ReviewItemData, pass: boolean, reason: string | undefined, actor: string): Promise<void> {
  const attempt = async () => {
    const raw = await webNotesInvoke<string | null>('notes_canvas_get', { id: item.canvasKey })
    if (!raw) throw new Error(`画布读取失败: ${item.canvasKey}`)
    const doc = JSON.parse(raw) as { revision?: number; sessionId?: string; cards?: Array<Record<string, unknown> & { id: string }> }
    const card = doc.cards?.find((c) => c.id === item.cardId)
    const submission = card && (card as { submission?: Record<string, unknown> }).submission
    if (!card || !submission || typeof submission !== 'object') {
      throw new Error(`目标卡或提审记录不存在: ${item.canvasKey}/${item.cardId}`)
    }
    const next = {
      ...card,
      submission: {
        ...submission,
        state: pass ? 'fully-passed' : 'not-fully-passed',
        verdictAt: new Date().toISOString(),
        verdictBy: actor,
        ...(reason ? { reason } : {}),
      },
    }
    try {
      await webNotesInvoke('notes_canvas_mutate', {
        id: item.canvasKey,
        session_id: doc.sessionId ?? '',
        actor,
        base_revision: doc.revision ?? 0,
        mutation_id: `verdict-${item.cardId}-${Date.now()}`,
        patch: { upsert_cards: [next] },
      })
    } catch (e) {
      const message = String(e instanceof Error ? e.message : e)
      if (message.includes('冲突') || message.includes('409') || message.includes('conflict')) throw new Error('conflict')
      throw e
    }
  }
  try {
    await attempt()
  } catch (e) {
    if (String(e instanceof Error ? e.message : e) !== 'conflict') throw e
    await attempt()
  }
}

/** 裁决按域分派(canvas-domain 架构): builtin 走 invoke 桥, workshop 走其协议端点。 */
async function verdictOnCanvas(item: ReviewItemData, pass: boolean, reason: string | undefined, actor: string): Promise<void> {
  if (item.domain === 'builtin') return verdictOnBuiltinCanvas(item, pass, reason, actor)
  return verdictOnWorkshopCanvas(item, pass, reason, actor)
}

/** 模态 = icon, 不占文字行(与旧队列行的 KindIcon 同位同尺寸)。 */
function AdapterIcon({ adapter, kind }: { adapter: string; kind: string }) {
  const size = 15
  if (adapter === 'image') return <ImageIcon size={size} aria-hidden />
  if (adapter === 'audio') return <Music size={size} aria-hidden />
  if (adapter === 'video') return <Video size={size} aria-hidden />
  if (adapter === 'web') return <Globe size={size} aria-hidden />
  if (adapter === 'text' || kind === 'text') return <FileText size={size} aria-hidden />
  return <FileIcon size={size} aria-hidden />
}

/** 状态色(蓝图 token, 不自定色值): 未看=warn / 已看=text-3 / 通过=ok / 未过=err。 */
const STATE_COLOR: Record<SubmissionState, string> = {
  unreviewed: 'var(--fp-warn)',
  seen: 'var(--fp-text-3)',
  'fully-passed': 'var(--fp-ok)',
  'not-fully-passed': 'var(--fp-err)',
}
const STATE_LABEL: Record<SubmissionState, string> = {
  unreviewed: '未看',
  seen: '已看',
  'fully-passed': '完全通过',
  'not-fully-passed': '未完全通过',
}
const STATE_ORDER: SubmissionState[] = ['unreviewed', 'seen', 'fully-passed', 'not-fully-passed']
// 与旧审阅台同口径: 进来只看待审(这里是「未看」), 其余状态要显式勾。
const DEFAULT_STATES: SubmissionState[] = ['unreviewed']
/** 聚合头上的模态中文名(与 AdapterIcon 同一套分型)。 */
const MOD_LABEL: Record<string, string> = {
  image: '图片', audio: '音频', video: '视频', web: '网页快照', pdf: 'PDF', text: '文本', file: '文件',
}

export function ReviewBoard({ focusCardId }: { focusCardId?: string }) {
  const [items, setItems] = useState<ReviewItemData[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [stateSel, setStateSel] = useState<Set<SubmissionState>>(() => new Set(DEFAULT_STATES))
  const [showArchived, setShowArchived] = useState(false)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [leftWidth, setLeftWidth] = useState(360)
  const [expandedClusters, setExpandedClusters] = useState<string[]>([])
  const rowRefs = useRef(new Map<string, HTMLDivElement>())
  const rootRef = useRef<HTMLDivElement>(null)
  // 手机/窄侧栏: 列表 360 + 详情根本排不下(390 屏上详情只剩 20px)。此档改成
  // 主从二选一 —— 点开一条就整幅看详情, 详情条上的同一个按钮退回列表。
  const narrow = useNarrowPane(rootRef)
  const selectItem = useCallback((key: string) => {
    setSelectedKey(key)
    if (narrow) setSidebarOpen(false)
  }, [narrow])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    fetch('/api/review/inbox')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json()
      })
      .then((d: { items?: InboxEntry[] }) => setItems((d.items ?? []).map(toReviewItem)))
      .catch(() => fetch('/bside/api/review/inbox')
        .then((r) => {
          if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
          return r.json()
        })
        .then((d: { unread?: InboxEntry[] }) => setItems((d.unread ?? []).map((e) => toReviewItem({ ...e, domain: 'workshop' })))))
      .catch((e) => setError(String(e instanceof Error ? e.message : e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const onVerdict = useCallback(async (item: ReviewItemData, pass: boolean, reason?: string) => {
    setError(null)
    try {
      await verdictOnCanvas(item, pass, reason, ACTOR)
      setItems((prev) => prev.map((x) => x.key === item.key ? {
        ...x,
        submission: {
          ...x.submission,
          state: pass ? 'fully-passed' : 'not-fully-passed',
          verdictAt: new Date().toISOString(),
          verdictBy: ACTOR,
          ...(reason ? { reason } : {}),
        },
      } : x))
    } catch (e) {
      setError(`裁决写回失败: ${String(e instanceof Error ? e.message : e)}`)
    }
  }, [])

  const stateCounts = useMemo(() => {
    const counts = new Map<SubmissionState, number>()
    for (const item of items) {
      const s = item.submission.state
      counts.set(s, (counts.get(s) || 0) + 1)
    }
    return counts
  }, [items])

  // 过滤 = 归档开关 ∩ 状态多选 ∩ 搜索(空格分词, 每词命中标题/画布/来源会话任一子串; 客户端即输即得)。
  const visible = useMemo(() => {
    const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return items.filter((item) => {
      // 归档件默认收起: 融合进来的 899 条历史归档里有 584 条状态是 unreviewed,
      // 不收起会把真待审埋掉(旧审阅台也是「归档归归档桶、不进待审」的口径)。
      if (!showArchived && item.submission.archived) return false
      if (!stateSel.has(item.submission.state)) return false
      if (!terms.length) return true
      const hay = [item.title, item.canvasTitle, item.sessionId, item.kind, item.adapter,
        item.submission.project, item.submission.track]
        .filter(Boolean).join('\n').toLowerCase()
      return terms.every((t) => hay.includes(t))
    })
  }, [items, search, stateSel, showArchived])

  const archivedCount = useMemo(
    () => items.filter((item) => item.submission.archived).length,
    [items],
  )

  const groups = useMemo(() => {
    const g = new Map<SubmissionState, ReviewItemData[]>()
    for (const s of STATE_ORDER) g.set(s, [])
    for (const item of visible) g.get(item.submission.state)?.push(item)
    return g
  }, [visible])

  const baseSelected = useMemo(
    () => visible.find((item) => item.key === selectedKey) ?? visible[0] ?? null,
    [visible, selectedKey],
  )

  // 选中项富化(一次): 画布文档取该卡 kind/adapter/正文引用; 失败/未命中静默回退列表字段。
  const [enriched, setEnriched] = useState<{ key: string; patch: Partial<ReviewItemData> } | null>(null)
  useEffect(() => {
    let live = true
    setEnriched(null)
    if (!baseSelected) return undefined
    const key = baseSelected.key
    void enrichFromCanvas(baseSelected).then((patch) => {
      if (live && patch) setEnriched({ key, patch })
    })
    return () => { live = false }
  }, [baseSelected])
  const selected = useMemo(
    () => (baseSelected && enriched?.key === baseSelected.key ? { ...baseSelected, ...enriched.patch } : baseSelected),
    [baseSelected, enriched],
  )

  // 外部聚焦(材料深链): 目标状态没勾就自动勾上, 再滚动到位。
  useEffect(() => {
    if (!focusCardId || !items.length) return
    const hit = items.find((item) => item.cardId === focusCardId || item.cardId === `mig-${focusCardId}`)
    if (!hit) return
    setSelectedKey(hit.key)
    setStateSel((prev) => prev.has(hit.submission.state) ? prev : new Set([...prev, hit.submission.state]))
    const cid = clusterKeyOf(hit.submission.state, hit)
    setExpandedClusters((prev) => prev.includes(cid) ? prev : [...prev, cid])
    const timer = setTimeout(() => rowRefs.current.get(hit.key)?.scrollIntoView({ block: 'center' }), 60)
    return () => clearTimeout(timer)
  }, [focusCardId, items])

  // 跳转按域分派: builtin 会话画布 → 驾驶舱会话页签深链(画布在侧边栏);
  // 其余域 → workshop 画布视图(提交正本所在存储), 聚焦该卡。
  const jumpUrl = (item: ReviewItemData) => {
    if (item.domain === 'builtin') {
      if (item.canvasKey.startsWith('session-') && item.sessionId) {
        return `/?open_type=cc_session&open_id=${encodeURIComponent(item.sessionId)}`
      }
      return '/'
    }
    return `/bside/canvas?canvas=${encodeURIComponent(item.canvasKey)}&card=${encodeURIComponent(item.cardId)}`
  }

  const renderRow = (item: ReviewItemData) => {
    const state = item.submission.state
    const current = selected?.key === item.key
    return (
      <div
        key={item.key}
        ref={(el) => { if (el) rowRefs.current.set(item.key, el); else rowRefs.current.delete(item.key) }}
        className="rf-qrow"
        role="button"
        tabIndex={0}
        aria-current={current}
        data-testid={`rb-row-${item.cardId}`}
        onClick={() => selectItem(item.key)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectItem(item.key) } }}
      >
        <span className="q-tdot" style={{ background: STATE_COLOR[state] }} role="img" aria-label={STATE_LABEL[state]} />
        <span className="q-ic"><AdapterIcon adapter={item.adapter} kind={item.kind} /></span>
        <span className="q-main">
          <span className="q-t">{item.title || '(无标题)'}</span>
          <span className="rb-sub">
            {String(item.submission.submittedAt ?? '').slice(0, 10) || '—'}
            {' · '}
            {item.sessionLabel}
          </span>
        </span>
      </div>
    )
  }

  // 聚合折叠(复用旧审阅台的 buildRenderUnits, 口径 = 旧「同项目同类 ≥3 折叠」在 material
  // 契约里的对应物: 同来源画布 · 同模态 ≥3 条收成一摞, 默认折叠, 点开才平铺)。
  const clusterKeyOf = (state: SubmissionState, item: ReviewItemData) =>
    `${state}|${item.canvasTitle || item.sessionLabel || 'unfiled'}|${item.adapter}`

  const renderUnits = (state: SubmissionState, list: ReviewItemData[]) => {
    const units = buildRenderUnits(list.map((item) => ({
      id: item.key,
      kind: item.adapter,
      project: item.canvasTitle || item.sessionLabel || 'unfiled',
      item,
    })))
    return units.flatMap((unit) => {
      if (unit.type === 'single') return [renderRow(unit.m.item)]
      const cid = `${state}|${unit.key}`
      const expanded = expandedClusters.includes(cid)
      const toggle = () => setExpandedClusters((prev) =>
        prev.includes(cid) ? prev.filter((x) => x !== cid) : [...prev, cid])
      const header = (
        <div
          key={`cluster-${cid}`}
          className="rf-qrow rf-cluster"
          role="button"
          tabIndex={0}
          aria-expanded={expanded}
          data-testid={`rb-cluster-${unit.key}`}
          onClick={toggle}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle() } }}
        >
          <span className="q-tdot" style={{ background: STATE_COLOR[state] }} aria-hidden />
          <span className="q-ic" style={{ color: 'var(--fp-text-3)' }}>
            {expanded ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
          </span>
          <span className="q-main">
            <span className="q-t" style={{ WebkitLineClamp: 1 }}>
              {/* 来源名只取括号前的主名: 画布标题常带括号注解, 摞头上是废话 */}
              {String(unit.project).split(/[（(]/)[0].trim() || unit.project}
              {' · '}
              {MOD_LABEL[unit.kind] ?? unit.kind}
            </span>
          </span>
          <span className="v2-count c-n" aria-label={`${unit.items.length} 条`}>{unit.items.length}</span>
        </div>
      )
      return expanded ? [header, ...unit.items.map((x) => renderRow(x.item))] : [header]
    })
  }

  return (
    <div className="omni-review-board" data-testid="review-board" ref={rootRef} data-narrow={narrow ? '1' : '0'}>
      {error && <div className="rb-error" data-testid="review-board-error">{error}</div>}
      <div className="rb-main">
        {sidebarOpen && <div className="rb-side-wrap" style={narrow ? undefined : { width: leftWidth }}>
          <div className="rf-tools rf-filter-tools v2-filterbar">
            <label className="v2-search" style={{ maxWidth: 'none' }}>
              <Search size={13} aria-hidden />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜标题/画布/会话…"
                data-testid="review-board-search"
              />
            </label>
            <PickerMenu
              label="状态"
              selected={STATE_ORDER.filter((s) => stateSel.has(s))}
              onChange={(next) => setStateSel(new Set(next as SubmissionState[]))}
              options={STATE_ORDER.map((s) => ({ value: s, label: STATE_LABEL[s], count: stateCounts.get(s) || 0 }))}
            />
            <button
              type="button"
              className="v2-iconbtn"
              onClick={() => setShowArchived((v) => !v)}
              aria-pressed={showArchived}
              title={showArchived ? `隐藏已归档（${archivedCount}）` : `显示已归档（${archivedCount}）`}
              aria-label={showArchived ? '隐藏已归档' : '显示已归档'}
              data-testid="review-board-toggle-archived"
              style={showArchived ? undefined : { opacity: 0.6 }}
            >
              <Archive size={15} aria-hidden />
            </button>
            <button
              type="button"
              className="v2-iconbtn"
              onClick={load}
              disabled={loading}
              title="刷新"
              aria-label="刷新"
              data-testid="review-board-refresh"
            >
              <RefreshCw size={15} aria-hidden className={loading ? 'rb-spin' : undefined} />
            </button>
            <a
              className="v2-iconbtn"
              href={`/bside/review?theme=omni`}
              target="_blank"
              rel="noopener noreferrer"
              title="在画布审阅面打开全体提审"
              aria-label="在画布审阅面打开全体提审"
              data-testid="review-board-open-notes"
            >
              <ExternalLink size={15} aria-hidden />
            </a>
          </div>
          <div className="rf-side rb-side fp-scroll" data-testid="review-board-list">
            {STATE_ORDER.map((s) => {
              const list = groups.get(s) ?? []
              if (!list.length) return null
              return (
                <div key={s} data-testid={`review-board-bucket-${s}`}>
                  <div className="rf-sechead">
                    <span className="nm" style={{ color: STATE_COLOR[s] }}>{STATE_LABEL[s]}</span>
                    <DimText>{list.length}</DimText>
                    <span className="ln" aria-hidden="true" />
                  </div>
                  {renderUnits(s, list)}
                </div>
              )
            })}
            {!visible.length && (
              <div className="rf-empty">
                {loading ? '读取审阅队列…' : '当前筛选下没有提审材料。'}
              </div>
            )}
          </div>
        </div>}
        {sidebarOpen && !narrow && <VSplitter side="right" onResize={(d) => setLeftWidth((w) => Math.max(240, Math.min(820, w + d)))} />}
        {(!narrow || !sidebarOpen) && <div className="rb-detail" data-testid="review-board-detail">
          {!selected && (
            <>
              <div className="rb-detail-toolbar">
                <SidebarToggleButton
                  side="left"
                  open={sidebarOpen}
                  onToggle={() => setSidebarOpen((value) => !value)}
                  label="审阅材料列表"
                  testId="review-board-toggle-sidebar"
                />
              </div>
              <div className="rf-empty">选择一条提审材料。</div>
            </>
          )}
          {selected && (
            <>
              <div className="rf-bar">
                <SidebarToggleButton
                  side="left"
                  open={sidebarOpen}
                  onToggle={() => setSidebarOpen((value) => !value)}
                  label="审阅材料列表"
                  testId="review-board-toggle-sidebar"
                />
                <span className="rf-titlewrap">
                  <span className="rf-title">{selected.title || '(无标题)'}</span>
                </span>
                <span className="rf-bizsub">
                  {selected.sessionLabel}
                  {selected.canvasTitle ? `「${selected.canvasTitle}」` : ''}
                  {selected.submission.submittedAt ? ` · 提审 ${String(selected.submission.submittedAt).slice(0, 10)}` : ''}
                  {selected.submission.submittedBy ? ` · ${selected.submission.submittedBy}` : ''}
                </span>
                <a
                  className="rf-bizaction"
                  href={jumpUrl(selected)}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="review-board-jump"
                >
                  <ExternalLink size={13} aria-hidden />
                  跳回画布
                </a>
                {/* 2026-08-16 用户裁定: 裁决压缩为勾/叉图标并进标题行, 独立顶栏整行去掉 */}
                <SubmissionVerdictBar
                  submission={selected.submission}
                  onVerdict={(pass, reason) => void onVerdict(selected, pass, reason)}
                />
              </div>
              <div className="rb-preview" data-testid="review-board-preview">
                <ReviewThumb item={selected} fetchText={fetchText} variant="full" />
              </div>
            </>
          )}
        </div>}
      </div>
    </div>
  )
}
