import React, { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Minus, TerminalSquare, X } from 'lucide-react'
import StudioFrame, { endStudioSession } from './StudioFrame'
import { useStudioSessions, type StudioOpenSession } from './studioSessions'
import { ccSessionRegistration, type CcSessionEntity } from '../cc_session'
import { PanelRuntimeProvider, usePanelRuntime, usePanelVisible, type PanelRuntimeValue } from '../../shell/panelRuntime'
import { VSplitter } from '../../shell/Splitter'
import { useNarrowPane } from '../../shell/useNarrowPane'
import './studio.css'

export { STUDIO_COMPANY_URL, STUDIO_PAGE_URL, STUDIO_PROJECT } from './StudioFrame'

/** 低于这个宽度左右两栏都挤, 改成会话区盖在群聊上面、能退回。 */
export const STUDIO_NARROW_WIDTH = 900
export const STUDIO_CHAT_WIDTH_DEFAULT = 440
const CHAT_WIDTH_MIN = 320
const SESSION_AREA_MIN = 420
const CHAT_WIDTH_KEY = 'omni.studio.chatWidth'

function readChatWidth(): number {
  try {
    const saved = Number(window.localStorage.getItem(CHAT_WIDTH_KEY))
    if (Number.isFinite(saved) && saved >= CHAT_WIDTH_MIN) return saved
  } catch { /* */ }
  return STUDIO_CHAT_WIDTH_DEFAULT
}

// 会话页签用的那一套(cc_session 的 Editor, 懒加载 xterm), 不另写终端。
const SessionEditor = ccSessionRegistration.renderer.Editor as React.ComponentType<{ entity: CcSessionEntity; panelVisible?: boolean }>

/**
 * 会话区里的一个会话: 解析实体后挂会话页签同款的终端组件。
 * 可见性走 PanelRuntimeProvider, 和主区域页签同一个约定: 不可见就释放 xterm 和连接,
 * 终端内容留在 PTY 宿主里, 再可见时按一帧屏幕恢复。已经结束的会话不挂(否则组件会去恢复它), 直接拿掉。
 */
function StudioSessionPane({ session, visible }: { session: StudioOpenSession; visible: boolean }) {
  const [entity, setEntity] = useState<CcSessionEntity | null>(null)
  const parentTabId = usePanelRuntime()?.tabId ?? 'studio:main'

  useEffect(() => {
    let active = true
    setEntity(null)
    ccSessionRegistration.resolver.fetch(session.id).then((resolved) => {
      if (!active) return
      if (resolved.alive) {
        setEntity(resolved)
      } else {
        const store = useStudioSessions.getState()
        store.collapse(session.id)
        store.sessionsChanged()
      }
    }).catch(() => { /* 取不到就停在「正在连接」, 结束 / 收起照样能用 */ })
    return () => { active = false }
  }, [session.id])

  const runtime = useMemo<PanelRuntimeValue>(
    () => ({ tabId: `${parentTabId}#${session.id}`, visible }),
    [parentTabId, session.id, visible],
  )

  return (
    <div
      className="studio-pane"
      data-testid="studio-session-pane"
      data-session-id={session.id}
      data-visible={visible ? '1' : '0'}
      hidden={!visible}
    >
      <PanelRuntimeProvider value={runtime}>
        {entity ? (
          <React.Suspense fallback={<div className="studio-pane-note">正在打开终端…</div>}>
            <SessionEditor entity={entity} panelVisible={visible} />
          </React.Suspense>
        ) : (
          <div className="studio-pane-note">正在连接会话…</div>
        )}
      </PanelRuntimeProvider>
    </div>
  )
}

function StudioSessionArea({ shown, narrow, onBack }: { shown: boolean; narrow: boolean; onBack: () => void }) {
  const open = useStudioSessions((store) => store.open)
  const activeId = useStudioSessions((store) => store.activeId)
  const setActive = useStudioSessions((store) => store.setActive)
  const collapse = useStudioSessions((store) => store.collapse)
  const studioVisible = usePanelVisible()
  const [ending, setEnding] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')
  const shownSessionId = activeId && open.some((item) => item.id === activeId) ? activeId : open[0]?.id ?? null

  const end = (id: string) => {
    if (ending[id]) return
    setEnding((state) => ({ ...state, [id]: true }))
    setError('')
    endStudioSession(id)
      .catch((cause) => setError(`结束会话失败：${cause instanceof Error ? cause.message : String(cause)}`))
      .finally(() => setEnding((state) => {
        const next = { ...state }
        delete next[id]
        return next
      }))
  }

  return (
    <div className="studio-sessions" data-testid="studio-session-area" hidden={!shown}>
      {(open.length > 0 || narrow) && (
        <div className="studio-tabs" role="tablist" aria-label="Studio 里开着的会话">
          {narrow && (
            <button type="button" className="studio-back" data-testid="studio-session-back" onClick={onBack} title="回到群聊（会话不结束）">
              <ArrowLeft size={14} />群聊
            </button>
          )}
          {open.map((item) => (
            <div
              key={item.id}
              role="tab"
              tabIndex={0}
              className="studio-tab"
              data-testid="studio-session-tab"
              data-session-id={item.id}
              aria-selected={item.id === shownSessionId}
              title={`${item.label}\n中键收起（会话不结束）`}
              onClick={() => setActive(item.id)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') setActive(item.id) }}
              onMouseDown={(event) => { if (event.button === 1) event.preventDefault() }}
              onAuxClick={(event) => {
                if (event.button !== 1) return
                event.preventDefault()
                collapse(item.id)
              }}
            >
              <TerminalSquare size={13} className="studio-tab-icon" />
              <span className="studio-tab-label">{item.label}</span>
              <button
                type="button"
                className="studio-tab-btn"
                data-testid="studio-session-collapse"
                title="收起（会话不结束，列表里再点能回来）"
                aria-label={`收起 ${item.label}`}
                onClick={(event) => { event.stopPropagation(); collapse(item.id) }}
              >
                <Minus size={13} />
              </button>
              <button
                type="button"
                className="studio-tab-btn studio-tab-end"
                data-testid="studio-session-end"
                title="结束这个会话"
                aria-label={`结束 ${item.label}`}
                disabled={!!ending[item.id]}
                onClick={(event) => { event.stopPropagation(); end(item.id) }}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      {error && <div className="studio-error" role="alert" data-testid="studio-session-error">{error}</div>}
      <div className="studio-panes">
        {open.length === 0 && (
          <div className="studio-empty" data-testid="studio-session-empty">从左边点一个会话，或给员工开一个新会话</div>
        )}
        {open.map((item) => (
          <StudioSessionPane key={item.id} session={item} visible={studioVisible && shown && item.id === shownSessionId} />
        ))}
      </div>
    </div>
  )
}

/**
 * 主区域的 Studio 页签(单例)。2026-11-16 夜业主定: 公司的会话就在这个窗口里操作、能直接结束。
 * 左栏是 Studio 页面的群聊 + 会话列表(view=chat, iframe 与桥接复用 StudioFrame), 右边是会话区:
 * 一排小页签, 每个挂一份会话页签同款的终端。窄于 900 时左栏占满, 会话区盖上来, 能退回群聊。
 * 侧栏(在右)的 Studio 页只放 3D 办公楼(view=scene), 不动。
 */
export default function StudioView() {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const narrow = useNarrowPane(rootRef, STUDIO_NARROW_WIDTH)
  const openCount = useStudioSessions((store) => store.open.length)
  const focusSeq = useStudioSessions((store) => store.focusSeq)
  const [chatWidth, setChatWidth] = useState(readChatWidth)
  const [dragging, setDragging] = useState(false)
  const [overlay, setOverlay] = useState(false)
  const seenFocusRef = useRef(focusSeq)

  // 打开 / 切到一个会话: 窄布局把会话区盖上来。挂载时已有的不算, 先看群聊。
  useEffect(() => {
    if (focusSeq === seenFocusRef.current) return
    seenFocusRef.current = focusSeq
    setOverlay(true)
  }, [focusSeq])

  useEffect(() => {
    if (!dragging) return undefined
    const stop = () => setDragging(false)
    window.addEventListener('mouseup', stop)
    return () => window.removeEventListener('mouseup', stop)
  }, [dragging])

  const resize = (delta: number) => {
    const total = rootRef.current?.getBoundingClientRect().width || 0
    const max = total > 0 ? Math.max(CHAT_WIDTH_MIN, total - SESSION_AREA_MIN) : Number.POSITIVE_INFINITY
    setChatWidth((width) => {
      const next = Math.round(Math.min(max, Math.max(CHAT_WIDTH_MIN, width + delta)))
      try { window.localStorage.setItem(CHAT_WIDTH_KEY, String(next)) } catch { /* */ }
      return next
    })
  }

  const sessionShown = !narrow || (overlay && openCount > 0)

  return (
    <div
      ref={rootRef}
      className="studio-view"
      data-testid="studio-view"
      data-layout={narrow ? 'narrow' : 'wide'}
      data-dragging={dragging ? '1' : '0'}
    >
      <div
        className="studio-chat"
        style={narrow ? undefined : { width: chatWidth }}
        hidden={narrow && sessionShown}
      >
        <StudioFrame testid="studio-chat" view="chat" />
        {narrow && openCount > 0 && !sessionShown && (
          <button type="button" className="studio-return" data-testid="studio-session-return" onClick={() => setOverlay(true)}>
            <TerminalSquare size={13} />会话（{openCount}）
          </button>
        )}
      </div>
      {!narrow && (
        <div className="studio-split" onMouseDownCapture={() => setDragging(true)}>
          <VSplitter onResize={resize} side="right" />
        </div>
      )}
      <StudioSessionArea shown={sessionShown} narrow={narrow} onBack={() => setOverlay(false)} />
    </div>
  )
}
