/**
 * sessionBootstrap.ts — 一次网关登录换 180 天持久会话 cookie。
 *
 * 问题(2026-08-16 诊断): 普通浏览器经 12443 网关只有 HTTP Basic, 凭证缓存是
 * 进程级内存 —— 重开浏览器/新窗口/手机切后台即丢, 表现为"反复要登录"。桌面版与
 * LOFA 早有持久 cookie 会话(omni_desktop_session), 普通浏览器缺一个入口。
 *
 * 机制: 网关侧 @desktop_session_bootstrap 规则本就接受「带 X-Omnicompany-Desktop: 1
 * 头的 POST /api/desktop/session」—— basic_auth 挑战通过后由网关注入私钥换出签名
 * cookie。浏览器 JS 完全可以走同一条路: Basic 缓存在则静默; 不在则浏览器对该 fetch
 * 弹一次原生登录框(正好是登录时机)。之后请求靠 cookie 过 forward_auth, Basic 缓存
 * 丢了也不受影响。
 *
 * localhost 直连(无网关)时端点返回 503 —— 静默放弃, 那里本来不需要登录。
 *
 * 2026-08-19 补: /state 也会把 **LOFA 的设备会话**(omni_lofa_session)算成"已登录"
 * (见 controlplane/desktop_sessions.py 的 _has_lofa_device_session)。否则在 LOFA 里
 * has_session 恒为 false, 这里每次加载都去打 Basic 挑战的 bootstrap 端点 → WebView
 * 弹登录框; 而换来的 cookie 是 SameSite=Lax, 在 LOFA 的跨站 iframe 里发不回来 ——
 * 用户看到的就是"远程网页无限要求登录"。别把判据改回"只认桌面 cookie"。
 */
const ID_KEY = 'omni.session.instanceId'

function stableInstanceId(): string {
  const fresh = (): string => {
    const id = (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}${Math.random()}`.repeat(2))
      .replace(/[^a-f0-9]/g, '')
    return (id + '0'.repeat(32)).slice(0, 32)
  }
  try {
    const existing = window.localStorage.getItem(ID_KEY)
    if (existing && /^[a-f0-9]{32}$/.test(existing)) return existing
    const id = fresh()
    window.localStorage.setItem(ID_KEY, id)
    return id
  } catch {
    return fresh()
  }
}

export async function ensureGatewaySession(): Promise<boolean> {
  try {
    const state = await fetch('/api/desktop/session/state', { cache: 'no-store' })
    if (!state.ok) return false // 无网关旁路或后端旧版: 不打扰
    const { has_session: has } = await state.json() as { has_session?: boolean }
    if (has) return true
    const res = await fetch('/api/desktop/session', {
      method: 'POST',
      // 网关只对带该头的 bootstrap 请求注入会话私钥; 浏览器主动带头是设计内路径。
      headers: { 'content-type': 'application/json', 'X-Omnicompany-Desktop': '1' },
      body: JSON.stringify({ instance_id: stableInstanceId() }),
    })
    return res.ok
  } catch {
    return false // 未登录(用户取消 Basic 框)或网络失败: 下次导航网关自己会挑战
  }
}
