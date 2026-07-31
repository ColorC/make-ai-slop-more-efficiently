/**
 * ConnectionStatus — 顶栏 dashboard / ccdaemon 链路状态指示.
 *
 * 三档:
 *   connected     绿点 + "在线"
 *   reconnecting  黄点 + 倒计时 + 累计重连次数 (累计 > 60s 切 disconnected)
 *   disconnected  红点 + 提示 "daemon 长时间不可达, 检查 omni cc daemon status"
 *
 * 业务侧 chat / pty 组件内部用 useWsAutoReconnect, 把 state + reconnectAttempts +
 * disconnectedAt 上报到全局 store / context, 顶栏 ConnectionStatus 消费总状态.
 *
 * MVP 实现: 接受 props 直接渲染. 真正全局状态通过 ConnectionStatusProvider
 * (后续 todo) 收集多 WS 链路状态聚合.
 */

import React from 'react'
import type { WsConnectionState } from '../lib/wsAutoReconnect'

export interface ConnectionStatusProps {
  state: WsConnectionState
  reconnectAttempts?: number
  disconnectedAt?: number | null
  /** 链路标签 (例 "chat" / "pty" / "ide-bus"), 用于多链路聚合 UI 显示哪个出问题. */
  label?: string
  hint?: string
}

const COLOR: Record<WsConnectionState, string> = {
  connecting: '#cfa44a',
  connected: '#4caf50',
  reconnecting: '#cfa44a',
  disconnected: '#ef5350',
}

const LABEL: Record<WsConnectionState, string> = {
  connecting: '连接中',
  connected: '在线',
  reconnecting: '重连中',
  disconnected: '断开',
}

export const ConnectionStatus: React.FC<ConnectionStatusProps> = ({
  state, reconnectAttempts = 0, disconnectedAt = null, label, hint,
}) => {
  const color = COLOR[state]
  const text = LABEL[state]
  const offlineForMs = disconnectedAt ? Date.now() - disconnectedAt : 0
  const offlineSec = Math.max(0, Math.floor(offlineForMs / 1000))

  let detail = ''
  if (state === 'reconnecting') {
    detail = ` · 第 ${reconnectAttempts} 次 · 已断 ${offlineSec}s`
  } else if (state === 'disconnected') {
    detail = hint ?? ' · 检查 omni cc daemon status'
  }

  return (
    <div
      title={hint ?? `${label ?? ''} ${text}${detail}`}
      data-conn-state={state}
      data-conn-label={label ?? ''}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '2px 8px', borderRadius: 12, background: '#181818',
        border: `1px solid ${color}33`, color: '#cccccc', fontSize: 14,
        fontFamily: 'monospace',
      }}
    >
      <span
        aria-hidden
        style={{
          display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
          background: color,
          boxShadow: state === 'connected' ? `0 0 4px ${color}aa` : 'none',
          animation: state === 'reconnecting' ? 'cs-pulse 1s infinite' : 'none',
        }}
      />
      {label && <span style={{ color: '#888' }}>{label}</span>}
      <span style={{ color }}>{text}</span>
      {detail && <span style={{ color: '#888' }}>{detail}</span>}
      <style>{`
        @keyframes cs-pulse {
          0%, 100% { opacity: 1 }
          50% { opacity: 0.4 }
        }
      `}</style>
    </div>
  )
}

export default ConnectionStatus
