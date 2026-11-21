// Web-display session pane — DeepSeek Harness Web UI 等"展示面是网页"的会话。
// 会话本体是 ccdaemon 里一个独立 Python supervisor 进程(见 config/agents/dsh/dsh-web.py),
// 这里只做两件事: 轮询 web_url 直到 ready, 然后用 iframe 内嵌官方 Web UI;
// 结束按钮走 /cc/sessions/{id} DELETE, 由 supervisor 把 dsh 进程树整体收掉。
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ExternalLink, Loader2, RotateCw, Square } from 'lucide-react'
import { ccApi, type CcSessionMeta } from '../../api/ccClient'
import type { CcSessionEntity } from './index'

const POLL_MS = 1200

function useWebMeta(entityId: string): { meta: CcSessionMeta | null; loading: boolean } {
  const [meta, setMeta] = useState<CcSessionMeta | null>(null)
  const [loading, setLoading] = useState(true)
  const live = useRef(true)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        // tab-states 是纯内存无缓存快照(线程面板同源轮询), URL 一宣布立刻可见;
        // /cc/sessions 有服务端 TTL 缓存, 启动中会多等一整个缓存窗。
        const states = await ccApi.tabStates()
        const found = states.web.find((item) => item.id === entityId)
          || states.pty.find((item) => item.id === entityId)
          || null
        if (!live.current) return
        setMeta(found ? (found as CcSessionMeta) : null)
        setLoading(false)
        if (!found?.alive) return
      } catch {
        if (live.current) setLoading(false)
      }
      timer = setTimeout(poll, POLL_MS)
    }
    void poll()
    return () => {
      live.current = false
      if (timer) clearTimeout(timer)
    }
  }, [entityId])

  return { meta, loading }
}

export function WebSessionPanel({ entity }: { entity: CcSessionEntity }) {
  const { meta, loading } = useWebMeta(entity.id)
  const [ending, setEnding] = useState(false)
  const [endError, setEndError] = useState('')

  const webUrl = meta?.web_url || entity.webUrl || null
  const phase = meta?.web_phase || entity.webPhase || 'starting'
  const ended = Boolean(meta && !meta.alive)

  const endSession = useCallback(async () => {
    if (ending) return
    setEnding(true)
    setEndError('')
    try {
      await ccApi.kill(entity.id)
    } catch (cause) {
      setEndError(cause instanceof Error ? cause.message : String(cause))
      setEnding(false)
    }
  }, [ending, entity.id])

  if (loading && !webUrl) {
    return (
      <div className="ws-root" data-testid="web-session-loading">
        <Loader2 size={18} className="ws-spin" aria-hidden />
        <span>正在启动 Web 会话…</span>
      </div>
    )
  }

  if (ended || (!loading && !meta?.alive && !webUrl)) {
    return (
      <div className="ws-root" data-testid="web-session-ended">
        <div className="ws-ended-title">Web 会话已结束</div>
        <div className="ws-ended-desc">supervisor 进程已退出；可在 CLI Hub 重新打开一个 Web 会话。</div>
        {endError && <div className="ws-ended-desc">{endError}</div>}
      </div>
    )
  }

  if (!webUrl) {
    return (
      <div className="ws-root" data-testid="web-session-starting">
        <Loader2 size={18} className="ws-spin" aria-hidden />
        <span>DeepSeek Harness 正在启动，分配端口…</span>
      </div>
    )
  }

  return (
    <div className="ws-root" data-testid="web-session-ready" data-web-url={webUrl}>
      <div className="ws-toolbar">
        <span className="v2-status st-ok" data-testid="web-session-status">
          {phase === 'ready' ? '运行中' : phase}
        </span>
        <span className="ws-url" title={webUrl}>{webUrl}</span>
        <span className="ws-spacer" />
        <button type="button" className="v2-btn sm" onClick={() => window.open(webUrl, '_blank', 'noopener,noreferrer')} data-testid="web-session-open-browser">
          <ExternalLink size={13} aria-hidden />浏览器打开
        </button>
        <button type="button" className="v2-btn sm" onClick={() => window.location.reload()} title="重新载入 iframe" data-testid="web-session-reload">
          <RotateCw size={13} aria-hidden />刷新
        </button>
        <button type="button" className="v2-btn sm danger" onClick={() => { void endSession() }} disabled={ending} data-testid="web-session-end">
          <Square size={13} aria-hidden />{ending ? '结束中…' : '结束会话'}
        </button>
      </div>
      <iframe
        className="ws-frame"
        src={webUrl}
        title="DeepSeek Harness Web UI"
        data-testid="web-session-frame"
      />
    </div>
  )
}

export default WebSessionPanel
