import React, { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { ccChatApi, type CcChatSessionMeta } from '../../api/ccChatClient'
import NativeChatPanel from '../cc_session/NativeChatPanel'

/**
 * 总控固定入口：复用最近一个仍存活的 controller session；没有时原地创建。
 * 这保证“打开总控”永远落到真实会话，而不是落点卡或另一个站点。
 */
export default function ControllerChat() {
  const [session, setSession] = useState<CcChatSessionMeta | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const ensureSession = useCallback(async (forceNew = false) => {
    setLoading(true)
    setError('')
    try {
      let found: CcChatSessionMeta | undefined
      if (!forceNew) {
        const sessions = await ccChatApi.list({ limit: 100, includeArchived: false })
        found = sessions
          .filter((item) => item.provider === 'controller' && item.alive)
          .sort((a, b) => b.started_at - a.started_at)[0]
      }
      setSession(found || await ccChatApi.create({ provider: 'controller' }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void ensureSession(false) }, [ensureSession])

  if (session) {
    return (
      <NativeChatPanel
        key={session.id}
        sessionId={session.id}
        initialMeta={session}
        title="总控对话"
        onNewSession={() => ensureSession(true)}
      />
    )
  }
  return (
    <div className="cs-handoff" data-testid="controller-chat-loading">
      <div className="cs-handoff-card">
        <div className="cs-handoff-title">{loading ? '正在连接总控…' : '总控会话暂不可用'}</div>
        {error && <div className="cs-handoff-desc">{error}</div>}
        {!loading && (
          <button type="button" className="cs-openbtn" onClick={() => { void ensureSession(false) }}>
            <RefreshCw size={14} />重试
          </button>
        )}
      </div>
    </div>
  )
}
