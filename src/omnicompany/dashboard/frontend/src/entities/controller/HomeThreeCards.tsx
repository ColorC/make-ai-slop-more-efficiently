// 首页 · collab platform式「最近访问」统一列表 — 计划 / 审阅材料 / 对话 合并, 纯按更新时间排, 下滑加载更多。
// 用户 2026-06-14: 三列改为一个统一列表(像collab platform云文档最近访问); 默认显示计划+审阅材料, 可选也显示对话;
//   每行看到 最近更新时间 / 所属计划 / 对应路径; 纯按更新时间排, 拉到底继续加载。
// 2026-07-19 蓝图 G 重置(阶段四第四波;合同=demo/MAPPING.md, analysis/controller.md 逐条核销):
//   · 工具行: 搜索=v2-search 虚线测量件;类型筛选 4 裸 pill → aria-pressed 多选 chip(✓ hatch 选中态 +
//     计数徽章,保留 home-filter-* testid);「+ 新对话」实心 accent 主按钮海 → 白线描边次级;刷新仍收 ⋯。
//   · 列表: 40 行平铺 → 时间分组(今天/昨天/本周/更早,mono 组头 + ⊢N⊣ 计数);列头/类型 pill/路径列
//     全撤——类型收敛为色点(与行首图标单源),所属计划·路径折叠为一行 mono meta,时间 mono 短格式。
//   · 行 = 厚框纸件整行可点(role=link + 键盘 Enter),kebab 次动作 hover 才现;tablet 降单卡(时间并 meta)。
// 数据接线 / testid / openProps(中键后台开) / 无限滚动全保留。
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Copy, Code2, ShieldCheck, RefreshCw, Plus, Search, Check } from 'lucide-react'
import { ccApi } from '../../api/ccClient'
import { ccChatApi, type ImportableSession, type CcChatSessionMeta } from '../../api/ccChatClient'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { projectsApi } from '../../api/projectsClient'
import { usePanels } from '../../stores/panelsStore'
import { openProps } from '../../utils/middleClick'
import { relTimeEn as relTime } from '../../lib/time'
import { ProjectIcon } from '../../lib/projectIcon'
import { copyText } from '../../lib/copyText'
import { openChatInVscode } from '../../lib/surface'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { DimText } from '../../components/Segmented'
import { domainColor } from '../../shell/tokens'

function planShortName(planId: string | null | undefined): string {
  if (!planId) return ''
  const last = planId.split('/').pop() || planId
  return last.replace(/^\[\d{4}-\d{2}-\d{2}\]/, '')
}
function isoMs(ts: string | null | undefined): number {
  if (!ts) return 0
  const t = new Date(ts).getTime()
  return Number.isNaN(t) ? 0 : t
}

type Kind = 'plan' | 'material' | 'conv' | 'team'
type Row = {
  key: string
  kind: Kind
  title: string
  ts: number // 统一成毫秒, 排序键
  plan: string // 所属计划(短名)
  path: string // 对应路径
  status?: string
  projId?: string // 所属项目 id(配 ProjectIcon)
  open: (bg?: boolean) => void
  menu?: KebabItem[] // 行尾「…更多」菜单(复制 id / VSCode 打开 等; 后续逐步加 plan audit)
}

const KIND_META: Record<Kind, { label: string; color: string }> = {
  plan: { label: '计划', color: 'var(--fp-link)' },
  material: { label: '审阅材料', color: domainColor.cyan },
  conv: { label: '对话', color: 'var(--fp-ok)' },
  team: { label: '管线', color: 'var(--fp-warn)' },
}
const RUN_STATUS: Record<string, string> = { working: '运行中', done: '已完成', waiting: '等待输入', idle: '空闲' }
const RUN_STATUS_CLS: Record<string, string> = { working: 'st-warn', done: 'st-ok', waiting: 'st-hollow', idle: 'st-idle' }

function matchQ(q: string, ...fields: (string | null | undefined)[]): boolean {
  if (!q) return true
  return fields.filter(Boolean).join(' ').toLowerCase().includes(q.toLowerCase())
}

const PAGE = 40

/** 时间分组(今天/昨天/本周/更早;组间秩序 = 列表唯一排序维度,替代不可点的列头)。 */
function bucketOf(ts: number, startOfDay: number): string {
  if (ts >= startOfDay) return 'today'
  if (ts >= startOfDay - 86_400_000) return 'yesterday'
  if (ts >= startOfDay - 7 * 86_400_000) return 'week'
  return 'earlier'
}
const BUCKETS: Array<{ key: string; label: string }> = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: 'week', label: '本周' },
  { key: 'earlier', label: '更早' },
]

export default function HomeThreeCards() {
  const [convs, setConvs] = useState<any[]>([])
  const [plans, setPlans] = useState<any[]>([])
  const [materials, setMaterials] = useState<Material[]>([])
  const [teams, setTeams] = useState<any[]>([])  // 管线(team*.py), 加进最近列表的一个 kind
  const [planProj, setPlanProj] = useState<Record<string, string>>({})  // planId → 所属项目 id
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [openError, setOpenError] = useState<string | null>(null)
  // 默认: 计划 + 审阅材料; 对话可选(默认关)
  const [kinds, setKinds] = useState<Record<Kind, boolean>>({ plan: true, material: true, conv: false, team: false })
  const [visible, setVisible] = useState(PAGE)
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [chat, active, allPlans, mats, teamsRaw] = await Promise.all([
        ccChatApi.list({ limit: 80, includeArchived: false }).catch(() => [] as CcChatSessionMeta[]),
        ccChatApi.activeSessions(7 * 86400, 80).catch(() => [] as ImportableSession[]),
        // 全量计划(120 条, 带 last_modified_ts) — 后端已有该接口, 不用 briefing 的 15 条活跃子集
        fetch('/api/boss-sight/plans').then((r) => (r.ok ? r.json() : { plans: [] })).then((d) => (d.plans as any[]) || []).catch(() => [] as any[]),
        reviewstageApi.list().then((r) => r.items).catch(() => [] as Material[]),
        fetch('/api/teams').then((r) => (r.ok ? r.json() : { items: [] })).then((d) => (d.items as any[]) || []).catch(() => [] as any[]),
      ])
      const omniByClaudeSid = new Map<string, CcChatSessionMeta>()
      for (const c of chat) { if (c.claude_session_id) omniByClaudeSid.set(c.claude_session_id, c) }
      const rows: any[] = active.map((it) => {
        const omni = omniByClaudeSid.get(it.session_id)
        return { provider: it.provider, status: it.status, digest: it.digest, preview: it.preview, last_user: it.last_user, last_did: it.last_did, mtime: it.mtime || 0, cwd: it.cwd, sessionId: it.session_id, omniId: omni?.id, activePlan: omni?.active_plan ?? null }
      })
      const activeSids = new Set(active.map((a) => a.session_id))
      for (const c of chat) {
        if (c.claude_session_id && activeSids.has(c.claude_session_id)) continue
        rows.push({ provider: c.provider || 'claude_code', status: c.alive ? 'idle' : 'done', preview: c.last_message || c.first_message, mtime: c.started_at || 0, sessionId: c.claude_session_id || c.id, omniId: c.id, activePlan: c.active_plan ?? null })
      }
      setConvs(rows)
      setPlans(allPlans)  // 全量 120 条; 展示走下方无限滚动分批(滚到底再多渲)
      setMaterials(mats)
      setTeams(teamsRaw)
      // planId → 所属项目 id(服务端归属权威, 不靠路径前缀猜), 给计划/材料配项目 icon
      void projectsApi.list().then((board) => {
        const projs = ((board as any)?.projects as any[]) || []
        return Promise.all(projs.map((p) =>
          projectsApi.plans(p.id).then((r) => ({ id: p.id as string, ids: (r.plan_ids || []) as string[] })).catch(() => ({ id: p.id as string, ids: [] as string[] })),
        ))
      }).then((lists) => {
        const map: Record<string, string> = {}
        for (const { id, ids } of (lists || [])) for (const pid of ids) map[pid] = id
        setPlanProj(map)
      }).catch(() => {})
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // 普通人用聊天迁到收编的 chatui(独立服务 :7348): "+新对话"不再建驾驶舱 cc_session, 直接开 chatui 首页。
  const onCreate = useCallback(async () => {
    setOpenError(null)
    try {
      const meta = await ccApi.create({})
      openTab({ type: 'cc_session', id: meta.id }, `Claude CLI · ${meta.id.slice(0, 8)}`)
      void load()
    } catch (cause) {
      setOpenError(`新建 CLI 失败: ${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }, [load, openTab])

  // ── 导航 ──
  // "最近对话"行点击: chatui 与驾驶舱会话 id 不通, 无法深链具体会话, 一律开 chatui 首页(新标签)。
  const openConv = async (c: any, bg = false) => {
    setOpenError(null)
    try {
      const meta = await ccApi.resumeProvider({
        provider: c.provider,
        provider_session_id: c.sessionId,
        cwd: c.cwd,
      })
      const open = bg ? openTabBg : openTab
      open({ type: 'cc_session', id: meta.id }, `${c.provider || 'AI'} CLI · ${String(c.sessionId).slice(0, 8)}`)
    } catch (cause) {
      setOpenError(`打开 CLI 失败: ${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }
  const openPlan = (p: any, bg = false) => {
    const ref = p.open_ref
    if (ref && ref.type && ref.id) (bg ? openTabBg : openTab)({ type: ref.type, id: String(ref.id) }, p.title || p.plan_id, ref.facet)
    else (bg ? openTabBg : openTab)({ type: 'plan', id: p.plan_id }, p.title || p.plan_id)
  }
  const openMaterial = (m: Material, bg = false) => (bg ? openTabBg : openTab)({ type: 'review_queue', id: 'main' }, '审阅队列', m.id)
  // 跑 plan audit: POST 起后台 job → 开 plan_audit 页签轮询渲染报告(分钟级)
  const startAudit = useCallback(async (against: 'conversation' | 'plan', id: string, provider?: string) => {
    try {
      const r = await fetch('/api/plan-audit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ against, id, provider }) })
      if (!r.ok) return
      const d = await r.json()
      if (d?.job_id) openTab({ type: 'plan_audit', id: d.job_id }, `审计:${String(id).slice(0, 8)}`)
    } catch { /* 静默 */ }
  }, [openTab])

  // ── 合并成统一行, 纯按更新时间(ts 毫秒)降序 ──
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    // digest 字段可能是便宜模型如实写的占位串(「信息不足」/「无」), 视同空以便回退到 preview/路径
    const cleanDigest = (s?: string) => { const v = (s || '').trim(); return v === '信息不足' || v === '无' ? '' : v }
    if (kinds.plan) {
      for (const p of plans) {
        out.push({ key: `plan-${p.plan_id}`, kind: 'plan', title: p.title || planShortName(p.plan_id), ts: isoMs(p.last_modified_ts), plan: planShortName(p.plan_id), path: p.plan_id || '', status: p.status, projId: planProj[p.plan_id], open: (bg) => openPlan(p, bg), menu: [
          { label: '复制 plan id', icon: <Copy size={14} />, testid: 'recent-kebab-copy-plan', onClick: () => { void copyText(String(p.plan_id || '')) } },
          { label: '跑 plan audit', icon: <ShieldCheck size={14} />, testid: 'recent-kebab-audit-plan', onClick: () => { void startAudit('plan', String(p.plan_id || '')) } },
        ] })
      }
    }
    if (kinds.material) {
      for (const m of materials) {
        out.push({ key: `mat-${m.id}`, kind: 'material', title: m.title, ts: isoMs(m.created_at), plan: planShortName(m.source_plan_id), path: `${m.kind || ''}${m.tier ? ' · ' + m.tier : ''}`, status: m.status, projId: planProj[m.source_plan_id || ''], open: (bg) => openMaterial(m, bg), menu: [{ label: '复制材料 id', icon: <Copy size={14} />, testid: 'recent-kebab-copy-mat', onClick: () => { void copyText(String(m.id)) } }] })
      }
    }
    if (kinds.conv) {
      for (const c of convs) {
        const planLabel = c.activePlan ? planShortName(c.activePlan) : cleanDigest(c.digest?.plan)
        out.push({ key: `conv-${c.sessionId}-${c.omniId || ''}`, kind: 'conv', title: cleanDigest(c.digest?.title) || c.last_user || c.preview || String(c.sessionId).slice(0, 12), ts: (c.mtime || 0) * 1000, plan: planLabel || cleanDigest(c.digest?.project), path: c.cwd || '', status: c.status, open: (bg) => openConv(c, bg), menu: [
          { label: '复制 session id', icon: <Copy size={14} />, testid: 'recent-kebab-copy-sid', onClick: () => { void copyText(String(c.sessionId || '')) } },
          { label: '在 VSCode 打开', icon: <Code2 size={14} />, testid: 'recent-kebab-vscode', onClick: () => openChatInVscode(c.provider, c.cwd, c.sessionId) },
          { label: '跑 plan audit', icon: <ShieldCheck size={14} />, testid: 'recent-kebab-audit-conv', onClick: () => { void startAudit('conversation', String(c.sessionId || ''), c.provider) } },
        ] })
      }
    }
    if (kinds.team) {
      for (const t of teams) {
        const pkg = String(t.package || '')
        const name = pkg.split('/').filter(Boolean).pop() || t.name || t.id
        out.push({ key: `team-${t.id}`, kind: 'team', title: name, ts: (t.mtime || 0) * 1000, plan: '', path: pkg, open: (bg) => (bg ? openTabBg : openTab)({ type: 'team', id: t.id }, name), menu: [
          { label: '复制管线 id', icon: <Copy size={14} />, testid: 'recent-kebab-copy-team', onClick: () => { void copyText(String(t.id)) } },
          ...(t.file_path ? [{ label: '复制源码路径', icon: <Code2 size={14} />, testid: 'recent-kebab-copy-teampath', onClick: () => { void copyText(String(t.file_path)) } }] : []),
        ] })
      }
    }
    out.sort((a, b) => b.ts - a.ts)
    return out
  }, [plans, materials, convs, teams, kinds, planProj]) // eslint-disable-line react-hooks/exhaustive-deps

  const fRows = useMemo(() => rows.filter((r) => matchQ(q, r.title, r.plan, r.path, KIND_META[r.kind].label)), [rows, q])
  const shown = fRows.slice(0, visible)

  // 筛选项计数 = 全量分布(不受当前筛选影响;「对话/管线里有多少条」一眼可读)
  const kindCounts: Record<Kind, number> = { plan: plans.length, material: materials.length, conv: convs.length, team: teams.length }

  // 时间分组(今天/昨天/本周/更早),空组不渲染
  const grouped = useMemo(() => {
    const now = new Date()
    const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    const map = new Map<string, Row[]>()
    for (const r of shown) {
      const b = bucketOf(r.ts, startOfDay)
      if (!map.has(b)) map.set(b, [])
      map.get(b)!.push(r)
    }
    return BUCKETS.filter((b) => map.has(b.key)).map((b) => ({ ...b, rows: map.get(b.key)! }))
  }, [shown])

  useEffect(() => { setVisible(PAGE) }, [q, kinds])

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120 && visible < fRows.length) {
      setVisible((v) => v + PAGE)
    }
  }

  const toggle = (k: Kind) => setKinds((s) => ({ ...s, [k]: !s[k] }))

  // 图签·筛选状态: 默认(计划+材料、无搜索词)不显示;有筛选实时显示条件+结果计数
  const filtering = q.trim() !== '' || !kinds.plan || !kinds.material || kinds.conv || kinds.team
  const filterText = [
    ...((['plan', 'material', 'conv', 'team'] as Kind[]).filter((k) => kinds[k]).map((k) => KIND_META[k].label)),
    ...(q.trim() ? [`“${q.trim()}”`] : []),
  ].join(' · ')

  return (
    <div className="ct-page" data-testid="home-recent-list">
      <div className="ct-tools">
        <div className="v2-filterbar" style={{ flex: 1, minWidth: 0 }}>
          <label className="v2-search">
            <Search size={14} aria-hidden />
            <input
              placeholder="搜计划 / 材料 / 对话 / 管线…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              data-testid="home-search"
            />
          </label>
          {/* 类型多选(chip 带 ✓ 态 + 计数徽章;aria-pressed 语义;保留 home-filter-* testid) */}
          {(['plan', 'material', 'conv', 'team'] as Kind[]).map((k) => (
            <button
              key={k}
              type="button"
              className="ct-chip"
              aria-pressed={kinds[k]}
              onClick={() => toggle(k)}
              data-testid={`home-filter-${k}`}
            >
              <span className="ck" aria-hidden="true"><Check size={10} strokeWidth={3.5} /></span>
              {KIND_META[k].label}
              <span className="v2-count">{kindCounts[k]}</span>
            </button>
          ))}
        </div>
        <span className="tools-right">
          {/* 图签·筛选状态(唯一合法 title block: 实时过滤条件+结果计数,无筛选整块隐藏) */}
          <span className={`bp-livetag${filtering ? ' show' : ''}`} aria-live="polite" data-testid="home-livetag">
            {filtering && (
              <span className="bp-titleblock">
                <span className="tb">筛选<b>{filterText || '—'}</b></span>
                <span className="tb">结果<b>{fRows.length} 项</b></span>
              </span>
            )}
          </span>
          {/* 新会话统一进入真实 CLI；低频“刷新”收进 ⋯。 */}
          <button type="button" className="ct-newbtn" onClick={() => onCreate()} data-testid="home-new-session">
            <Plus size={13} aria-hidden />新 CLI
          </button>
          <KebabMenu testid="home-actions" items={[
            { label: '刷新', icon: <RefreshCw size={15} />, testid: 'home-refresh', onClick: () => { void load() } },
          ] as KebabItem[]} />
        </span>
      </div>
      {openError && <div className="tm-error" role="alert">{openError}</div>}
      <div className="ct-scroll" onScroll={onScroll} data-testid="home-recent-scroll">
        {!loading && fRows.length === 0 && <div className="ct-empty">无内容(换个筛选或搜索词)</div>}
        {grouped.map((g) => (
          <div key={g.key} data-testid={`home-group-${g.key}`}>
            <div className="ct-ghead">
              {g.label}
              <DimText>{g.rows.length}</DimText>
            </div>
            {g.rows.map((r) => {
              const m = KIND_META[r.kind]
              return (
                <div
                  key={r.key}
                  className="ct-row"
                  data-testid="home-recent-row"
                  role="link"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') r.open() }}
                  {...openProps(() => r.open(), () => r.open(true))}
                >
                  {r.projId
                    ? <ProjectIcon id={r.projId} size={24} />
                    : <span className="ico-s" style={{ width: 24, height: 24 }} aria-hidden="true"><span className="ct-kinddot" style={{ background: m.color }} /></span>}
                  <div style={{ minWidth: 0 }}>
                    <div className="ct-title" title={r.title}>
                      {r.kind === 'conv' && r.status && (
                        <span className={`v2-status ${RUN_STATUS_CLS[r.status] || 'st-idle'}`} style={{ flex: 'none' }}>
                          <i className="led" aria-hidden />{RUN_STATUS[r.status] || ''}
                        </span>
                      )}
                      <span className="t">{r.title || '(无标题)'}</span>
                    </div>
                    {/* 次级信息折叠为一行 meta: 类型 · 所属计划 · 路径(窄屏再并时间) */}
                    <div className="ct-meta" title={r.path || undefined}>
                      {m.label} · {r.plan || '—'} · {r.path || '—'}
                      <span className="m-time"> · {r.ts ? relTime(r.ts / 1000) : '—'}</span>
                    </div>
                  </div>
                  <span className="ct-side">
                    <span className="ct-kinddot" style={{ background: m.color }} role="img" aria-label={m.label} title={m.label} />
                    <span className="ct-time">{r.ts ? relTime(r.ts / 1000) : '—'}</span>
                  </span>
                  <span className="ct-kebab" data-omni-capture-ignore="true" onClick={(e) => e.stopPropagation()}>
                    {r.menu && <KebabMenu items={r.menu} testid={`recent-kebab-${r.kind}`} iconSize={14} />}
                  </span>
                </div>
              )
            })}
          </div>
        ))}
        {shown.length < fRows.length && <div className="ct-more">下滑加载更多 · 已显示 {shown.length}/{fRows.length}</div>}
      </div>
    </div>
  )
}
