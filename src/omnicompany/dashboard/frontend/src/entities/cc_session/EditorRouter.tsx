// 被 cc_session/index.tsx 用 React.lazy 动态引入。PTY 路线的 ./Editor 依赖 xterm(~291KB),
// 拆到这里后 xterm 只在真正打开一个 cc_session tab 时才下载, 不再常驻首屏 bundle。
import React, { useState } from 'react'
import Editor from './Editor'
import WebSessionPanel from './WebSessionPanel'
import type { CcSessionEntity } from './index'
import { ccApi } from '../../api/ccClient'
import { usePanels } from '../../stores/panelsStore'

function ArchivedChat({ entity }: { entity: CcSessionEntity }) {
  const [error, setError] = useState('')
  const provider = String(entity.provider || '')
  const resumable = ['claude_code', 'codex', 'codex_the_company', 'kimi', 'opencode', 'codebuddy', 'kiro', 'qoder'].includes(provider)
    && Boolean(entity.claudeSessionId || entity.providerSessionId)
  const resume = async () => {
    try {
      const meta = await ccApi.createAgentWorker({
        provider: provider as 'claude_code' | 'codex' | 'codex_the_company' | 'kimi' | 'opencode' | 'codebuddy' | 'kiro' | 'qoder',
        resume_provider_session_id: entity.providerSessionId || entity.claudeSessionId || undefined,
        cwd: entity.cwd,
        visibility: 'visible',
      })
      usePanels.getState().closeTab(`cc_session:${entity.id}`)
      usePanels.getState().openTab({ type: 'cc_session', id: meta.id }, `CLI · ${meta.id.slice(0, 8)}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }
  return (
    <div className="cs-handoff">
      <div className="cs-handoff-card">
        <div className="cs-handoff-title">旧 Chat 已封存</div>
        <div className="cs-handoff-desc">历史数据保持只读；新的工作统一在 CLI Agent Worker 中继续。</div>
        {resumable && <button type="button" className="cs-openbtn" onClick={() => { void resume() }}>恢复到 CLI</button>}
        {error && <div className="cs-handoff-desc">{error}</div>}
      </div>
    </div>
  )
}

const EditorRouter: React.FC<{ entity: CcSessionEntity; facet?: string }> = ({ entity }) => {
  if (entity.kind === 'chat') {
    return <ArchivedChat entity={entity} />
  }
  if (entity.kind === 'web') {
    return <WebSessionPanel entity={entity} />
  }
  return <Editor entity={entity} />
}

export default EditorRouter
