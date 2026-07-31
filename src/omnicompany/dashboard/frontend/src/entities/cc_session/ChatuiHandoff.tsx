// 人用聊天落点卡片 —— 全部人用聊天(普通会话 + 总控)已迁到收编的 chatui(独立服务 :7348)。
// chatui 与驾驶舱 cc_session 是两套 session 系统(id 不通), 无法深链到某条具体会话,
// 一律开 chatui(新标签);总控带 ?provider=controller 让 chatui 预选总控 provider。
import React from 'react'
import { openChatui } from '../../lib/surface'

export const ChatuiHandoff: React.FC<{ provider?: string }> = ({ provider }) => {
  const isController = provider === 'controller'
  return (
    <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24, color: 'var(--fp-text)', background: 'transparent' }} data-testid="cc-chat-chatui-handoff">
      <div style={{ maxWidth: 420, textAlign: 'center', border: '1px solid var(--fp-border)', borderRadius: 8, padding: 24, background: 'var(--fp-solid)' }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
          {isController ? '总控对话已迁到 chatui' : '聊天已迁到 chatui'}
        </div>
        <div style={{ color: 'var(--fp-text-3)', fontSize: 14, lineHeight: 1.6, marginBottom: 16 }}>
          {isController
            ? '总控(BOSS SIGHT)现在是收编 chatui 里的一个 provider。点下方在 chatui 打开并预选总控。'
            : '人用聊天现在走收编的独立 chatui 服务。它和驾驶舱的会话是两套体系, 无法深链到这条具体会话, 点下方在 chatui 里开新会话。'}
        </div>
        <button
          type="button"
          style={{ border: '1px solid var(--fp-accent)', background: 'var(--fp-accent)', color: '#fff', borderRadius: 5, padding: '7px 16px', cursor: 'pointer', fontSize: 14 }}
          onClick={() => openChatui(isController ? 'controller' : undefined)}
          data-testid="cc-chat-open-chatui"
        >
          {isController ? '在 chatui 打开总控' : '在 chatui 打开'}
        </button>
      </div>
    </div>
  )
}

export default ChatuiHandoff
