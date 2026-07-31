// [OMNI] origin=claude-code type=dashboard summary="设置>Token统计 页签: 消费 /api/boss-sight/ccusage 的会话明细, 按 软件/项目 多选筛选并实时联动聚合全部图表(卡片/5h块/光谱热力图/按软件/按项目/按模型/按天趋势); 手搓 div/格子" why="用户要多选+全联动+omni项目划分; 数据引擎走 ccusage(路线B), 项目归属走 omni 项目 roots + LLM 后台标识" tags=ccusage,token-stats,frontend,settings
// 蓝图 G 重置(2026-07-19 阶段四第四波;合同=TRIFORM-UX-REDESIGN-V2/demo/MAPPING.md):
//   · 手搓 seg(灰底小按钮)→ 共享 Segmented(role=radiogroup,hatch 选中);刷新 → 34px iconbtn。
//   · 统计卡/图表面板 → 厚框纸件(2px 白框+纸叠纸硬影);条形槽=深图板;硬编码灰色清零 → token。
//   · 按软件/项目/计划条形行=多选筛选 → 补 role=checkbox + aria-checked + 键盘 Enter/Space
//     (span/div 裸奔当控件 → 语义元素+role);已选 chip 改 button;图表刻度标注抬到 ≥12px。
//   · 热力图浮层=深色描图纸(无 blur,守 glass-scope 白名单)。数据流(/api/boss-sight/ccusage)未动。
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, X, Check, TriangleAlert } from 'lucide-react'
import { Segmented } from '../../components/Segmented'
import './settings.css'

interface ModelBreak { model: string; cost: number; tokens: number }
interface Session {
  sid: string
  project: string
  plan?: string
  agent: string
  day: string
  totalCost: number
  totalTokens: number
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheCreationTokens: number
  models: ModelBreak[]
}
interface ActiveBlock {
  startTime?: string
  models?: string[]
  totalTokens?: number
  costUSD?: number
  burnRate?: { costPerHour?: number; tokensPerMinute?: number }
  projection?: { remainingMinutes?: number; totalCost?: number; totalTokens?: number }
}
interface CcusageData {
  available: boolean
  computing?: boolean
  stale?: boolean
  error?: string
  generated_at?: number
  source?: string
  note?: string
  agents?: string[]
  sessions?: Session[]
  active_block?: ActiveBlock | null
}

// 聚合桶
interface Agg { cost: number; tokens: number; input: number; output: number; cacheRead: number; sessions: number }
interface DayRow { period: string; totalCost: number; totalTokens: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheCreationTokens: number; modelsUsed: string[] }

const AGENT_LABEL: Record<string, string> = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini', qwen: 'Qwen', kimi: 'Kimi', glm: 'GLM', other: '其它' }
const AGENT_COLOR: Record<string, string> = { claude: '#d97757', codex: '#10a37f', gemini: '#4285f4', qwen: '#7c3aed', kimi: '#e11d48', glm: '#0891b2', other: '#8a8a8a' }
const PROJECT_PALETTE = ['#6ea8fe', '#d97757', '#10a37f', '#c084fc', '#f0a020', '#e06c75', '#4fd1c5', '#b088f9', '#e5a13a', '#5fb3b3', '#8a8a8a']

function fmtTokens(n?: number): string {
  const v = n || 0
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return String(Math.round(v))
}
function fmtUsd(n?: number): string { return '$' + (n || 0).toLocaleString('en-US', { maximumFractionDigits: 2 }) }
function ymd(d: Date): string { return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}` }
function agentLabel(k: string): string { return AGENT_LABEL[k] || k }
function agentColorForModel(model: string): string {
  const n = model.toLowerCase()
  if (n.includes('claude')) return AGENT_COLOR.claude
  if (n.startsWith('gpt') || n.includes('codex')) return AGENT_COLOR.codex
  if (n.includes('gemini')) return AGENT_COLOR.gemini
  if (n.includes('qwen')) return AGENT_COLOR.qwen
  if (n.includes('kimi')) return AGENT_COLOR.kimi
  if (n.includes('glm')) return AGENT_COLOR.glm
  return AGENT_COLOR.other
}
function emptyAgg(): Agg { return { cost: 0, tokens: 0, input: 0, output: 0, cacheRead: 0, sessions: 0 } }

/** Enter/Space 触发(条形多选行键盘等价)。 */
const keyActivate = (fn: () => void) => (e: React.KeyboardEvent) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn() }
}

// ── 贡献热力图: 列=周, 连续变色(log归一→绿色系渐变), 深色描图纸浮层 ──
const Heatmap: React.FC<{ days: DayRow[]; metric: 'tokens' | 'cost' }> = ({ days, metric }) => {
  const [tip, setTip] = useState<{ x: number; y: number; row: DayRow } | null>(null)
  if (!days.length) return <div className="st-muted">无数据</div>
  const valOf = (d: DayRow) => (metric === 'cost' ? d.totalCost : d.totalTokens)
  const rowMap = new Map(days.map((d) => [d.period, d]))
  const nz = days.map(valOf).filter((v) => v > 0)
  const lo = nz.length ? Math.min(...nz) : 0
  const hi = nz.length ? Math.max(...nz) : 1
  const colorOf = (v: number) => {
    if (v <= 0) return 'var(--fp-bp-solid)'
    const t = hi <= lo ? 1 : (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo))
    return `hsl(140, ${Math.round(45 + t * 40)}%, ${Math.round(14 + t * 46)}%)`
  }
  const first = new Date(days[0].period + 'T00:00:00')
  const last = new Date(days[days.length - 1].period + 'T00:00:00')
  const start = new Date(first)
  start.setDate(start.getDate() - start.getDay())
  const cols: Array<{ key: string; month: number; cells: Array<string | null> }> = []
  const cur = new Date(start)
  while (cur <= last) {
    const cells: Array<string | null> = []
    const colMonth = cur.getMonth()
    let colKey = ''
    for (let i = 0; i < 7; i++) {
      const key = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}-${String(cur.getDate()).padStart(2, '0')}`
      if (!colKey) colKey = key
      cells.push(cur < first || cur > last ? null : key)
      cur.setDate(cur.getDate() + 1)
    }
    cols.push({ key: colKey, month: colMonth, cells })
  }
  const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六']
  return (
    <div style={{ overflowX: 'auto', paddingBottom: 4 }}>
      <div style={{ display: 'flex', gap: 3, marginBottom: 4, height: 14, marginLeft: 20 }}>
        {cols.map((col, ci) => (
          <div key={ci} style={{ width: 13, fontSize: 12, color: 'var(--fp-text-3)', fontFamily: 'var(--fp-font-mono)', whiteSpace: 'nowrap', overflow: 'visible' }}>
            {ci === 0 || col.month !== cols[ci - 1].month ? `${col.month + 1}月` : ''}
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 3 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginRight: 4 }} aria-hidden="true">
          {WEEKDAY.map((w, i) => (
            <div key={i} style={{ width: 13, height: 13, fontSize: 12, color: 'var(--fp-text-3)', lineHeight: '13px', textAlign: 'center' }}>{i % 2 === 1 ? w : ''}</div>
          ))}
        </div>
        {cols.map((col) => (
          <div key={col.key} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {col.cells.map((key, ri) => {
              const row = key ? rowMap.get(key) : undefined
              const v = row ? valOf(row) : 0
              return (
                <div
                  key={ri}
                  onMouseEnter={(e) => row && setTip({ x: e.clientX, y: e.clientY, row })}
                  onMouseMove={(e) => row && setTip({ x: e.clientX, y: e.clientY, row })}
                  onMouseLeave={() => setTip(null)}
                  style={{ width: 13, height: 13, borderRadius: 2, cursor: row ? 'pointer' : 'default', background: key ? colorOf(v) : 'transparent' }}
                />
              )
            })}
          </div>
        ))}
      </div>
      {tip && (
        <div className="tk-tip" style={{ left: Math.min(tip.x + 14, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 240), top: tip.y + 14 }}>
          <div style={{ color: 'var(--fp-bp-brass-hi)', marginBottom: 5, fontWeight: 600, fontFamily: 'var(--fp-font-mono)' }}>{tip.row.period}</div>
          <div>成本 <b style={{ color: 'var(--fp-text)' }}>{fmtUsd(tip.row.totalCost)}</b></div>
          <div>Token <b style={{ color: 'var(--fp-text)' }}>{fmtTokens(tip.row.totalTokens)}</b></div>
          <div style={{ color: 'var(--fp-text-2)' }}>= 输入 {fmtTokens(tip.row.inputTokens)} + 输出 {fmtTokens(tip.row.outputTokens)} + 缓存读 {fmtTokens(tip.row.cacheReadTokens)} + 缓存写 {fmtTokens(tip.row.cacheCreationTokens)}</div>
          {tip.row.modelsUsed.length > 0 && <div style={{ color: 'var(--fp-text-3)', marginTop: 5, maxWidth: 260, whiteSpace: 'normal' }}>模型: {tip.row.modelsUsed.join(', ')}</div>}
        </div>
      )}
    </div>
  )
}

const RANGES = [
  { id: '7d', label: '近 7 天', days: 7 },
  { id: '30d', label: '近 30 天', days: 30 },
  { id: '90d', label: '近 90 天', days: 90 },
  { id: 'all', label: '全部', days: 0 },
]

const TokenStatsTab: React.FC = () => {
  const [range, setRange] = useState('30d')
  const [metric, setMetric] = useState<'tokens' | 'cost'>('cost')
  const [agentSel, setAgentSel] = useState<Set<string>>(new Set())
  const [projectSel, setProjectSel] = useState<Set<string>>(new Set())
  const [planSel, setPlanSel] = useState<Set<string>>(new Set())
  const [data, setData] = useState<CcusageData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback((force?: boolean) => {
    setLoading(true)
    setError(null)
    const r = RANGES.find((x) => x.id === range)!
    const parts: string[] = []
    if (r.days > 0) { const d = new Date(); d.setDate(d.getDate() - r.days + 1); parts.push('since=' + ymd(d)) }
    if (force) parts.push('force=true')
    const qs = parts.length ? '?' + parts.join('&') : ''
    fetch('/api/boss-sight/ccusage' + qs)
      .then((r) => r.json())
      .then((d: CcusageData) => {
        setData(d)
        if (d.computing) { window.setTimeout(() => load(false), 4000); return }
        if (!d.available) setError(d.error || 'ccusage 不可用')
        setLoading(false)
      })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }, [range])

  useEffect(() => { load() }, [load])
  // 换时间范围时清筛选(项目/软件集合可能变)
  useEffect(() => { setAgentSel(new Set()); setProjectSel(new Set()); setPlanSel(new Set()) }, [range])

  const toggle = (set: Set<string>, key: string, setter: (s: Set<string>) => void) => {
    const n = new Set(set); n.has(key) ? n.delete(key) : n.add(key); setter(n)
  }

  const metricVal = (a: Agg) => (metric === 'cost' ? a.cost : a.tokens)
  const fmtMetric = (v: number) => (metric === 'cost' ? fmtUsd(v) : fmtTokens(v))

  const sessions = data?.sessions || []
  // 按多选筛选(空集=全部)
  const filtered = useMemo(() => sessions.filter((s) =>
    (agentSel.size === 0 || agentSel.has(s.agent))
    && (projectSel.size === 0 || projectSel.has(s.project))
    && (planSel.size === 0 || planSel.has(s.plan || '未绑定计划'))
  ), [sessions, agentSel, projectSel, planSel])

  // 从筛选后的会话实时聚合全部维度
  const agg = useMemo(() => {
    const totals = emptyAgg()
    const byAgent: Record<string, Agg> = {}
    const byProject: Record<string, Agg> = {}
    const byPlan: Record<string, Agg> = {}
    const byModel: Record<string, Agg> = {}
    const byDay: Record<string, DayRow> = {}
    const accA = (m: Record<string, Agg>, k: string, s: Session) => {
      const a = (m[k] = m[k] || emptyAgg())
      a.cost += s.totalCost; a.tokens += s.totalTokens; a.input += s.inputTokens; a.output += s.outputTokens; a.cacheRead += s.cacheReadTokens; a.sessions += 1
    }
    for (const s of filtered) {
      totals.cost += s.totalCost; totals.tokens += s.totalTokens; totals.input += s.inputTokens; totals.output += s.outputTokens; totals.cacheRead += s.cacheReadTokens; totals.sessions += 1
      accA(byAgent, s.agent, s)
      accA(byProject, s.project, s)
      accA(byPlan, s.plan || '未绑定计划', s)
      for (const m of s.models) {
        const a = (byModel[m.model] = byModel[m.model] || emptyAgg())
        a.cost += m.cost; a.tokens += m.tokens; a.sessions += 0
      }
      if (s.day) {
        const d = (byDay[s.day] = byDay[s.day] || { period: s.day, totalCost: 0, totalTokens: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreationTokens: 0, modelsUsed: [] })
        d.totalCost += s.totalCost; d.totalTokens += s.totalTokens; d.inputTokens += s.inputTokens; d.outputTokens += s.outputTokens
        d.cacheReadTokens += s.cacheReadTokens; d.cacheCreationTokens += s.cacheCreationTokens
        for (const m of s.models) if (!d.modelsUsed.includes(m.model)) d.modelsUsed.push(m.model)
      }
    }
    return { totals, byAgent, byProject, byPlan, byModel, byDay }
  }, [filtered])

  const BOTTOM = new Set(['未归属', '未绑定计划', '临时/测试会话'])
  const sortEntries = (m: Record<string, Agg>) => {
    const arr = Object.entries(m).sort((a, b) => metricVal(b[1]) - metricVal(a[1]))
    // 未归属/未绑定类沉底: 真实项目/计划排前面
    return [...arr.filter(([k]) => !BOTTOM.has(k)), ...arr.filter(([k]) => BOTTOM.has(k))]
  }
  const byAgent = useMemo(() => sortEntries(agg.byAgent), [agg, metric])
  const byProject = useMemo(() => sortEntries(agg.byProject), [agg, metric])
  const byPlan = useMemo(() => sortEntries(agg.byPlan), [agg, metric])
  const byModel = useMemo(() => sortEntries(agg.byModel), [agg, metric])
  const days = useMemo(() => Object.values(agg.byDay).sort((a, b) => (a.period < b.period ? -1 : 1)), [agg])
  const t = agg.totals
  const ab = data?.active_block
  const filterOn = agentSel.size > 0 || projectSel.size > 0 || planSel.size > 0

  // 已选筛选 chip(数据色描边+软底,button 语义)
  const chipStyle = (color: string): React.CSSProperties => ({ border: `1px solid ${color}`, background: color + '22', color: 'var(--fp-text)' })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="v2-filterbar">
        <Segmented
          label="时间范围"
          items={RANGES.map((r) => ({ value: r.id, label: r.label }))}
          current={range}
          onChange={setRange}
        />
        <Segmented
          label="指标"
          items={[{ value: 'cost', label: '成本 $' }, { value: 'tokens', label: 'Token' }]}
          current={metric}
          onChange={(v) => setMetric(v as 'tokens' | 'cost')}
        />
        <button className="v2-iconbtn" onClick={() => load(true)} title="强制刷新(重跑 ccusage)" aria-label="强制刷新">
          <RefreshCw size={14} aria-hidden />
        </button>
        {data?.generated_at && <span className="tk-meta">更新于 {new Date(data.generated_at * 1000).toLocaleTimeString('zh-CN')} · {data.source || 'ccusage'}{data.stale ? ' · 后台刷新中' : ''}</span>}
      </div>

      {loading && <div className="st-muted">{data?.computing ? '首次计算中(约 15 秒扫本地日志), 页面会自动刷新…' : '正在加载…'}</div>}
      {error && <div className="st-err">{error}</div>}

      {data?.available && (
        <>
          {filterOn && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span className="tk-sec" style={{ margin: 0 }}>已筛选</span>
              {[...agentSel].map((a) => <button key={a} className="tk-chip" style={chipStyle(AGENT_COLOR[a] || AGENT_COLOR.other)} onClick={() => toggle(agentSel, a, setAgentSel)}>{agentLabel(a)} <X size={11} aria-hidden style={{ verticalAlign: -1 }} /></button>)}
              {[...projectSel].map((p) => <button key={p} className="tk-chip" style={chipStyle('#6ea8fe')} onClick={() => toggle(projectSel, p, setProjectSel)}>{p} <X size={11} aria-hidden style={{ verticalAlign: -1 }} /></button>)}
              {[...planSel].map((p) => <button key={p} className="tk-chip" style={chipStyle('#c084fc')} onClick={() => toggle(planSel, p, setPlanSel)}>{p} <X size={11} aria-hidden style={{ verticalAlign: -1 }} /></button>)}
              <button className="st-btn" style={{ padding: '3px 10px' }} onClick={() => { setAgentSel(new Set()); setProjectSel(new Set()); setPlanSel(new Set()) }}>清除全部</button>
            </div>
          )}

          <div className="tk-cards">
            <div className="tk-card">
              <div className="l">累计 Token{filterOn ? ' · 已筛选' : ''}</div>
              <div className="v">{fmtTokens(t.tokens)}</div>
              <div className="s">输入 {fmtTokens(t.input)} · 输出 {fmtTokens(t.output)}</div>
            </div>
            <div className="tk-card">
              <div className="l">累计成本(估)</div>
              <div className="v">{fmtUsd(t.cost)}</div>
              <div className="s">缓存读 {fmtTokens(t.cacheRead)}</div>
            </div>
            <div className="tk-card">
              <div className="l">会话数</div>
              <div className="v">{t.sessions}</div>
              <div className="s">{byAgent.map(([k]) => agentLabel(k)).join(' · ') || '—'}</div>
            </div>
            <div className="tk-card">
              <div className="l">天数</div>
              <div className="v">{days.length}</div>
              <div className="s">{days.length ? `${days[0].period} ~ ${days[days.length - 1].period}` : '—'}</div>
            </div>
          </div>

          {ab && !filterOn && (
            <div className="tk-panel">
              <div className="tk-sec" style={{ marginTop: 0 }}>当前 5 小时计费块</div>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', color: 'var(--fp-text-2)', fontSize: 'var(--fp-fs-3)' }}>
                <span>已用 <b style={{ color: 'var(--fp-text)' }}>{fmtTokens(ab.totalTokens)}</b> token · <b style={{ color: 'var(--fp-text)' }}>{fmtUsd(ab.costUSD)}</b></span>
                <span>燃烧率 <b style={{ color: 'var(--fp-text)' }}>{fmtTokens(ab.burnRate?.tokensPerMinute)}/分</b> · {fmtUsd(ab.burnRate?.costPerHour)}/时</span>
                {ab.projection && <span>预计到期 <b style={{ color: 'var(--fp-text)' }}>{fmtUsd(ab.projection.totalCost)}</b> · 剩 {ab.projection.remainingMinutes} 分</span>}
              </div>
            </div>
          )}

          <div>
            <div className="tk-sec">贡献热力图({metric === 'cost' ? '成本' : 'Token'} / 天{filterOn ? ' · 已筛选' : ''})</div>
            <div className="tk-panel"><Heatmap days={days} metric={metric} /></div>
          </div>

          <div>
            <div className="tk-sec">按软件(点击多选筛选)</div>
            <div className="tk-panel">
              {byAgent.length === 0 && <div className="st-muted">无数据</div>}
              {(() => {
                const max = Math.max(1, ...byAgent.map(([, v]) => metricVal(v)))
                return byAgent.map(([k, v]) => {
                  const on = agentSel.has(k)
                  return (
                    <div
                      key={k}
                      className="tk-row"
                      role="checkbox"
                      aria-checked={on}
                      tabIndex={0}
                      style={{ opacity: agentSel.size && !on ? 0.5 : 1 }}
                      onClick={() => toggle(agentSel, k, setAgentSel)}
                      onKeyDown={keyActivate(() => toggle(agentSel, k, setAgentSel))}
                    >
                      <span className="n" style={{ color: on ? 'var(--fp-text)' : undefined }}>
                        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: AGENT_COLOR[k] || AGENT_COLOR.other, marginRight: 6 }} />{agentLabel(k)}{on && <Check size={12} aria-hidden style={{ verticalAlign: -2, marginLeft: 4 }} />}
                      </span>
                      <span className="t"><span style={{ display: 'block', height: '100%', width: `${(metricVal(v) / max) * 100}%`, background: AGENT_COLOR[k] || AGENT_COLOR.other }} /></span>
                      <span className="val">{fmtMetric(metricVal(v))} · {v.sessions} 会话</span>
                    </div>
                  )
                })
              })()}
            </div>
          </div>

          <div>
            <div className="tk-sec">按项目(点击多选筛选)</div>
            <div className="tk-panel">
              {byProject.length === 0 && <div className="st-muted">无数据</div>}
              {(() => {
                const max = Math.max(1, ...byProject.map(([, v]) => metricVal(v)))
                return byProject.map(([k, v], i) => {
                  const on = projectSel.has(k)
                  const color = PROJECT_PALETTE[i % PROJECT_PALETTE.length]
                  return (
                    <div
                      key={k}
                      className="tk-row"
                      role="checkbox"
                      aria-checked={on}
                      tabIndex={0}
                      style={{ opacity: projectSel.size && !on ? 0.5 : 1 }}
                      onClick={() => toggle(projectSel, k, setProjectSel)}
                      onKeyDown={keyActivate(() => toggle(projectSel, k, setProjectSel))}
                    >
                      <span className="n" style={{ color: on ? 'var(--fp-text)' : undefined }} title={k}>{k}{on && <Check size={12} aria-hidden style={{ verticalAlign: -2, marginLeft: 4 }} />}</span>
                      <span className="t"><span style={{ display: 'block', height: '100%', width: `${(metricVal(v) / max) * 100}%`, background: color }} /></span>
                      <span className="val">{fmtMetric(metricVal(v))} · {v.sessions} 会话</span>
                    </div>
                  )
                })
              })()}
            </div>
          </div>

          <div>
            <div className="tk-sec">按计划(点击多选筛选)</div>
            <div className="tk-panel">
              {byPlan.length === 0 && <div className="st-muted">无数据</div>}
              {(() => {
                const max = Math.max(1, ...byPlan.map(([, v]) => metricVal(v)))
                return byPlan.slice(0, 20).map(([k, v]) => {
                  const on = planSel.has(k)
                  return (
                    <div
                      key={k}
                      className="tk-row"
                      role="checkbox"
                      aria-checked={on}
                      tabIndex={0}
                      style={{ opacity: planSel.size && !on ? 0.5 : 1 }}
                      onClick={() => toggle(planSel, k, setPlanSel)}
                      onKeyDown={keyActivate(() => toggle(planSel, k, setPlanSel))}
                    >
                      <span className="n" style={{ width: 240, color: on ? 'var(--fp-text)' : k === '未绑定计划' ? 'var(--fp-text-3)' : undefined }} title={k}>{k}{on && <Check size={12} aria-hidden style={{ verticalAlign: -2, marginLeft: 4 }} />}</span>
                      <span className="t"><span style={{ display: 'block', height: '100%', width: `${(metricVal(v) / max) * 100}%`, background: k === '未绑定计划' ? 'var(--fp-border-subtle)' : '#c084fc' }} /></span>
                      <span className="val">{fmtMetric(metricVal(v))} · {v.sessions} 会话</span>
                    </div>
                  )
                })
              })()}
              {byPlan.length > 20 && <div className="st-muted" style={{ fontSize: 'var(--fp-fs-4)' }}>…共 {byPlan.length} 个计划, 只显示前 20</div>}
            </div>
          </div>

          <div>
            <div className="tk-sec">按模型</div>
            <div className="tk-panel">
              {byModel.length === 0 && <div className="st-muted">无数据</div>}
              {(() => {
                const max = Math.max(1, ...byModel.map(([, v]) => metricVal(v)))
                return byModel.map(([k, v]) => (
                  <div key={k} className="tk-row">
                    <span className="n" title={k}>{k}</span>
                    <span className="t"><span style={{ display: 'block', height: '100%', width: `${(metricVal(v) / max) * 100}%`, background: agentColorForModel(k) }} /></span>
                    <span className="val">{fmtMetric(metricVal(v))} · {fmtTokens(v.tokens)}</span>
                  </div>
                ))
              })()}
            </div>
          </div>

          <div>
            <div className="tk-sec">按天趋势({metric === 'cost' ? '成本' : 'Token'})</div>
            <div className="tk-panel">
              {days.length === 0 && <div className="st-muted">无数据</div>}
              {days.length > 0 && (() => {
                const vals = days.map((d) => (metric === 'cost' ? d.totalCost : d.totalTokens))
                const max = Math.max(1, ...vals)
                // 刻度抽稀: 最多 ~15 枚,字号地板 12px(图表标注 mono)
                const labelEvery = Math.max(1, Math.ceil(days.length / 15))
                return (
                  <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 140, overflowX: 'auto' }}>
                    {days.map((d, i) => (
                      <div key={d.period} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end', minWidth: 14, flex: '1 0 14px', height: '100%' }} title={`${d.period}\n${fmtTokens(d.totalTokens)} token\n${fmtUsd(d.totalCost)}`}>
                        <div style={{ width: '70%', height: `${(vals[i] / max) * 100}%`, minHeight: 2, background: 'var(--fp-link)', borderRadius: '2px 2px 0 0' }} />
                        <div style={{ color: 'var(--fp-text-3)', fontSize: 12, fontFamily: 'var(--fp-font-mono)', marginTop: 4, transform: 'rotate(-45deg)', transformOrigin: 'center', whiteSpace: 'nowrap' }}>{i % labelEvery === 0 ? d.period.slice(5) : ''}</div>
                      </div>
                    ))}
                  </div>
                )
              })()}
            </div>
          </div>

          {data?.note && <div className="tk-note"><TriangleAlert size={13} aria-hidden style={{ verticalAlign: -2 }} /> {data.note} · 项目归属: 优先 omni 项目目录匹配, 匹配不到的暂归"未归属"(LLM 后台标识中); Codex 暂未按项目细分。按天用会话最后活动日聚合。</div>}
        </>
      )}
    </div>
  )
}

export default TokenStatsTab
