import React, { useEffect, useMemo, useState } from 'react'
import { fetchList, type NoteEntity } from './resolver'
import type { SidebarViewProps } from '../registry'
import { copyText } from '../../lib/copyText'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { FileText, Copy, ChevronRight, ChevronDown } from 'lucide-react'

interface TreeNode {
  name: string
  path: string
  children: Map<string, TreeNode>
  notes: NoteEntity[]
}

function buildTree(notes: NoteEntity[]): TreeNode {
  const root: TreeNode = { name: '', path: '', children: new Map(), notes: [] }
  for (const n of notes) {
    const parts = n.id.split('/')
    const dirParts = parts.slice(0, -1)
    let cur = root
    for (const p of dirParts) {
      let next = cur.children.get(p)
      if (!next) {
        next = { name: p, path: cur.path ? cur.path + '/' + p : p, children: new Map(), notes: [] }
        cur.children.set(p, next)
      }
      cur = next
    }
    cur.notes.push(n)
  }
  return root
}

function filterTree(node: TreeNode, q: string): TreeNode | null {
  const matchedNotes = node.notes.filter((n) => n.id.toLowerCase().includes(q) || n.title.toLowerCase().includes(q))
  const filteredChildren = new Map<string, TreeNode>()
  for (const [k, v] of node.children) {
    const fc = filterTree(v, q)
    if (fc) filteredChildren.set(k, fc)
  }
  if (matchedNotes.length === 0 && filteredChildren.size === 0 && node.path) return null
  return { ...node, notes: matchedNotes, children: filteredChildren }
}

function countNotes(node: TreeNode): number {
  let n = node.notes.length
  for (const c of node.children.values()) n += countNotes(c)
  return n
}

// frostpane 深度重建(2026-06-29 第二轮): 抛弃拥挤等宽折叠树, 改成「分区分组 + 玻璃笔记卡网格」,
// 与同形态的 PlanSidebar 对齐。第一轮只换 token 被否(布局/交互没变), 这轮真动结构:
// - 目录 = 分区标题条(chevron + 15px/13px 醒目标题 + mono 计数弱灰), 用 border-left 缩进编码层级,
//   不再是一排等宽缩进折叠行制造的拥挤感; 间距(区间 12 / 子区 8)编码分组。
// - 笔记 = 磨砂玻璃卡(var(--fp-glass)+blur26 saturate190 + inset 顶部高光 + radius11),
//   卡解剖: 顶域徽章 + 叶子名 flex1 醒目(15px) + 共享 KebabMenu ⋯ 收纳低频(复制 id);
//   note id 弱灰 12px 等宽; 主操作「打开」做底部整宽显眼按钮。
// - 卡网格 repeat(auto-fill, minmax(240px,1fr)): 窄侧栏退化单列, 宽面板自动成网格。
// - 信息层级靠字阶(15/13/12)非纯加粗; 4px 栅格放宽呼吸; 界面无说明文字。
// 2026-06-30 补齐重建标准缺口: ① 无标题头(本就无, 内容从顶部直接开始, 页签已标识身份);
//   ② root background:transparent 吃 body 全局冷渐变(原缺, 会顶掉渐变); ③ 颜色全 var(--fp-*),
//   抹掉残留裸 hex/accent rgba(active 边→fp-accent / hover 边→fp-border-strong / 主按钮→fp-accent-weak)。
const MONO = "'Berkeley Mono','SF Mono','Cascadia Code',Consolas,Menlo,monospace"
const SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,'PingFang SC','Microsoft YaHei',sans-serif"
const GLASS = 'var(--fp-blur)'
const EASE = 'cubic-bezier(0.175,0.885,0.32,1.1)'

const S: Record<string, any> = {
  // 侧栏 root 透明: 吃 body 全局统一冷渐变, 玻璃卡浮其上才有玻璃感(不铺实底把渐变顶掉)。
  root: { fontFamily: SANS, background: 'transparent', color: 'var(--fp-text-2)', padding: '4px 4px 16px', minHeight: '100%' },

  // 分区(目录): 间距 + border-left 编码层级, 标题靠字号建立 — 不再用一排等宽折叠行制造拥挤。
  section: (depth: number): React.CSSProperties => ({
    marginTop: depth === 0 ? 12 : 8,
    marginLeft: depth > 0 ? 8 : 0,
    paddingLeft: depth > 0 ? 8 : 0,
    borderLeft: depth > 0 ? '1px solid var(--fp-border-subtle)' : undefined,
  }),
  sectionHead: {
    display: 'flex', alignItems: 'center', gap: 6,
    padding: '4px 6px', cursor: 'pointer', userSelect: 'none' as const,
    borderRadius: 7, transition: `background 150ms ${EASE}`,
  },
  // 顶层目录 = 本区最重要信息 15px/650; 越深越收敛 13px。
  sectionTitle: (depth: number): React.CSSProperties => ({
    color: depth === 0 ? 'var(--fp-text)' : 'var(--fp-text-2)',
    fontSize: depth === 0 ? 15 : 13,
    fontWeight: depth === 0 ? 650 : 550,
    letterSpacing: depth === 0 ? '-0.01em' : undefined,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
  }),
  caret: { color: 'var(--fp-text-3)', display: 'inline-flex', flexShrink: 0 },
  // 计数 = 最次级 12px 弱灰等宽。
  count: { color: 'var(--fp-text-3)', marginLeft: 'auto', fontSize: 12, fontFamily: MONO, flexShrink: 0 },

  // 笔记卡网格: 窄侧栏 1 列 / 宽面板自动多列。
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8, marginTop: 6 },
  // 磨砂玻璃卡: inset 顶部高光 + 冷色描边 + 11 圆角。
  card: (active: boolean): React.CSSProperties => ({
    display: 'flex', flexDirection: 'column', minWidth: 0,
    background: 'var(--fp-glass)', backdropFilter: GLASS, WebkitBackdropFilter: GLASS,
    border: `1px solid ${active ? 'var(--fp-accent)' : 'var(--fp-border)'}`, borderRadius: 11, padding: 12,
    boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
    transition: `border-color 150ms ${EASE}`,
  }),
  cardTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
  // 顶域徽章 = 微胶囊弱底, 标记所属目录域, 不抢标题。
  badge: { display: 'inline-flex', alignItems: 'center', padding: '1px 8px', borderRadius: 999, fontSize: 12, fontWeight: 600, color: 'var(--fp-text-3)', background: 'var(--fp-border-subtle)', flexShrink: 0, maxWidth: 96, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  // 标题 = 卡内主信息 15px/650 醒目。
  cardTitle: (active: boolean): React.CSSProperties => ({
    flex: 1, minWidth: 0, color: active ? 'var(--fp-link)' : 'var(--fp-text)', fontWeight: 650, fontSize: 15,
    letterSpacing: '-0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const, cursor: 'pointer',
  }),
  // 卡内次信息 = note id, 弱灰 12px 等宽。
  meta: { color: 'var(--fp-text-3)', fontSize: 12, marginTop: 8, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  // 主操作 = 底部整宽显眼按钮。
  primary: { marginTop: 10, width: '100%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, border: '1px solid var(--fp-border)', background: 'var(--fp-accent-weak)', color: 'var(--fp-link)', borderRadius: 7, padding: '6px 10px', cursor: 'pointer', fontSize: 13, fontWeight: 550, fontFamily: SANS, transition: `all 150ms ${EASE}` },

  // 空/加载态: 弱灰常规体, 居中给呼吸。
  empty: { padding: '24px 16px', color: 'var(--fp-text-3)', fontSize: 13, textAlign: 'center' as const },
}

interface NoteCardProps {
  note: NoteEntity
  active: boolean
  onOpen: () => void
}

// 单条笔记 = 一张磨砂玻璃卡。低频动作(复制 id)进共享 ⋯; 主操作(打开)做底部整宽按钮。
function NoteCard({ note: n, active, onOpen }: NoteCardProps) {
  const leaf = n.id.split('/').pop() || n.id
  const domain = (n.tags && n.tags[0]) || n.id.split('/')[0] || 'root'
  const kebab: KebabItem[] = [
    { label: '复制 note id', icon: <Copy size={15} />, testid: `note-copy-id-${n.id}`, onClick: () => { void copyText(n.id) } },
  ]
  return (
    <div
      style={S.card(active)}
      title={n.id}
      onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--fp-border-strong)' }}
      onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--fp-border)' }}
    >
      <div style={S.cardTop}>
        <span style={S.badge} title={domain}>{domain}</span>
        <span
          style={S.cardTitle(active)}
          title={`${leaf} — 点击打开`}
          onClick={onOpen}
        >
          {leaf}
        </span>
        {/* 低频操作(复制 id)收进共享 ⋯ 菜单 — 不再一排等权按钮 */}
        <KebabMenu testid={`note-more-${n.id}`} items={kebab} />
      </div>
      {/* 次级元信息: 完整 note id, 弱灰等宽 */}
      <div style={S.meta} title={n.id}>{n.id}</div>
      {/* 主操作 = 显眼底部整宽按钮: 打开笔记 */}
      <button
        type="button"
        style={S.primary}
        data-testid={`note-open-${n.id}`}
        title={`打开 ${n.id}`}
        onClick={onOpen}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'color-mix(in srgb, var(--fp-accent) 28%, transparent)'; e.currentTarget.style.color = 'var(--fp-text)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--fp-accent-weak)'; e.currentTarget.style.color = 'var(--fp-link)' }}
      >
        <FileText size={14} /> 打开
      </button>
    </div>
  )
}

interface NodeSectionProps {
  node: TreeNode
  depth: number
  expanded: Set<string>
  toggle: (path: string) => void
  activeId: string | null
  onOpen: SidebarViewProps['openTab']
  forceExpandAll?: boolean
}

function NodeSection({ node, depth, expanded, toggle, activeId, onOpen, forceExpandAll }: NodeSectionProps) {
  const isExpanded = forceExpandAll || expanded.has(node.path)
  const total = countNotes(node)
  return (
    <div style={S.section(depth)}>
      <div
        style={S.sectionHead}
        onClick={() => toggle(node.path)}
        title={node.path}
        onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,.04)' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
      >
        <span style={S.caret}>{isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span style={S.sectionTitle(depth)}>{node.name}</span>
        <span style={S.count}>{total}</span>
      </div>
      {isExpanded && (
        <div>
          {[...node.children.values()].map((c) => (
            <NodeSection key={c.path} node={c} depth={depth + 1} expanded={expanded} toggle={toggle} activeId={activeId} onOpen={onOpen} forceExpandAll={forceExpandAll} />
          ))}
          {node.notes.length > 0 && (
            <div style={S.grid}>
              {node.notes.map((n) => {
                const tabId = `note:${n.id}`
                const leaf = n.id.split('/').pop() || n.id
                return (
                  <NoteCard
                    key={n.id}
                    note={n}
                    active={activeId === tabId}
                    onOpen={() => onOpen({ type: 'note', id: n.id }, leaf)}
                  />
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function NoteSidebar({ filter, activeId, openTab }: SidebarViewProps) {
  const [list, setList] = useState<NoteEntity[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [autoKey, setAutoKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchList().then((d) => {
      if (cancelled) return
      setList(d)
      // default expand top-level dirs (e.g. plans/, standards/, ...) — but NOT root files
      const top = new Set<string>()
      for (const n of d) {
        const parts = n.id.split('/')
        if (parts.length > 1) top.add(parts[0])
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
    setAutoKey((k) => k + 1)
    return filterTree(tree, ql) || { ...tree, children: new Map(), notes: [] }
  }, [tree, ql])

  const toggle = (path: string) => setExpanded((s) => {
    const n = new Set(s)
    n.has(path) ? n.delete(path) : n.add(path)
    return n
  })

  if (loading) return <div style={S.empty}>加载中...</div>
  if (filteredTree.children.size === 0 && filteredTree.notes.length === 0) {
    return <div style={S.empty}>{ql ? '无匹配' : '无 note'}</div>
  }

  return (
    <div style={S.root} data-tree="note">
      {[...filteredTree.children.values()].map((c) => (
        <NodeSection
          key={c.path + ':' + autoKey}
          node={c}
          depth={0}
          expanded={expanded}
          toggle={toggle}
          activeId={activeId}
          onOpen={openTab}
          forceExpandAll={!!ql}
        />
      ))}
      {/* root 直接子 note(无目录前缀) = 单独成网格 */}
      {filteredTree.notes.length > 0 && (
        <div style={S.grid}>
          {filteredTree.notes.map((n) => {
            const tabId = `note:${n.id}`
            return (
              <NoteCard
                key={n.id}
                note={n}
                active={activeId === tabId}
                onOpen={() => openTab({ type: 'note', id: n.id }, n.id)}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
