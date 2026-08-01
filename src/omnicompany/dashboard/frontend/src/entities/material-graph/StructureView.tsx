import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import ReactFlow, { Background, Controls, Handle, Position, MarkerType } from 'reactflow'
import 'reactflow/dist/style.css'
import { layout, relatives, falsifyImpact, NODE_W, type Graph, type GNode } from './graph-logic'

// 结构视图:确定性裸 DAG(真本体投影,不叙事化)。决策树 tab 默认视图——先看客观结构,叙事泳道降为子切换。
// 节点=决策/信念/指正/否决/执法器/产物/设施/真源;边=真实关系(rests_on/supersedes/parent/related/
// rejected/enforced_by/refines/critiques/responds_to_critique)。因果边(refines/critiques/
// responds_to_critique)可能带 rationale_verified=false——未核的因果关系必须视觉区分,不能冒充已核实。

// 节点语义色:kind → 色 token。决策=violet、信念=violet 偏白、指正=err、否决=warn(降透明度+虚线边框)、
// 执法器=accent-2、产物=ok、其余(设施/真源等)= text-3 弱色。全走 token,不写裸 hex。
const KIND_COLOR: Record<string, string> = {
  决策: 'var(--fp-violet)',
  信念: 'color-mix(in srgb, var(--fp-violet) 70%, white)',
  指正: 'var(--fp-err)',
  否决: 'var(--fp-warn)',
  执法器: 'var(--fp-accent-2)',
  产物: 'var(--fp-ok)',
}
const kindColor = (kind: string) => KIND_COLOR[kind] ?? 'var(--fp-text-3)'

const REL_LABEL: Record<string, string> = {
  rests_on: '立足', supersedes: '下一版', parent: '承袭', related: '相关', rejected: '否决',
  enforced_by: '编译进', refines: '精化', critiques: '批评', responds_to_critique: '回应批评',
}
const EDGE_ENFORCED = 'var(--fp-accent-2)'
const EDGE_REJECTED = 'var(--fp-warn)'
const EDGE_CAUSAL = 'var(--fp-accent)'
const EDGE_DEFAULT = 'var(--fp-border-strong)'

function edgeColor(rel: string): string {
  if (rel === 'enforced_by') return EDGE_ENFORCED
  if (rel === 'rejected') return EDGE_REJECTED
  if (rel === 'refines' || rel === 'critiques' || rel === 'responds_to_critique') return EDGE_CAUSAL
  return EDGE_DEFAULT
}

function Badge({ c, children }: { c: string; children: ReactNode }) {
  return <span style={{ fontSize: 9, color: c, border: `1px solid ${c}`, borderRadius: 8, padding: '0 4px', opacity: 0.92 }}>{children}</span>
}

function StructureNode({ data, selected }: { data: { node: GNode; dim: boolean; falsified: boolean }; selected: boolean }) {
  const n = data.node
  const c = kindColor(n.kind)
  const isRejected = n.kind === '否决'
  const falsifyColor = 'var(--fp-err)'
  return (
    <div
      data-testid={`structure-node-${n.id}`}
      style={{
        width: NODE_W, boxSizing: 'border-box', background: 'var(--fp-solid)', borderRadius: 7, padding: '7px 10px 9px',
        border: `1px ${isRejected ? 'dashed' : 'solid'} ${data.falsified ? falsifyColor : (selected ? c : 'var(--fp-border)')}`,
        borderLeft: `4px solid ${c}`,
        boxShadow: data.falsified ? `0 0 0 1.5px ${falsifyColor}` : (selected ? `0 0 0 1.5px ${c}` : undefined),
        opacity: data.dim ? 0.25 : (isRejected ? 0.75 : (n.deleted ? 0.6 : 1)),
        backfaceVisibility: 'hidden', transform: 'translateZ(0)', WebkitFontSmoothing: 'antialiased',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 5, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: c }}>{n.kind}</span>
        {n.is_root && <Badge c="var(--fp-text-3)">散落根</Badge>}
        {n.deleted && <Badge c="var(--fp-text-3)">墓碑</Badge>}
        {n.is_gap && <Badge c="var(--fp-text-3)">缺口</Badge>}
        <span style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--fp-text-3)' }}>{n.id.length > 10 ? `${n.id.slice(0, 10)}…` : n.id}</span>
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.42, color: 'var(--fp-text)' }}>{n.label}</div>
    </div>
  )
}

const nodeTypes = { structure: StructureNode }

export default function StructureView({ project }: { project: string }) {
  const [statusFilter, setStatusFilter] = useState<'adopted' | 'all'>('adopted')
  const [data, setData] = useState<Graph | null>(null)
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const [falsifyOn, setFalsifyOn] = useState(false)

  useEffect(() => {
    setLoading(true); setSel(null)
    const q = `?project=${encodeURIComponent(project)}&include_deleted=false${statusFilter === 'adopted' ? '&status=adopted' : ''}`
    fetch(`/api/v2/material-graph${q}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [project, statusFilter])

  const nodesById = useMemo(() => {
    const m = new Map<string, GNode>()
    for (const n of data?.nodes ?? []) m.set(n.id, n)
    return m
  }, [data])

  const relSet = useMemo(() => (sel ? relatives(sel, data?.edges ?? []) : null), [sel, data])
  const falsifySet = useMemo(
    () => (falsifyOn && sel ? falsifyImpact(sel, data?.edges ?? []) : null),
    [falsifyOn, sel, data],
  )

  const { nodes, edges } = useMemo(() => {
    const gnodes = data?.nodes ?? []
    const gedges = data?.edges ?? []
    if (!gnodes.length) return { nodes: [], edges: [] }
    const pos = layout(gnodes, gedges)
    const rNodes = gnodes.map((n) => ({
      id: n.id, type: 'structure', selected: sel === n.id,
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { node: n, dim: relSet ? !relSet.has(n.id) : false, falsified: falsifySet ? falsifySet.has(n.id) : false },
    }))
    const rEdges = gedges.map((e, i) => {
      const col = edgeColor(e.rel)
      const unverified = e.rationale_verified === false
      const label = (REL_LABEL[e.rel] ?? e.rel) + (unverified ? '·未核' : '')
      const dashed = e.rel === 'rejected' || unverified
      return {
        id: `e${i}`, source: e.source, target: e.target, type: 'smoothstep',
        label,
        labelStyle: { fill: 'var(--fp-text-2)', fontSize: 9.5, fontWeight: 600 },
        labelBgStyle: { fill: 'var(--fp-solid)', fillOpacity: 0.94 },
        labelBgPadding: [4, 2] as [number, number], labelBgBorderRadius: 3,
        style: { stroke: col, strokeWidth: 1.5, strokeDasharray: dashed ? '5 4' : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color: col, width: 13, height: 13 },
      }
    })
    return { nodes: rNodes, edges: rEdges }
  }, [data, sel, relSet, falsifySet])

  const selNode = sel == null ? null : nodesById.get(sel) ?? null
  const empty = !loading && (!data || (data.nodes ?? []).length === 0)

  // 编译进执法器:选中节点沿 enforced_by 出边指向的执法器节点。
  const enforcedTargets = useMemo(() => {
    if (!selNode) return []
    return (data?.edges ?? [])
      .filter((e) => e.rel === 'enforced_by' && e.source === selNode.id)
      .map((e) => nodesById.get(e.target))
      .filter((n): n is GNode => !!n)
  }, [selNode, data, nodesById])
  // 执法器节点选中时:反向列出指向它的裁决。
  const enforcedBySources = useMemo(() => {
    if (!selNode || selNode.kind !== '执法器') return []
    return (data?.edges ?? [])
      .filter((e) => e.rel === 'enforced_by' && e.target === selNode.id)
      .map((e) => nodesById.get(e.source))
      .filter((n): n is GNode => !!n)
  }, [selNode, data, nodesById])
  const rejectedOptions = useMemo(
    () => (selNode?.decision_space ?? []).filter((o) => o.chosen === false),
    [selNode],
  )

  return (
    <div style={S.root} data-testid="structure-view">
      <div style={S.main}>
        <div style={S.toolbar}>
          <div style={S.seg}>
            <Seg active={statusFilter === 'adopted'} onClick={() => setStatusFilter('adopted')} testid="structure-filter-adopted">已拍板</Seg>
            <Seg active={statusFilter === 'all'} onClick={() => setStatusFilter('all')} testid="structure-filter-all">全部</Seg>
          </div>
          {loading && <span style={S.loading}>载入中…</span>}
          <span style={S.count}>{(data?.nodes ?? []).length} 节点 · {(data?.edges ?? []).length} 边</span>
        </div>

        <div style={S.canvasWrap}>
          {empty ? (
            <div style={S.emptyBox} data-testid="structure-empty">
              {statusFilter === 'adopted' ? '该项目还没有已拍板的决策记录。' : '该项目还没有决策记录。'}
              {statusFilter === 'adopted' && <div style={{ marginTop: 6 }}>试试切到全部。</div>}
            </div>
          ) : (
            <ReactFlow
              nodes={nodes} edges={edges} nodeTypes={nodeTypes}
              onNodeClick={(_, n) => setSel((c) => (c === n.id ? null : n.id))}
              onPaneClick={() => setSel(null)}
              fitView minZoom={0.15} maxZoom={1.6}
              nodesDraggable={false} nodesConnectable={false} elementsSelectable
              proOptions={{ hideAttribution: true }}
            >
              <Background color="var(--fp-border)" gap={22} />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </div>
      </div>

      {selNode && (
        <div style={S.sidebar} data-testid="structure-detail">
          <div style={S.metaLine}>
            {selNode.kind} · {selNode.status || '(无状态)'} · {selNode.project || '(未归位)'}
          </div>
          <div style={S.sidebarTitle}>{selNode.label}</div>
          {selNode.statement && (
            <Section title="陈述">
              <div style={{ color: 'var(--fp-text-2)', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{selNode.statement}</div>
            </Section>
          )}
          {selNode.anchor?.excerpt && (
            <Section title="原话引文">
              <div style={{ fontStyle: 'italic', color: 'var(--fp-text-3)', whiteSpace: 'pre-wrap' }}>{selNode.anchor.excerpt}</div>
            </Section>
          )}
          {selNode.anchor?.ref && (
            <Section title="规则文档">
              <div style={{ fontFamily: 'var(--fp-font-mono)', color: 'var(--fp-text-2)', wordBreak: 'break-all' }}>{selNode.anchor.ref}</div>
            </Section>
          )}
          {!!rejectedOptions.length && (
            <Section title={`被否决项(${rejectedOptions.length})`}>
              <ul style={{ margin: 0, paddingLeft: 16, color: 'var(--fp-warn)' }}>
                {rejectedOptions.map((o, i) => (
                  <li key={i} style={{ marginBottom: 3, lineHeight: 1.4 }}>
                    {o.option}{o.why ? ` —— ${o.why}` : ''}
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {!!enforcedTargets.length && (
            <Section title="编译进执法器">
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {enforcedTargets.map((t) => (
                  <li key={t.id} style={{ marginBottom: 3 }}>
                    <button type="button" style={S.linkBtn} onClick={() => setSel(t.id)}>{t.label}</button>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {!!enforcedBySources.length && (
            <Section title="被此执法器约束的裁决">
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {enforcedBySources.map((s) => (
                  <li key={s.id} style={{ marginBottom: 3 }}>
                    <button type="button" style={S.linkBtn} onClick={() => setSel(s.id)}>{s.label}</button>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {selNode.kind === '信念' && (
            <Section title="核验状态">
              <div style={{ color: 'var(--fp-text-2)', marginBottom: 6 }}>{selNode.verification_status || '(未核验)'}</div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--fp-text-3)', cursor: 'pointer' }}>
                <input type="checkbox" checked={falsifyOn} onChange={(e) => setFalsifyOn(e.target.checked)} />
                证伪波及
              </label>
            </Section>
          )}
        </div>
      )}
    </div>
  )
}

function Seg({ active, onClick, children, testid }: { active: boolean; onClick: () => void; children: ReactNode; testid?: string }) {
  const s: CSSProperties = {
    padding: '4px 12px', fontSize: 13, cursor: 'pointer', border: 'none', borderRadius: 6, fontFamily: 'inherit',
    background: active ? 'var(--fp-surface)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-2)',
    fontWeight: active ? 600 : 500,
    boxShadow: active ? 'var(--fp-shadow-sm)' : 'none',
    transition: 'color 150ms var(--fp-ease), background 150ms var(--fp-ease)',
  }
  return <button type="button" style={s} onClick={onClick} data-testid={testid}>{children}</button>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--fp-text-3)', marginBottom: 4 }}>{title}</div>
      {children}
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  root: { display: 'flex', height: '100%', width: '100%', fontFamily: 'var(--fp-font-sans)', background: 'transparent', color: 'var(--fp-text)' },
  main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', padding: 10, gap: 10, boxSizing: 'border-box' },
  toolbar: {
    display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', borderRadius: 11,
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  seg: { display: 'inline-flex', gap: 2, padding: 2, borderRadius: 8, background: 'var(--fp-glass-2)', border: '1px solid var(--fp-border)' },
  loading: { color: 'var(--fp-text-3)', fontSize: 12 },
  count: { marginLeft: 'auto', color: 'var(--fp-text-3)', fontSize: 12 },
  canvasWrap: {
    flex: 1, minHeight: 0, position: 'relative', borderRadius: 11, overflow: 'hidden',
    background: 'var(--fp-solid)', border: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.05)',
  },
  emptyBox: { padding: 28, color: 'var(--fp-text-3)', fontSize: 13, lineHeight: 1.7 },
  sidebar: {
    width: 360, flexShrink: 0, overflow: 'auto', padding: 16, fontSize: 13, margin: '10px 10px 10px 0', borderRadius: 11,
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', boxShadow: 'var(--fp-shadow-sm), inset 0 1px 0 rgba(255,255,255,.08)',
    color: 'var(--fp-text-2)',
  },
  metaLine: { fontSize: 12, color: 'var(--fp-text-3)' },
  sidebarTitle: { fontWeight: 600, margin: '4px 0 10px', color: 'var(--fp-text)', fontSize: 15, lineHeight: 1.45 },
  linkBtn: {
    background: 'none', border: 'none', padding: 0, color: 'var(--fp-accent)', cursor: 'pointer',
    font: 'inherit', textDecoration: 'underline', textAlign: 'left',
  },
}
