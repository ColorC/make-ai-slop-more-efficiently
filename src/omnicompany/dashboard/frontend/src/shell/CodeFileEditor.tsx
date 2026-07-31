/**
 * Shared editor for "single python source + optional DESIGN.md" entity types.
 * Used by Worker.设计 facet, Team entity, Material entity.
 */
import React, { Suspense, useEffect, useState } from 'react'
import MarkdownRenderer from './MarkdownRenderer'
import PaneHeader from './PaneHeader'
import EmptyState from './EmptyState'
import { colors, spacing, fonts } from './tokens'
import { usePanels } from '../stores/panelsStore'

const MonacoEditor = React.lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.default })))

export interface CodeFileDetail {
  id: string
  name: string
  package: string
  file_path: string
  design_md: string | null
  source: string
}

const S = {
  root: { display: 'flex', flexDirection: 'column' as const, height: '100%', background: colors.bg, color: colors.text, fontFamily: fonts.mono, fontSize: 14 },
  tabs: { display: 'flex', gap: 1, background: colors.bgPanel, borderBottom: `1px solid ${colors.border}`, flexShrink: 0 },
  tab: (active: boolean): React.CSSProperties => ({
    padding: '4px 12px',
    background: active ? '#1a1a2a' : 'transparent',
    border: 'none',
    borderBottom: active ? `2px solid ${colors.accent}` : '2px solid transparent',
    color: active ? colors.accent : colors.textFaint,
    cursor: 'pointer', fontSize: 14, fontFamily: fonts.mono,
  }),
  body: { flex: 1, overflow: 'auto', padding: spacing.xl },
  bodyMono: { flex: 1, overflow: 'hidden' },
}

interface Props {
  detail: CodeFileDetail
  language?: string
  /** Defaults to 'design' if design_md exists, else 'source'. */
  defaultView?: 'design' | 'source'
}

export default function CodeFileEditor({ detail, language = 'python', defaultView }: Props) {
  const initial = defaultView || (detail.design_md ? 'design' : 'source')
  const [view, setView] = useState<'design' | 'source'>(initial)
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    setView(defaultView || (detail.design_md ? 'design' : 'source'))
  }, [detail.id, defaultView, detail.design_md])

  const jumpToWikilink = (target: string) => {
    openTab({ type: 'note', id: target }, target.split('/').pop() || target)
  }

  return (
    <div style={S.root}>
      <PaneHeader title={detail.name} subtitle={`${detail.package} · ${detail.file_path}`} />
      <div style={S.tabs}>
        <button style={S.tab(view === 'design')} onClick={() => setView('design')}>DESIGN.md</button>
        <button style={S.tab(view === 'source')} onClick={() => setView('source')}>源码</button>
      </div>
      {view === 'design' ? (
        <div style={S.body}>
          {detail.design_md ? (
            <MarkdownRenderer source={detail.design_md} onWikilinkClick={jumpToWikilink} currentPath={detail.id} />
          ) : (
            <EmptyState text={`无 DESIGN.md：${detail.package}`} hint="切到源码查看代码" />
          )}
        </div>
      ) : (
        <div style={S.bodyMono}>
          <Suspense fallback={<EmptyState text="加载编辑器..." />}>
            <MonacoEditor
              value={detail.source}
              language={language}
              theme="vs-dark"
              options={{ readOnly: true, minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false }}
            />
          </Suspense>
        </div>
      )}
    </div>
  )
}
