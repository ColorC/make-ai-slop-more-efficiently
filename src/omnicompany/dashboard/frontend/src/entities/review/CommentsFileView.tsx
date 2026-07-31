/**
 * CommentsFileView — 每材料一个评论 markdown 文件的视图(用户 2026-06-13/06-14)。
 * 评论落一个 .md, 一条 = 一个 `## [时间] 作者` 段。
 *
 * 2026-06-30 按 frostpane「面板重做标准」重排(非打补丁):
 * - 撤掉重复页签名的面板标题头(dockview/VSCode 页签已标识材料身份); 内容从顶部直接开始。
 *   只留一条无标题的右对齐工具条: 左=安静计数 meta, 右=刷新(高频常露)+ headerActions + ⋯(收"在 VSCode 打开")。
 * - root 透明吃 body 全局冷渐变; 玻璃卡浮其上。
 * - 逐条玻璃卡解剖: 作者/时间=最弱 12 等宽弱灰、正文=主焦点 14、锚点=冷蓝引用片;
 *   编辑/删除低频操作收进共享 KebabMenu 的 ⋯(删除标 danger), 编辑态才显式露保存/取消。
 * - 颜色全 var(--fp-*) / color-mix, 信息层级靠字号不靠纯加粗。
 * 行为零变化, 数据接线/testid 全保留。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, Pencil, Trash2 } from 'lucide-react'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { createRenderer } from '@wiki-core/render'
import '@wiki-core/ui.css'
import { COLORS } from './shared'
import { openInVscode } from '../../lib/openInVscode'
import { VscodeIcon } from '../../components/VscodeIcon'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'

const md = createRenderer()

const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"
// frostpane 玻璃配方(对齐 theme.css --blur/--glass + rim 高光): 卡=半透深底 + blur + 顶高光 + 11 圆角。
const GLASS = 'var(--fp-glass)'
const BLUR = 'var(--fp-blur)'
const RIM = '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)'

const S: Record<string, React.CSSProperties> = {
  // 无标题头(Linear 风): 仅一条右对齐工具条。左侧安静计数 meta, 右侧操作; 不再放材料名当面板标题。
  bar: {
    flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
  },
  // 计数 = 最弱一档(12 等宽弱灰), 当上下文 meta 不当标题。
  barMeta: { flex: 1, minWidth: 0, fontSize: 12, color: COLORS.processual, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  barActions: { display: 'inline-flex', gap: 6, alignItems: 'center', flexShrink: 0 },
  iconGhost: {
    width: 28, height: 28, border: `1px solid ${COLORS.border}`, borderRadius: 7, background: 'transparent',
    color: COLORS.processual, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0,
    transition: 'border-color 150ms cubic-bezier(0.175,0.885,0.32,1.1), color 150ms',
  },
  // 滚动区: 卡之间 12px 呼吸。
  scroll: { flex: 1, minHeight: 0, overflow: 'auto', padding: '4px 16px 16px', display: 'flex', flexDirection: 'column', gap: 12 },
  // 评论卡: 玻璃解剖。
  card: {
    display: 'flex', flexDirection: 'column', minWidth: 0,
    background: GLASS, backdropFilter: BLUR, WebkitBackdropFilter: BLUR,
    border: `1px solid ${COLORS.border}`, borderRadius: 11, padding: 14,
    boxShadow: RIM,
    transition: 'border-color 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  },
  cardTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
  // 作者/时间 = 最弱一档(12 弱灰等宽), 靠字号弱化不靠缩字。
  cardMeta: { flex: 1, minWidth: 0, fontSize: 12, color: COLORS.processual, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  // 锚点 = 冷蓝引用片, 区别于正文。
  anchor: {
    fontSize: 13, color: COLORS.borderActive, marginTop: 10,
    padding: '6px 10px', borderRadius: 7, background: 'var(--fp-accent-weak)',
    borderLeft: `2px solid ${COLORS.borderActive}`, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  },
  // 正文 = 主焦点, 14px。
  body: { fontSize: 14, color: COLORS.text, marginTop: 10, lineHeight: 1.5 },
  editArea: {
    width: '100%', boxSizing: 'border-box', minHeight: 88, marginTop: 10, padding: 10,
    background: 'var(--fp-surface)', color: COLORS.text, border: `1px solid ${COLORS.border}`, borderRadius: 7,
    fontSize: 14, fontFamily: 'inherit', resize: 'vertical',
  },
  editFoot: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 },
  savePrimary: { padding: '5px 14px', background: COLORS.borderActive, color: 'var(--fp-accent-fg)', border: 0, borderRadius: 7, cursor: 'pointer', fontSize: 13, fontWeight: 600 },
  cancelGhost: { padding: '5px 12px', background: 'transparent', color: COLORS.textDim, border: `1px solid ${COLORS.border}`, borderRadius: 7, cursor: 'pointer', fontSize: 13 },
  empty: { color: COLORS.textDim, fontSize: 13, padding: 18, border: `1px dashed ${COLORS.border}`, borderRadius: 11, textAlign: 'center' },
  // 追加区(composer): 底部薄玻璃, 主操作显眼。
  composer: {
    borderTop: `1px solid ${COLORS.border}`, padding: 16, display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0,
    background: GLASS, backdropFilter: BLUR, WebkitBackdropFilter: BLUR,
  },
  composerAnchor: {
    fontSize: 13, color: COLORS.borderActive, display: 'flex', alignItems: 'center', gap: 8,
    padding: '6px 10px', borderRadius: 7, background: 'var(--fp-accent-weak)', borderLeft: `2px solid ${COLORS.borderActive}`,
  },
  composerAnchorText: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  composerArea: {
    width: '100%', boxSizing: 'border-box', minHeight: 76, padding: 12,
    background: 'var(--fp-surface)', color: COLORS.text, border: `1px solid ${COLORS.border}`, borderRadius: 9,
    fontSize: 14, fontFamily: 'inherit', resize: 'vertical',
  },
  composerFoot: { display: 'flex', justifyContent: 'flex-end' },
}

interface Block { headerLine: string; anchorLine: string | null; body: string; meta: string }

function parseBlocks(text: string): Block[] {
  const out: Block[] = []
  let cur: { headerLine: string; anchorLine: string | null; bodyLines: string[] } | null = null
  for (const line of (text || '').split('\n')) {
    if (line.startsWith('## ')) {
      if (cur) out.push(finalize(cur))
      cur = { headerLine: line, anchorLine: null, bodyLines: [] }
    } else if (cur) {
      if (cur.bodyLines.length === 0 && cur.anchorLine === null && line.startsWith('> 锚点')) cur.anchorLine = line
      else cur.bodyLines.push(line)
    }
    // 首个 ## 之前的内容(历史预置说明等)直接丢弃 —— 文件即纯评论。
  }
  if (cur) out.push(finalize(cur))
  return out
}
function finalize(c: { headerLine: string; anchorLine: string | null; bodyLines: string[] }): Block {
  return { headerLine: c.headerLine, anchorLine: c.anchorLine, body: c.bodyLines.join('\n').trim(), meta: c.headerLine.replace(/^##\s*/, '') }
}
function blocksToFile(blocks: Block[]): string {
  return blocks.map((b) => `${b.headerLine}\n${b.anchorLine ? b.anchorLine + '\n' : ''}${b.body}\n`).join('\n').trim()
}

export function CommentsFileView({ material, pendingAnchor, clearPendingAnchor, headerActions }: {
  material: Material
  pendingAnchor?: string | null
  clearPendingAnchor?: () => void
  /** 保留(API 兼容): 面板身份已由页签标识, 标题不再在视图内重复展示。 */
  title?: string
  headerActions?: React.ReactNode
}) {
  const [content, setContent] = useState('')
  const [absPath, setAbsPath] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')
  const [editIdx, setEditIdx] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')

  const load = useCallback(() => {
    reviewstageApi.getCommentsFile(material.id, material.title)
      .then((r) => { setContent(r.content); setAbsPath(r.abs_path) })
      .catch(() => { /* 静默 */ })
  }, [material.id, material.title])
  useEffect(() => { load() }, [load])

  const blocks = useMemo(() => parseBlocks(content), [content])
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(''), 1000) }

  const append = async () => {
    const text = draft.trim()
    if (!text) return
    setBusy(true)
    try {
      const r = await reviewstageApi.appendCommentsFile(material.id, text, pendingAnchor || undefined, material.title)
      setContent(r.content); setDraft(''); clearPendingAnchor?.()
    } catch (e) { flash(`追加失败: ${String(e instanceof Error ? e.message : e)}`) } finally { setBusy(false) }
  }

  const saveFile = async (next: Block[]) => {
    setBusy(true)
    try { const r = await reviewstageApi.writeCommentsFile(material.id, blocksToFile(next)); setContent(r.content) }
    catch (e) { flash(`保存失败: ${String(e instanceof Error ? e.message : e)}`) } finally { setBusy(false) }
  }
  const saveEdit = async (i: number) => {
    const next = blocks.map((b, idx) => idx === i ? { ...b, body: editDraft.trim() } : b)
    setEditIdx(null); setEditDraft('')
    await saveFile(next)
  }
  const remove = async (i: number) => {
    if (typeof window !== 'undefined' && !window.confirm('删除这条评论?')) return
    await saveFile(blocks.filter((_, idx) => idx !== i))
  }

  return (
    <div data-testid="material-comments-file" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: 'transparent', color: COLORS.text }}>
      {/* 无标题头: 右对齐工具条。左=安静计数 meta(非标题), 右=刷新(高频常露)+ headerActions + ⋯(收"在 VSCode 打开") */}
      <div style={S.bar}>
        <span style={S.barMeta} data-testid="comments-count">{blocks.length} 条评论{toast ? ` · ${toast}` : ''}</span>
        <div style={S.barActions}>
          <button
            type="button" style={S.iconGhost} data-testid="comments-refresh" title="刷新" onClick={load}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = COLORS.borderActive; e.currentTarget.style.color = COLORS.text }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = COLORS.border; e.currentTarget.style.color = COLORS.processual }}
          ><RefreshCw size={14} /></button>
          <KebabMenu
            testid="comments-file-more"
            items={[
              { label: '在 VSCode 打开 .md', icon: <VscodeIcon size={15} />, testid: 'comments-open-vscode', onClick: () => openInVscode(absPath) },
            ] as KebabItem[]}
          />
          {headerActions}
        </div>
      </div>

      <div style={S.scroll}>
        {blocks.length === 0 && <div style={S.empty} data-testid="comments-empty">还没有评论。在下面写第一条。</div>}
        {blocks.map((b, i) => {
          const editing = editIdx === i
          return (
            <div key={i} data-testid="comment-block" style={S.card}>
              <div style={S.cardTop}>
                <span style={S.cardMeta}>{b.meta}</span>
                {editing
                  ? null
                  : <KebabMenu
                      testid="comment-more"
                      items={[
                        { label: '编辑', icon: <Pencil size={15} />, testid: 'comment-edit', onClick: () => { setEditIdx(i); setEditDraft(b.body) } },
                        { label: '删除', icon: <Trash2 size={15} />, testid: 'comment-delete', danger: true, disabled: busy, onClick: () => { void remove(i) } },
                      ] as KebabItem[]}
                    />}
              </div>
              {b.anchorLine && <div style={S.anchor} title={b.anchorLine.replace(/^>\s*/, '')}>{b.anchorLine.replace(/^>\s*/, '')}</div>}
              {editing
                ? <>
                    <textarea value={editDraft} onChange={(e) => setEditDraft(e.target.value)} data-testid="comment-edit-input" style={S.editArea} />
                    <div style={S.editFoot}>
                      <button type="button" style={S.cancelGhost} onClick={() => { setEditIdx(null); setEditDraft('') }}>取消</button>
                      <button type="button" style={S.savePrimary} data-testid="comment-edit-save" onClick={() => void saveEdit(i)} disabled={busy}>保存</button>
                    </div>
                  </>
                : <div className="wiki-prose" style={S.body} dangerouslySetInnerHTML={{ __html: md.render(b.body || '') }} />}
            </div>
          )
        })}
      </div>

      <div style={S.composer}>
        {pendingAnchor && (
          <div style={S.composerAnchor}>
            <span style={S.composerAnchorText}>锚点(选中文本): {pendingAnchor.slice(0, 90)}</span>
            <button type="button" style={S.cancelGhost} onClick={clearPendingAnchor}>清除</button>
          </div>
        )}
        <textarea
          data-testid="comment-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="追加一条评论(支持 markdown)…"
          style={S.composerArea}
        />
        <div style={S.composerFoot}>
          <button type="button" data-testid="comment-submit" onClick={() => void append()} disabled={!draft.trim() || busy}
            style={{ padding: '8px 20px', background: draft.trim() ? COLORS.borderActive : COLORS.border, color: 'var(--fp-accent-fg)', border: 0, borderRadius: 9, cursor: draft.trim() ? 'pointer' : 'default', fontSize: 14, fontWeight: 600, opacity: draft.trim() ? 1 : 0.6, transition: 'background 150ms, opacity 150ms' }}>
            {busy ? '…' : '追加评论'}
          </button>
        </div>
      </div>
    </div>
  )
}
