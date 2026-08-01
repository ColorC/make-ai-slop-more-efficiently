// 决策树/探索图的纯逻辑(类型 + dagre 布局 + 因果上下游/证伪传播 BFS)。
// 抽出来便于 vitest 直测,不依赖 React/reactflow。
import dagre from '@dagrejs/dagre'

// ── 后端投影数据结构(对齐 exploration/projection.build_graph)────────────────
export interface GNode {
  id: string
  kind: string
  record_kind?: string
  label: string
  statement?: string
  status?: string
  project?: string
  deleted?: boolean
  is_gap?: boolean
  is_root?: boolean
  has_excerpt?: boolean
  session_ref?: string
  verification_status?: string
  anchor?: { kind?: string; ref?: string; excerpt?: string }
  decision_space?: { option: string; chosen?: boolean; why?: string }[]
  version?: number
  version_family?: string
  source_file?: string
  material_id?: string
}
export interface GEdge {
  source: string
  target: string
  rel: string
  rationale?: string
  rationale_verified?: boolean | null
  note?: string
}
export interface Graph {
  nodes: GNode[]
  edges: GEdge[]
  roots: string[]
  version_chains: string[][]
  kinds: string[]
  stats: Record<string, unknown>
}
export interface DecisionRecord {
  id: string
  kind: string
  statement?: string
  status?: string
  rationale?: string
  project?: string
  anchor?: { kind?: string; ref?: string; excerpt?: string }
  origin?: { channel?: string; session_ref?: string }
  decision_space?: { option: string; chosen?: boolean; why?: string }[]
  links?: Record<string, unknown>
  verification_status?: string
}

export const NODE_W = 248
export const NODE_H = 78
export const CAUSAL_RELS = new Set(['critiques', 'refines', 'responds_to_critique'])

function push(m: Map<string, string[]>, k: string, v: string): void {
  const a = m.get(k)
  if (a) a.push(v)
  else m.set(k, [v])
}

/** dagre 分层布局(LR);返回每节点左上角坐标。版本链由 supersedes 边在 LR 下横向排开。 */
export function layout(nodes: GNode[], edges: GEdge[]): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 52, ranksep: 190, edgesep: 26, marginx: 36, marginy: 36 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H })
  for (const e of edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source, e.target)
  }
  dagre.layout(g)
  const pos = new Map<string, { x: number; y: number }>()
  for (const n of nodes) {
    const dn = g.node(n.id)
    if (dn) pos.set(n.id, { x: dn.x - NODE_W / 2, y: dn.y - NODE_H / 2 })
  }
  return pos
}

/** 选中节点的因果上下游(沿所有边双向 BFS)。 */
export function relatives(id: string, edges: GEdge[]): Set<string> {
  const fwd = new Map<string, string[]>()
  const bwd = new Map<string, string[]>()
  for (const e of edges) {
    push(fwd, e.source, e.target)
    push(bwd, e.target, e.source)
  }
  const out = new Set<string>([id])
  const walk = (m: Map<string, string[]>) => {
    const stack = [id]
    while (stack.length) {
      const cur = stack.pop()!
      for (const nx of m.get(cur) ?? []) if (!out.has(nx)) { out.add(nx); stack.push(nx) }
    }
  }
  walk(fwd)
  walk(bwd)
  return out
}

/** 证伪传播:从选中信念沿 rests_on 正向(信念→依赖它的决策)可达集。 */
export function falsifyImpact(id: string, edges: GEdge[]): Set<string> {
  const fwd = new Map<string, string[]>()
  for (const e of edges) if (e.rel === 'rests_on') push(fwd, e.source, e.target)
  const out = new Set<string>([id])
  const stack = [id]
  while (stack.length) {
    const cur = stack.pop()!
    for (const nx of fwd.get(cur) ?? []) if (!out.has(nx)) { out.add(nx); stack.push(nx) }
  }
  return out
}
