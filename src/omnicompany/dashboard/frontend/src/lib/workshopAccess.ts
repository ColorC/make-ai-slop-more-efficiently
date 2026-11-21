// 驾驶舱嵌 Workshop 页面前先问一句「Workshop 认不认得我」。
// 机主设备上驾驶舱后端的 /bside 代理会替机主带上身份, 这里直接就是 ok;
// 别的访客要自己登录 Workshop, 登录页不能放在嵌入框里(collab platform登录拒绝被嵌),
// 所以由宿主给一个「在新窗口登录」的入口, 登完再嵌。
const CONTEXT_URL = '/bside/api/workspace/context'

export type WorkshopAccess = 'ok' | 'login-required'

export async function checkWorkshopAccess(fetchImpl: typeof fetch = fetch): Promise<WorkshopAccess> {
  try {
    const response = await fetchImpl(CONTEXT_URL, { credentials: 'same-origin', headers: { accept: 'application/json' } })
    if (response.status === 401 || response.status === 403) return 'login-required'
    // 其它情况(包括 Workshop 还在启动)都交给页面自己: 它有自己的启动页和报错。
    return 'ok'
  } catch {
    return 'ok'
  }
}

/** Workshop 登录页地址; next 是登完要回到的 Workshop 页面(去掉嵌入参数, 新窗口里看的是完整页面)。 */
export function workshopLoginUrl(embeddedPageUrl: string): string {
  const next = embeddedPageUrl.replace(/^\/bside/, '').replace('embed=1&', '').replace(/[?&]embed=1$/, '') || '/'
  return `/bside/login?next=${encodeURIComponent(next)}`
}
