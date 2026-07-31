/**
 * multiagent view 的数据客户端 — 配套 boss_sight/routes.py 的 GET /api/boss-sight/residents。
 * 数据源 Rust agent-scanner 索引(回落 Python 扫描),agent_digest 回填,总控第一公民。
 * 见 docs/plans/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (P1/P3)。
 */

export interface Resident {
  key: string
  provider: string
  session_id: string
  cwd?: string
  file?: string
  name: string
  project: string
  role: string
  identity: string
  location: string
  preview?: string
  current_task?: string
  initial_task?: string
  title?: string
  last_step?: string
  run_status: string
  running: boolean
  mtime?: number
  pty_id?: string | null
  active_plan?: string | null
  // 会话级任务绑定(SESSION-SELF-BINDING 阶段三前多为空, 属预期)。
  task_id?: string | null
  // 有会话自己声明的绑定(vs digest 推测) — residents.py: rec["authoritative"]=True。
  authoritative?: boolean
  is_controller?: boolean
  pinned?: boolean
  // agent 主动「请审阅本对话」举手(见 boss_sight/services/agent_attention.py)
  attention?: boolean
  attention_headline?: string
  attention_ts?: number
}

export interface ResidentsResponse {
  source: string
  count: number
  now: number
  residents: Resident[]
}

export async function fetchResidents(): Promise<ResidentsResponse> {
  const r = await fetch('/api/boss-sight/residents')
  if (!r.ok) throw new Error(`residents ${r.status}`)
  return r.json()
}

export interface TailLine {
  role: string
  text: string
}

/** 某 agent 会话最近活动行(详情面板"它在干嘛"feed)。失败/无 scanner → 空。 */
export async function fetchTail(sessionId: string, n = 14): Promise<TailLine[]> {
  try {
    const r = await fetch(`/api/boss-sight/residents/${encodeURIComponent(sessionId)}/tail?n=${n}`)
    if (!r.ok) return []
    const d = await r.json()
    return Array.isArray(d.lines) ? d.lines : []
  } catch {
    return []
  }
}

/** 选中某 agent → 设为当前跟随上下文(审阅台跟随视图据此过滤)。失败静默。 */
export async function setActiveContext(sessionId: string, kind = 'conversation'): Promise<void> {
  try {
    await fetch('/api/boss-sight/context/active', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, kind, source: 'multiagent-pick' }),
    })
  } catch {
    /* 测试环境/无 fetch: 静默 */
  }
}

/** 「发回意见」→ 压入该对话反馈队列, agent 侧 hook 取走继续。resolve=true 同时清除举手。 */
export async function sendFeedback(sessionId: string, message: string, resolve = false): Promise<boolean> {
  try {
    const r = await fetch('/api/boss-sight/agent/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message, author: 'user', resolve }),
    })
    return r.ok
  } catch {
    return false
  }
}

// 5 态归一(研究收敛): run_status(working/done/waiting/idle) → 展示分组。
// Needs-input 最该顶, 其次 Working, 然后 Done, 最后 Idle。
export type AgentGroup = 'needs_input' | 'working' | 'done' | 'idle'

export function groupOf(r: { run_status?: string; running?: boolean }): AgentGroup {
  switch (r.run_status) {
    case 'waiting':
      return 'needs_input'
    case 'working':
      return 'working'
    case 'done':
      return 'done'
    case 'idle':
      return 'idle'
    default:
      // Python 回落无 run_status 时,按 running 布尔粗分(在跑→运行中,否则空闲)。
      return r.running ? 'working' : 'idle'
  }
}
