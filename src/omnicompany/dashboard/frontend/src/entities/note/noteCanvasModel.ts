import dagre from '@dagrejs/dagre'
import { groupCanvasItems, normalizeCanvasFontSize, ungroupCanvasItems } from '@material-note-core'

export type NoteCanvasNodeKind = 'note' | 'mindmap' | 'flowchart' | 'group'

export interface NoteCanvasNode {
  id: string
  kind: NoteCanvasNodeKind
  label: string
  x: number
  y: number
  width: number
  height: number
  fontSize?: number
  parentId?: string
}

export interface NoteCanvasEdge {
  id: string
  source: string
  target: string
  label?: string
  lineStyle?: NoteCanvasLineStyle
}

export type NoteCanvasLineStyle = 'curve' | 'straight'

export interface NoteCanvasDocument {
  version: 1
  nodes: NoteCanvasNode[]
  edges: NoteCanvasEdge[]
}

const CANVAS_FENCE = /(?:^|\n)```omni-canvas\s*\n([\s\S]*?)\n```(?=\n|$)/m

export function emptyNoteCanvas(): NoteCanvasDocument {
  return { version: 1, nodes: [], edges: [] }
}

export function noteCanvasFontSize(value: number): number {
  return normalizeCanvasFontSize(value, 10, 64, 14)
}

export function noteCanvasLineStyle(value: unknown): NoteCanvasLineStyle {
  return value === 'straight' ? 'straight' : 'curve'
}

export function parseNoteCanvas(markdown: string): NoteCanvasDocument {
  const match = markdown.match(CANVAS_FENCE)
  if (!match) return emptyNoteCanvas()
  try {
    const value = JSON.parse(match[1]!) as Partial<NoteCanvasDocument>
    if (!Array.isArray(value.nodes) || !Array.isArray(value.edges)) return emptyNoteCanvas()
    return {
      version: 1,
      nodes: value.nodes,
      edges: value.edges.map((edge) => ({ ...edge, lineStyle: noteCanvasLineStyle(edge.lineStyle) })),
    }
  } catch {
    return emptyNoteCanvas()
  }
}

export function writeNoteCanvas(markdown: string, canvas: NoteCanvasDocument): string {
  const block = `\`\`\`omni-canvas\n${JSON.stringify(canvas, null, 2)}\n\`\`\``
  if (CANVAS_FENCE.test(markdown)) return markdown.replace(CANVAS_FENCE, (full) => `${full.startsWith('\n') ? '\n' : ''}${block}`)
  const body = markdown.replace(/\s+$/, '')
  return `${body}${body ? '\n\n' : ''}${block}\n`
}

export function layoutNoteCanvas(canvas: NoteCanvasDocument): NoteCanvasDocument {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'TB', ranksep: 72, nodesep: 44, marginx: 32, marginy: 32 })
  graph.setDefaultEdgeLabel(() => ({}))
  const topLevel = canvas.nodes.filter((node) => !node.parentId && node.kind !== 'group')
  const ids = new Set(topLevel.map((node) => node.id))
  topLevel.forEach((node) => graph.setNode(node.id, { width: node.width, height: node.height }))
  canvas.edges.forEach((edge) => {
    if (ids.has(edge.source) && ids.has(edge.target)) graph.setEdge(edge.source, edge.target)
  })
  dagre.layout(graph)
  return {
    ...canvas,
    nodes: canvas.nodes.map((node) => {
      if (!ids.has(node.id)) return node
      const point = graph.node(node.id) as { x: number; y: number } | undefined
      return point ? { ...node, x: Math.round(point.x - node.width / 2), y: Math.round(point.y - node.height / 2) } : node
    }),
  }
}

export function groupNoteCanvasNodes(canvas: NoteCanvasDocument, selectedIds: string[], id: string): NoteCanvasDocument {
  const nodes = groupCanvasItems(canvas.nodes, selectedIds, (bounds): NoteCanvasNode => ({
    id, kind: 'group', label: '内容块', ...bounds,
  }), 42, 42, 42)
  return nodes === canvas.nodes ? canvas : { ...canvas, nodes }
}

export function ungroupNoteCanvas(canvas: NoteCanvasDocument, groupId: string): NoteCanvasDocument {
  const nodes = ungroupCanvasItems(canvas.nodes, [groupId])
  return nodes === canvas.nodes ? canvas : { ...canvas, nodes }
}
