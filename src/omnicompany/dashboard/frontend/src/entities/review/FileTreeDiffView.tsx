/**
 * FileTreeDiffView — filetree_diff_v1 渲染器(custom_web_template 载体)。
 * 树骨架抄 entities/note/NoteSidebar 模式；单文件 diff 复用 chat/tools 的 ToolDiffViewer。
 * 改动文件恒高亮；显示全部/仅 diff 目录同级 切换；点击看 diff、右键复制路径、可逐文件批注。
 *
 * 2026-06-30 重做(frostpane 面板重做标准): 删重复"文件树 diff"标题头 → 顶部只留 counts 玻璃统计条;
 * root 透明吃全局冷渐变; 长 diff 树用极淡 var(--fp-surface) 安静面(不盖死渐变); 字号统层(禁 11px);
 * 逐文件低频操作(在页签打开/批注/复制路径)收进共享 KebabMenu(⋯); 颜色全 var(--fp-*) 无裸 hex。
 * 数据接线/props/data-testid 全部保留, 仅改呈现+交互。
 */
import React, { useMemo, useState } from 'react'
import { Copy, ExternalLink, PencilLine } from 'lucide-react'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { ToolDiffViewer } from './ToolDiffViewer'
import { COLORS } from './shared'

type FileStatus = 'added' | 'modified' | 'deleted' | 'renamed' | 'unchanged'
interface FilePreviewData { kind: 'image' | 'html'; data_url?: string; content?: string; oversized?: boolean }
export interface FileEntry {
  path: string
  old_path?: string | null
  status: FileStatus
  additions?: number
  deletions?: number
  annotation?: string | null
  diff?: string | null
  preview?: FilePreviewData | null
}
interface FileTreeDiffData {
  schema?: string
  source?: { mode?: string; root?: string; ref?: string | null; is_git?: boolean; generated_at?: string }
  counts?: Record<string, number>
  files?: FileEntry[]
}

interface TreeNode { name: string; path: string; children: Map<string, TreeNode>; files: FileEntry[] }

function buildTree(files: FileEntry[]): TreeNode {
  const root: TreeNode = { name: '', path: '', children: new Map(), files: [] }
  for (const f of files) {
    const parts = f.path.split('/')
    let cur = root
    for (const p of parts.slice(0, -1)) {
      let next = cur.children.get(p)
      if (!next) { next = { name: p, path: cur.path ? `${cur.path}/${p}` : p, children: new Map(), files: [] }; cur.children.set(p, next) }
      cur = next
    }
    cur.files.push(f)
  }
  return root
}

// 改动语义色 全部走 fp token(unchanged 用最弱灰 text-3)。
const STATUS_COLOR: Record<FileStatus, string> = {
  added: 'var(--fp-ok)', modified: 'var(--fp-warn)', deleted: 'var(--fp-err)', renamed: 'var(--fp-accent)', unchanged: 'var(--fp-text-3)',
}
const STATUS_MARK: Record<FileStatus, string> = { added: 'A', modified: 'M', deleted: 'D', renamed: 'R', unchanged: '·' }

function parseUnifiedToDiffLines(diffText: string): { type: string; content: string; lineNum: number }[] {
  const out: { type: string; content: string; lineNum: number }[] = []
  let n = 0
  for (const raw of (diffText || '').split('\n')) {
    if (/^(\+\+\+|---|diff |index |@@|new file|deleted file|rename |Binary |similarity)/.test(raw)) continue
    if (raw.startsWith('+')) out.push({ type: 'added', content: raw.slice(1), lineNum: ++n })
    else if (raw.startsWith('-')) out.push({ type: 'removed', content: raw.slice(1), lineNum: ++n })
  }
  return out
}

const S: Record<string, React.CSSProperties> = {
  // root 透明: 吃 body 全局冷渐变, 不再铺 var(--fp-bg) 实底顶掉渐变。
  root: { height: '100%', overflow: 'auto', background: 'transparent', color: 'var(--fp-text)', fontFamily: 'var(--fp-font-mono)' },
  // 顶部统计条 = 玻璃外壳(磨砂 + 边缘高光), 粘顶。不是面板标题, 只承载 counts + 切换。
  statHead: {
    position: 'sticky', top: 0, zIndex: 5,
    display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
    padding: '10px 14px',
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    borderBottom: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  // 统计 chip: 次级字, +增/~改/-删 各带语义色, 来源弱灰。
  counts: { display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 14 },
  srcTag: { color: 'var(--fp-text-3)', fontSize: 13, fontFamily: 'var(--fp-font-mono)' },
  total: { color: 'var(--fp-text-2)', fontSize: 13 },
  // 切换钮: 安静幽灵钮(非主操作显眼底), hover 浮淡底。
  toggle: { border: '1px solid var(--fp-border)', background: 'var(--fp-surface)', color: 'var(--fp-text-2)', borderRadius: 7, padding: '4px 11px', cursor: 'pointer', fontSize: 13, transition: 'border-color 150ms var(--fp-ease), color 150ms var(--fp-ease)' },
  // 树容器: 极淡安静面(护眼读长列表), 不盖死渐变。
  treeWrap: { margin: 12, padding: '6px 8px', background: 'var(--fp-surface)', border: '1px solid var(--fp-border-subtle)', borderRadius: 11, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.05)' },
  dirRow: { padding: '3px 4px', cursor: 'pointer', color: 'var(--fp-text-3)', userSelect: 'none', display: 'flex', alignItems: 'center', borderRadius: 6, fontSize: 13 },
  arr: { color: 'var(--fp-text-3)', display: 'inline-block', width: 14, flexShrink: 0 },
  leafRow: { padding: '3px 4px 3px 8px', display: 'flex', alignItems: 'center', gap: 6, borderRadius: 6 },
  mark: { fontWeight: 700, width: 12, flexShrink: 0, fontFamily: 'var(--fp-font-mono)', fontSize: 13 },
  empty: { color: 'var(--fp-text-3)', fontSize: 13, padding: 8 },
  toast: { color: 'var(--fp-ok)', fontSize: 13 },
}

// 单文件预览: 图片直接出图(内嵌 base64), html 渲染, 其余看 diff。被内联展开与文件页签共用。
export function FilePreview({ file }: { file: FileEntry }) {
  const pv = file.preview
  if (pv?.kind === 'image') {
    if (pv.oversized || !pv.data_url) return <div style={S.empty}>图片过大未内嵌（{file.path}）</div>
    return <div style={{ padding: 8, background: 'var(--fp-surface)', borderRadius: 8, textAlign: 'center' }}><img src={pv.data_url} alt={file.path} style={{ maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain', borderRadius: 6 }} /></div>
  }
  if (pv?.kind === 'html') {
    return <iframe data-testid="filetree-html-preview" srcDoc={pv.content || ''} sandbox="allow-same-origin" style={{ width: '100%', height: '60vh', border: 'none', borderRadius: 8, background: '#fff' }} title={file.path} />
  }
  if (file.diff) {
    return (
      <ToolDiffViewer
        oldContent="" newContent={file.diff}
        filePath={file.old_path ? `${file.old_path} → ${file.path}` : file.path}
        createDiff={() => parseUnifiedToDiffLines(file.diff || '')}
        badge={file.status} badgeColor={file.status === 'added' ? 'green' : 'gray'}
      />
    )
  }
  return <div style={S.empty}>无可预览内容（{file.status}）</div>
}

function FileLeaf({ file, depth, material, onOpenFile }: { file: FileEntry; depth: number; material?: Material; onOpenFile?: (f: FileEntry) => void }) {
  const [open, setOpen] = useState(false)
  const canPreview = !!(file.diff || file.preview)
  const [annoting, setAnnoting] = useState(false)
  const [annoteText, setAnnoteText] = useState('')
  const [saved, setSaved] = useState<string | null>(file.annotation ?? null)
  const [toast, setToast] = useState('')
  const leaf = file.path.split('/').pop() || file.path
  const color = STATUS_COLOR[file.status] || 'var(--fp-text-3)'

  const copyPath = (e: React.MouseEvent) => {
    e.preventDefault()
    void navigator.clipboard?.writeText(file.path)
    setToast('已复制路径'); setTimeout(() => setToast(''), 900)
  }
  const submitAnnote = async () => {
    if (!material || !annoteText.trim()) return
    try {
      await reviewstageApi.addAnnotation(material.id, annoteText.trim(), { kind: 'filetree_file', path: file.path })
      setSaved(annoteText.trim()); setAnnoteText(''); setAnnoting(false)
    } catch (e) {
      setToast(`批注失败: ${String(e instanceof Error ? e.message : e)}`); setTimeout(() => setToast(''), 1500)
    }
  }

  // 低频逐文件操作收进 ⋯: 在页签打开 / 批注 / 复制路径。原散落角钮的 testid 全部迁进 item.testid 保留可测。
  const kebabItems: KebabItem[] = []
  if (onOpenFile && canPreview) kebabItems.push({ label: '在文件页签打开', icon: <ExternalLink size={14} />, testid: 'filetree-open-file', onClick: () => onOpenFile(file) })
  if (material) kebabItems.push({ label: annoting ? '收起批注' : '批注此文件', icon: <PencilLine size={14} />, testid: 'filetree-annotate', onClick: () => setAnnoting((v) => !v) })
  kebabItems.push({ label: '复制路径', icon: <Copy size={14} />, testid: 'filetree-copy-path', onClick: () => { void navigator.clipboard?.writeText(file.path); setToast('已复制路径'); setTimeout(() => setToast(''), 900) } })

  return (
    <div style={{ paddingLeft: 4 + depth * 12 }}>
      <div
        style={{
          ...S.leafRow,
          cursor: canPreview ? 'pointer' : 'default',
          borderLeft: file.status !== 'unchanged' ? `2px solid ${color}` : '2px solid transparent',
        }}
        title={file.path}
        onClick={() => canPreview && setOpen((v) => !v)}
        onContextMenu={copyPath}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,.04)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
      >
        <span style={{ ...S.mark, color }}>{STATUS_MARK[file.status]}</span>
        <span style={{ color: file.status === 'unchanged' ? 'var(--fp-text-3)' : 'var(--fp-text)', fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{leaf}</span>
        {(file.additions || file.deletions) ? (
          <span style={{ fontSize: 13, flexShrink: 0 }}>
            <span style={{ color: 'var(--fp-ok)' }}>+{file.additions || 0}</span>{' '}
            <span style={{ color: 'var(--fp-err)' }}>-{file.deletions || 0}</span>
          </span>
        ) : null}
        {toast && <span style={S.toast}>{toast}</span>}
        <div style={{ marginLeft: 'auto', flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
          <KebabMenu items={kebabItems} testid="filetree-leaf-more" iconSize={14} />
        </div>
      </div>
      {saved && <div style={{ paddingLeft: 20, fontSize: 13, color: 'var(--fp-warn)' }}>批注: {saved}</div>}
      {annoting && (
        <div style={{ paddingLeft: 20, display: 'flex', gap: 6, margin: '4px 0' }}>
          <input
            data-testid="filetree-annotate-input"
            value={annoteText} onChange={(e) => setAnnoteText(e.target.value)}
            placeholder="对这个文件的改动写点说明…"
            style={{ flex: 1, background: 'var(--fp-card)', border: '1px solid var(--fp-border)', color: 'var(--fp-text)', borderRadius: 6, padding: '4px 7px', fontSize: 13 }}
            onKeyDown={(e) => { if (e.key === 'Enter') void submitAnnote() }}
          />
          <button type="button" onClick={() => void submitAnnote()} style={{ background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', border: 0, borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 13 }}>提交</button>
        </div>
      )}
      {open && (
        <div style={{ padding: '4px 0 8px 12px' }}>
          <FilePreview file={file} />
        </div>
      )}
    </div>
  )
}

function DirRow({ node, depth, forceExpand, material, onOpenFile }: { node: TreeNode; depth: number; forceExpand: boolean; material?: Material; onOpenFile?: (f: FileEntry) => void }) {
  const [expanded, setExpanded] = useState(true)
  const isOpen = forceExpand || expanded
  const childCount = useMemo(() => countChanged(node), [node])
  return (
    <div>
      {node.path !== '' && (
        <div
          style={{ ...S.dirRow, paddingLeft: 4 + depth * 12 }}
          onClick={() => setExpanded((v) => !v)}
          title={node.path}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,.04)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        >
          <span style={S.arr}>{isOpen ? '▾' : '▸'}</span>
          <span style={{ color: 'var(--fp-text-2)' }}>{node.name}</span>
          <span style={{ color: 'var(--fp-text-3)', marginLeft: 6, fontSize: 13 }}>{childCount}</span>
        </div>
      )}
      {isOpen && (
        <div>
          {[...node.children.values()].map((c) => (
            <DirRow key={c.path} node={c} depth={node.path === '' ? depth : depth + 1} forceExpand={forceExpand} material={material} onOpenFile={onOpenFile} />
          ))}
          {node.files.map((f) => (
            <FileLeaf key={f.path} file={f} depth={node.path === '' ? depth : depth + 1} material={material} onOpenFile={onOpenFile} />
          ))}
        </div>
      )}
    </div>
  )
}

function countChanged(node: TreeNode): number {
  let n = node.files.filter((f) => f.status !== 'unchanged').length
  for (const c of node.children.values()) n += countChanged(c)
  return n
}

export function FileTreeDiffView({ data, material, onOpenFile }: { data: FileTreeDiffData; material?: Material; onOpenFile?: (f: FileEntry) => void }) {
  const [showAll, setShowAll] = useState(false)
  const files = data.files || []
  const hasUnchanged = useMemo(() => files.some((f) => f.status === 'unchanged'), [files])
  const shown = useMemo(() => (showAll ? files : files.filter((f) => f.status !== 'unchanged')), [files, showAll])
  const tree = useMemo(() => buildTree(shown), [shown])
  const counts = data.counts || {}
  const src = data.source || {}

  return (
    <div data-testid="material-filetree-diff" style={S.root}>
      {/* 无标题头(Linear 风内容优先): 页签已标识"文件树 diff" 身份, 这里只留 counts 玻璃统计条 + 切换。 */}
      <div style={S.statHead}>
        <span style={S.counts}>
          <span style={{ color: 'var(--fp-ok)' }}>+{counts.added || 0}</span>
          <span style={{ color: 'var(--fp-warn)' }}>~{counts.modified || 0}</span>
          <span style={{ color: 'var(--fp-err)' }}>-{counts.deleted || 0}</span>
          {counts.renamed ? <span style={{ color: 'var(--fp-accent)' }}>R{counts.renamed}</span> : null}
        </span>
        <span style={S.total}>共 {counts.total || files.length} 个</span>
        <span style={S.srcTag}>{src.mode || '?'}{src.ref ? ` @ ${src.ref}` : ''}</span>
        {hasUnchanged && (
          <button
            type="button" data-testid="filetree-toggle-all"
            onClick={() => setShowAll((v) => !v)}
            style={{ ...S.toggle, marginLeft: 'auto' }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border-strong)'; e.currentTarget.style.color = 'var(--fp-text)' }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border)'; e.currentTarget.style.color = 'var(--fp-text-2)' }}
          >{showAll ? '只看改动' : '显示全部'}</button>
        )}
      </div>
      <div style={S.treeWrap}>
        {shown.length === 0 ? <div style={S.empty}>没有改动文件。</div> : <DirRow node={tree} depth={0} forceExpand={false} material={material} onOpenFile={onOpenFile} />}
      </div>
    </div>
  )
}
