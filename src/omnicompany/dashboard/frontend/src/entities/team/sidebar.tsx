import React, { useEffect, useMemo, useState } from 'react'
import type { Entity, EntityType } from '../types'
import type { SidebarViewProps } from '../registry'

interface CodeEntity extends Entity {
  package: string
  file_path: string
}

interface TreeNode {
  name: string
  path: string
  children: Map<string, TreeNode>
  items: CodeEntity[]
}

function buildTree(items: CodeEntity[]): TreeNode {
  const root: TreeNode = { name: '', path: '', children: new Map(), items: [] }
  for (const it of items) {
    const parts = it.package.split('/')
    let cur = root
    for (const p of parts) {
      if (!p) continue
      let next = cur.children.get(p)
      if (!next) {
        next = { name: p, path: cur.path ? cur.path + '/' + p : p, children: new Map(), items: [] }
        cur.children.set(p, next)
      }
      cur = next
    }
    cur.items.push(it)
  }
  return root
}

function filterTree(node: TreeNode, q: string): TreeNode | null {
  const matched = node.items.filter((i) => i.id.toLowerCase().includes(q) || i.title.toLowerCase().includes(q))
  const fc = new Map<string, TreeNode>()
  for (const [k, v] of node.children) {
    const f = filterTree(v, q)
    if (f) fc.set(k, f)
  }
  if (matched.length === 0 && fc.size === 0 && node.path) return null
  return { ...node, items: matched, children: fc }
}

function countItems(n: TreeNode): number {
  let c = n.items.length
  for (const ch of n.children.values()) c += countItems(ch)
  return c
}

const S: Record<string, any> = {
  tree: { fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' },
  dir: (depth: number): React.CSSProperties => ({
    padding: '2px 0', paddingLeft: 4 + depth * 12, cursor: 'pointer',
    color: '#a8a8a8', userSelect: 'none' as const,
    fontWeight: depth === 0 ? 'bold' as const : 'normal' as const,
  }),
  arr: { color: '#666', display: 'inline-block', width: 12 },
  count: { color: '#777', marginLeft: 6, fontSize: 14 },
  item: (depth: number, active: boolean): React.CSSProperties => ({
    padding: '2px 0', paddingLeft: 4 + depth * 12 + 12, cursor: 'pointer',
    color: active ? '#90caf9' : '#d0d0d0',
    background: active ? '#1a2a3a' : 'transparent',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
  }),
  empty: { padding: 8, color: '#888', fontSize: 14 },
}

interface NodeRowProps {
  node: TreeNode
  depth: number
  expanded: Set<string>
  toggle: (path: string) => void
  activeId: string | null
  entityType: EntityType
  onOpen: SidebarViewProps['openTab']
  forceExpand?: boolean
}

function NodeRow({ node, depth, expanded, toggle, activeId, entityType, onOpen, forceExpand }: NodeRowProps) {
  const isExpanded = forceExpand || expanded.has(node.path)
  const total = countItems(node)
  return (
    <div>
      <div style={S.dir(depth)} onClick={() => toggle(node.path)} title={node.path}>
        <span style={S.arr}>{isExpanded ? '▾' : '▸'}</span>
        {node.name}
        <span style={S.count}>{total}</span>
      </div>
      {isExpanded && (
        <div>
          {[...node.children.values()].map((c) => (
            <NodeRow key={c.path} node={c} depth={depth + 1} expanded={expanded} toggle={toggle} activeId={activeId} entityType={entityType} onOpen={onOpen} forceExpand={forceExpand} />
          ))}
          {node.items.map((it) => {
            const tabId = `${entityType}:${it.id}`
            return (
              <div key={it.id} style={S.item(depth, activeId === tabId)} title={it.id} onClick={() => onOpen({ type: entityType, id: it.id }, it.title)}>
                {it.title}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

interface CodeFileSidebarProps extends SidebarViewProps {
  entityType: EntityType
  fetchList: () => Promise<CodeEntity[]>
}

export function CodeFileSidebar({ filter, activeId, openTab, entityType, fetchList }: CodeFileSidebarProps) {
  const [list, setList] = useState<CodeEntity[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchList().then((d) => {
      if (cancelled) return
      setList(d)
      const top = new Set<string>()
      for (const it of d) {
        const seg = it.package.split('/')[0]
        if (seg) top.add(seg)
      }
      setExpanded(top)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  const tree = useMemo(() => buildTree(list), [list])
  const ql = filter.trim().toLowerCase()
  const filteredTree = useMemo(() => {
    if (!ql) return tree
    return filterTree(tree, ql) || { ...tree, children: new Map(), items: [] }
  }, [tree, ql])

  const toggle = (path: string) => setExpanded((s) => {
    const n = new Set(s)
    n.has(path) ? n.delete(path) : n.add(path)
    return n
  })

  if (loading) return <div style={S.empty}>加载中...</div>
  if (filteredTree.children.size === 0 && filteredTree.items.length === 0) {
    return <div style={S.empty}>{ql ? '无匹配' : `无 ${entityType}`}</div>
  }

  return (
    <div style={S.tree} data-tree={entityType}>
      {[...filteredTree.children.values()].map((c) => (
        <NodeRow
          key={c.path + ':' + (ql ? 'f' : '')}
          node={c} depth={0}
          expanded={expanded} toggle={toggle}
          activeId={activeId}
          entityType={entityType}
          onOpen={openTab}
          forceExpand={!!ql}
        />
      ))}
      {filteredTree.items.map((it) => {
        const tabId = `${entityType}:${it.id}`
        return (
          <div key={it.id} style={S.item(0, activeId === tabId)} title={it.id} onClick={() => openTab({ type: entityType, id: it.id }, it.title)}>
            {it.title}
          </div>
        )
      })}
    </div>
  )
}
