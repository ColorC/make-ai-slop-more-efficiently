import React, { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Eye, Pencil, Columns2, Save, RefreshCw } from 'lucide-react'
import { fetchDetail, fetchLinks, saveNote, type NoteDetail, type NoteEntity, type NoteLinks } from './resolver'
import { usePanels } from '../../stores/panelsStore'
import { VSplitter } from '../../shell/Splitter'
import MarkdownRenderer from '../../shell/MarkdownRenderer'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { type Annotation, listAnnotations, paragraphHash, snippetOf, extractText } from './annotations'
import AnnotatedParagraph from './AnnotationLayer'
import AnnotationModal from './AnnotationModal'

const MonacoEditor = React.lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.default })))

const FONT_MONO = "'Berkeley Mono','Consolas','Menlo',monospace"
const FONT_READ = "var(--fp-font-sans)"

const S: Record<string, any> = {
  // 面板 root 透明 → 吃 body 全局冷渐变(阅读/编辑区安静), 不再铺 #0f0f0f 实底把渐变顶掉。
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: 'var(--fp-text)', overflow: 'hidden' },
  // 工具条 = 玻璃外壳(磨砂 + 边缘高光), 浮在安静的阅读/编辑区之上。
  toolbar: {
    flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    borderBottom: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  // 标题不再单列重复页签名(VSCode/驾驶舱页签已标识), 退化成工具条左侧的弱化路径微字 + 脏标。
  pathCrumb: { flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6, color: 'var(--fp-text-3)', fontSize: 12, fontFamily: FONT_MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  dirty: { color: 'var(--fp-warn)', fontSize: 14, flexShrink: 0 },
  // 模式切换 = shadcn 分段控件(选中页同 surface 无缝, 未选中弱底凹陷, 无底边)。
  seg: { display: 'inline-flex', gap: 2, background: 'var(--fp-glass-2)', border: '1px solid var(--fp-border)', borderRadius: 7, padding: 2 },
  segBtn: (active: boolean): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', gap: 5, border: 0, borderRadius: 5,
    padding: '4px 11px', fontSize: 13, fontFamily: 'inherit', cursor: 'pointer',
    background: active ? 'var(--fp-surface)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-2)',
    boxShadow: active ? 'var(--fp-shadow-sm)' : 'none',
  }),
  // 主操作: 保存(显眼 primary)。
  saveBtn: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 13px', background: 'var(--fp-accent)', border: '1px solid var(--fp-accent)', borderRadius: 7, color: 'var(--fp-accent-fg)', cursor: 'pointer', fontSize: 13, fontWeight: 550, fontFamily: 'inherit' },
  saveBtnDisabled: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 13px', background: 'var(--fp-card)', border: '1px solid var(--fp-border)', borderRadius: 7, color: 'var(--fp-text-3)', cursor: 'not-allowed', fontSize: 13, fontWeight: 550, fontFamily: 'inherit' },
  saveMsg: (ok: boolean): React.CSSProperties => ({ fontSize: 13, color: ok ? 'var(--fp-ok)' : 'var(--fp-err)', flexShrink: 0 }),
  body: { flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 },
  // 阅读区: 安静(极淡 surface, 不盖死渐变), 正文用 sans 衬护眼。
  main: { flex: 1, overflow: 'auto', padding: 24, background: 'var(--fp-surface)', color: 'var(--fp-text)', fontSize: 15, lineHeight: 1.6, fontFamily: FONT_READ, minWidth: 0 },
  editorWrap: { flex: 1, minHeight: 0, overflow: 'hidden' },
  splitPreview: { width: '50%', borderLeft: '1px solid var(--fp-border)', overflow: 'auto', padding: 24, background: 'var(--fp-surface)', color: 'var(--fp-text)', fontSize: 15, lineHeight: 1.6, fontFamily: FONT_READ },
  // 侧栏 = 玻璃外壳(批注/链接), 浮在阅读区右侧。
  sidePanel: (w: number): React.CSSProperties => ({
    width: w, borderLeft: '1px solid var(--fp-border)', display: 'flex', flexDirection: 'column' as const,
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    flexShrink: 0, minWidth: 120, maxWidth: 600,
  }),
  sideHeader: { padding: '8px 12px', color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 600, letterSpacing: '.04em', textTransform: 'uppercase' as const, borderBottom: '1px solid var(--fp-border-subtle)' },
  sideList: { flex: 1, overflow: 'auto', padding: '4px 0' },
  link: { display: 'block', padding: '4px 12px', cursor: 'pointer', fontSize: 13, color: 'var(--fp-link)', fontFamily: FONT_MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  unresolved: { display: 'block', padding: '4px 12px', fontSize: 13, color: 'var(--fp-text-3)', fontFamily: FONT_MONO, fontStyle: 'italic' as const },
  annItem: { padding: '6px 12px', fontSize: 13, color: 'var(--fp-text-2)', borderBottom: '1px solid var(--fp-border-subtle)', cursor: 'pointer' },
  annSnippet: { color: 'var(--fp-text-3)', fontSize: 13, fontStyle: 'italic' as const, marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  err: { padding: 24, color: 'var(--fp-err)' },
  empty: { padding: 24, color: 'var(--fp-text-3)', fontStyle: 'italic' as const },
  emptyMini: { padding: '8px 12px', color: 'var(--fp-text-3)', fontSize: 12, fontStyle: 'italic' as const },
}

/** Note-specific element wrappers that add annotation markers. Per user round 21 #7,
 *  every meaningful block-level element is annotatable: paragraphs, headings,
 *  list items, tables, blockquotes, and (non-language) code blocks. HR is a
 *  separator and intentionally NOT wrapped — it sits *between* annotatable blocks.
 *  Note: language-tagged code blocks bypass `pre` (they go to SyntaxHighlighter),
 *  so they're not annotatable for now — known limit, document in demo.
 */
const buildAnnotationOverride = (
  annotationsByHash: Map<string, Annotation[]>,
  onAdd: (hash: string, snippet: string) => void,
  onOpenThread: (hash: string) => void,
) => {
  const wrap = (Tag: keyof JSX.IntrinsicElements) =>
    ({ node, children }: any) => {
      const text = extractText(node)
      if (!text || text.length < 4) return React.createElement(Tag, {}, children)
      const hash = paragraphHash(text)
      const matched = annotationsByHash.get(hash) || []
      return (
        <AnnotatedParagraph
          hash={hash}
          snippet={snippetOf(text)}
          matched={matched}
          onAdd={onAdd}
          onOpen={onOpenThread}
        >
          {React.createElement(Tag, { 'data-anno-tag': Tag } as any, children)}
        </AnnotatedParagraph>
      )
    }

  return {
    p: wrap('p'),
    h1: wrap('h1'), h2: wrap('h2'), h3: wrap('h3'),
    h4: wrap('h4'), h5: wrap('h5'), h6: wrap('h6'),
    li: wrap('li'),
    table: wrap('table'),
    blockquote: wrap('blockquote'),
    pre: wrap('pre'),
  }
}

type Mode = 'view' | 'edit' | 'split'

export default function NoteEditor({ entity }: { entity: NoteEntity }) {
  const [detail, setDetail] = useState<NoteDetail | null>(null)
  const [links, setLinks] = useState<NoteLinks | null>(null)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('view')
  const [sideW, setSideW] = useState(260)
  const [draft, setDraft] = useState<string>('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [annoModal, setAnnoModal] = useState<{ anchor: { hash: string; snippet: string } | null; open: boolean }>({ anchor: null, open: false })
  const [livingHashes, setLivingHashes] = useState<Set<string>>(new Set())
  const openTab = usePanels((s) => s.openTab)
  const draftRef = useRef('')

  const loadAnnotations = (id: string) => {
    listAnnotations(id).then(setAnnotations).catch(() => setAnnotations([]))
  }

  useEffect(() => {
    let cancelled = false
    setDetail(null); setLinks(null); setAnnotations([]); setError(null); setDirty(false); setSaveMsg(null); setLivingHashes(new Set())
    fetchDetail(entity.id).then((d) => {
      if (cancelled) return
      setDetail(d)
      setDraft(d.content)
      draftRef.current = d.content
    }).catch((e) => { if (!cancelled) setError(String(e)) })
    fetchLinks(entity.id).then((l) => { if (!cancelled) setLinks(l) }).catch(() => {
      if (!cancelled) setLinks({ outgoing: [], outgoing_unresolved: [], backlinks: [] })
    })
    loadAnnotations(entity.id)
    return () => { cancelled = true }
  }, [entity.id])

  // Track which paragraph hashes exist in current rendered content (to detect orphan anchors)
  useEffect(() => {
    if (!detail) return
    // simple md paragraph splitter (reasonable approximation; ReactMarkdown agrees on \n\n bounds)
    const paragraphs = detail.content.split(/\n\s*\n/).filter((p) => p.trim().length >= 4)
    const hashes = new Set<string>()
    for (const p of paragraphs) {
      // remove markdown syntax to align with extractText (rough)
      const plain = p
        .replace(/^#+\s+/gm, '')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/^\s*[-*+]\s+/gm, '')
        .replace(/^>\s*/gm, '')
      hashes.add(paragraphHash(plain))
    }
    setLivingHashes(hashes)
  }, [detail])

  const annotationsByHash = useMemo(() => {
    const map = new Map<string, Annotation[]>()
    for (const a of annotations) {
      const arr = map.get(a.anchor.hash) || []
      arr.push(a)
      map.set(a.anchor.hash, arr)
    }
    return map
  }, [annotations])

  const orphanAnnotations = useMemo(
    () => annotations.filter((a) => livingHashes.size > 0 && !livingHashes.has(a.anchor.hash)),
    [annotations, livingHashes],
  )

  const onChange = (v: string | undefined) => {
    const next = v || ''
    setDraft(next)
    draftRef.current = next
    setDirty(detail ? next !== detail.content : false)
    setSaveMsg(null)
  }

  const doSave = async () => {
    if (!detail || saving || !dirty) return
    setSaving(true); setSaveMsg(null)
    try {
      const r = await saveNote(entity.id, draftRef.current)
      setDetail({ ...detail, content: draftRef.current })
      setDirty(false)
      setSaveMsg({ ok: true, text: `已保存 (${r.size}B)` })
    } catch (e) {
      setSaveMsg({ ok: false, text: `保存失败: ${e}` })
    } finally { setSaving(false) }
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && mode !== 'view') {
        e.preventDefault(); doSave()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [mode, detail, dirty, saving])

  const switchMode = (next: Mode) => {
    if (next === 'view' && dirty) {
      if (!window.confirm('有未保存修改, 切换到只读模式会丢失. 继续?')) return
      setDraft(detail?.content || ''); draftRef.current = detail?.content || ''; setDirty(false)
    }
    setMode(next)
  }

  const jumpTo = (target: string) => {
    if (dirty && !window.confirm('有未保存修改, 跳到其他笔记会丢失. 继续?')) return
    openTab({ type: 'note', id: target }, target.split('/').pop() || target)
  }

  const openAdd = (hash: string, snippet: string) => setAnnoModal({ anchor: { hash, snippet }, open: true })
  const openThread = (hash: string) => {
    const matched = annotationsByHash.get(hash) || []
    const snippet = matched[0]?.anchor.snippet || ''
    setAnnoModal({ anchor: { hash, snippet }, open: true })
  }

  if (error) return <div style={{ ...S.root, ...S.err }}>加载失败: {error}</div>
  if (!detail) return <div style={{ ...S.root, ...S.empty }}>loading…</div>

  const linkedCount = links?.outgoing.length || 0
  const backlinkCount = links?.backlinks.length || 0
  const unresolvedCount = links?.outgoing_unresolved.length || 0
  const annCount = annotations.length

  const annoOverride = buildAnnotationOverride(annotationsByHash, openAdd, openThread)

  const previewNode = (
    <MarkdownRenderer
      source={mode === 'split' ? draft : detail.content}
      onWikilinkClick={jumpTo}
      componentsOverride={annoOverride}
      currentPath={entity.id}
    />
  )

  const modalExisting = annoModal.anchor
    ? (annotationsByHash.get(annoModal.anchor.hash) || [])
    : []

  // 低频操作收进共享 ⋯ 菜单(刷新批注), 主操作(模式切换 + 保存)留在工具条常显。
  const kebabItems: KebabItem[] = [
    { label: '刷新批注', icon: <RefreshCw size={15} />, testid: 'note-refresh-annotations', onClick: () => loadAnnotations(entity.id) },
  ]

  return (
    <div style={S.root}>
      {/* 无标题头(Linear 风内容优先): 页签已标识笔记身份, 不再重复标题。工具条只留弱化路径 + 模式切换 + 保存。 */}
      <div style={S.toolbar}>
        <div style={S.pathCrumb} title={detail.path}>
          {dirty && <span style={S.dirty} title="未保存的修改">●</span>}
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail.path}</span>
        </div>
        <div style={S.seg} role="tablist" aria-label="查看模式">
          <button style={S.segBtn(mode === 'view')} onClick={() => switchMode('view')} title="只读"><Eye size={14} /> 只读</button>
          <button style={S.segBtn(mode === 'edit')} onClick={() => switchMode('edit')} title="编辑"><Pencil size={14} /> 编辑</button>
          <button style={S.segBtn(mode === 'split')} onClick={() => switchMode('split')} title="分屏"><Columns2 size={14} /> 分屏</button>
        </div>
        <button
          style={dirty && !saving ? S.saveBtn : S.saveBtnDisabled}
          disabled={!dirty || saving}
          onClick={doSave}
          title="Ctrl+S"
        >
          <Save size={14} />{saving ? '保存中…' : '保存'}
        </button>
        {saveMsg && <span style={S.saveMsg(saveMsg.ok)}>{saveMsg.text}</span>}
        <KebabMenu testid="note-actions" items={kebabItems} />
      </div>
      <div style={S.body}>
        {mode === 'view' && <div style={S.main}>{previewNode}</div>}
        {mode === 'edit' && (
          <div style={S.editorWrap}>
            <Suspense fallback={<div style={S.empty}>loading editor…</div>}>
              <MonacoEditor value={draft} language="markdown" theme="vs-dark" onChange={onChange}
                options={{ minimap: { enabled: false }, fontSize: 14, wordWrap: 'on', scrollBeyondLastLine: false }} />
            </Suspense>
          </div>
        )}
        {mode === 'split' && (
          <>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Suspense fallback={<div style={S.empty}>loading editor…</div>}>
                <MonacoEditor value={draft} language="markdown" theme="vs-dark" onChange={onChange}
                  options={{ minimap: { enabled: false }, fontSize: 14, wordWrap: 'on', scrollBeyondLastLine: false }} />
              </Suspense>
            </div>
            <div style={S.splitPreview}>{previewNode}</div>
          </>
        )}
        <VSplitter onResize={(d) => setSideW((w) => Math.max(120, Math.min(600, w - d)))} side="left" />
        <div style={S.sidePanel(sideW)}>
          <div style={S.sideHeader}>批注 · {annCount}{orphanAnnotations.length > 0 ? ` (${orphanAnnotations.length} 失锚)` : ''}</div>
          <div style={S.sideList}>
            {annCount === 0 && <div style={S.emptyMini}>无批注. 鼠标悬停段落右侧 + 添加.</div>}
            {annotations.map((a) => {
              const orphan = !livingHashes.has(a.anchor.hash) && livingHashes.size > 0
              return (
                <div
                  key={a.id}
                  style={S.annItem}
                  onClick={() => openThread(a.anchor.hash)}
                  title={orphan ? '锚点失效 (段落已改/删)' : '点击查看 / 添加新评论'}
                >
                  <div style={{ ...S.annSnippet, color: orphan ? 'var(--fp-err)' : 'var(--fp-text-3)' }}>
                    {orphan && '⚠ '}{a.anchor.snippet}
                  </div>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
                    {a.comment}
                  </div>
                </div>
              )
            })}
          </div>
          <div style={S.sideHeader}>反链 · {backlinkCount}</div>
          <div style={S.sideList}>
            {!links || backlinkCount === 0 ? (
              <div style={S.emptyMini}>{links ? '无反链' : '加载中...'}</div>
            ) : (
              links!.backlinks.map((b) => (
                <span key={b} style={S.link} title={b} onClick={() => jumpTo(b)}>← {b}</span>
              ))
            )}
          </div>
          <div style={S.sideHeader}>外链 · {linkedCount}{unresolvedCount > 0 ? ` (+${unresolvedCount} 未解析)` : ''}</div>
          <div style={S.sideList}>
            {linkedCount === 0 && unresolvedCount === 0 && <div style={S.emptyMini}>无外链</div>}
            {links?.outgoing.map((o) => (
              <span key={o} style={S.link} title={o} onClick={() => jumpTo(o)}>→ {o}</span>
            ))}
            {links?.outgoing_unresolved.map((o, i) => (
              <span key={`u-${i}`} style={S.unresolved} title={`未匹配的目标: ${o}`}>? {o}</span>
            ))}
          </div>
        </div>
      </div>
      <AnnotationModal
        noteId={entity.id}
        open={annoModal.open}
        anchor={annoModal.anchor}
        existing={modalExisting}
        onClose={() => setAnnoModal({ anchor: null, open: false })}
        onChange={() => loadAnnotations(entity.id)}
      />
    </div>
  )
}
