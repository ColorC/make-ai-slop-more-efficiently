/**
 * entities/review-canvas/DecisionLens — 图壳的「浏览姿态」(决策库透镜)。
 *
 * 决策本体阶段四(plan=[2026-07-10]DECISION-ONTOLOGY §七): 一套图壳两种姿态——
 * 裁决姿态=材料轨迹(ReviewCanvas 本体, 可操作), 浏览姿态=本组件(决策库只读下钻)。
 * 手册浏览面的"一张图"不新开第三个组件, 是同一图壳(LaneBands/FlowLayer/CardNode/layout)
 * 换数据源(/api/v2/material-graph)+三个透镜:
 *   互引   = related/rests_on/parent 引用关系(supersedes 之外的一切互指);
 *   判例   = 蒸馏态手册条目 + 其 links 指到的判例记录;
 *   全链路 = 全部边(含 supersedes 版本链), 来龙去脉一次看全。
 * 默认隐藏零边孤点(库内 97% 记录无链, 全画=散落列表, DEC-2026-07-04-240 已裁死该外观)。
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import type { CanvasLink, CanvasMaterial, CanvasResponse } from '../../api/reviewstageClient'
import { computeLayout, type CanvasLayout } from './layout'
import { CardNode, FlowLayer, LaneBands, STATUS_LABEL, statusColor } from './ReviewCanvas'

type LensKey = 'refs' | 'cases' | 'chain'
const LENSES: { key: LensKey; label: string; hint: string }[] = [
  { key: 'refs', label: '互引', hint: '记录间引用(rests_on/related/parent)' },
  { key: 'cases', label: '判例', hint: '手册蒸馏态条目与其判例' },
  { key: 'chain', label: '全链路', hint: '全部边含版本取代链' },
]

interface GraphNode {
  id: string
  kind?: string
  record_kind?: string
  label?: string
  statement?: string
  status?: string
  project?: string
  deleted?: boolean
  distilled?: boolean
  book_ref?: string
  decision_space?: { option: string; chosen?: boolean; why?: string; ruling?: string }[]
  anchor?: { kind?: string; ref?: string; excerpt?: string }
}
interface GraphEdge { source: string; target: string; rel: string; rationale?: string }
interface GraphResponse { nodes: GraphNode[]; edges: GraphEdge[] }

// 图 rel → 画布三视觉类(实线=supersedes 版本链; 其余虚线两档)。真 rel 留详情栏展示。
function visualRel(rel: string): CanvasLink['rel'] {
  if (rel === 'supersedes') return 'supersedes'
  if (rel === 'rests_on' || rel === 'parent') return 'parent'
  return 'related'
}

// rel → 六词正典动词(与 domains/decisions/verbs.REL_TO_VERB 同表; 机器真源在后端)。
const REL_VERB: Record<string, string> = {
  rests_on: '推导', parent: '拆分', related: '联想', supersedes: '延伸', enforced_by: '生成',
  refines: '生成', critiques: '反证', responds_to_critique: '延伸', rejected: '反证', challenge: '反证',
}

const KIND_LANE: Record<string, string> = {
  决策: '决策', 信念: '信念', 指正: '指正',
}

function toCanvas(nodes: GraphNode[], edges: GraphEdge[]): CanvasResponse {
  // 泳道 = 手册(蒸馏态)/决策/信念/指正; 列序用 id 内嵌日期(created_at 代理)。
  const byLane = new Map<string, GraphNode[]>()
  for (const n of nodes) {
    const lane = n.distilled ? '手册' : (KIND_LANE[n.kind || ''] || n.kind || '其他')
    if (!byLane.has(lane)) byLane.set(lane, [])
    byLane.get(lane)!.push(n)
  }
  const laneOrder = ['手册', '决策', '信念', '指正']
  const tracks = Array.from(byLane.entries())
    .sort((a, b) => {
      const ia = laneOrder.indexOf(a[0]); const ib = laneOrder.indexOf(b[0])
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
    .map(([track, ns]) => ({
      track,
      families: ns.map((n) => ({
        family: n.id,
        materials: [{
          id: n.id,
          kind: 'markdown',
          tier: 'processual',
          title: n.label || n.statement || n.id,
          status: 'pending',
          created_at: n.id.replace(/^(?:DEC|BLF|CMT)-/, ''),
          updated_at: '',
          annotations: [], comments: [], annotations_allowed: false,
          history: [], pushed_to_user: false, archived: false,
          file_relpath: null, inline_content: null,
          source_subagent_id: null, source_plan_id: null,
          pushed_reason: null, pushed_at: null,
          extra: { record_status: n.status, distilled: !!n.distilled },
          project: n.project || '',
          track,
          version: null,
          version_family: n.id,
          links: {},
        } as unknown as CanvasMaterial],
      })),
    }))
  const links: CanvasLink[] = edges.map((e) => ({
    source: e.source, target: e.target, rel: visualRel(e.rel),
  }))
  const total = nodes.length
  return {
    tracks,
    links,
    unassigned: [],
    stats: { total, tracks: tracks.length, unassigned: 0, links: links.length },
  }
}

export default function DecisionLens({ project }: { project: string }) {
  const [graph, setGraph] = useState<GraphResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [lens, setLens] = useState<LensKey>('refs')
  const [selId, setSelId] = useState<string | null>(null)
  const [selEdge, setSelEdge] = useState<GraphEdge | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    fetch(`/api/v2/material-graph?project=${encodeURIComponent(project)}&include_deleted=false`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { if (!cancelled) setGraph({ nodes: d.nodes || [], edges: d.edges || [] }) })
      .catch((e) => { if (!cancelled) { setGraph(null); setError(String(e?.message || e)) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [project])

  const { canvas, hiddenIsolates, shownEdges } = useMemo(() => {
    if (!graph) return { canvas: null as CanvasResponse | null, hiddenIsolates: 0, shownEdges: [] as GraphEdge[] }
    const recordIds = new Set(graph.nodes.filter((n) => !n.deleted).map((n) => n.id))
    // 透镜筛边(只保留两端都是记录节点的边; 否决伪节点/执法器节点不进浏览姿态)
    let edges = graph.edges.filter((e) => recordIds.has(e.source) && recordIds.has(e.target))
    if (lens === 'refs') edges = edges.filter((e) => e.rel !== 'supersedes' && e.rel !== 'rejected')
    const nodeById = new Map(graph.nodes.map((n) => [n.id, n]))
    if (lens === 'cases') {
      const distilledIds = new Set(graph.nodes.filter((n) => n.distilled).map((n) => n.id))
      edges = edges.filter((e) => distilledIds.has(e.source) || distilledIds.has(e.target))
    }
    const connected = new Set<string>()
    for (const e of edges) { connected.add(e.source); connected.add(e.target) }
    // 判例透镜: 蒸馏态条目即使孤立也显示(手册面即它们的家)
    if (lens === 'cases') {
      for (const n of graph.nodes) if (n.distilled) connected.add(n.id)
    }
    const nodes = graph.nodes.filter((n) => !n.deleted && connected.has(n.id))
    const hidden = graph.nodes.filter((n) => !n.deleted).length - nodes.length
    return { canvas: toCanvas(nodes, edges), hiddenIsolates: hidden, shownEdges: edges }
  }, [graph, lens])

  const layout: CanvasLayout = useMemo(() => computeLayout(canvas), [canvas])
  const selNode = useMemo(
    () => (selId && graph ? graph.nodes.find((n) => n.id === selId) || null : null),
    [selId, graph],
  )
  const trueRel = useCallback((l: CanvasLink): GraphEdge | null => {
    return shownEdges.find((e) => e.source === l.source && e.target === l.target) || null
  }, [shownEdges])

  const onCardClick = useCallback((id: string) => { setSelEdge(null); setSelId((c) => (c === id ? null : id)) }, [])
  const onEdgeClick = useCallback((l: CanvasLink) => { setSelId(null); setSelEdge(trueRel(l)) }, [trueRel])
  const edgeSelected = useCallback(
    (l: CanvasLink) => selEdge != null && selEdge.source === l.source && selEdge.target === l.target,
    [selEdge],
  )

  const empty = !loading && (!canvas || layout.cards.length === 0)

  return (
    <div style={LS.root} data-testid="decision-lens">
      <div style={LS.main}>
        <div style={LS.toolbar}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fp-text-2)' }}>决策浏览</span>
          {LENSES.map((l) => (
            <button
              key={l.key} type="button" title={l.hint}
              style={LS.lensBtn(lens === l.key)}
              data-testid={`lens-${l.key}`}
              onClick={() => { setLens(l.key); setSelId(null); setSelEdge(null) }}
            >{l.label}</button>
          ))}
          {loading && <span style={LS.dim}>载入中…</span>}
          {canvas && (
            <span style={LS.dim}>
              {canvas.stats.total} 记录 · {canvas.stats.links} 边
              {hiddenIsolates > 0 && ` · 孤点 ${hiddenIsolates} 已隐(接树: omni decisions link)`}
            </span>
          )}
        </div>
        <div style={LS.canvasWrap}>
          {error ? (
            <div style={LS.emptyBox}>载入决策库投影出错: {error}</div>
          ) : empty ? (
            <div style={LS.emptyBox} data-testid="lens-empty">
              该项目({project})在当前透镜下没有成链的记录。
              <div style={{ marginTop: 6, color: 'var(--fp-text-3)' }}>
                记录之间连上边(rests_on/related/supersedes)后会在这里成图; 孤点不画(散落列表无意义)。
              </div>
            </div>
          ) : (
            <div style={LS.scroll} onClick={() => { setSelId(null); setSelEdge(null) }}>
              <div style={{ position: 'relative', width: layout.width, height: layout.height, minWidth: '100%' }}>
                <LaneBands layout={layout} />
                <FlowLayer layout={layout} onEdgeClick={onEdgeClick} edgeSelected={edgeSelected} />
                {layout.cards.map((c) => (
                  <CardNode
                    key={c.mat.id}
                    x={c.x} y={c.y} color={c.color}
                    mat={c.mat}
                    selected={selId === c.mat.id}
                    commentCount={0}
                    onClick={onCardClick}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      {selNode && <RecordDetail node={selNode} />}
      {selEdge && !selNode && (
        <div style={LS.side}>
          <div style={LS.sideTitle}>边 · {REL_VERB[selEdge.rel] || '?'}({selEdge.rel})</div>
          <div style={LS.kv}><span style={LS.k}>从</span><span style={LS.v}>{selEdge.source}</span></div>
          <div style={LS.kv}><span style={LS.k}>到</span><span style={LS.v}>{selEdge.target}</span></div>
          {selEdge.rationale && <div style={{ ...LS.v, marginTop: 8 }}>理由: {selEdge.rationale}</div>}
          <div style={{ ...LS.dim, marginTop: 10 }}>动词正典=六词表(拆分/推导/联想/生成/反证/延伸)</div>
        </div>
      )}
    </div>
  )
}

function RecordDetail({ node }: { node: GraphNode }) {
  const rejected = (node.decision_space || []).filter((o) => o.chosen === false)
  return (
    <div style={LS.side} data-testid="record-detail">
      <div style={LS.sideTitle}>
        {node.distilled ? '手册条目 ' : ''}{node.id}
        <span style={{ marginLeft: 8, color: statusColor(node.status), fontSize: 12 }}>
          ● {STATUS_LABEL[node.status || ''] || node.status}
        </span>
      </div>
      <div style={{ fontSize: 13, color: 'var(--fp-text-1)', lineHeight: 1.6 }}>{node.statement || node.label}</div>
      {node.book_ref && (
        <div style={LS.kv}><span style={LS.k}>手册</span><span style={LS.v}>{node.book_ref}</span></div>
      )}
      {node.anchor?.ref && (
        <div style={LS.kv}><span style={LS.k}>锚点</span><span style={LS.v}>{node.anchor.ref}</span></div>
      )}
      {node.anchor?.excerpt && (
        <div style={{ ...LS.v, marginTop: 6, fontStyle: 'italic' }}>「{node.anchor.excerpt.slice(0, 160)}」</div>
      )}
      {rejected.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ ...LS.k, marginBottom: 4 }}>被否项(结论与反方同屏)</div>
          {rejected.map((o, i) => (
            <div key={i} style={LS.rejected}>
              ✗ {o.option}
              {o.ruling && <span style={{ marginLeft: 6, color: 'var(--fp-warn)' }}>[{o.ruling}]</span>}
              {o.why && <div style={{ color: 'var(--fp-text-3)', marginTop: 2 }}>{o.why}</div>}
            </div>
          ))}
        </div>
      )}
      <div style={{ ...LS.dim, marginTop: 12 }}>
        改它? 走候选流水线: omni decisions candidate --action revise --target {node.id}
      </div>
    </div>
  )
}

const LS: Record<string, any> = {
  root: { display: 'flex', height: '100%', minHeight: 0, background: 'var(--fp-solid)' },
  main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' },
  toolbar: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px',
    borderBottom: '1px solid var(--fp-border-subtle)',
  },
  lensBtn: (active: boolean): CSSProperties => ({
    padding: '2px 10px', borderRadius: 5, fontSize: 12, cursor: 'pointer',
    background: active ? 'color-mix(in srgb, var(--fp-accent) 18%, transparent)' : 'transparent',
    color: active ? 'var(--fp-accent)' : 'var(--fp-text-3)',
    border: `1px solid ${active ? 'var(--fp-accent)' : 'var(--fp-border-subtle)'}`,
  }),
  dim: { fontSize: 12, color: 'var(--fp-text-3)' },
  canvasWrap: { flex: 1, minHeight: 0, position: 'relative' },
  scroll: { position: 'absolute', inset: 0, overflow: 'auto' },
  emptyBox: {
    margin: 18, padding: 16, border: '1px dashed var(--fp-border-subtle)', borderRadius: 8,
    color: 'var(--fp-text-2)', fontSize: 13, lineHeight: 1.7,
  },
  side: {
    width: 300, flexShrink: 0, borderLeft: '1px solid var(--fp-border-subtle)',
    padding: 12, overflow: 'auto', fontSize: 13,
  },
  sideTitle: { fontWeight: 600, fontSize: 13, color: 'var(--fp-text-1)', marginBottom: 8 },
  kv: { display: 'flex', gap: 6, marginTop: 6 },
  k: { color: 'var(--fp-text-3)', fontSize: 12, flexShrink: 0 },
  v: { color: 'var(--fp-text-2)', fontSize: 12, wordBreak: 'break-all' },
  rejected: {
    padding: '4px 8px', marginTop: 4, borderLeft: '2px solid var(--fp-err)',
    background: 'color-mix(in srgb, var(--fp-err) 6%, transparent)', fontSize: 12,
    color: 'var(--fp-text-2)', borderRadius: '0 4px 4px 0',
  },
}
