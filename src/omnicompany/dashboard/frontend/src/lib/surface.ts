// 单区渲染(三区化)的真源: ?surface=<queue|material|comments|full> 决定本页只渲染哪个语义区。
// full / 缺省 = 完整驾驶舱(浏览器、主 omnichat webview); 其余 = 把该区单独挂进 VSCode 原生表面
// (主侧栏 queue / 编辑页签 material / 次级侧栏 comments)。同一份前端, 只是挂载位置不同, 零分叉。

export type Surface = 'full' | 'queue' | 'material' | 'material-embed' | 'comments' | 'project' | 'plan' | 'threads' | 'authored' | 'multiagent' | 'review-overview' | 'session-companion'

const REGION_SURFACES: Surface[] = ['queue', 'material', 'material-embed', 'comments', 'project', 'plan', 'threads', 'authored', 'multiagent', 'review-overview', 'session-companion']

export function readSurface(search: string = window.location.search): { surface: Surface; id: string | null } {
  const p = new URLSearchParams(search)
  const raw = (p.get('surface') || 'full') as Surface
  return {
    surface: REGION_SURFACES.includes(raw) ? raw : 'full',
    id: p.get('id'),
  }
}

/** 页面是否嵌在 VSCode webview iframe 里(有不同的父窗口)。 */
export function isInWebview(): boolean {
  try { return !!(window.parent && window.parent !== window) } catch { return true }
}

/** 给 webview 宿主(扩展)发消息 —— 外壳会把带 __omnichat 标记的消息转发给扩展 impl。
 * 既发 parent(一级 iframe)也发 top(多层嵌套), 与 copyText/openInVscode 同款冗余。 */
export function postHostMessage(msg: Record<string, unknown>): void {
  const payload = { __omnichat: true, ...msg }
  try { window.parent?.postMessage(payload, '*') } catch { /* */ }
  try { if (window.top && window.top !== window.parent) window.top.postMessage(payload, '*') } catch { /* */ }
}

/** 在 omnidashboard(完整驾驶舱)里打开某条目: 宿主开一个完整壳编辑页签并深链到该条目。
 * 这是 VSCode 主侧栏各 section 列表点条目的默认行为(条目在编辑区打开, 侧栏只管导航)。 */
export function openInOmnidashboard(openType: string, openId: string, facet?: string, title?: string): void {
  postHostMessage({ type: 'open-omnidashboard', openType, openId, facet: facet || null, title: title || openId })
}

/** 页面是否运行在本扩展渲染的 webview 外壳里(外壳在 iframe URL 上加 omniext=1 标记)。
 *  只有这种宿主才有 __omnichat postMessage 转发链; Simple Browser / 普通浏览器 / 其它 iframe 宿主都没有,
 *  在那些地方发 postMessage 是静默丢弃 —— "在 VSCode 打开点了没反应"的一类根因。 */
export function isInExtShell(): boolean {
  try { return new URLSearchParams(window.location.search).has('omniext') } catch { return false }
}

/** 轻量角标提示(纯 DOM, 不依赖 React 树): "在 VSCode 打开"这类效果发生在另一个窗口的动作,
 *  必须给页面内可见反馈, 否则成功也像"点了没反应"(2026-07-04 用户三连"点不了"的教训)。 */
export function notice(text: string, kind: 'pending' | 'ok' | 'err' = 'ok'): void {
  const ID = 'omni-surface-notice'
  let el = document.getElementById(ID)
  if (!el) {
    el = document.createElement('div')
    el.id = ID
    el.setAttribute('data-testid', 'surface-notice')
    Object.assign(el.style, {
      position: 'fixed', right: '16px', bottom: '16px', zIndex: '99999',
      maxWidth: '360px', padding: '10px 14px', borderRadius: '8px',
      fontSize: '13px', lineHeight: '1.5', color: '#e8eef6',
      background: 'rgba(20,26,36,0.96)', border: '1px solid #3a4761',
      boxShadow: '0 6px 24px rgba(0,0,0,.45)', whiteSpace: 'pre-line',
    } as CSSStyleDeclaration)
    document.body.appendChild(el)
  }
  el.style.borderColor = kind === 'err' ? '#c2504f' : kind === 'pending' ? '#3a4761' : '#3f8f5f'
  el.textContent = text
  const w = window as unknown as { __omniNoticeTimer?: number }
  if (w.__omniNoticeTimer) window.clearTimeout(w.__omniNoticeTimer)
  if (kind !== 'pending') {
    w.__omniNoticeTimer = window.setTimeout(() => { el?.remove() }, kind === 'err' ? 12000 : 7000)
  }
}

/** 材料"在 VSCode 编辑页签打开"的统一入口, 三级链路(每级都给页面内可见反馈):
 *  ① 本扩展外壳里 → postMessage 转发(最快); 1.5s 内收到扩展 ack 即成。
 *  ② 无 ack(webview→扩展桥接失联是已知慢性病, 2026-07-02/04 实锤)或根本不在外壳里
 *     → 后端 /api/dev/open-vscode-uri 本机执行 code --open-url vscode://…(完全不依赖消息桥)。
 *     注意: 页签开在【桌面 VSCode 窗口】且不抢焦点 —— 提示里明说, 免得像没反应。
 *  ③ 后端也失败 → 浏览器直开 vscode:// 协议链接(会弹"打开 VSCode?"确认)。 */
export function openMaterialNative(materialId: string, title?: string): void {
  const q = new URLSearchParams({ id: materialId })
  if (title) q.set('title', title)
  const deepLink = `vscode://omnicompany.omni-chat/material?${q.toString()}`
  const viaDeepLink = () => {
    // 深链备胎: URI 送达会弹"允许扩展打开?"确认框且弹在后台窗口 — 仅当队列通道不可用才用。
    void fetch('/api/dev/open-vscode-uri', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri: deepLink }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
      notice('已发深链到桌面 VSCode。\n注意: VSCode 窗口里可能弹出"是否允许 Omni Chat 打开此 URI"确认框, 切过去点允许。', 'ok')
    }).catch((e) => {
      notice(`后端不可达: ${String(e).slice(0, 160)}\n改试浏览器协议直开(会弹确认框)…`, 'err')
      try { window.open(deepLink, '_blank') } catch { /* 到头了 */ }
    })
  }
  const viaBackend = () => {
    notice('正在请求 VSCode 打开材料页签…', 'pending')
    void fetch('/api/dev/request-open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'material', id: materialId, title: title || materialId }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
      notice('已被 VSCode 领取, 页签随即打开(不抢前台焦点, 切窗口看)。\n第一次打开要加载界面, 会慢几秒; 之后就快了。\n若一直没有: 说明没有活着的 VSCode 扩展在领取, 把这句话发给 AI。', 'ok')
    }).catch(() => { viaDeepLink() })
  }
  if (!isInExtShell()) { viaBackend(); return }
  notice('已请求 VSCode 打开页签…', 'pending')
  let acked = false
  const onAck = (ev: MessageEvent) => {
    const d = ev.data as { __omnichat_ack?: boolean; type?: string; materialId?: string } | null
    if (d && d.__omnichat_ack === true && d.type === 'open-material-native-ack' && d.materialId === materialId) {
      acked = true
      window.removeEventListener('message', onAck)
      notice('VSCode 已打开材料页签', 'ok')
    }
  }
  window.addEventListener('message', onAck)
  postHostMessage({ type: 'open-material-native', materialId, title })
  window.setTimeout(() => {
    window.removeEventListener('message', onAck)
    if (!acked) viaBackend()
  }, 1500)
}

/** 收编的 chatui(独立 node 服务, 上游原版 CCUI)地址。默认同主机 :7348, 可由注入的 window.__OMNI_CHATUI_URL 覆盖。
 *  与 main.tsx 的 chatuiUrl 同逻辑(单一来源)。
 *  可选 provider: 带上 ?provider=<x> 让 chatui 预选 provider(如 controller=总控)。 */
export function chatuiUrl(provider?: string): string {
  const injected = (window as unknown as { __OMNI_CHATUI_URL?: string }).__OMNI_CHATUI_URL
  const base = injected || `${window.location.protocol}//${window.location.hostname}:7348/`
  if (!provider) return base
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}provider=${encodeURIComponent(provider)}`
}

/** 人用聊天落点: 新标签打开 chatui 首页。
 *  chatui 与驾驶舱 cc_session 是两套 session 系统, id 不通, 所以不深链具体会话, 一律开首页。
 *  可选 provider 预选(总控对话落点传 'controller')。
 *  用 window.open(新标签)而非导航, 以免丢掉驾驶舱本页; 不依赖宿主(浏览器/webview 下都成立)。 */
export function openChatui(provider?: string): void {
  window.open(chatuiUrl(provider), '_blank')
}

/** 对话"在 VSCode 打开": claude_code → 唤起 Claude Code(官方插件/CLI); 其它(codex 等) → 开 PowerShell 终端跑 codex resume。 */
export function openChatInVscode(provider: string | undefined, cwd: string | undefined, sessionId?: string): void {
  const isClaude = (provider || '').includes('claude')
  postHostMessage({
    type: isClaude ? 'open-in-claude-code' : 'open-codex-terminal',
    cwd: cwd || '',
    sessionId: sessionId || '',
  })
}
