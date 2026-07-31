// 被 cc_session/index.tsx 用 React.lazy 动态引入。PTY 路线的 ./Editor 依赖 xterm(~291KB),
// 拆到这里后 xterm 只在真正打开一个 cc_session tab 时才下载, 不再常驻首屏 bundle。
import React from 'react'
import Editor from './Editor'
import type { CcSessionEntity } from './index'
import { ChatuiHandoff } from './ChatuiHandoff'

// 全部人用聊天(含总控 provider==='controller')已迁到收编的 chatui(独立服务 :7348)。
// chatui 与驾驶舱 cc_session 是两套 session 系统(id 不通), 无法深链具体会话, 一律开 chatui(新标签);
// 总控带 ?provider=controller 预选。手搓 CcChatPanel 已删。pty 分支保持原样。
const EditorRouter: React.FC<{ entity: CcSessionEntity; facet?: string }> = ({ entity }) => {
  if (entity.kind === 'chat') {
    const isController = (entity.provider || '') === 'controller'
    return <ChatuiHandoff provider={isController ? 'controller' : undefined} />
  }
  return <Editor entity={entity} />
}

export default EditorRouter
