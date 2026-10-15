import React, { useCallback, useState } from 'react'
import { Play } from 'lucide-react'
import {
  ccApi,
  ASSISTANT_TOPIC,
  rememberAssistantSession,
  rememberedAssistantSession,
  type CcSessionMeta,
} from '../../api/ccClient'
import Editor from '../cc_session/Editor'
import type { CcSessionEntity } from '../cc_session'

function toEntity(meta: CcSessionMeta): CcSessionEntity {
  return {
    type: 'cc_session',
    kind: 'pty',
    id: meta.id,
    title: `assistant · ${meta.id.slice(0, 8)}`,
    cwd: meta.cwd,
    alive: meta.alive,
    status: meta.status || 'alive',
    cmd: meta.cmd,
    startedAt: meta.started_at,
    claudeSessionId: meta.claude_session_id || null,
    providerSessionId: meta.provider_session_id || null,
    providerTitle: meta.provider_title || null,
    activePlan: meta.active_plan || null,
    provider: meta.provider || 'codex',
    topic: meta.topic || ASSISTANT_TOPIC,
    tags: ['assistant', 'agent-worker', 'cli'],
  }
}

/**
 * 总控实体内的对话视图 —— 与「会话」页的 assistant 按钮同源: 都是 assistant
 * 会话(机主的会话与材料管家, 模型由后端 assistant 阶梯选择)。
 * 已有活着的 assistant 会话就直接接上, 没有才新建。
 */
export default function ControllerChat() {
  const [session, setSession] = useState<CcSessionMeta | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const startSession = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const remembered = rememberedAssistantSession()
      const sessions = await ccApi.list({ includeRecoverable: false })
      const live = sessions.find((item) => (
        item.alive && remembered !== null && item.id === remembered
      )) ?? sessions.find((item) => item.alive && item.topic === ASSISTANT_TOPIC)
      let next = live
      if (!next) {
        const assistant = (await ccApi.agentWorkerCatalog()).assistant
        next = await ccApi.createAgentWorker({
          provider: assistant.provider,
          model: assistant.model || undefined,
          effort: assistant.effort || undefined,
          topic: ASSISTANT_TOPIC,
          visibility: 'hidden',
        })
      }
      rememberAssistantSession(next.id)
      setSession(next)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  if (session) return <Editor entity={toEntity(session)} />

  return (
    <div className="cs-handoff" data-testid="controller-cli-idle">
      <div className="cs-handoff-card">
        <div className="cs-handoff-title">{loading ? '正在启动 assistant…' : 'assistant 未启动'}</div>
        {!loading && !error && <div className="cs-handoff-desc">会话与材料管家：搜索、整理、管理所有会话与札记。需要时手动启动。</div>}
        {error && <div className="cs-handoff-desc">{error}</div>}
        <button
          type="button"
          className="cs-openbtn"
          data-testid="controller-cli-start"
          disabled={loading}
          onClick={() => { void startSession() }}
        >
          <Play size={14} />{error ? '重试启动' : '启动 assistant'}
        </button>
      </div>
    </div>
  )
}
