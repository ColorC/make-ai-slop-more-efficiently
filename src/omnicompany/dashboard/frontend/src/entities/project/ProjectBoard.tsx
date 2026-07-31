// 项目工作板(首页) — 2026-07-19 蓝图 G 1:1 重置(阶段四第三波;对照 demo ?theme=g#/board 页签内形态,
// 合同=TRIFORM-UX-REDESIGN-V2/demo/MAPPING.md):
//   · 页签内形态(G.3⑤): 页面无大标题顶栏,从筛选工具行开始(搜索虚线测量件 + 分组 seg 带计数
//     + 活跃度 picker + 图例 + 图签筛选状态);视图切换(列表/应用)收进工具行右端 seg。
//   · 分组折叠头: 空心描边大字 + chevron + ⊢N⊣ 计数尺寸标注(bp-dim);组间十字对位标;「其他」组默认折叠。
//   · 整卡可点: 行/卡本身为主按钮(27 个「打开项目」主按钮已杀死),次动作 kebab/图钉 hover 才现;
//     搁置=dashed 未探索;在跑=朱红 seal 点徽章;pinned=左缘朱红条(客户端 localStorage 图钉,纯表现层)。
//   · D 密度收敛: 行默认只留 图标/名称/在跑/新鲜度 7 方格 hatch 阵/短格式时间;
//     描述/链接/活跃度详情收进 hover 预览卡(深色描图纸, desktop hover 300ms)。
// 数据: 项目走 GET /api/projects;快捷入口走 GET /api/project-views;活跃对话走 active-bindings(均未改)。

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Copy, ExternalLink, FolderOpen, ChevronDown, ChevronRight, Link2, Pin, RefreshCw, Search } from 'lucide-react'
import { DynamicIcon } from 'lucide-react/dynamic'
import {
  projectsApi,
  type ProjectItem,
  type ProjectsBoard,
  type ProjectViewApp,
} from '../../api/projectsClient'
import { usePanels } from '../../stores/panelsStore'
import { useRefreshBus } from '../../stores/refreshBus'
import { openProps } from '../../utils/middleClick'
import { copyText } from '../../lib/copyText'
import { relTimeZh } from '../../lib/time'
import { openChatInVscode } from '../../lib/surface'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { bossSightApi, type BossSightBindingBucket } from '../../api/bossSightClient'
import { Segmented, DimText } from '../../components/Segmented'
import { PickerMenu } from '../../components/PickerMenu'
import { useHoverPreview } from '../../components/HoverCard'
import { ActivityStrip } from './ActivityStrip'
import './projectBoard.css'

// ── 视图模式记忆(localStorage) ──────────────────────────────────────────────
type ViewMode = 'list' | 'apps'
const VIEW_KEY = 'omni.projectBoard.view'
function loadViewMode(): ViewMode {
  try {
    const v = localStorage.getItem(VIEW_KEY)
    if (v === 'apps' || v === 'list') return v
  } catch { /* privacy mode 兜底 */ }
  return 'list' // 默认列表
}
function saveViewMode(m: ViewMode): void {
  try { localStorage.setItem(VIEW_KEY, m) } catch { /* */ }
}

// ── 分组折叠态记忆(localStorage) ────────────────────────────────────────────
const COLLAPSE_KEY = 'omni.projectBoard.collapsed'
function loadCollapsed(): Record<string, boolean> {
  try {
    const v = localStorage.getItem(COLLAPSE_KEY)
    if (v) { const o = JSON.parse(v); if (o && typeof o === 'object') return o }
  } catch { /* */ }
  return {}
}
function saveCollapsed(m: Record<string, boolean>): void {
  try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(m)) } catch { /* */ }
}

// ── 客户端图钉(G 限定,纯表现层;localStorage 记忆,不动注册表真源) ─────────────
const PIN_KEY = 'omni.projectBoard.pins'
function loadPins(): Record<string, boolean> {
  try {
    const v = localStorage.getItem(PIN_KEY)
    if (v) { const o = JSON.parse(v); if (o && typeof o === 'object') return o }
  } catch { /* */ }
  return {}
}
function savePins(m: Record<string, boolean>): void {
  try { localStorage.setItem(PIN_KEY, JSON.stringify(m)) } catch { /* */ }
}

/** 外链在新标签打开(预览卡链接 chip / 应用视图快捷入口共用)。 */
function openUrl(url: string): void {
  try { window.open(url, '_blank', 'noopener') } catch { /* */ }
}

/** 活跃度分桶(demo actClass 同口径): <24h=d1(24h 内); ≤7d=w1(本周); 其余/无记录=idle(已搁置)。 */
type ActClass = 'd1' | 'w1' | 'idle'
function actClass(p: ProjectItem): ActClass {
  const t = p.last_active ? new Date(p.last_active).getTime() : NaN
  if (Number.isNaN(t)) return 'idle'
  const d = Date.now() - t
  if (d < 86_400_000) return 'd1'
  if (d <= 7 * 86_400_000) return 'w1'
  return 'idle'
}

/** 行内时间:短格式相对时间(无"活跃"后缀);无记录=—。 */
function timeText(p: ProjectItem): string {
  return relTimeZh(p.last_active) || '—'
}
/** 预览卡活跃度全文。 */
function act7Text(p: ProjectItem): string {
  const n = (p.activity_7d || []).filter(Boolean).length
  return `近 7 天活跃 ${n} 天`
}
function activityFullText(p: ProjectItem): string {
  const rel = relTimeZh(p.last_active)
  return rel ? `${rel}活跃` : '暂无活跃记录'
}

/** 一行简介: 描述优先, 退回极短昵称。空则不渲染。 */
function introText(p: ProjectItem): string {
  return (p.desc || p.short || '').trim()
}

/** 项目小图标(lucide 矢量, kebab 名存在注册表 icon 字段)。未知名/未设置时不渲染, 不会崩。 */
export function ProjectIcon({ name, size = 16, color = 'currentColor' }: { name?: string; size?: number; color?: string }) {
  if (!name) return null
  return (
    <span data-testid="project-card-icon" style={{ display: 'inline-flex', alignItems: 'center', flexShrink: 0, color }}>
      <DynamicIcon name={name as never} size={size} />
    </span>
  )
}

/** 行尾「…更多」菜单: 收纳低频操作(后台打开 / 复制 id / 复制 index 路径 / 在编辑器打开根目录)。
 *  「后台打开」是中键后台开的触屏等价路径(M3: 触屏没有中键, hover-only 交互必须有第二条路)。 */
function RowKebab({ p, onCopyIndex, onOpenBg }: { p: ProjectItem; onCopyIndex: () => void; onOpenBg?: () => void }) {
  const items: KebabItem[] = []
  if (onOpenBg) items.push({ label: '后台打开', icon: <FolderOpen size={14} />, testid: 'project-kebab-open-bg', onClick: onOpenBg })
  items.push({ label: '复制项目 id', icon: <Copy size={14} />, testid: 'project-kebab-copy-id', onClick: () => { void copyText(p.id) } })
  if (p.index_path) {
    items.push({
      label: '复制 index 路径',
      icon: <Copy size={14} />, testid: 'project-card-copy-index', onClick: onCopyIndex,
    })
  }
  const root = p.roots && p.roots[0]
  if (root) items.push({ label: '在编辑器打开根目录', icon: <FolderOpen size={14} />, testid: 'project-kebab-open-root', onClick: () => openChatInVscode('claude_code', root) })
  return (
    <span className="kebab-wrap" data-omni-capture-ignore="true" onClick={(e) => e.stopPropagation()}>
      <KebabMenu items={items} testid="project-kebab" iconSize={16}
        triggerStyle={{ border: '1px solid transparent', borderRadius: 3, color: 'var(--fp-text-3)', background: 'transparent' }} />
    </span>
  )
}

/** 在跑徽章(朱红 seal 点): 有在跑对话时渲染,hover 名单沿用 active-bindings 桶。 */
function RunningBadge({ binding }: { binding?: BossSightBindingBucket }) {
  if (!binding || !binding.active || binding.active <= 0) return null
  const tip = (binding.sessions || []).slice(0, 12)
    .map((s) => `${s.running ? '[在跑]' : '[已停]'} ${s.title || s.name || (s.session_id ? s.session_id.slice(0, 8) : s.key)}`)
    .join('\n') || `${binding.active} 个活跃对话在推进`
  return (
    <span className="v2-status st-accent" title={tip} data-testid="active-convo-badge">
      <i className="led" aria-hidden />在跑{binding.active > 1 ? ` ${binding.active}` : ''}
    </span>
  )
}

/** hover 预览卡内容(D 密度收敛的承载:描述全文/链接/活跃度详情都收这里)。 */
function BoardPreview({ p, binding }: { p: ProjectItem; binding?: BossSightBindingBucket }) {
  const [expand, setExpand] = useState(false)
  const intro = introText(p)
  const long = intro.length > 70
  const links = p.links || []
  return (
    <div>
      <div className="pv-t">
        <span>{p.name || p.id}</span>
        <RunningBadge binding={binding} />
      </div>
      {binding && binding.active > 0 && (
        <div className="pv-badge-sub">{binding.active} 个对话在跑{binding.total > binding.active ? ` · 共 ${binding.total} 条绑定` : ''}</div>
      )}
      {intro && <div className={`pv-d${long && !expand ? ' clamp' : ''}`}>{intro}</div>}
      {long && (
        <button type="button" className="pv-more" onClick={(e) => { e.stopPropagation(); setExpand((v) => !v) }}>
          {expand ? '收起' : '展开全部'}
        </button>
      )}
      <div className="pv-meta">
        <ActivityStrip days={p.activity_7d} />
        <span>{act7Text(p)}</span>
        <span aria-hidden="true">·</span>
        <span>{activityFullText(p)}</span>
      </div>
      {links.length > 0 && (
        <div className="pv-links">
          {links.map((lk, i) => (
            <button
              key={`${lk.url}-${i}`}
              type="button"
              className="lk-chip"
              title={lk.url}
              onClick={(e) => { e.stopPropagation(); openUrl(lk.url) }}
            >
              <ExternalLink size={11} aria-hidden />{lk.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** 列表行:整卡可点(role=button + Enter/Space;kebab/图钉为真实子按钮,故不用原生 button 嵌套)。 */
function ProjectRow({ p, binding, pinned, onOpen, onCopyIndex, onTogglePin, preview }: {
  p: ProjectItem
  binding?: BossSightBindingBucket
  pinned: boolean
  onOpen: (bg?: boolean) => void
  onCopyIndex: () => void
  onTogglePin: () => void
  preview: ReturnType<typeof useHoverPreview>
}) {
  const intro = introText(p)
  const fine = () => typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches
  return (
    <div
      className={`v2-card pb-card${pinned ? ' pinned' : ''}`}
      role="button"
      tabIndex={0}
      data-act={actClass(p)}
      data-testid="project-list-row"
      data-omni-uri={`omni://project/${encodeURIComponent(p.id)}`}
      data-omni-kind="project"
      data-omni-title={p.name || p.id}
      title={`${p.id} · 左键打开 / 中键后台开`}
      {...openProps(() => onOpen(), () => onOpen(true))}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }}
      onMouseEnter={(e) => { if (fine()) preview.show(e.currentTarget, <BoardPreview p={p} binding={binding} />) }}
      onMouseLeave={() => preview.scheduleHide()}
    >
      <span className="pc-icon" aria-hidden="true">
        {p.icon ? <ProjectIcon name={p.icon} size={20} /> : (p.name || p.id || '?').trim().charAt(0) || '·'}
      </span>
      <span className="pc-main">
        <span className="pc-t">
          <span className="t">{p.name || p.id}</span>
          <RunningBadge binding={binding} />
        </span>
        {intro && <span className="pc-d">{intro}</span>}
      </span>
      <span className="pc-side">
        <ActivityStrip days={p.activity_7d} />
        <span className="pc-time lg-dim" data-testid="project-list-active"><DimText>{timeText(p)}</DimText></span>
        <button
          type="button"
          className="pc-pin"
          aria-label={pinned ? '取消置顶' : '置顶'}
          aria-pressed={pinned}
          data-testid="project-pin"
          onClick={(e) => { e.stopPropagation(); onTogglePin() }}
        >
          <Pin size={13} aria-hidden />
        </button>
        <RowKebab p={p} onCopyIndex={onCopyIndex} onOpenBg={() => onOpen(true)} />
      </span>
    </div>
  )
}

// 悬浮/离开: 无卡片框, 只给一层轻圆角底色(启动器观感;应用视图保留 V1 形态, 本波只跟随 token)。
function tileHoverIn(e: React.MouseEvent<HTMLDivElement>) {
  e.currentTarget.style.background = 'var(--fp-accent-weak)'
}
function tileHoverOut(e: React.MouseEvent<HTMLDivElement>) {
  e.currentTarget.style.background = 'transparent'
}

/** 应用视图快捷入口的一格: 图标(icon_url 图优先, 否则 emoji)+ 名字, 点击 window.open(url)。 */
function AppTile({ app }: { app: ProjectViewApp }) {
  const [imgOk, setImgOk] = useState(true)
  const useImg = !!app.icon_url && imgOk
  return (
    <div
      style={S.tile}
      data-testid="project-app-tile"
      title={app.url}
      onClick={() => openUrl(app.url)}
      onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); openUrl(app.url) } }}
      onMouseEnter={tileHoverIn}
      onMouseLeave={tileHoverOut}
    >
      {useImg
        ? <img src={app.icon_url as string} alt="" style={S.tileIconImg} onError={() => setImgOk(false)} />
        : <span style={S.tileIcon}>{app.icon || <Link2 size={26} aria-hidden />}</span>}
      <span style={S.tileLabel}>{app.label}</span>
    </div>
  )
}

/** 应用视图项目的一格: 卡图(有 bg)→ 方形小图标; 否则 lucide 图标; 再否则底衬+首字。点击进详情。 */
function ProjectTile({ p, onOpen }: { p: ProjectItem; onOpen: (bg?: boolean) => void }) {
  const name = p.name || p.id
  const firstChar = name.trim().charAt(0) || '·'
  const hasBg = !!(p.bg || '').trim()
  const kebabItems: KebabItem[] = [
    { label: '打开项目', icon: <FolderOpen size={14} />, testid: 'project-tile-open', onClick: () => onOpen() },
    { label: '后台打开', icon: <FolderOpen size={14} />, testid: 'project-tile-open-bg', onClick: () => onOpen(true) },
  ]
  return (
    <div
      style={{ ...S.tile, position: 'relative' }}
      data-testid="project-app-project-tile"
      title={`${p.id} · 左键打开 / 中键后台开`}
      {...openProps(() => onOpen(), () => onOpen(true))}
      onMouseEnter={tileHoverIn}
      onMouseLeave={tileHoverOut}
    >
      <span style={{ position: 'absolute', top: 2, right: 2 }} data-omni-capture-ignore="true" onClick={(e) => e.stopPropagation()}>
        <KebabMenu items={kebabItems} testid="project-tile-more" iconSize={14} triggerStyle={{ background: 'rgba(11,15,23,.55)', borderColor: 'transparent' }} />
      </span>
      {hasBg
        ? <div style={{ ...S.tileIconImg, background: thumbBackground(p) }} data-testid="project-app-tile-img" />
        : p.icon
          ? <span style={S.tileIcon}><ProjectIcon name={p.icon} size={30} /></span>
          : <span style={{ ...S.tileIconLetter }}>{firstChar}</span>}
      <span style={S.tileLabel}>{name}</span>
    </div>
  )
}

// 缩略图背景: 有图走图(实色垫底, 走后端 ?w= 缩略档), 否则统一 --fp-card 中性实底(应用视图沿用)。
function thumbBackground(p: ProjectItem): string {
  const bg = (p.bg || '').trim()
  if (bg) {
    if (/^(https?:|data:|\/|\.\/)/.test(bg) || /\.(png|jpe?g|webp|gif|svg)(\?|$)/i.test(bg)) {
      const url = bg.startsWith('/api/project-assets/') ? `${bg}${bg.includes('?') ? '&' : '?'}w=320` : bg
      return `center/cover no-repeat url("${url.replace(/"/g, '%22')}"), var(--fp-card)`
    }
    return bg
  }
  return 'var(--fp-card)'
}

// 应用视图(启动器)保留 V1 内联样式(本波不重置, 只跟随 token 换皮)。
const S: Record<string, any> = {
  launcherCap: { color: 'var(--fp-text-2)', fontSize: 15, fontWeight: 600, margin: '0 2px 12px' },
  launcherCapSplit: { color: 'var(--fp-text-2)', fontSize: 15, fontWeight: 600, margin: '28px 2px 12px', paddingTop: 20, borderTop: '1px solid var(--fp-border)' },
  appsGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))', gap: 10 },
  tile: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-start', gap: 9,
    padding: '10px 6px', borderRadius: 3, cursor: 'pointer', minWidth: 0,
    background: 'transparent', border: '1px solid transparent',
    transition: 'background 150ms ease',
  },
  tileIcon: { width: 58, height: 58, borderRadius: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 30, lineHeight: 1, color: 'var(--fp-text)', background: 'var(--fp-accent-weak)', border: '1px solid var(--fp-border)', flexShrink: 0 },
  tileIconImg: { width: 58, height: 58, borderRadius: 3, border: '1px solid rgba(0,0,0,.35)', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.05)', flexShrink: 0, objectFit: 'cover', display: 'block' },
  tileIconLetter: { width: 58, height: 58, borderRadius: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24, fontWeight: 600, lineHeight: 1, color: 'var(--fp-text-2)', border: '1px solid rgba(235,245,255,.45)', flexShrink: 0, fontFamily: 'var(--fp-bp-font-display)' },
  tileLabel: { color: 'var(--fp-text)', fontSize: 13, fontWeight: 500, textAlign: 'center', width: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
}

export default function ProjectBoard() {
  const [board, setBoard] = useState<ProjectsBoard | null>(null)
  const [apps, setApps] = useState<ProjectViewApp[] | null>(null)
  const [view, setView] = useState<ViewMode>(loadViewMode)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadCollapsed)
  const [pins, setPins] = useState<Record<string, boolean>>(loadPins)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 筛选三件套(demo boardState 同构): 搜索词 / 分组 seg / 活跃度多选。
  const [q, setQ] = useState('')
  const [group, setGroup] = useState('all')
  const [act, setAct] = useState<string[]>([])
  // 活跃对话聚合(by_project) — 行徽章按 p.id 查桶(SESSION-SELF-BINDING 4.5)。
  const [bindings, setBindings] = useState<Record<string, BossSightBindingBucket>>({})
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)
  const refreshNonce = useRefreshBus((s) => s.nonce)
  const preview = useHoverPreview()

  useEffect(() => {
    bossSightApi.activeBindings().then((d) => setBindings(d.by_project || {})).catch(() => setBindings({}))
  }, [refreshNonce])

  // 快捷入口条目(端点未生效/网络失败时客户端已降级为空数组)。
  useEffect(() => {
    projectsApi.views().then((d) => setApps(d.apps || [])).catch(() => setApps([]))
  }, [refreshNonce])

  const load = useCallback((fresh = false) => {
    setBusy(true)
    projectsApi.list(fresh).then((raw) => {
      const b: ProjectsBoard = raw && Array.isArray((raw as any).projects)
        ? raw : { projects: [], groups_order: [], group_labels: {} }
      setBoard(b)
      setError(null)
    }).catch((e) => setError(String(e?.message || e))).finally(() => setBusy(false))
  }, [])
  useEffect(() => { load(refreshNonce > 0) }, [load, refreshNonce])

  const setViewMode = (m: ViewMode) => { setView(m); saveViewMode(m) }
  const toggleGroup = (g: string) => setCollapsed((prev) => {
    const next = { ...prev, [g]: !prev[g] }
    saveCollapsed(next)
    return next
  })
  const togglePin = (id: string) => setPins((prev) => {
    const next = { ...prev, [id]: !prev[id] }
    if (!next[id]) delete next[id]
    savePins(next)
    return next
  })

  const open = (p: ProjectItem, bg = false) => {
    (bg ? openTabBg : openTab)({ type: 'project', id: p.id }, p.name || p.id)
  }
  const copyIndex = (p: ProjectItem) => {
    if (!p.index_path) return
    void copyText(p.index_path)
  }

  const groups: string[] = board
    ? [...board.groups_order, ...Array.from(new Set(board.projects.map((p) => p.group))).filter((g) => !board.groups_order.includes(g))]
    : []
  const groupLabel = (g: string) => board?.group_labels[g] || g

  // 活跃度分桶计数(picker 各选项带真实计数, demo 同)。
  const actCounts = useMemo(() => {
    const c: Record<ActClass, number> = { d1: 0, w1: 0, idle: 0 }
    for (const p of board?.projects || []) c[actClass(p)] += 1
    return c
  }, [board])

  const filtering = q.trim() !== '' || group !== 'all' || act.length > 0

  // 过滤 + 图钉排组首(demo paintBoard 同序)。
  const rowsOf = (g: string): ProjectItem[] => {
    if (!board) return []
    const needle = q.trim().toLowerCase()
    const rows = board.projects.filter((p) => {
      if (p.group !== g) return false
      if (act.length && !act.includes(actClass(p))) return false
      if (needle && !`${p.name || ''} ${p.id} ${p.desc || ''} ${p.short || ''}`.toLowerCase().includes(needle)) return false
      return true
    })
    return [...rows].sort((a, b) => Number(!!pins[b.id] || !!b.pinned) - Number(!!pins[a.id] || !!a.pinned))
  }
  const shownCount = board ? groups.reduce((n, g) => n + (group !== 'all' && g !== group ? 0 : rowsOf(g).length), 0) : 0

  // 应用视图的项目拍平: pinned 在前, 其余按分组顺序(组内保留 enrich 的活跃降序)。
  const flatProjects: ProjectItem[] = board
    ? [
        ...board.projects.filter((p) => p.pinned),
        ...groups.flatMap((g) => board.projects.filter((p) => !p.pinned && p.group === g)),
      ]
    : []

  return (
    <div className="pb-page" data-testid="project-board">
      {/* 筛选工具行(页签内形态: 页面从这里开始, 无大标题顶栏)。 */}
      <div className="pb-tools">
        <div className="v2-filterbar" style={{ flex: 1, minWidth: 0 }}>
          <label className="v2-search">
            <Search size={14} aria-hidden />
            <input
              data-testid="project-board-search"
              placeholder="搜索项目名 / 描述…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </label>
          {view === 'list' && (
            <Segmented
              label="分组"
              current={group}
              onChange={setGroup}
              items={[{ value: 'all', label: '全部', count: board?.projects.length ?? 0 }].concat(
                groups.map((g) => ({ value: g, label: groupLabel(g), count: (board?.projects || []).filter((p) => p.group === g).length })),
              )}
            />
          )}
          {view === 'list' && (
            <PickerMenu
              label="活跃度"
              selected={act}
              onChange={setAct}
              options={[
                { value: 'd1', label: '24h 内活跃', count: actCounts.d1 },
                { value: 'w1', label: '本周活跃', count: actCounts.w1 },
                { value: 'idle', label: '已搁置', count: actCounts.idle },
              ]}
            />
          )}
        </div>
        <span className="tools-right">
          {view === 'list' && (
            <span className="v2-legend">
              <span className="lg-i"><i className="led" style={{ background: 'var(--fp-ok)' }} />近 7 天活跃</span>
            </span>
          )}
          {/* 图签·筛选状态(唯一合法 title block: 实时过滤条件+结果计数, 无筛选整块隐藏) */}
          <span className={`bp-livetag${filtering ? ' show' : ''}`} aria-live="polite" data-testid="project-board-livetag">
            {filtering && (
              <span className="bp-titleblock">
                <span className="tb">筛选<b>{[
                  ...(group !== 'all' ? [groupLabel(group)] : []),
                  ...act.map((a) => ({ d1: '24h 内', w1: '本周', idle: '已搁置' } as Record<string, string>)[a]),
                  ...(q.trim() ? [`“${q.trim()}”`] : []),
                ].join(' · ') || '—'}</b></span>
                <span className="tb">结果<b>{shownCount} 项</b></span>
              </span>
            )}
          </span>
          <Segmented
            label="视图"
            small
            current={view}
            onChange={(v) => setViewMode(v as ViewMode)}
            items={[{ value: 'list', label: '列表' }, { value: 'apps', label: '应用' }]}
          />
          <button
            type="button"
            className="v2-iconbtn"
            style={{ opacity: busy ? 0.45 : 1 }}
            disabled={busy}
            aria-label={busy ? '刷新中…' : '刷新(穿透缓存)'}
            data-testid="project-board-refresh"
            onClick={() => load(true)}
          ><RefreshCw size={15} /></button>
        </span>
      </div>

      {error && <div style={{ color: 'var(--fp-err)', fontSize: 13, padding: '8px 22px' }}>{error}</div>}

      {/* ── 应用视图(启动器): 快捷入口置顶 + 全部项目图标宫格(V1 形态保留, token 跟随) ── */}
      {view === 'apps' && (
        <div className="pb-body" data-testid="project-apps-view">
          {apps === null && (
            <div style={S.appsGrid} data-testid="project-apps-skeleton">
              {Array.from({ length: 4 }, (_, i) => <div key={i} className="fp-skeleton" style={{ height: 120, borderRadius: 3 }} />)}
            </div>
          )}
          {apps && apps.length > 0 && (
            <>
              <div style={S.launcherCap}>快捷入口</div>
              <div style={S.appsGrid} data-testid="project-apps-shortcuts">
                {apps.map((a) => <AppTile key={a.id} app={a} />)}
              </div>
            </>
          )}
          {!board && (
            <div style={{ ...S.appsGrid, marginTop: 20 }}>
              {Array.from({ length: 12 }, (_, i) => <div key={i} className="fp-skeleton" style={{ height: 120, borderRadius: 3 }} />)}
            </div>
          )}
          {board && flatProjects.length > 0 && (
            <>
              <div style={apps && apps.length > 0 ? S.launcherCapSplit : S.launcherCap}>全部项目</div>
              <div style={S.appsGrid} data-testid="project-apps-projects">
                {flatProjects.map((p) => <ProjectTile key={p.id} p={p} onOpen={(bg) => open(p, bg)} />)}
              </div>
            </>
          )}
        </div>
      )}

      {/* ── 列表视图(蓝图 G 形态) ── */}
      {view === 'list' && (
        <div className="pb-body">
          {!board && !error && (
            <div style={{ display: 'grid', gap: 6 }} data-testid="project-board-skeleton">
              {Array.from({ length: 8 }, (_, i) => <div key={i} className="fp-skeleton" style={{ height: 64, borderRadius: 3 }} />)}
            </div>
          )}
          {board && board.projects.length === 0 && (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 13, padding: '48px 8px', textAlign: 'center', lineHeight: 1.7 }}>
              还没有注册项目。用 <code>omni project register</code> 注册, 或让总控来。
            </div>
          )}
          {board && shownCount === 0 && board.projects.length > 0 && (
            <div style={{ padding: 48, textAlign: 'center', color: 'var(--fp-text-3)' }}>无匹配项目——清空搜索或筛选条件</div>
          )}
          {board && groups.map((g) => {
            if (group !== 'all' && g !== group) return null
            const rows = rowsOf(g)
            if (!rows.length) return null
            // 「其他」组默认折叠(无筛选时);有筛选/指定分组时展开(demo 同口径)。
            const isCollapsed = collapsed[g] ?? (g === 'other' && !filtering)
            return (
              <div key={g} data-testid={`project-group-${g}`}>
                <button
                  type="button"
                  className="pb-ghead"
                  data-testid={`project-group-head-${g}`}
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleGroup(g)}
                >
                  <span className="chev" aria-hidden="true">{isCollapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}</span>
                  <span className="gh-name">{groupLabel(g)}</span>
                  <DimText>{rows.length}</DimText>
                </button>
                <div className={`pb-gitems${isCollapsed ? ' hidden' : ''}`}>
                  {rows.map((p) => (
                    <ProjectRow
                      key={p.id}
                      p={p}
                      binding={bindings[p.id]}
                      pinned={!!pins[p.id] || !!p.pinned}
                      onOpen={(bg) => open(p, bg)}
                      onCopyIndex={() => copyIndex(p)}
                      onTogglePin={() => togglePin(p.id)}
                      preview={preview}
                    />
                  ))}
                </div>
                <div className="bp-gsep" aria-hidden="true"><i /><b>+</b><i /></div>
              </div>
            )
          })}
        </div>
      )}
      {preview.host}
    </div>
  )
}
