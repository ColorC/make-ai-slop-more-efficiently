import React, { Suspense, useEffect, useState } from 'react'
import { Copy, FileCode2 } from 'lucide-react'
import { fetchWorkerDetail, type WorkerDetail, type WorkerEntity } from '../resolver'
import MarkdownRenderer from '../../../shell/MarkdownRenderer'
import EmptyState from '../../../shell/EmptyState'
import KebabMenu, { type KebabItem } from '../../../shared/view/ui/KebabMenu'
import { usePanels } from '../../../stores/panelsStore'
import { copyText } from '../../../lib/copyText'
import { openInVscode } from '../../../lib/openInVscode'

const MonacoEditor = React.lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.default })))

const VIEWS = [
  { key: 'design', label: 'DESIGN.md' },
  { key: 'source', label: '源码' },
] as const
type ViewKey = (typeof VIEWS)[number]['key']

const S: Record<string, any> = {
  // root 透明 —— 吃全局冷渐变, 玻璃浮其上。
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: 'var(--fp-text)' },
  // 顶栏: DESIGN.md/源码 shadcn 分段控件 + 文件名次级标识 + ⋯(低频文件操作)。无标题头。
  bar: { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', flexShrink: 0 },
  seg: {
    display: 'inline-flex', gap: 2, padding: 3, borderRadius: 9,
    background: 'var(--fp-surface)', border: '1px solid var(--fp-border-subtle)', flexShrink: 0,
  },
  segBtn: (active: boolean): React.CSSProperties => ({
    padding: '3px 13px', borderRadius: 7, border: '1px solid transparent', cursor: 'pointer',
    background: active ? 'var(--fp-glass)' : 'transparent',
    boxShadow: active ? 'inset 0 1px 0 rgba(255,255,255,.08)' : 'none',
    borderColor: active ? 'var(--fp-border)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-3)',
    fontSize: 14, fontWeight: active ? 600 : 500,
    transition: 'background 150ms cubic-bezier(0.175,0.885,0.32,1.1), color 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  // 文件路径: 最弱等宽弱灰(12px 地板, 非 11), 截断省略。
  path: { flex: 1, minWidth: 0, color: 'var(--fp-text-3)', fontSize: 12, fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  body: { flex: 1, overflow: 'hidden', minHeight: 0, display: 'flex', flexDirection: 'column' },
  // DESIGN.md 阅读区: 玻璃卡浮在渐变上(磨砂 + 边缘高光 + token 描边 + r11), 留呼吸边距。
  docCard: {
    flex: 1, overflow: 'auto', margin: '0 14px 14px', borderRadius: 11, padding: '4px 22px 18px',
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  // 长 mono 源码区保持安静(极淡 surface, 不盖死渐变)。
  codeWrap: { flex: 1, overflow: 'hidden', minHeight: 0, margin: '0 14px 14px', borderRadius: 11, border: '1px solid var(--fp-border)', background: 'var(--fp-surface)' },
}

export default function WorkerDesignFacet({ entity }: { entity: WorkerEntity }) {
  const [detail, setDetail] = useState<WorkerDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<ViewKey>('design')
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    setDetail(null); setError(null)
    fetchWorkerDetail(entity.id).then(setDetail).catch((e) => setError(String(e)))
  }, [entity.id])

  // 加载完成后, 没有 DESIGN.md 的 worker 默认落到源码。
  useEffect(() => {
    if (detail) setView(detail.design_md ? 'design' : 'source')
  }, [detail])

  if (error) return <EmptyState text={`加载失败: ${error}`} />
  if (!detail) return <EmptyState text="加载中..." />

  const jumpToWikilink = (target: string) => {
    openTab({ type: 'note', id: target }, target.split('/').pop() || target)
  }

  // 低频文件操作收进共享 ⋯ 菜单(复制路径 / 在 VSCode 打开), 不在界面堆等权按钮。
  const kebabItems: KebabItem[] = [
    { label: '复制文件路径', icon: <Copy size={14} />, testid: 'worker-design-copy-path', onClick: () => { void copyText(detail.file_path) } },
    { label: '在 VSCode 打开', icon: <FileCode2 size={14} />, testid: 'worker-design-open-vscode', onClick: () => { openInVscode(detail.file_path) } },
  ]

  return (
    <div style={S.root} data-testid="worker-design-facet">
      <div style={S.bar}>
        {/* DESIGN.md / 源码 切换 = shadcn 分段控件(选中无缝/无分割线) */}
        <div style={S.seg} role="tablist">
          {VIEWS.map((v) => {
            const active = view === v.key
            return (
              <button
                key={v.key}
                type="button"
                role="tab"
                aria-selected={active}
                style={S.segBtn(active)}
                data-testid={`worker-design-view-${v.key}`}
                data-active={active ? '1' : '0'}
                onClick={() => setView(v.key)}
                onMouseEnter={(e) => { if (!active) e.currentTarget.style.color = 'var(--fp-text-2)' }}
                onMouseLeave={(e) => { if (!active) e.currentTarget.style.color = 'var(--fp-text-3)' }}
              >
                {v.label}
              </button>
            )
          })}
        </div>
        {/* 文件路径: 最弱等宽弱灰标识(取代 PaneHeader 的重复标题) */}
        <span style={S.path} title={`${detail.package} · ${detail.file_path}`}>{detail.file_path}</span>
        <KebabMenu items={kebabItems} testid="worker-design-actions" iconSize={15} />
      </div>
      <div style={S.body}>
        {view === 'design' ? (
          detail.design_md ? (
            <div style={S.docCard} data-testid="worker-design-doc">
              <MarkdownRenderer source={detail.design_md} onWikilinkClick={jumpToWikilink} currentPath={detail.id} />
            </div>
          ) : (
            <EmptyState text={`无 DESIGN.md：${detail.package}`} hint="切到源码查看代码" />
          )
        ) : (
          <div style={S.codeWrap} data-testid="worker-design-source">
            <Suspense fallback={<EmptyState text="加载编辑器..." />}>
              <MonacoEditor
                value={detail.source}
                language="python"
                theme="vs-dark"
                options={{ readOnly: true, minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false }}
              />
            </Suspense>
          </div>
        )}
      </div>
    </div>
  )
}
