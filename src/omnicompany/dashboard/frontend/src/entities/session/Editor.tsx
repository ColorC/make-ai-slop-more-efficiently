import React, { useEffect, useState } from 'react'
import { useIDEStore } from '../../stores/ideStore'
import AgentStatusBar from '../../components/ide/AgentStatusBar'
import ChatPanel from '../../components/ide/ChatPanel'
import TerminalPanel from '../../components/ide/TerminalPanel'
import FileViewer from '../../components/ide/FileViewer'
import EventTimeline from '../../components/ide/EventTimeline'
import SessionContextPanel from '../cc_session/SessionContextPanel'
import type { SessionEntity } from './index'

type RightTab = 'context' | 'files' | 'terminal'

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  body: { flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 },
  center: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, overflow: 'hidden' },
  right: { width: 380, borderLeft: '1px solid #222', display: 'flex', flexDirection: 'column', flexShrink: 0 },
  rTabs: { display: 'flex', borderBottom: '1px solid #222', background: 'var(--fp-bg)' },
  rTab: (active: boolean): React.CSSProperties => ({
    flex: 1, padding: '4px 8px',
    background: active ? '#1a1a2a' : 'transparent',
    border: 'none',
    borderBottom: active ? '2px solid #90caf9' : '2px solid transparent',
    color: active ? '#90caf9' : '#666',
    cursor: 'pointer', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace',
  }),
  closeBtn: { background: 'transparent', border: 'none', color: '#555', cursor: 'pointer', padding: '4px 8px', fontSize: 14 },
  rBody: { flex: 1, overflow: 'hidden' },
  timeline: { height: 32, borderTop: '1px solid #222', display: 'flex', flexShrink: 0 },
}

export default function SessionEditor({ entity }: { entity: SessionEntity }) {
  const { connectToTrace, disconnect } = useIDEStore()
  const [rightTab, setRightTab] = useState<RightTab>('context')
  const [showRight, setShowRight] = useState(true)
  const [showTimeline, setShowTimeline] = useState(false)
  const [pendingContext, setPendingContext] = useState<string | null>(null)
  const fileChanges = useIDEStore((s) => s.fileChanges)
  const agentState = useIDEStore((s) => s.agentState)
  const isLive = agentState === 'running' || agentState === 'thinking'

  useEffect(() => {
    connectToTrace(entity.id)
    return () => { disconnect() }
  }, [entity.id])

  useEffect(() => {
    if (fileChanges.length > 0 && !showRight) {
      setShowRight(true)
      setRightTab('files')
    }
  }, [fileChanges.length])

  const handleFileClick = () => {
    setShowRight(true)
    setRightTab('files')
  }

  const handleLoadContext = (text: string) => setPendingContext(text)

  return (
    <div style={S.root}>
      <div style={S.body}>
        <div style={S.center}>
          <AgentStatusBar showTimeline={showTimeline} onToggleTimeline={() => setShowTimeline(!showTimeline)} />
          <ChatPanel
            onFileClick={handleFileClick}
            appendText={pendingContext}
            onAppendConsumed={() => setPendingContext(null)}
          />
        </div>
        {showRight && (
          <div style={S.right}>
            <div style={S.rTabs}>
              {(['context', 'files', 'terminal'] as const).map((t) => (
                <button key={t} style={S.rTab(rightTab === t)} onClick={() => setRightTab(t)}>
                  {t === 'context' ? 'Context' : t === 'files' ? 'Files' : 'Terminal'}
                </button>
              ))}
              <button style={S.closeBtn} title="收起" onClick={() => setShowRight(false)}>×</button>
            </div>
            <div style={S.rBody}>
              {rightTab === 'context' && (
                <SessionContextPanel
                  sessionId={entity.id}
                  alive={isLive}
                  kind="native"
                />
              )}
              {rightTab === 'files' && <FileViewer />}
              {rightTab === 'terminal' && <TerminalPanel />}
            </div>
          </div>
        )}
      </div>
      {showTimeline && (
        <div style={S.timeline}>
          <EventTimeline />
          <button style={{ ...S.closeBtn, borderLeft: '1px solid #222' }} onClick={() => setShowTimeline(false)}>×</button>
        </div>
      )}
    </div>
  )
}
