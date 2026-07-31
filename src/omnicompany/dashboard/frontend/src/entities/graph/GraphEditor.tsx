// 重查看器: cytoscape 关系图谱。被 graph/index.tsx 用 React.lazy 动态引入 ——
// cytoscape(~442KB)随之拆成独立 chunk, 只有真正打开「关系图谱」tab 才下载, 不再常驻首屏。
import React, { useEffect, useRef, useState } from 'react'
import cytoscape, { type Core } from 'cytoscape'
import { fetchFullGraph, type FullLinkGraph } from '../note/resolver'
import { usePanels } from '../../stores/panelsStore'
import type { GraphEntity } from './index'

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent' },
  bar: { padding: '6px 12px', borderBottom: '1px solid #222', color: '#666', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace', display: 'flex', gap: 12, alignItems: 'center' },
  badge: { color: '#90caf9' },
  cy: { flex: 1, minHeight: 0 },
  empty: { padding: 16, color: '#666', fontStyle: 'italic' },
}

const GraphEditor: React.FC<{ entity: GraphEntity }> = () => {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cyRef = useRef<Core | null>(null)
  const [graph, setGraph] = useState<FullLinkGraph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    let cancelled = false
    fetchFullGraph().then((g) => { if (!cancelled) setGraph(g) }).catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!graph || !containerRef.current) return
    if (cyRef.current) { cyRef.current.destroy(); cyRef.current = null }

    const nodeIds = new Set<string>()
    for (const [s, t] of graph.edges) { nodeIds.add(s); nodeIds.add(t) }
    Object.keys(graph.out_links).forEach((s) => nodeIds.add(s))
    Object.keys(graph.backlinks).forEach((s) => nodeIds.add(s))

    const elements: any[] = []
    nodeIds.forEach((id) => {
      const label = id.split('/').pop() || id
      elements.push({ data: { id, label, fullId: id } })
    })
    graph.edges.forEach(([s, t], i) => {
      elements.push({ data: { id: `e${i}`, source: s, target: t } })
    })

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': '#1a2a3a',
            'border-color': '#2a3a4a',
            'border-width': 1,
            'label': 'data(label)',
            'color': '#90caf9',
            'font-size': 8,
            'font-family': 'Consolas, monospace',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'text-margin-y': 4,
            'width': 12, 'height': 12,
          },
        },
        {
          selector: 'edge',
          style: {
            'line-color': '#333',
            'target-arrow-color': '#333',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'width': 1,
            'arrow-scale': 0.7,
          },
        },
        {
          selector: 'node:selected',
          style: { 'background-color': '#90caf9', 'border-color': '#90caf9', 'color': '#fff' },
        },
      ],
      layout: { name: 'cose', animate: false, idealEdgeLength: 80, nodeRepulsion: 4000 } as any,
      wheelSensitivity: 0.2,
      minZoom: 0.1, maxZoom: 3,
    })
    cy.on('tap', 'node', (evt) => {
      const id = evt.target.data('fullId')
      openTab({ type: 'note', id }, id.split('/').pop() || id)
    })
    cyRef.current = cy

    return () => { cy.destroy(); cyRef.current = null }
  }, [graph, openTab])

  if (error) return <div style={{ ...S.root, padding: 16, color: '#ef5350' }}>加载失败: {error}</div>
  if (!graph) return <div style={{ ...S.root, padding: 16, color: '#666' }}>loading 链接图...</div>

  return (
    <div style={S.root}>
      <div style={S.bar}>
        <span>关系图谱</span>
        <span>·</span>
        <span><span style={S.badge}>{graph.node_count}</span> 节点</span>
        <span><span style={S.badge}>{graph.edge_count}</span> 边</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: '#444' }}>滚轮缩放 · 拖拽 · 点节点开 note tab</span>
      </div>
      <div ref={containerRef} style={S.cy} />
      {graph.edge_count === 0 && (
        <div style={{ position: 'absolute', top: 60, right: 16, padding: 12, background: '#0d0d0d', border: '1px solid #2a3a4a', borderRadius: 4, color: '#ffb74d', fontSize: 14, maxWidth: 280 }}>
          docs/ 当前没有 [[wiki-link]] 形式的双链 (大部分笔记用普通 markdown 链接), 所以图谱基本是孤点. 把笔记里的相关引用改成 [[name]] 即会出现在图中.
        </div>
      )}
    </div>
  )
}

export default GraphEditor
