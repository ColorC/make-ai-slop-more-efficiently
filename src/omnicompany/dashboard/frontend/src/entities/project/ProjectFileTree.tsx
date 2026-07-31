// 项目详情页「文件」页签 — 真目录树(懒加载, 每次展开取一层)。
// 2026-07-06 用户: 原来的 roots 快捷方式列表作用不对, 应当是文件目录树; 可勾选"展示所有目录"
// (全部注册项目的 roots)但突出显示本项目相关; 支持在 VSCode 打开、复制路径; 文件/目录在
// omnicompany 里有 material 身份或 [OMNI] 功能注释时旁边显示概要, 单击选中开详情面板。
// 后端 controlplane/project_fs.py(/api/projects/{id}/fs 与 /fs/detail)。

import React, { useCallback, useEffect, useState } from 'react'
import { ChevronRight, Copy, ExternalLink, File, Folder, X } from 'lucide-react'
import { copyText } from '../../lib/copyText'
import { openInVscode } from '../../lib/openInVscode'
import { GLASS, MONO, dimStyle } from './cards'

interface FsNode {
  name: string
  path: string
  dir: boolean
  related: boolean
  note?: string | null
  projects?: string[]
  material?: { id: string; title: string; kind: string; status?: string | null }
  omni?: { summary: string; type: string; status: string }
}
interface FsDetail {
  path: string
  dir: boolean
  size?: number
  mtime?: string
  omni?: { origin: string; ts: string; type: string; status: string; domain: string; summary: string; why: string; tags: string[]; material_id?: string | null }
  material?: { id: string; title: string; kind: string; status?: string | null }
  instances?: { entity_id: string; type: string; name: string; package: string }[]
}

const ROW_H = 26

function fmtSize(n?: number): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 行内小图标按钮(默认半透明, hover 提亮) — 树行右侧的"VSCode/复制路径"。 */
function RowBtn({ title, onClick, children, testid }: { title: string; onClick: () => void; children: React.ReactNode; testid?: string }) {
  return (
    <button
      type="button" title={title} data-testid={testid}
      style={{ border: 'none', background: 'transparent', color: 'var(--fp-text-3)', opacity: 0.55, cursor: 'pointer', padding: '2px 4px', display: 'inline-flex', alignItems: 'center', borderRadius: 5 }}
      onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.color = 'var(--fp-text)' }}
      onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.55'; e.currentTarget.style.color = 'var(--fp-text-3)' }}
      onClick={(e) => { e.stopPropagation(); onClick() }}
    >{children}</button>
  )
}

export default function ProjectFileTree({ projectId, onOpenMaterial }: {
  projectId: string
  onOpenMaterial: (id: string, title: string) => void
}) {
  const [allMode, setAllMode] = useState(false)
  const [roots, setRoots] = useState<FsNode[] | null>(null)
  const [rootsError, setRootsError] = useState<string | null>(null)
  const [children, setChildren] = useState<Record<string, FsNode[] | 'loading' | 'error'>>({})
  const [truncated, setTruncated] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<FsNode | null>(null)
  const [detail, setDetail] = useState<FsDetail | 'loading' | null>(null)

  useEffect(() => {
    setRoots(null)
    setRootsError(null)
    setChildren({})
    setExpanded(new Set())
    fetch(`/api/projects/${encodeURIComponent(projectId)}/fs${allMode ? '?all=1' : ''}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d) => setRoots((d.roots as FsNode[]) || []))
      .catch((e) => setRootsError(String(e?.message || e)))
  }, [projectId, allMode])

  const loadChildren = useCallback((path: string) => {
    setChildren((m) => ({ ...m, [path]: 'loading' }))
    fetch(`/api/projects/${encodeURIComponent(projectId)}/fs?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d) => {
        setChildren((m) => ({ ...m, [path]: (d.items as FsNode[]) || [] }))
        if (d.truncated) setTruncated((t) => ({ ...t, [path]: true }))
      })
      .catch(() => setChildren((m) => ({ ...m, [path]: 'error' })))
  }, [projectId])

  const toggle = (node: FsNode) => {
    if (!node.dir) return
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(node.path)) next.delete(node.path)
      else {
        next.add(node.path)
        if (children[node.path] == null) loadChildren(node.path)
      }
      return next
    })
  }

  const select = (node: FsNode) => {
    setSelected(node)
    setDetail('loading')
    fetch(`/api/projects/${encodeURIComponent(projectId)}/fs/detail?path=${encodeURIComponent(node.path)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d) => setDetail(d as FsDetail))
      .catch(() => setDetail(null))
  }

  const renderNode = (node: FsNode, depth: number): React.ReactNode => {
    const isOpen = node.dir && expanded.has(node.path)
    const kids = children[node.path]
    const isSelected = selected?.path === node.path
    const annot = node.material
      ? `material · ${node.material.title || node.material.id}`
      : node.omni?.summary || ''
    return (
      <React.Fragment key={node.path}>
        <div
          role="treeitem" aria-expanded={node.dir ? isOpen : undefined}
          style={{
            display: 'flex', alignItems: 'center', gap: 4, minHeight: ROW_H,
            paddingLeft: 6 + depth * 16, paddingRight: 6, cursor: 'pointer', borderRadius: 6,
            background: isSelected ? 'color-mix(in srgb, var(--fp-accent) 14%, transparent)' : 'transparent',
          }}
          onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = 'var(--fp-surface)' }}
          onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}
          onClick={() => select(node)}
        >
          <span
            style={{ width: 16, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fp-text-3)' }}
            onClick={(e) => { e.stopPropagation(); toggle(node) }}
            data-testid={node.dir ? 'fs-toggle' : undefined}
          >
            {node.dir && <ChevronRight size={13} style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />}
          </span>
          {node.dir
            ? <Folder size={13} style={{ flexShrink: 0, color: node.related ? 'var(--fp-link)' : 'var(--fp-text-3)' }} />
            : <File size={13} style={{ flexShrink: 0, color: 'var(--fp-text-3)' }} />}
          <span style={{
            fontSize: 13, whiteSpace: 'nowrap',
            color: node.related ? 'var(--fp-text)' : 'var(--fp-text-2)',
            fontWeight: node.related ? 600 : 400,
          }}>{node.name}</span>
          {node.related && depth === 0 && (
            <span style={{ flexShrink: 0, fontSize: 11, color: 'var(--fp-link)', border: '1px solid var(--fp-border)', borderRadius: 999, padding: '0 7px', background: 'var(--fp-accent-weak)' }}>本项目</span>
          )}
          {node.note && <span style={{ flexShrink: 0, fontSize: 11, color: 'var(--fp-text-3)' }} title={node.note}>{node.note}</span>}
          {allMode && depth === 0 && !node.related && node.projects && node.projects.length > 0 && (
            <span style={{ flexShrink: 0, fontSize: 11, color: 'var(--fp-text-3)', fontFamily: MONO }}>{node.projects.join(' ')}</span>
          )}
          {annot && (
            <span style={{ marginLeft: 6, fontSize: 12, color: node.material ? 'var(--fp-link)' : 'var(--fp-text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0, flexShrink: 1 }} title={annot}>
              {annot}
            </span>
          )}
          <span style={{ flex: 1 }} />
          <RowBtn title={`在 VSCode 打开\n${node.path}`} onClick={() => openInVscode(node.path)} testid="fs-open-vscode"><ExternalLink size={12} /></RowBtn>
          <RowBtn title="复制路径" onClick={() => { void copyText(node.path) }}><Copy size={12} /></RowBtn>
        </div>
        {isOpen && kids === 'loading' && (
          <div style={{ paddingLeft: 6 + (depth + 1) * 16, color: 'var(--fp-text-3)', fontSize: 12, minHeight: ROW_H, display: 'flex', alignItems: 'center' }}>加载中…</div>
        )}
        {isOpen && kids === 'error' && (
          <div style={{ paddingLeft: 6 + (depth + 1) * 16, color: 'var(--fp-warn)', fontSize: 12, minHeight: ROW_H, display: 'flex', alignItems: 'center' }}>读目录失败</div>
        )}
        {isOpen && Array.isArray(kids) && kids.map((k) => renderNode(k, depth + 1))}
        {isOpen && Array.isArray(kids) && kids.length === 0 && (
          <div style={{ paddingLeft: 6 + (depth + 1) * 16, color: 'var(--fp-text-3)', fontSize: 12, minHeight: ROW_H, display: 'flex', alignItems: 'center' }}>(空目录)</div>
        )}
        {isOpen && truncated[node.path] && (
          <div style={{ paddingLeft: 6 + (depth + 1) * 16, color: 'var(--fp-warn)', fontSize: 12 }}>条目过多, 只显示前 500 项</div>
        )}
      </React.Fragment>
    )
  }

  const d = detail !== 'loading' ? detail : null
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
      <div style={{ ...GLASS, flex: 1, minWidth: 0, padding: '10px 8px', maxHeight: '72vh', overflow: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '2px 6px 10px', flexWrap: 'wrap' }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--fp-text-2)', cursor: 'pointer' }}>
            <input type="checkbox" checked={allMode} onChange={(e) => setAllMode(e.target.checked)} data-testid="fs-all-toggle" />
            展示所有目录(全部注册项目)
          </label>
          <span style={{ fontSize: 12, color: 'var(--fp-text-3)' }}>
            {allMode ? '高亮为本项目相关目录' : '仅本项目 index/注册表登记的目录'} · 单击看详情 · 旁注 = material 身份 / [OMNI] 注释概要
          </span>
        </div>
        {rootsError && <div style={dimStyle}>目录树加载失败: {rootsError}</div>}
        {!rootsError && roots === null && <div style={dimStyle}>加载中…</div>}
        {roots !== null && roots.length === 0 && <div style={dimStyle}>本项目没有登记任何目录(index 的 roots)</div>}
        <div role="tree">{(roots || []).map((r) => renderNode(r, 0))}</div>
      </div>

      {selected && (
        <div style={{ ...GLASS, width: 400, flexShrink: 0, padding: 14, maxHeight: '72vh', overflow: 'auto' }} data-testid="fs-detail-panel">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {selected.dir ? <Folder size={15} style={{ color: 'var(--fp-link)' }} /> : <File size={15} style={{ color: 'var(--fp-text-2)' }} />}
            <span style={{ flex: 1, minWidth: 0, fontSize: 15, fontWeight: 650, color: 'var(--fp-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={selected.name}>{selected.name}</span>
            <RowBtn title="关闭" onClick={() => { setSelected(null); setDetail(null) }}><X size={14} /></RowBtn>
          </div>
          <div style={{ marginTop: 8, fontSize: 12, fontFamily: MONO, color: 'var(--fp-text-3)', wordBreak: 'break-all' }}>{selected.path}</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button type="button" data-testid="open-in-vscode"
              style={{ flex: 1, border: '1px solid var(--fp-border)', background: 'color-mix(in srgb, var(--fp-accent) 10%, transparent)', color: 'var(--fp-link)', borderRadius: 7, padding: '7px 0', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
              onClick={() => openInVscode(selected.path)}>在 VSCode 打开</button>
            <button type="button"
              style={{ flex: 1, border: '1px solid var(--fp-border)', background: 'var(--fp-surface)', color: 'var(--fp-text-2)', borderRadius: 7, padding: '7px 0', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
              onClick={() => { void copyText(selected.path) }}>复制路径</button>
          </div>

          {detail === 'loading' && <div style={{ ...dimStyle, padding: 14 }}>详情加载中…</div>}
          {d && (
            <>
              <div style={{ marginTop: 10, fontSize: 12, color: 'var(--fp-text-3)', fontFamily: MONO }}>
                {d.dir ? '目录' : fmtSize(d.size)}{d.mtime ? ` · ${d.mtime}` : ''}
              </div>
              {d.material && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--fp-link)' }}>material 身份</div>
                  <div style={{ marginTop: 6, fontSize: 13, color: 'var(--fp-text)' }}>{d.material.title || d.material.id}</div>
                  <div style={{ marginTop: 4, fontSize: 12, color: 'var(--fp-text-3)', fontFamily: MONO }}>
                    {d.material.kind}{d.material.status ? ` · ${d.material.status}` : ''} · {d.material.id}
                  </div>
                  <button type="button"
                    style={{ marginTop: 8, width: '100%', border: '1px solid var(--fp-border)', background: 'color-mix(in srgb, var(--fp-accent) 10%, transparent)', color: 'var(--fp-link)', borderRadius: 7, padding: '6px 0', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
                    onClick={() => onOpenMaterial(d.material!.id, d.material!.title || d.material!.id)}>打开材料</button>
                </div>
              )}
              {d.omni && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--fp-text-2)' }}>[OMNI] 功能注释</div>
                  {d.omni.summary && <div style={{ marginTop: 6, fontSize: 13, color: 'var(--fp-text)', lineHeight: 1.5 }}>{d.omni.summary}</div>}
                  {d.omni.why && <div style={{ marginTop: 6, fontSize: 12, color: 'var(--fp-text-2)', lineHeight: 1.5 }}>为什么在这: {d.omni.why}</div>}
                  <div style={{ marginTop: 6, fontSize: 12, color: 'var(--fp-text-3)', fontFamily: MONO, lineHeight: 1.6 }}>
                    {[d.omni.type && `type=${d.omni.type}`, d.omni.origin && `origin=${d.omni.origin}`, d.omni.status && `status=${d.omni.status}`, d.omni.ts && `ts=${d.omni.ts}`].filter(Boolean).join(' · ')}
                    {d.omni.tags?.length ? <><br />tags: {d.omni.tags.join(', ')}</> : null}
                  </div>
                </div>
              )}
              {d.instances && d.instances.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--fp-text-2)' }}>注册系统实体</div>
                  {d.instances.map((it) => (
                    <div key={it.entity_id} style={{ marginTop: 6, fontSize: 12, fontFamily: MONO, color: 'var(--fp-text-3)', wordBreak: 'break-all' }}>
                      {it.type} · {it.name} <span style={{ opacity: 0.7 }}>({it.entity_id})</span>
                    </div>
                  ))}
                </div>
              )}
              {!d.material && !d.omni && (!d.instances || d.instances.length === 0) && (
                <div style={{ marginTop: 14, fontSize: 12, color: 'var(--fp-text-3)' }}>在 omnicompany 里没有 material 身份或功能注释。</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
