// ⚠️ 已退役(DECOMMISSIONED)— 旧 IDE session 实体(chat+file+terminal+timeline, legacy trace 路线)。
// 已被 cc_session(PTY/会话实体)取代; 人用对话迁到收编 chatui; 事件由 EventStream/TraceList 覆盖。
// 当前唯一打开入口是已退役的 Sidebar.tsx:128(旧壳, 不再挂载)→ 驾驶舱内不可达。
// 仍保留注册(registerEntities.ts)+ 文件: note/wikilinks 类型白名单含 'session', 贸然注销有残链风险;
// 按「先禁用后删·不急于删」, 确认长期无引用后才在独立提交里删。处置依据见 plan.md(§3/§5/§9 Phase 4)。
// 注意: trace 实体**仍活跃**(底部 TraceList 会 openTab trace), 不在退役之列。
import { ideApi } from '../../api/ideClient'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import Editor from './Editor'

export interface SessionEntity extends Entity {
  type: 'session'
  status: string
  task_desc: string | null
  created_at: string
}

const resolver: EntityResolver<SessionEntity> = {
  type: 'session',
  async fetch(id) {
    const sessions = await ideApi.sessions()
    const s = sessions.find((x) => x.trace_id === id)
    if (!s) {
      return {
        type: 'session', id, title: id.slice(0, 24),
        status: 'unknown', task_desc: null, created_at: '',
      }
    }
    return {
      type: 'session', id: s.trace_id, title: s.task_desc || s.trace_id.slice(0, 24),
      status: s.status, task_desc: s.task_desc, created_at: s.created_at,
    }
  },
  async list() {
    const sessions = await ideApi.sessions()
    return sessions.map((s) => ({
      type: 'session' as const, id: s.trace_id, title: s.task_desc || s.trace_id.slice(0, 24),
      status: s.status, task_desc: s.task_desc, created_at: s.created_at,
    }))
  },
}

export const sessionRegistration: EntityRegistration<SessionEntity> = {
  resolver,
  renderer: { type: 'session', Editor },
  label: 'Agent 会话',
  icon: '⌨',
}
