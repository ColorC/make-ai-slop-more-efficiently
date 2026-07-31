import React, { useEffect, useRef, useState } from 'react'
import type { Entity, EntityType } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { Save, Trash2, Copy, FolderInput, ArrowUpRight, ArrowLeft } from 'lucide-react'
import { usePanels } from '../../stores/panelsStore'
import { authoredApi, type AuthoredNote } from '../../api/authoredClient'
import { relTimeZh } from '../../lib/time'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import Composer from './Composer'

// 统一札记(评论·草稿·决策)集中管理面。一个 tab 看全部自撰内容: 列表+筛选+搜索+跳转+编辑+删除。
export interface AuthoredEntity extends Entity {
  type: 'authored'
}

const SINGLE: AuthoredEntity = {
  type: 'authored',
  id: 'main',
  title: '札记',
  tags: ['boss-sight', 'authored'],
}

const resolver: EntityResolver<AuthoredEntity> = {
  type: 'authored',
  async fetch(id) {
    if (id === 'main') return SINGLE
    return { ...SINGLE, id }
  },
  async list() { return [SINGLE] },
}

const TARGET_KIND_LABEL: Record<string, string> = {
  material: '审阅材料', project: '项目', plan: '计划',
  llm_session: '对话', page_element: '页面元素', new_object: '新建对象',
}
const USES_LABEL: Record<string, string> = { comment: '评论', draft: '草稿', llm_input: 'LLM输入' }
const STATUS_LABEL: Record<string, string> = {
  saved: '已保存', delivered: '已发送', read: '已读', to_todo: '待办', todo_done: '已办',
}

function targetSummary(t: AuthoredNote['target']): string {
  if (!t || !t.kind) return '(无关联)'
  const k = TARGET_KIND_LABEL[t.kind] || t.kind
  const sub = t.sub_kind ? ` · ${t.sub_kind}${t.sub_id ? ':' + t.sub_id : ''}` : ''
  const id = t.id ? ` ${String(t.id).slice(0, 28)}` : ''
  return `${k}${id}${sub}`
}

function jumpRefFor(t: AuthoredNote['target']): { type: EntityType; id: string } | { url: string } | null {
  if (!t || !t.kind) return null
  if (t.kind === 'material' && t.id) return { type: 'review_material', id: String(t.id) }
  if (t.kind === 'plan' && t.id) return { type: 'plan', id: String(t.id) }
  if (t.kind === 'project' && t.id) return { type: 'project', id: String(t.id) }
  if (t.kind === 'llm_session' && t.id) return { type: 'cc_session', id: String(t.id) }
  if (t.url) return { url: String(t.url) }
  return null
}

const Panel: React.FC<{ entity: AuthoredEntity }> = () => {
  const openTab = usePanels((s) => s.openTab)
  const [items, setItems] = useState<AuthoredNote[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [fKind, setFKind] = useState('')
  const [fProject, setFProject] = useState('')
  const [fUses, setFUses] = useState('')
  const [q, setQ] = useState('')
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState('')
  const [title, setTitle] = useState('')
  const [composing, setComposing] = useState(false)

  // 窄容器自适应: 挂进 vscode 原生侧栏(~300px)时左 340 列表 + 右编辑列并排放不下,
  // 右编辑列被压成≈0 → "点新建/点条目看不见在编辑". 窄态切单列抽屉式(列表 ↔ 编辑切换).
  const rootRef = useRef<HTMLDivElement>(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const el = rootRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setNarrow(e.contentRect.width < 560)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const load = () => {
    setLoading(true)
    authoredApi.list({ target: fKind || undefined, project: fProject || undefined, uses: fUses || undefined, q: q || undefined })
      .then((r) => {
        setItems(r.items || [])
        setSelectedId((prev) => (prev && r.items.some((n) => n.id === prev)) ? prev : (r.items[0]?.id || null))
      })
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }
  useEffect(load, [fKind, fProject, fUses, q]) // eslint-disable-line react-hooks/exhaustive-deps

  const selected = items.find((n) => n.id === selectedId) || null
  useEffect(() => { setDraft(selected?.content || ''); setStatus(selected?.feedback_status || ''); setTitle(selected?.title || '') }, [selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  const projects = Array.from(new Set(items.map((n) => n.project_id).filter(Boolean)))
  const showEditor = composing || !!selected  // 窄态: 决定显示列表还是编辑抽屉

  const onJump = (n: AuthoredNote) => {
    const ref = jumpRefFor(n.target)
    if (!ref) return
    if ('url' in ref) { window.location.href = ref.url; return }
    openTab(ref, targetSummary(n.target))
  }
  const onSave = () => {
    if (!selected) return
    authoredApi.update(selected.id, { content: draft, feedback_status: status || undefined, title }).then(load)
  }
  const onDelete = () => {
    if (!selected || !window.confirm('删除这条札记？(软归档)')) return
    authoredApi.remove(selected.id).then(load)
  }
  const onCopyPath = () => { if (selected?.json_path) navigator.clipboard?.writeText(selected.json_path) }
  const onExportDraft = async () => {
    if (!selected) return
    const t: any = selected.target || {}
    const fromTarget = (t.new_object && t.new_object.dest_dir) || t.dest_dir || ''
    const dest = window.prompt('导出草稿成品到哪个项目目录?(绝对路径, 或相对 WindowsWorkspace)', fromTarget)
    if (!dest) return
    try {
      const r = await authoredApi.exportDraft(selected.id, { dest_dir: dest })
      window.alert('已导出成品到:\n' + r.exported_path); load()
    } catch (e) {
      if (String(e).includes('409') && window.confirm('文件已存在, 覆盖?')) {
        try {
          const r = await authoredApi.exportDraft(selected.id, { dest_dir: dest, overwrite: true })
          window.alert('已覆盖导出:\n' + r.exported_path); load()
        } catch (e2) { window.alert('导出失败: ' + e2) }
      } else { window.alert('导出失败: ' + e) }
    }
  }

  // 字号层级靠 token(标题 17 / 正文 15 / 最弱 13 等宽弱灰, 禁 11)。颜色全 var(--fp-*)。
  const sel: React.CSSProperties = { flex: 1, minWidth: 0, background: 'var(--fp-card)', color: 'var(--fp-text)', border: '1px solid var(--fp-border)', borderRadius: 6, padding: '6px 8px', fontSize: 13 }
  const primary: React.CSSProperties = { background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', border: '1px solid var(--fp-accent)', borderRadius: 7, padding: '7px 16px', fontSize: 14, fontWeight: 600, cursor: 'pointer' }

  // 低频操作收进共享 KebabMenu(⋯): 导出成品 / 复制路径 / 删除(危险)。主操作「保存」常显显眼。
  const kebabItems: KebabItem[] = selected ? [
    ...((selected.uses || []).includes('draft')
      ? [{ label: '导出成品到目录', icon: <FolderInput size={15} />, testid: 'authored-export-draft', onClick: () => { void onExportDraft() } }]
      : []),
    { label: '复制文件位置', icon: <Copy size={15} />, testid: 'authored-copy-path', onClick: onCopyPath },
    { label: '删除', icon: <Trash2 size={15} />, testid: 'authored-delete', danger: true, onClick: onDelete },
  ] : []

  return (
    // root 透明: 吃 body 全局冷渐变, 玻璃浮其上(不再铺实底把渐变顶掉)。
    <div ref={rootRef} style={{ display: 'flex', flexDirection: narrow ? 'column' : 'row', height: '100%', fontSize: 15, background: 'transparent', color: 'var(--fp-text)' }}>
      {/* 左: 新建 + 筛选 + 列表(玻璃外壳)。窄态占满宽; 进编辑/新建抽屉时隐藏。 */}
      <div style={{
        width: narrow ? '100%' : 340,
        minWidth: narrow ? 0 : 280,
        ...(narrow ? { borderBottom: '1px solid var(--fp-border)' } : { borderRight: '1px solid var(--fp-border)' }),
        display: narrow && showEditor ? 'none' : 'flex',
        flex: narrow ? 1 : undefined,
        minHeight: 0,
        flexDirection: 'column',
        background: 'var(--fp-glass)',
        backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
      }}>
        <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8, borderBottom: '1px solid var(--fp-border)' }}>
          <button type="button" data-testid="authored-new" onClick={() => { setComposing(true); setSelectedId(null) }} style={{ ...primary, padding: '9px 12px' }}>＋ 新建草稿</button>
          <input placeholder="搜索内容 / 对象 / 项目…" value={q} onChange={(e) => setQ(e.target.value)}
                 style={{ padding: '7px 10px', borderRadius: 6, border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-text)', fontSize: 15, outline: 'none' }} />
          <div style={{ display: 'flex', gap: 6 }}>
            <select value={fKind} onChange={(e) => setFKind(e.target.value)} style={sel}>
              <option value="">全部对象</option>
              {Object.entries(TARGET_KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select value={fProject} onChange={(e) => setFProject(e.target.value)} style={sel}>
              <option value="">全部项目</option>
              {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <select value={fUses} onChange={(e) => setFUses(e.target.value)} style={sel}>
              <option value="">全部用途</option>
              {Object.entries(USES_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div style={{ color: 'var(--fp-text-3)', fontSize: 13 }}>{loading ? '加载中…' : `${items.length} 条`}</div>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {items.map((n) => {
            const active = n.id === selectedId && !composing
            return (
              <button key={n.id} onClick={() => { setComposing(false); setSelectedId(n.id) }}
                style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px', border: 'none',
                         borderLeft: `2px solid ${active ? 'var(--fp-accent)' : 'transparent'}`,
                         borderBottom: '1px solid var(--fp-border-subtle)', cursor: 'pointer',
                         background: active ? 'var(--fp-accent-weak)' : 'transparent', color: 'var(--fp-text)' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <div style={{ flex: 1, fontWeight: 600, fontSize: 15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {n.title?.trim() || n.content.slice(0, 46) || '(空)'}
                  </div>
                  {relTimeZh(n.updated_at || n.created_at) && (
                    <span style={{ flexShrink: 0, color: 'var(--fp-text-3)', fontSize: 13 }}>{relTimeZh(n.updated_at || n.created_at)}</span>
                  )}
                </div>
                <div style={{ color: 'var(--fp-text-2)', fontSize: 13, marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {targetSummary(n.target)} · {STATUS_LABEL[n.feedback_status] || n.feedback_status}
                </div>
              </button>
            )
          })}
          {!items.length && !loading && <div style={{ padding: 18, color: 'var(--fp-text-3)', fontSize: 13 }}>没有匹配的草稿。</div>}
        </div>
      </div>

      {/* 右: 新建 / 文档式编辑(占满宽高); 窄态作抽屉, 仅编辑/新建时显示, 顶部加返回 */}
      <div style={{ flex: 1, minWidth: 0, display: narrow && !showEditor ? 'none' : 'flex', flexDirection: 'column', overflow: 'hidden', background: 'transparent' }}>
        {narrow && showEditor && (
          <button type="button" data-testid="authored-narrow-back"
            onClick={() => { setComposing(false); setSelectedId(null) }}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px', border: 'none', borderBottom: '1px solid var(--fp-border)', background: 'transparent', color: 'var(--fp-accent)', cursor: 'pointer', fontSize: 15, textAlign: 'left', flexShrink: 0 }}>
            <ArrowLeft size={15} /> 返回列表
          </button>
        )}
        {composing ? (
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: '18px 28px 22px' }}>
            <div style={{ fontWeight: 650, marginBottom: 14, fontSize: 18, letterSpacing: '-0.01em' }}>新建草稿</div>
            <Composer
              uses={['draft']}
              fill
              autoFocus
              placeholder="随手写。纯文本 + 拖图即可,格式化和正式化交给 AI。可不挂对象(自由草稿)。"
              onSaved={() => { setComposing(false); load() }}
              onCancel={() => setComposing(false)}
            />
          </div>
        ) : !selected ? (
          <div style={{ color: 'var(--fp-text-3)', fontSize: 15, margin: 'auto' }}>
            选一条草稿查看 / 编辑,或点左上「＋ 新建草稿」。
          </div>
        ) : (
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12, padding: '16px 28px 18px' }}>
              {/* 上下文(针对谁) */}
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', color: 'var(--fp-text-2)', fontSize: 13 }}>
                <span>针对 <span style={{ color: 'var(--fp-text)' }}>{targetSummary(selected.target)}</span></span>
                {jumpRefFor(selected.target) && (
                  <button onClick={() => onJump(selected)} title="跳到该对象"
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'transparent', color: 'var(--fp-link)', border: 'none', cursor: 'pointer', fontSize: 13 }}>
                    <ArrowUpRight size={14} /> 跳转
                  </button>
                )}
                {selected.project_id && selected.project_id !== 'unfiled' && <span>· 项目 {selected.project_id}</span>}
                {(selected.uses || []).map((u) => <span key={u} style={{ background: 'rgba(255,255,255,.06)', border: '1px solid var(--fp-border)', padding: '2px 8px', borderRadius: 999, color: 'var(--fp-text-2)' }}>{USES_LABEL[u] || u}</span>)}
              </div>

              {/* 标题(可重命名): 列表与页签按它显示; 留空回退正文首行。改完点「保存」生效。 */}
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'Enter')) { e.preventDefault(); onSave() } }}
                placeholder={(selected.content.slice(0, 46) || '给这条起个名字') + '（标题可选，留空用正文首行）'}
                data-testid="authored-title-input"
                style={{ width: '100%', boxSizing: 'border-box', background: 'var(--fp-bg-doc)', color: 'var(--fp-text)', border: '1px solid var(--fp-border)', borderRadius: 6, padding: '8px 12px', fontSize: 17, fontWeight: 600, outline: 'none' }}
              />

              {/* 编辑器外壳 = 玻璃卡: 顶部工具栏(主操作显眼 + ⋯ 收低频) + 干净正文(占满剩余高度) */}
              <div style={{ flex: 1, minHeight: 0, border: '1px solid var(--fp-border)', borderRadius: 11, background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px', borderBottom: '1px solid var(--fp-border-subtle)' }}>
                  <div style={{ flex: 1 }} />
                  <select value={status} onChange={(e) => setStatus(e.target.value)} title="反馈状态"
                    style={{ background: 'var(--fp-card)', color: 'var(--fp-text)', border: '1px solid var(--fp-border)', borderRadius: 6, padding: '4px 6px', fontSize: 13, height: 30 }}>
                    {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                  <button onClick={onSave} title="保存(Ctrl/⌘+S)" data-testid="authored-save"
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 30, padding: '0 14px', background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', border: 'none', borderRadius: 7, fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
                    <Save size={15} /> 保存
                  </button>
                  <KebabMenu items={kebabItems} testid="authored-more" iconSize={18} />
                </div>
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false}
                  onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'Enter')) { e.preventDefault(); onSave() } }}
                  style={{ flex: 1, minHeight: 0, width: '100%', boxSizing: 'border-box', padding: '16px 18px', border: 'none', background: 'transparent', color: 'var(--fp-text)', fontFamily: 'inherit', fontSize: 16, lineHeight: 1.6, resize: 'none', outline: 'none' }} />
                {(selected.captures || []).length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '0 16px 14px' }}>
                    {selected.captures!.map((c) => (
                      <img key={c} src={`/api/boss-sight/captures/file?path=${encodeURIComponent(c)}`} alt="" style={{ maxHeight: 140, borderRadius: 7, border: '1px solid var(--fp-border)' }} />
                    ))}
                  </div>
                )}
              </div>
              {(selected.extra as any)?.exported_to && (
                <div style={{ color: 'var(--fp-ok)', fontSize: 13 }}>已导出成品 → {(selected.extra as any).exported_to}</div>
              )}
              <div style={{ color: 'var(--fp-text-3)', fontSize: 13 }}>作者 {selected.author} · 创建 {selected.created_at?.slice(0, 19).replace('T', ' ')}</div>
            </div>
          )}
      </div>
    </div>
  )
}

// 2026-06-28: 看板「笔记」面板 = overlay-shell BlockSuite 笔记(经 8210 同源 /lofa/overlay/app/)。
// 文字札记全面淘汰; 旧 Panel 不再用作面板, 但评审评论/草稿/决策仍走共用 authored 后端(Composer/NotesForTarget)。
const OverlayNotesPanel: React.FC<{ entity: AuthoredEntity }> = () => (
  <iframe
    src="/lofa/overlay/app/"
    title="overlay-shell 笔记"
    style={{ width: '100%', height: '100%', border: 0, background: 'transparent' }}
  />
)

export const authoredRegistration: EntityRegistration<AuthoredEntity> = {
  resolver,
  renderer: { type: 'authored', Editor: OverlayNotesPanel },
  label: '笔记',
  icon: '✎',
}
