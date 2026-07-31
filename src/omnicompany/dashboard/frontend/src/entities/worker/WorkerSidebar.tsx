import React, { useEffect, useMemo, useState } from 'react'
import { workerResolver, type WorkerEntity } from './resolver'
import type { SidebarViewProps } from '../registry'

interface TreeNode {
  name: string
  path: string
  children: Map<string, TreeNode>
  workers: WorkerEntity[]
}

function buildTree(workers: WorkerEntity[]): TreeNode {
  const root: TreeNode = { name: '', path: '', children: new Map(), workers: [] }
  for (const w of workers) {
    const parts = w.id.split('/')
    const dirParts = parts.slice(0, -2)
    let cur = root
    for (const p of dirParts) {
      let next = cur.children.get(p)
      if (!next) {
        next = { name: p, path: cur.path ? cur.path + '/' + p : p, children: new Map(), workers: [] }
        cur.children.set(p, next)
      }
      cur = next
    }
    cur.workers.push(w)
  }
  return root
}

function filterTree(node: TreeNode, q: string): TreeNode | null {
  const matchedWorkers = node.workers.filter((w) =>
    w.id.toLowerCase().includes(q) || w.title.toLowerCase().includes(q),
  )
  const filteredChildren = new Map<string, TreeNode>()
  for (const [k, v] of node.children) {
    const fc = filterTree(v, q)
    if (fc) filteredChildren.set(k, fc)
  }
  if (matchedWorkers.length === 0 && filteredChildren.size === 0 && node.path) return null
  return { ...node, workers: matchedWorkers, children: filteredChildren }
}

const S: Record<string, any> = {
  treeNode: { fontSize: 14, fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace" },
  // 目录行: 次级文字, 顶层一档加粗; hover 浮极淡底(克制反馈)。
  dir: (depth: number, _expanded: boolean): React.CSSProperties => ({
    padding: '3px 6px', paddingLeft: 4 + depth * 12, cursor: 'pointer', borderRadius: 6,
    color: 'var(--fp-text-2)', userSelect: 'none' as const,
    fontWeight: depth === 0 ? 600 : 'normal' as const,
    transition: 'background 120ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  arr: { color: 'var(--fp-text-3)', display: 'inline-block', width: 12 },
  count: { color: 'var(--fp-text-3)', marginLeft: 6, fontSize: 12 },
  // worker 叶子: 选中浮玻璃淡底 + 强调字(无缝, 无描边); 未选弱字, hover 极淡底。
  worker: (depth: number, active: boolean): React.CSSProperties => ({
    padding: '3px 6px', paddingLeft: 4 + depth * 12 + 12, cursor: 'pointer', borderRadius: 6,
    color: active ? 'var(--fp-link)' : 'var(--fp-text)',
    background: active ? 'var(--fp-accent-weak)' : 'transparent',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    transition: 'background 120ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  empty: { padding: 8, color: 'var(--fp-text-3)', fontSize: 13 },
}

const HOVER_BG = 'rgba(255,255,255,.05)'

interface NodeRowProps {
  node: TreeNode
  depth: number
  expanded: Set<string>
  toggle: (path: string) => void
  activeId: string | null
  onOpen: SidebarViewProps['openTab']
  initiallyOpen: boolean
  forceExpandAll?: boolean
}

function NodeRow({ node, depth, expanded, toggle, activeId, onOpen, initiallyOpen, forceExpandAll }: NodeRowProps) {
  const isExpanded = initiallyOpen || forceExpandAll || expanded.has(node.path)
  const total = countWorkers(node)
  return (
    <div>
      <div
        style={S.dir(depth, isExpanded)}
        onClick={() => toggle(node.path)}
        title={node.path}
        onMouseEnter={(e) => { e.currentTarget.style.background = HOVER_BG }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
      >
        <span style={S.arr}>{isExpanded ? '▾' : '▸'}</span>
        {node.name}
        <span style={S.count}>{total}</span>
      </div>
      {isExpanded && (
        <div>
          {[...node.children.values()].map((c) => (
            <NodeRow
              key={c.path}
              node={c}
              depth={depth + 1}
              expanded={expanded}
              toggle={toggle}
              activeId={activeId}
              onOpen={onOpen}
              initiallyOpen={false}
              forceExpandAll={forceExpandAll}
            />
          ))}
          {node.workers.map((w) => {
            const tabId = `worker:${w.id}`
            return (
              <div
                key={w.id}
                style={S.worker(depth, activeId === tabId)}
                title={w.id}
                onClick={() => onOpen({ type: 'worker', id: w.id }, w.title)}
                onMouseEnter={(e) => { if (activeId !== tabId) e.currentTarget.style.background = HOVER_BG }}
                onMouseLeave={(e) => { if (activeId !== tabId) e.currentTarget.style.background = 'transparent' }}
              >
                {w.title}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function countWorkers(node: TreeNode): number {
  let n = node.workers.length
  for (const c of node.children.values()) n += countWorkers(c)
  return n
}

export default function WorkerSidebar({ filter, activeId, openTab }: SidebarViewProps) {
  const [list, setList] = useState<WorkerEntity[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [autoExpandKey, setAutoExpandKey] = useState(0)

  useEffect(() => {
    setLoading(true)
    workerResolver.list().then((d) => {
      setList(d)
      const top = new Set<string>()
      for (const w of d) {
        const seg = w.id.split('/')[0]
        if (seg) top.add(seg)
      }
      setExpanded(top)
      setLoading(false)
    })
  }, [])

  const tree = useMemo(() => buildTree(list), [list])
  const ql = filter.trim().toLowerCase()
  const filteredTree = useMemo(() => {
    if (!ql) return tree
    setAutoExpandKey((k) => k + 1)
    return filterTree(tree, ql) || { ...tree, children: new Map(), workers: [] }
  }, [tree, ql])

  const toggle = (path: string) => setExpanded((s) => {
    const n = new Set(s)
    n.has(path) ? n.delete(path) : n.add(path)
    return n
  })

  if (loading) return <div style={S.empty}>加载中...</div>
  if (filteredTree.children.size === 0 && filteredTree.workers.length === 0) {
    return <div style={S.empty}>{ql ? '无匹配' : '无 worker'}</div>
  }

  return (
    <div style={S.treeNode} data-tree="worker">
      {[...filteredTree.children.values()].map((c) => (
        <NodeRow
          key={c.path + ':' + autoExpandKey}
          node={c}
          depth={0}
          expanded={expanded}
          toggle={toggle}
          activeId={activeId}
          onOpen={openTab}
          initiallyOpen={!!ql}
          forceExpandAll={!!ql}
        />
      ))}
      {filteredTree.workers.map((w) => {
        const tabId = `worker:${w.id}`
        return (
          <div
            key={w.id}
            style={S.worker(0, activeId === tabId)}
            title={w.id}
            onClick={() => openTab({ type: 'worker', id: w.id }, w.title)}
            onMouseEnter={(e) => { if (activeId !== tabId) e.currentTarget.style.background = HOVER_BG }}
            onMouseLeave={(e) => { if (activeId !== tabId) e.currentTarget.style.background = 'transparent' }}
          >
            {w.title}
          </div>
        )
      })}
    </div>
  )
}
