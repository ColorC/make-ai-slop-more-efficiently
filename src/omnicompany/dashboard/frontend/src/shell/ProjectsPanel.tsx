// 侧栏项目面板 — 与首页"项目工作板"同一数据源 (/api/projects, core 层唯一权威注册表)。
// 旧 WorkboardPanel(三态 lane 的 plan/card 策展, data/boss_sight/workboard.json)已于
// 2026-06-12 按用户要求退役: "双写和功能不统一…本体应该独立于 dashboard 存放, 有唯一权威,
// 任何其他位置都应该被删除"。用量小组件保留。
//
// 2026-06-29 深度重建(照 ThreadMonitorPanel 的卡片解剖深度, 不是换色): 拥挤的竖排单行列表
// → 卡片网格(repeat(auto-fill,minmax(208px,1fr))); 每卡=磨砂玻璃解剖(状态徽章 + 标题 flex1
// 醒目 + 共享 KebabMenu ⋯ 收纳低频操作[复制 id/复制 index 路径/在编辑器打开根目录]); 活跃格
// + 时间下沉成弱灰等宽微脚注; 主操作=整卡可点(左键开/中键后台)。冷色全走现有 token。
//
// 2026-06-30 补齐 frostpane「面板重做标准」缺的两步(早先只上了卡片网格):
//  ①删重复页签名的标题头(原"项目"标题栏退役 —— VSCode 侧栏 section / 驾驶舱页签已标识身份,
//    内容从顶部直接开始); ②root 透明吃 body 全局冷渐变(原无 background, 且 borderTop/marginTop
//    是为旧 CockpitShell 多 section 堆叠留的, surface 下整页即此面板, 一并撤掉)。
//  刷新做成右上角浮动小图标(零行高不占头部, 照 ProjectBoard); 低频的"项目板/打开首页"收进
//  面板级共享 KebabMenu ⋯, 不再与刷新一排等权。groupCount 的裸 hex / 状态徽章裸 hex 全换 token。

import React, { useCallback, useEffect, useState } from 'react'
import { RefreshCw, LayoutGrid, ClipboardList, Copy, FileText, FolderOpen } from 'lucide-react'
import { projectsApi, type ProjectItem, type ProjectsBoard } from '../api/projectsClient'
import { usePanels } from '../stores/panelsStore'
import { useControllerView } from '../entities/controller/viewStore'
import { useRefreshBus } from '../stores/refreshBus'
import { ActivityStrip } from '../entities/project/ActivityStrip'
import { openProps } from '../utils/middleClick'
import { relTimeZh as relTime } from '../lib/time'
import { ProjectIcon } from '../lib/projectIcon'
import { copyText } from '../lib/copyText'
import { openChatInVscode } from '../lib/surface'
import KebabMenu, { type KebabItem } from '../shared/view/ui/KebabMenu'
import Tooltip from '../shared/view/ui/Tooltip'

// frostpane: 侧栏=磨砂玻璃外壳(本组件落在玻璃侧栏里), 用量块=安静近实色玻璃面板分层,
// 项目卡=带顶部高光的磨砂玻璃; 信息层级靠 4 档字阶(18/15/13/12)拉开, 不靠纯加粗;
// 4px 栅格放宽呼吸; 冷色 token (var(--fp-bg)/var(--fp-solid)/var(--fp-text)/var(--fp-text-2)/var(--fp-text-3)/var(--fp-accent)/var(--fp-border))。
const GLASS = 'var(--fp-blur)'
const EASE = 'cubic-bezier(0.175,0.885,0.32,1.1)'
const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"
const S: Record<string, any> = {
  // 标准②: root 透明吃 #bp-grid 全视口统一网格(G.7), 玻璃卡浮其上才有玻璃感 —— 不铺实底顶掉网格。
  // position:relative 给右上角浮动控件做定位锚; 顶部留一点呼吸 + 给浮动控件让出行高。
  root: { position: 'relative' as const, background: 'transparent', paddingTop: 8 },
  // 标准①: 无标题头 —— "项目"标题栏退役(VSCode section/页签已标识)。仅留右上角浮动控件簇(刷新 + ⋯), 零行高不占头部。
  ctrls: { position: 'absolute' as const, top: 4, right: 6, zIndex: 5, display: 'flex', gap: 2 },
  // 图标按钮 = 安静近无形, hover 才浮出极淡玻璃; 放大触达 (Fitts)
  iconBtn: { width: 28, height: 28, border: '1px solid transparent', borderRadius: 7, background: 'transparent', color: 'var(--fp-text-3)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0, transition: `all 150ms ${EASE}` },
  // 用量块 = 安静近实色玻璃面板: 与下方项目卡分层, 顶部高光勾边
  usage: {
    background: 'var(--fp-glass)', backdropFilter: GLASS, WebkitBackdropFilter: GLASS,
    border: '1px solid var(--fp-border)', borderRadius: 11, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
    padding: '10px 12px', margin: '0 4px 12px',
  },
  // 块标题 = 次级弱灰微字, 不与项目区标题抢焦点
  usageHead: { color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 600 as const, letterSpacing: '.02em', margin: '0 0 6px' },
  usageRow: { display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' as const, padding: '2px 0' },
  // 提供方名 = 冷蓝次级标签
  usageName: { color: 'var(--fp-link)', fontSize: 13, fontWeight: 550 as const, width: 50, flexShrink: 0 },
  // 数值 = 主文本色等宽, 便于对齐扫读 (frostpane: 数字/ID 用等宽)
  usageVal: { color: 'var(--fp-text-2)', fontSize: 13, fontFamily: MONO },
  // 最次 = 弱灰微字
  usageDim: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: MONO },
  // 分组标题 = 次级弱灰小字 + 计数, 用间距编码分组 (组间 > 组内)
  groupHead: { display: 'flex', alignItems: 'baseline', gap: 6, color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 600 as const, letterSpacing: '.02em', margin: '14px 8px 6px' },
  groupCount: { color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 400 as const, fontFamily: MONO },
  // 卡片网格: 照 ThreadMonitorPanel 的 auto-fill 网格, 侧栏窄 → minmax 收到 208; 4px 栅格 gap=8
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(208px, 1fr))', gap: 8, margin: '0 4px' },
  // 项目卡 = 磨砂玻璃解剖体(背景图缩成左侧图章, 卡身实玻璃面 — 不再整卡铺图压字);
  // r2=11 圆角家族 + 顶部高光; hover 描边收紧到强调蓝并微浮
  card: {
    position: 'relative' as const, display: 'flex', flexDirection: 'column' as const, minWidth: 0,
    background: 'var(--fp-glass)', backdropFilter: GLASS, WebkitBackdropFilter: GLASS,
    border: '1px solid var(--fp-border)', borderRadius: 11,
    boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
    padding: 12, cursor: 'pointer', transition: `border-color 150ms ${EASE}, transform 150ms ${EASE}`,
  },
  // 卡头一行: 图章 + 标题(flex1 撑开醒目) + ⋯ 菜单
  cardTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
  // 项目图章: 项目背景图缩成 28 圆角方块(有 bg 用图, 无则落 ProjectIcon lucide/字母)
  stamp: { width: 28, height: 28, borderRadius: 7, flexShrink: 0, overflow: 'hidden', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: '1px solid var(--fp-border)' },
  // 卡名 = 卡内主焦点: 15px / 650, 主文本色; 单行省略
  cardName: { flex: 1, minWidth: 0, color: 'var(--fp-text)', fontSize: 15, fontWeight: 650 as const, letterSpacing: '-0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  // 脚注一行: 活跃格 + 相对时间; 弱灰微字, 等宽便于扫读
  foot: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 },
  footTime: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  empty: { color: 'var(--fp-text-3)', fontSize: 13, padding: '4px 8px 6px' },
  err: { color: 'var(--fp-err)', fontSize: 13, padding: '4px 8px' },
}

function cardBackground(p: ProjectItem): string {
  const bg = (p.bg || '').trim()
  if (bg) {
    if (/^(https?:|data:|\/|\.\/)/.test(bg) || /\.(png|jpe?g|webp|gif|svg)(\?|$)/i.test(bg)) {
      // 图章只有 28px 方块: 走后端 ?w= 最小缩略档(320)而非原图(1.3-1.9MB/张)。
      // 实色垫底(2026-07-18 W2: 彩虹 idGradient 已删): 图片未下载完/加载失败时先露中性底, 不闪空白。
      const url = bg.startsWith('/api/project-assets/') ? `${bg}${bg.includes('?') ? '&' : '?'}w=320` : bg
      return `center/cover no-repeat url("${url.replace(/"/g, '%22')}"), var(--fp-card)`
    }
    return bg
  }
  return 'var(--fp-card)'
}

// 状态徽章: 进行中线久未核对 → 待核对(琥珀); 否则按近 7 天有无活跃分 活跃/沉寂。
// 不打分, 用语义色 + 文案双编码(色盲友好), 与 ThreadMonitorPanel.StatusBadge 同形。
function projStatus(p: ProjectItem): { label: string; color: string; bg: string; border: string } {
  if (p.index_stale) return { label: '待核对', color: 'var(--fp-warn)', bg: 'color-mix(in srgb, var(--fp-warn) 12%, transparent)', border: 'color-mix(in srgb, var(--fp-warn) 38%, transparent)' }
  const active = (p.activity_7d || []).some(Boolean)
  return active
    ? { label: '活跃', color: 'var(--fp-ok)', bg: 'color-mix(in srgb, var(--fp-ok) 12%, transparent)', border: 'color-mix(in srgb, var(--fp-ok) 38%, transparent)' }
    : { label: '沉寂', color: 'var(--fp-text-3)', bg: 'var(--fp-solid)', border: 'var(--fp-border)' }
}

function StatusChip({ p }: { p: ProjectItem }) {
  const m = projStatus(p)
  return (
    <Tooltip content={p.index_stale ? (p.stale_reason || 'index 久未核对, 状态可能过期') : undefined} position="left">
      <span
        data-testid="projects-panel-status"
        style={{ flexShrink: 0, fontSize: 12, fontWeight: 600, borderRadius: 999, padding: '1px 8px', color: m.color, background: m.bg, border: `1px solid ${m.border}` }}
      >
        {m.label}
      </span>
    </Tooltip>
  )
}

// 项目图章: 有背景图渲成缩略方块, 无则交给 lucide/字母 ProjectIcon。
function Stamp({ p }: { p: ProjectItem }) {
  const bg = (p.bg || '').trim()
  const isImg = bg && (/^(https?:|data:|\/|\.\/)/.test(bg) || /\.(png|jpe?g|webp|gif|svg)(\?|$)/i.test(bg))
  if (isImg) {
    return <span style={{ ...S.stamp, background: cardBackground(p) }} />
  }
  return <span style={{ ...S.stamp, border: 0 }}><ProjectIcon id={p.id} size={28} /></span>
}

// #5 用量小组件: Claude **官方剩余配额**(直接读 Anthropic oauth/usage 端点, 见 boss_sight/usage.py)。
interface UsageWin { used_pct: number; remaining_pct: number; resets_at?: string | null; reset_in_sec?: number | null }
interface ProvUsage { available?: boolean; reason?: string; five_hour?: UsageWin | null; seven_day?: UsageWin | null; note?: string; stale?: boolean; stale_reason?: string }
interface RuntimeUsage {
  available?: boolean
  summary?: { call_count?: number; total_tokens?: number; cost_usd?: number }
  batch?: { active_count?: number; completed_count?: number; failed_count?: number; run_count?: number }
}

function fmtReset(sec?: number | null): string {
  if (sec == null) return ''
  if (sec <= 0) return '即将重置'
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
  if (h >= 24) return `${Math.floor(h / 24)}天${h % 24}h`
  if (h >= 1) return `${h}h${m}m`
  return `${m}m`
}

function remainColor(pct: number): string {
  if (pct <= 10) return 'var(--fp-err)'
  if (pct <= 30) return 'var(--fp-warn)'
  return 'var(--fp-ok)'
}

function UsageMeter() {
  const [data, setData] = useState<{ claude?: ProvUsage; codex?: ProvUsage; internal?: RuntimeUsage } | null>(null)
  const load = useCallback(() => {
    fetch('/api/boss-sight/usage')
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((d) => setData(d))
      .catch(() => setData({}))
  }, [])
  useEffect(() => {
    load()
    const t = window.setInterval(load, 180000)
    return () => window.clearInterval(t)
  }, [load])
  const win = (tag: string, w?: UsageWin | null) => {
    if (!w) return null
    const r = fmtReset(w.reset_in_sec)
    return (
      <Tooltip content={`官方已用 ${w.used_pct}%${w.resets_at ? ' · 重置 ' + w.resets_at : ''}`} position="left">
        <span style={S.usageVal}>
          {tag} <b style={{ color: remainColor(w.remaining_pct) }}>{w.remaining_pct}%</b>
          {r && <span style={S.usageDim}> <RefreshCw size={10} aria-hidden style={{ verticalAlign: -1 }} />{r}</span>}
        </span>
      </Tooltip>
    )
  }
  const row = (label: string, p?: ProvUsage) => {
    if (!p) return null
    if (!p.available) return <div style={S.usageRow}><span style={S.usageName}>{label}</span><span style={S.usageDim}>{p.reason || '不可用'}</span></div>
    return (
      <div style={S.usageRow}>
        <span style={S.usageName}>{label}</span>
        {win('5h', p.five_hour)}
        {win('7天', p.seven_day)}
        {p.stale && (
          <Tooltip content={p.stale_reason || '显示上次读数'} position="left">
            <span style={S.usageDim}>·旧</span>
          </Tooltip>
        )}
      </div>
    )
  }
  const runtimeRow = (p?: RuntimeUsage) => {
    if (!p) return null
    const calls = p.summary?.call_count || 0
    const tokens = p.summary?.total_tokens || 0
    const cost = p.summary?.cost_usd || 0
    const active = p.batch?.active_count || 0
    const failed = p.batch?.failed_count || 0
    const batches = p.batch?.run_count || 0
    return (
      <div style={S.usageRow}>
        <span style={S.usageName}>LLM</span>
        <span style={S.usageVal}>{calls} calls</span>
        <span style={S.usageVal}>{tokens.toLocaleString()} tok</span>
        <span style={S.usageVal}>${cost.toFixed(4)}</span>
        <span style={S.usageDim}>{active ? `${active} running` : `${batches} batches`}{failed ? ` · ${failed} failed` : ''}</span>
      </div>
    )
  }
  return (
    <div style={S.usage} data-testid="workboard-usage">
      <div style={S.usageHead}>用量 · 官方剩余配额</div>
      {data === null && <div style={S.usageDim}>加载中…</div>}
      {data !== null && (<>{row('Claude', data.claude)}{row('Codex', data.codex)}{runtimeRow(data.internal)}</>)}
    </div>
  )
}

// 单张项目卡: 磨砂玻璃解剖 — 图章 + 状态徽章 + 标题(flex1) + 共享 KebabMenu(低频操作),
// 脚注=活跃格 + 相对时间(弱灰等宽)。整卡可点=主操作(左键开/中键后台), ⋯ 内 stopPropagation。
function ProjectCard({ p, onOpen }: { p: ProjectItem; onOpen: (p: ProjectItem, bg?: boolean) => void }) {
  // 低频操作收进 ⋯ 菜单(沿用 ProjectBoard.CardKebab 同款条目, 旧版根本没暴露这些操作)。
  const items: KebabItem[] = [
    { label: '复制项目 id', icon: <Copy size={15} />, testid: 'projects-panel-copy-id', onClick: () => { void copyText(p.id) } },
  ]
  if (p.index_path) items.push({ label: '复制 index 路径', icon: <FileText size={15} />, testid: 'projects-panel-copy-index', onClick: () => { void copyText(p.index_path!) } })
  const root = p.roots && p.roots[0]
  if (root) items.push({ label: '在编辑器打开根目录', icon: <FolderOpen size={15} />, testid: 'projects-panel-open-root', onClick: () => openChatInVscode('claude_code', root) })

  return (
    // wrapper display:flex 保卡片网格行内等高(原 card 是 grid item 自带 stretch, 包一层后由 wrapper 拉伸)。
    <Tooltip content={`${p.id} · 左键打开 / 中键后台开`} position="top" containerStyle={{ display: 'flex', minWidth: 0 }}>
      <div
        style={S.card}
        data-testid="projects-panel-card"
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--fp-accent)'; e.currentTarget.style.transform = 'translateY(-1px)' }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border)'; e.currentTarget.style.transform = 'none' }}
        {...openProps(() => onOpen(p), () => onOpen(p, true))}
      >
        <div style={S.cardTop}>
          <Stamp p={p} />
          <span style={S.cardName}>{p.name || p.id}</span>
          <StatusChip p={p} />
          <span data-omni-capture-ignore="true">
            <KebabMenu items={items} testid="projects-panel-more" />
          </span>
        </div>
        <div style={S.foot}>
          <ActivityStrip days={p.activity_7d} />
          <span style={S.footTime}>活跃 {relTime(p.last_active)}</span>
        </div>
      </div>
    </Tooltip>
  )
}

function ProjectsPanel() {
  const [board, setBoard] = useState<ProjectsBoard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)
  const refreshNonce = useRefreshBus((s) => s.nonce)

  const load = useCallback((fresh = false) => {
    setBusy(true)
    projectsApi.list(fresh).then((raw) => {
      // 防御: 返回形状不对(代理/测试 fallthrough)时归一成空板, 别让侧栏崩
      const b: ProjectsBoard = raw && Array.isArray((raw as any).projects)
        ? raw : { projects: [], groups_order: [], group_labels: {} }
      setBoard(b)
      setError(null)
    }).catch((e) => setError(String(e?.message || e))).finally(() => setBusy(false))
  }, [])
  useEffect(() => { load(refreshNonce > 0) }, [load, refreshNonce])

  const open = (p: ProjectItem, bg = false) => {
    (bg ? openTabBg : openTab)({ type: 'project', id: p.id }, p.name || p.id)
  }

  const groups: string[] = board
    ? [...board.groups_order, ...Array.from(new Set(board.projects.map((p) => p.group))).filter((g) => !board.groups_order.includes(g))]
    : []

  return (
    <div style={S.root} data-testid="cockpit-projects-panel">
      {/* 标准①+⑤: 无标题头 —— 右上角浮动控件簇, 主操作(刷新)显眼常显, 低频(项目板/打开首页)收进共享 ⋯, 不一排等权。 */}
      <div style={S.ctrls}>
        <Tooltip content={busy ? '刷新中…' : '刷新(穿透缓存)'} position="left">
          <button
            type="button"
            style={{ ...S.iconBtn, opacity: busy ? 0.45 : 1 }}
            disabled={busy}
            aria-label="刷新"
            data-testid="projects-panel-refresh"
            onClick={() => load(true)}
            onMouseEnter={(e) => { if (!busy) { e.currentTarget.style.background = 'var(--fp-accent-weak)'; e.currentTarget.style.color = 'var(--fp-text)' } }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fp-text-3)' }}
          ><RefreshCw size={15} /></button>
        </Tooltip>
        <KebabMenu
          testid="projects-panel-actions"
          items={[
            {
              label: '在驾驶舱打开项目板', icon: <LayoutGrid size={15} />, testid: 'projects-panel-board',
              onClick: () => { openTab({ type: 'controller', id: 'main' }, '总控'); useControllerView.getState().setView('project') },
            },
            {
              label: '打开任务窗口', icon: <ClipboardList size={15} />, testid: 'projects-panel-open-quests',
              onClick: () => { openTab({ type: 'quest_board', id: 'main' }, '任务窗口') },
            },
          ] as KebabItem[]}
        />
      </div>
      <UsageMeter />
      {error && <div style={S.err}>{error}</div>}
      {board && board.projects.length === 0 && <div style={S.empty}>还没有注册项目 (omni project register)。</div>}
      {board && groups.map((g) => {
        const rows = board.projects.filter((p) => p.group === g)
        if (!rows.length) return null
        return (
          <div key={g} data-testid={`projects-panel-group-${g}`}>
            <div style={S.groupHead}>
              <span>{board.group_labels[g] || g}</span>
              <span style={S.groupCount}>{rows.length}</span>
            </div>
            <div style={S.grid}>
              {rows.map((p) => (
                <ProjectCard key={p.id} p={p} onOpen={open} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// 无 props, memo 出口 — 父级高频 state 变更不级联重渲(沿用旧 WorkboardPanel 的性能策略)。
export default React.memo(ProjectsPanel)
