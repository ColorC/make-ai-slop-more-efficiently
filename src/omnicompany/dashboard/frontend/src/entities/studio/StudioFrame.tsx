import React, { useEffect, useRef, useState } from 'react'
import { ccApi, type CcSessionMeta } from '../../api/ccClient'
import { usePanels } from '../../stores/panelsStore'
import { STUDIO_TAB_TITLE } from './index'
import { useStudioSessions } from './studioSessions'

export const STUDIO_PAGE_URL = '/api/embed/studio/index.html'
export const STUDIO_COMPANY_URL = '/api/studio/company'
export const STUDIO_SESSIONS_URL = '/api/studio/sessions'
export const STUDIO_PROJECT = 'hunao-games'

type StudioState = 'checking' | 'ready' | 'missing'

interface StudioSource {
  provider: string
  model?: string | null
  effort?: string | null
}

interface StudioStaff extends StudioSource {
  id: string
  name: string
  dir: string
  /** 这名员工可以开在哪些源上(账号 + 模型)。第一项是默认; 没写就只有顶层那一组。 */
  sources?: StudioSource[]
}

interface StudioCompany {
  project?: string
  bootstrap_task?: string
  staff?: StudioStaff[]
}

type StudioReply =
  | { type: 'omni-studio:session-created'; staff: string; session: string }
  | { type: 'omni-studio:sessions-changed'; ended?: string }
  | { type: 'omni-studio:error'; message: string }

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

async function loadCompany(): Promise<StudioCompany> {
  const response = await fetch(STUDIO_COMPANY_URL, { credentials: 'same-origin', cache: 'no-store' })
  if (!response.ok) throw new Error(`读取员工配置失败：HTTP ${response.status}`)
  return response.json()
}

/** 小页签上的字: 页面给了就用页面的(它知道员工名和标题), 否则用会话自己的标题, 再不行用 id 前 8 位。 */
function sessionLabel(wanted: unknown, meta: CcSessionMeta | undefined, id: string): string {
  if (typeof wanted === 'string' && wanted.trim()) return wanted
  return String(meta?.display_title || meta?.provider_title || '').trim() || id.slice(0, 8)
}

/**
 * 约定第 3 节: 按 staff.json 给这名员工开新会话, 在 Studio 页签的会话区打开它, 返回会话 id。
 * 页面可以点名一个源(provider id); 只认 staff.json 里给这名员工列出的, 不认页面随手写的。
 */
async function createStaffSession(staffId: string, extraTask: unknown, wantedSource: unknown): Promise<{ staff: StudioStaff; id: string }> {
  const company = await loadCompany()
  const staff = (company.staff || []).find((item) => item.id === staffId)
  if (!staff) throw new Error(`没有这名员工：${staffId}`)
  const sources: StudioSource[] = staff.sources?.length ? staff.sources : [staff]
  const source = typeof wantedSource === 'string' && wantedSource
    ? sources.find((item) => item.provider === wantedSource)
    : sources[0]
  if (!source) throw new Error(`${staff.name} 不能开在这个源上：${String(wantedSource)}`)
  const boss = typeof extraTask === 'string' ? extraTask.trim() : ''
  const bootstrap = company.bootstrap_task || ''
  const task = boss ? `${bootstrap}\n老板交代：${boss}` : bootstrap
  const meta = await ccApi.createAgentWorker({
    provider: source.provider,
    model: source.model || undefined,
    effort: source.effort || undefined,
    cwd: staff.dir,
    project: STUDIO_PROJECT,
    topic: `studio:${staff.id}`,
    task: task || undefined,
    visibility: 'visible',
  })
  useStudioSessions.getState().openSession(meta.id, `${staff.name} · ${meta.id.slice(0, 8)}`)
  return { staff, id: meta.id }
}

async function openStudioSession(sessionId: unknown, label: unknown): Promise<void> {
  if (typeof sessionId !== 'string' || !sessionId) throw new Error('缺少会话 id')
  const sessions = await ccApi.list({ includeRecoverable: false })
  const meta = sessions.find((item) => item.id === sessionId)
  if (!meta || !meta.alive) throw new Error(`会话已结束，打不开：${sessionId}`)
  useStudioSessions.getState().openSession(sessionId, sessionLabel(label, meta, sessionId))
}

/**
 * 结束一个会话(仪表盘现成的结束接口), 从会话区拿掉, 并让嵌着的 Studio 页面刷新会话列表。
 * 结束接口报错但会话其实已经不在了(比如自己先退出了), 也照样拿掉。
 */
export async function endStudioSession(sessionId: string): Promise<void> {
  try {
    await ccApi.kill(sessionId)
  } catch (cause) {
    const list = await ccApi.list({ includeRecoverable: false }).catch(() => null)
    const meta = list?.find((item) => item.id === sessionId)
    if (!list || meta?.alive) throw cause
  }
  const store = useStudioSessions.getState()
  store.collapse(sessionId)
  store.sessionsChanged(sessionId)
}

/**
 * 页面要求结束的会话必须是公司名下的(就是页面会话列表里那些), 不替页面结束别的会话。
 * 先看仪表盘会话列表里的归属(快); 对不上再问 /api/studio/sessions(要两三秒, 它还认按 provider 对上的会话)。
 */
async function endCompanySession(sessionId: unknown): Promise<void> {
  if (typeof sessionId !== 'string' || !sessionId) throw new Error('缺少会话 id')
  const list = await ccApi.list({ includeRecoverable: false }).catch(() => [] as CcSessionMeta[])
  const meta = list.find((item) => item.id === sessionId)
  const ownedHere = !!meta && (meta.project === STUDIO_PROJECT || String(meta.topic || '').startsWith('studio:'))
  if (!ownedHere) {
    const response = await fetch(STUDIO_SESSIONS_URL, { credentials: 'same-origin', cache: 'no-store' })
    if (!response.ok) throw new Error(`读取公司会话失败：HTTP ${response.status}`)
    const data = await response.json() as { sessions?: Array<{ id?: unknown }> }
    if (!(data.sessions || []).some((item) => item.id === sessionId)) {
      throw new Error(`不是公司的会话，不结束：${sessionId}`)
    }
  }
  await endStudioSession(sessionId)
}

/** 页面两个视图: chat = 群聊 + 会话列表(Studio 页签左栏), scene = 3D 办公楼(会话侧栏)。 */
export type StudioPageView = 'chat' | 'scene'

export function studioPageUrl(view: StudioPageView): string {
  return `${STUDIO_PAGE_URL}?view=${view}`
}

export interface StudioFrameProps {
  /** 外层容器的 data-testid。Studio 页签左栏用 studio-chat, 侧栏用自己的。 */
  testid?: string
  /** 嵌入哪个视图。两个视图之间由页面自己用 BroadcastChannel 通气, 仪表盘不转发。 */
  view: StudioPageView
}

/**
 * 撑满容器的 Studio iframe + 页面 ↔ 仪表盘桥接。Studio 页签左栏(view=chat)
 * 与会话侧栏的 Studio 页(view=scene)共用这一份, 各自只认自己 iframe 发来的消息。iframe 拿不到 HTTP 状态码, 所以先探一次入口页: 非 200 显示
 * 一行提示, 不留白屏。挂载 / 卸载由调用方决定。
 *
 * 页面 ↔ 仪表盘走同源 postMessage(hunao-games/docs/studio-api.md 第 3 节):
 * 只认这个 iframe 发来的、同源的消息。开会话、打开会话都放进 Studio 页签的会话区(useStudioSessions),
 * 不另开主区域页签; 侧栏(scene)里点的, 顺带把 Studio 页签切到前台。
 */
export default function StudioFrame({ testid = 'studio-view', view }: StudioFrameProps) {
  const [state, setState] = useState<StudioState>('checking')
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const sessionsVersion = useStudioSessions((store) => store.sessionsVersion)
  const lastEnded = useStudioSessions((store) => store.lastEnded)
  const postedVersionRef = useRef(sessionsVersion)

  useEffect(() => {
    let active = true
    fetch(STUDIO_PAGE_URL, { credentials: 'same-origin', cache: 'no-store' })
      .then((response) => { if (active) setState(response.ok ? 'ready' : 'missing') })
      .catch(() => { if (active) setState('missing') })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const origin = window.location.origin
    const reply = (target: Window, message: StudioReply) => {
      try {
        target.postMessage(message, origin)
      } catch {
        // iframe 已导航走或卸载: 没人可回。
      }
    }
    // 侧栏里点的: Studio 页签不一定在前台, 切过去(单例页签, 已开着就只是切到它)。
    const reveal = () => {
      if (view === 'scene') usePanels.getState().openTab({ type: 'studio', id: 'main' }, STUDIO_TAB_TITLE)
    }
    const onMessage = (event: MessageEvent) => {
      const target = frameRef.current?.contentWindow
      if (!target || event.source !== target || event.origin !== origin) return
      const data = event.data as { type?: unknown; staff?: unknown; source?: unknown; task?: unknown; session?: unknown; label?: unknown } | null
      if (!data || typeof data !== 'object') return
      if (data.type === 'omni-studio:new-session') {
        const staffId = typeof data.staff === 'string' ? data.staff : ''
        createStaffSession(staffId, data.task, data.source)
          .then(({ staff, id }) => {
            reveal()
            reply(target, { type: 'omni-studio:session-created', staff: staff.id, session: id })
          })
          .catch((cause) => reply(target, { type: 'omni-studio:error', message: `开新会话失败：${errorText(cause)}` }))
      } else if (data.type === 'omni-studio:open-session') {
        openStudioSession(data.session, data.label)
          .then(reveal)
          .catch((cause) => reply(target, { type: 'omni-studio:error', message: errorText(cause) }))
      } else if (data.type === 'omni-studio:end-session') {
        // 成功后由 sessionsVersion 变化统一通知页面刷新(两个视图都收到)。
        endCompanySession(data.session)
          .catch((cause) => reply(target, { type: 'omni-studio:error', message: `结束会话失败：${errorText(cause)}` }))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [view])

  // 公司会话变了(结束了一个): 让页面马上重拉会话列表, 不等它自己的 15 秒轮询;
  // 带上结束的 id, 页面在守护进程跟上之前先把那一行标成已结束。
  useEffect(() => {
    if (sessionsVersion === postedVersionRef.current) return
    postedVersionRef.current = sessionsVersion
    const target = frameRef.current?.contentWindow
    if (!target) return
    const message: StudioReply = lastEnded
      ? { type: 'omni-studio:sessions-changed', ended: lastEnded }
      : { type: 'omni-studio:sessions-changed' }
    try {
      target.postMessage(message, window.location.origin)
    } catch { /* iframe 已卸载 */ }
  }, [lastEnded, sessionsVersion])

  return (
    <div
      data-testid={testid}
      data-state={state}
      style={{ position: 'relative', width: '100%', height: '100%', minHeight: 0, overflow: 'hidden' }}
    >
      {state === 'ready' && (
        <iframe
          ref={frameRef}
          title="胡闹游戏 Studio"
          src={studioPageUrl(view)}
          data-testid="studio-frame"
          data-view={view}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 0, display: 'block' }}
        />
      )}
      {state === 'missing' && (
        <div data-testid="studio-missing" style={{ padding: 16 }}>Studio 页面还没发布</div>
      )}
    </div>
  )
}
