// material_registry 任务材料登记 · 2026-07-19 蓝图 G 重置(阶段四第四波;
// 对照 analysis/material_registry.md 病灶清单):
//   · 300 枚 accent「打开」主按钮海 → 整卡可点(点击=选进详情 drawer);唯一主按钮在 drawer 底部。
//   · 4 枚等权小下拉 → 分形筛选: 层级(2 项)=segmented;作用(7)=单选 picker(统计条并入,
//     选项带真实计数);类型(21)=带搜索的单选 picker;状态=带色点的单选 picker。
//     数据流未改: 仍是 GET /api/boss-sight/material-registry 单值参数,只换控件形态。
//   · 5 枚统计 tile → 工具行 facet 汇总条;卡上伪控件 pill → 真筛选 chip(点击=按该维度筛选)。
//   · todo=n/m → hatch 剖面进度条;状态枚举 → v2-status 色点徽章;300 shown → ⊢N/M⊣ 尺寸标注。
//   · 列表按类型分组(空心描边组头) + 每组 8 张渐进披露;drawer 桌面内嵌分栏(不再 fixed
//     遮筛选条),≤900px 全屏 sheet(CSS)。
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ExternalLink, RefreshCw, Copy, Link2, Search, SlidersHorizontal, X } from 'lucide-react'
import { bossSightApi, type MaterialRegistryItem, type MaterialRegistryResponse } from '../../api/bossSightClient'
import { registry } from '../registry'
import type { EntityType } from '../types'
import { usePanels } from '../../stores/panelsStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { Segmented, DimText } from '../../components/Segmented'
import './materialRegistry.css'

const KIND_LABEL: Record<string, string> = {
  roadmap: '路线',
  plan: '计划',
  project: '项目',
  decision: '决策',
  guard: '守卫',
  policy: '策略',
  standard: '标准',
  template: '模板',
  prompt: 'Prompt',
  progress: '进度',
  handoff: '交接',
  report: '报告',
  audit: '审计',
  reflection: '反思',
  review_material: '审阅材料',
  material_definition: '材料定义',
  worker: 'Worker',
  team: 'Team',
  subagent: 'Subagent',
}

const ROLE_LABEL: Record<string, string> = {
  direction: '方向',
  boundary: '边界',
  reference: '参考',
  progress: '进度',
  review: '审阅',
  executor: '执行者',
  project_asset: '项目资产',
}

const LAYER_LABEL: Record<string, string> = {
  context: '上下文',
  executor: '执行层',
}

const ROLE_KEYS = ['direction', 'boundary', 'progress', 'review', 'reference', 'executor', 'project_asset']

/** 状态 → v2-status 六态色点(图形化状态标记,文本作副标)。 */
function statusTone(status?: string | null): string {
  if (!status) return 'st-idle'
  if (['active', 'in_progress', 'in_progress_with_known_gaps', 'verified', 'done'].includes(status)) return 'st-ok'
  if (['validated-draft', 'todo', 'pending'].includes(status)) return 'st-warn'
  if (['paused'].includes(status)) return 'st-idle'
  return 'st-hollow'
}
function statusColor(status?: string | null): string {
  const t = statusTone(status)
  if (t === 'st-ok') return 'var(--fp-ok)'
  if (t === 'st-warn') return 'var(--fp-warn)'
  return 'var(--fp-text-3)'
}

function label(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return 'unknown'
  return map[value] || value
}

function canOpen(item: MaterialRegistryItem): boolean {
  const ref = item.open_ref || {}
  if (ref.url || ref.command) return true
  return !!(ref.type && ref.id && registry.has(ref.type as EntityType))
}

/** snippet 里的 todo=n/m → 进度条数据(图形化,不再逐卡读数字)。 */
function todoOf(snippet?: string): { done: number; total: number } | null {
  const m = (snippet || '').match(/todo=(\d+)\s*\/\s*(\d+)/)
  if (!m) return null
  return { done: Number(m[1]), total: Number(m[2]) }
}

// ── 单选 facet picker(按钮面=虚线件+当前值黄铜;弹层=描图纸实底,行=圆点 radio) ──
interface FacetOption { value: string; label: string; count?: number; dot?: string }
function FacetPicker({ label, options, current, onChange, searchable = false, allLabel }: {
  label: string
  options: FacetOption[]
  current: string            // '' = 全部
  onChange: (v: string) => void
  searchable?: boolean
  allLabel: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const rootRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && e.target instanceof Node && !rootRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const curLabel = current ? (options.find((o) => o.value === current)?.label || current) : ''
  const shown = q.trim()
    ? options.filter((o) => `${o.label} ${o.value}`.toLowerCase().includes(q.trim().toLowerCase()))
    : options

  const row = (value: string, text: string, count?: number, dot?: string) => (
    <button
      key={value || '__all__'}
      type="button"
      className="mr-radio"
      role="radio"
      aria-checked={current === value}
      onClick={() => { onChange(value); setOpen(false); setQ('') }}
    >
      <span className="rb" aria-hidden />
      {dot && <span className="st-dot" style={{ background: dot }} aria-hidden />}
      <span className="rr-t">{text}</span>
      {count != null && <span className="rr-d">{count}</span>}
    </button>
  )

  return (
    <span className="v2-picker" ref={rootRef} data-testid={`picker-${label}`}>
      <button type="button" className="pk-btn" aria-expanded={open} aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}>
        <SlidersHorizontal size={13} aria-hidden />
        <span>{label}</span>
        {curLabel && <span className="pk-cur">{curLabel}</span>}
        <ChevronDown size={12} aria-hidden />
      </button>
      {open && (
        <div className="mr-pop" role="radiogroup" aria-label={label}>
          <div className="pop-t">{label}(单选)</div>
          {searchable && (
            <span className="pop-search">
              <Search size={12} aria-hidden style={{ color: 'var(--fp-text-3)', flex: 'none' }} />
              <input
                aria-label={`搜索${label}`}
                placeholder="过滤…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </span>
          )}
          <div className="pop-list">
            {row('', allLabel)}
            {shown.map((o) => row(o.value, o.label, o.count, o.dot))}
            {shown.length === 0 && <div style={{ padding: '8px 10px', color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-4)' }}>无匹配</div>}
          </div>
        </div>
      )}
    </span>
  )
}

/** 卡上真筛选 chip(点击=按该维度筛选;S5 伪控件 pill 的合规解)。 */
function FacetChip({ text, active, onClick }: { text: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`mr-chip${active ? ' on' : ''}`}
      title={`按「${text}」筛选`}
      onClick={(e) => { e.stopPropagation(); onClick() }}
    >
      {text}
    </button>
  )
}

export default function MaterialRegistryPanel() {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [role, setRole] = useState('')
  const [layer, setLayer] = useState('')
  const [status, setStatus] = useState('')
  const [data, setData] = useState<MaterialRegistryResponse | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [showAll, setShowAll] = useState<Record<string, boolean>>({})
  const openTab = usePanels((s) => s.openTab)

  const load = () => {
    setLoading(true)
    setError(null)
    bossSightApi.getMaterialRegistry({ q, kind, role, layer, status, limit: 300 })
      .then((next) => {
        setData(next)
        setSelectedId((prev) => next.items.some((item) => item.id === prev) ? prev : null)
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const t = window.setTimeout(load, 150)
    return () => window.clearTimeout(t)
  }, [q, kind, role, layer, status]) // eslint-disable-line react-hooks/exhaustive-deps

  const selected = useMemo(() => data?.items.find((item) => item.id === selectedId) || null, [data, selectedId])
  const kinds = useMemo(() => Object.keys(data?.counts.by_kind || {}).sort(), [data])
  const statuses = useMemo(() => Object.keys(data?.counts.by_status || {}).filter((s) => s !== 'unknown').sort(), [data])

  // 类型分组(按 count 降序) — 替代 300 张卡无结构平铺。
  const groups = useMemo(() => {
    const map = new Map<string, MaterialRegistryItem[]>()
    for (const it of data?.items || []) {
      const k = it.kind || 'other'
      if (!map.has(k)) map.set(k, [])
      map.get(k)!.push(it)
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [data])

  // facet 汇总条(统计条并入筛选条): 层级/作用计数一行 mono。
  const facetLine = useMemo(() => {
    const c = data?.counts
    if (!c) return ''
    const parts: string[] = []
    if (c.by_layer.context) parts.push(`${c.by_layer.context} 条上下文`)
    if (c.by_layer.executor) parts.push(`${c.by_layer.executor} 个执行层`)
    for (const [k, v] of Object.entries(c.by_role)) {
      if (v > 0 && ROLE_LABEL[k]) parts.push(`${v} 个${ROLE_LABEL[k]}`)
    }
    return parts.join(' · ')
  }, [data])

  const filtering = !!(q.trim() || kind || role || layer || status)

  function openItem(item: MaterialRegistryItem) {
    const ref = item.open_ref || {}
    if (ref.url) {
      window.location.href = ref.url
      return
    }
    if (ref.type && ref.id && registry.has(ref.type as EntityType)) {
      openTab({ type: ref.type as EntityType, id: ref.id }, item.title, ref.facet)
      return
    }
    navigator.clipboard?.writeText(ref.command || item.uri).catch(() => {})
  }

  // 低频操作收进 ⋯ 菜单 (复制 URI / 复制路径 / 复制打开命令), 保留各自 testid。
  function cardMenuItems(item: MaterialRegistryItem): KebabItem[] {
    const ref = item.open_ref || {}
    const items: KebabItem[] = [
      { label: '复制 URI', icon: <Link2 size={15} />, testid: 'material-copy-uri', onClick: () => { navigator.clipboard?.writeText(item.uri).catch(() => {}) } },
      { label: '复制路径', icon: <Copy size={15} />, testid: 'material-copy-path', onClick: () => { navigator.clipboard?.writeText(item.path || item.id).catch(() => {}) } },
    ]
    if (ref.command) {
      items.push({ label: '复制打开命令', icon: <Copy size={15} />, testid: 'material-copy-command', onClick: () => { navigator.clipboard?.writeText(ref.command || '').catch(() => {}) } })
    }
    return items
  }

  const PAGE = 8

  return (
    <div className="mr-page" data-testid="material-registry-panel">
      {/* 筛选工具行(页签内形态: 页面从这里开始) */}
      <div className="mr-tools">
        <div className="v2-filterbar" style={{ flex: 1, minWidth: 0 }}>
          <label className="v2-search">
            <Search size={14} aria-hidden />
            <input
              aria-label="搜索任务材料"
              placeholder="搜索标题、路径、摘要、uri"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </label>
          {/* 层级(2 项) = segmented(S3) */}
          <Segmented
            label="层级"
            small
            current={layer || 'all'}
            onChange={(v) => setLayer(v === 'all' ? '' : v)}
            items={[
              { value: 'all', label: '全部', count: data?.total },
              { value: 'context', label: '上下文', count: data?.counts.by_layer.context },
              { value: 'executor', label: '执行层', count: data?.counts.by_layer.executor },
            ]}
          />
          {/* 作用(7 项) = 单选 picker,统计条并入(S2) */}
          <FacetPicker
            label="作用"
            allLabel="全部作用"
            current={role}
            onChange={setRole}
            options={ROLE_KEYS.map((r) => ({ value: r, label: label(ROLE_LABEL, r), count: data?.counts.by_role[r] }))}
          />
          {/* 类型(21 项) = 带搜索的单选 picker(S1) */}
          <FacetPicker
            label="类型"
            allLabel="全部类型"
            searchable
            current={kind}
            onChange={setKind}
            options={kinds.map((k) => ({ value: k, label: label(KIND_LABEL, k), count: data?.counts.by_kind[k] }))}
          />
          {/* 状态 = 带色点的单选 picker(S4) */}
          <FacetPicker
            label="状态"
            allLabel="全部状态"
            current={status}
            onChange={setStatus}
            options={statuses.map((s) => ({ value: s, label: s, count: data?.counts.by_status[s], dot: statusColor(s) }))}
          />
        </div>
        <span className="mr-right">
          {facetLine && <span className="mr-facet" data-testid="material-facet-line">{facetLine}</span>}
          {/* 图签·筛选状态(无筛选整块隐藏) */}
          <span className={`bp-livetag${filtering ? ' show' : ''}`} aria-live="polite" data-testid="material-registry-livetag">
            {filtering && (
              <span className="bp-titleblock">
                <span className="tb">筛选<b>{[
                  ...(layer ? [label(LAYER_LABEL, layer)] : []),
                  ...(role ? [label(ROLE_LABEL, role)] : []),
                  ...(kind ? [label(KIND_LABEL, kind)] : []),
                  ...(status ? [status] : []),
                  ...(q.trim() ? [`“${q.trim()}”`] : []),
                ].join(' · ')}</b></span>
                <span className="tb">结果<b>{data?.returned ?? 0} 项</b></span>
              </span>
            )}
          </span>
          <span title="已显示 / 总数"><DimText>{`${data?.returned ?? 0} / ${data?.total ?? 0}`}</DimText></span>
          <button
            type="button"
            className="v2-iconbtn"
            style={{ opacity: loading ? 0.45 : 1 }}
            disabled={loading}
            aria-label={loading ? '加载中…' : '刷新任务材料'}
            data-testid="material-registry-refresh"
            onClick={load}
          ><RefreshCw size={15} /></button>
        </span>
      </div>

      <div className="mr-split">
        <div className="mr-body">
          {error && <div style={{ color: 'var(--fp-err)', fontSize: 'var(--fp-fs-3)', padding: 18 }}>{error}</div>}
          {!error && !data && <div style={{ color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-3)', padding: 24, textAlign: 'center' }}>加载中...</div>}
          {!error && data && data.items.length === 0 && (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-3)', padding: 24, textAlign: 'center' }}>无匹配材料</div>
          )}
          {!error && data && groups.map(([g, items]) => {
            const isCollapsed = !!collapsed[g]
            const expanded = !!showAll[g]
            const visible = expanded ? items : items.slice(0, PAGE)
            return (
              <div key={g} data-testid={`material-group-${g}`}>
                <button
                  type="button"
                  className="mr-ghead"
                  aria-expanded={!isCollapsed}
                  onClick={() => setCollapsed((prev) => ({ ...prev, [g]: !prev[g] }))}
                >
                  <span className="chev" aria-hidden><ChevronDown size={15} /></span>
                  <span className="gh-name">{label(KIND_LABEL, g)}</span>
                  <DimText>{items.length}</DimText>
                </button>
                <div className={`mr-grid${isCollapsed ? ' hidden' : ''}`}>
                  {visible.map((item) => {
                    const todo = todoOf(item.snippet)
                    const snippet = item.snippet && !/^todo=\d+\s*\/\s*\d+$/.test(item.snippet.trim()) ? item.snippet : ''
                    return (
                      <div
                        key={item.uri}
                        className="v2-card mr-card"
                        role="button"
                        tabIndex={0}
                        data-testid="material-registry-row"
                        title={`${item.uri} · 点击看详情`}
                        onClick={() => setSelectedId(item.id)}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedId(item.id) } }}
                      >
                        <span className="mc-top">
                          {item.status && (
                            <span className={`v2-status ${statusTone(item.status)}`}>
                              <i className="led" aria-hidden />{item.status}
                            </span>
                          )}
                          <span className="mc-t">{item.title}</span>
                          <span onClick={(e) => e.stopPropagation()} style={{ flex: 'none' }}>
                            <KebabMenu testid="material-row-more" items={cardMenuItems(item)} />
                          </span>
                        </span>
                        <span className="mr-chips">
                          <FacetChip text={label(LAYER_LABEL, item.layer)} active={layer === item.layer} onClick={() => setLayer(layer === item.layer ? '' : item.layer)} />
                          <FacetChip text={label(ROLE_LABEL, item.role)} active={role === item.role} onClick={() => setRole(role === item.role ? '' : item.role)} />
                          <FacetChip text={label(KIND_LABEL, item.kind)} active={kind === item.kind} onClick={() => setKind(kind === item.kind ? '' : item.kind)} />
                        </span>
                        {todo && todo.total > 0 && (
                          <span className="mr-todo" title={`todo ${todo.done}/${todo.total}`}>
                            <span className="track"><span className="fill" style={{ display: 'block', width: `${Math.min(100, (todo.done / todo.total) * 100)}%` }} /></span>
                            <span className="n">{todo.done}/{todo.total}</span>
                          </span>
                        )}
                        {snippet && <span className="mc-s">{snippet}</span>}
                      </div>
                    )
                  })}
                  {!isCollapsed && items.length > PAGE && !expanded && (
                    <button
                      type="button"
                      className="mr-chip mr-more"
                      onClick={() => setShowAll((prev) => ({ ...prev, [g]: true }))}
                    >
                      展开其余 {items.length - PAGE} 张 <ChevronDown size={11} aria-hidden />
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* 详情 drawer: 桌面内嵌分栏;≤900px 全屏 sheet(见 css) */}
        {selected && (
          <>
            <div className="mr-scrim" onClick={() => setSelectedId(null)} />
            <div className="mr-drawer" data-testid="material-detail-drawer">
              <div className="mr-d-head">
                <div className="mr-d-title">{selected.title}</div>
                <button type="button" className="v2-iconbtn" aria-label="关闭详情" data-testid="material-detail-close" onClick={() => setSelectedId(null)}>
                  <X size={15} />
                </button>
              </div>
              <div className="mr-d-body">
                <div className="mr-chips" style={{ marginBottom: 12 }}>
                  {selected.status && (
                    <span className={`v2-status ${statusTone(selected.status)}`}><i className="led" aria-hidden />{selected.status}</span>
                  )}
                  <FacetChip text={label(LAYER_LABEL, selected.layer)} active={layer === selected.layer} onClick={() => setLayer(selected.layer)} />
                  <FacetChip text={label(ROLE_LABEL, selected.role)} active={role === selected.role} onClick={() => setRole(selected.role)} />
                  <FacetChip text={label(KIND_LABEL, selected.kind)} active={kind === selected.kind} onClick={() => setKind(selected.kind)} />
                </div>
                <div>
                  <div className="mr-kv"><span className="k">类型</span><span className="v">{label(KIND_LABEL, selected.kind)}</span></div>
                  <div className="mr-kv"><span className="k">作用</span><span className="v">{label(ROLE_LABEL, selected.role)}</span></div>
                  <div className="mr-kv"><span className="k">层级</span><span className="v">{label(LAYER_LABEL, selected.layer)}</span></div>
                  <div className="mr-kv"><span className="k">状态</span><span className="v">{selected.status || 'unknown'}</span></div>
                  {/* URI 长串收成单行截断 + 复制图标钮(不再整段平铺) */}
                  <div className="mr-kv">
                    <span className="k">URI</span>
                    <span className="v uri">
                      <span className="u" title={selected.uri}>{selected.uri}</span>
                      <button type="button" className="v2-iconbtn" style={{ width: 24, height: 24 }} aria-label="复制 URI"
                        onClick={() => { navigator.clipboard?.writeText(selected.uri).catch(() => {}) }}>
                        <Copy size={12} />
                      </button>
                    </span>
                  </div>
                  <div className="mr-kv"><span className="k">路径</span><span className="v mono">{selected.path || selected.id}</span></div>
                  <div className="mr-kv"><span className="k">来源</span><span className="v mono">{selected.source}</span></div>
                  <div className="mr-kv">
                    <span className="k">关系</span>
                    <span className="v">
                      {selected.relations.length === 0 ? '无' : selected.relations.map((rel) => {
                        const jumpable = registry.has(rel.kind as EntityType) && !!rel.id
                        return (
                          <button
                            key={`${rel.kind}:${rel.id}:${rel.label}`}
                            type="button"
                            className={`mr-rel${jumpable ? '' : ' static'}`}
                            title={jumpable ? `打开 ${rel.kind}/${rel.id}` : rel.uri || `${rel.kind}/${rel.id}`}
                            onClick={jumpable ? () => openTab({ type: rel.kind as EntityType, id: rel.id }, `${rel.kind}/${rel.id}`) : undefined}
                          >
                            {rel.label}: {rel.kind}/{rel.id}
                          </button>
                        )
                      })}
                    </span>
                  </div>
                </div>
                <div className="mr-d-foot">
                  <button type="button" className="mr-btn-primary" data-testid="material-detail-open" onClick={() => openItem(selected)}>
                    <ExternalLink size={14} aria-hidden />{canOpen(selected) ? '打开' : '复制 URI'}
                  </button>
                  <KebabMenu testid="material-detail-more" items={cardMenuItems(selected)} triggerStyle={{ width: 36, height: 36 }} />
                </div>
                <div className="mr-d-snippet">{selected.snippet || '(no preview)'}</div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
