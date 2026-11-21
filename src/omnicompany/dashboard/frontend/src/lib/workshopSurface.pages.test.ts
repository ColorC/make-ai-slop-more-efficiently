import { describe, expect, it } from 'vitest'
import { isWorkshopPage, workshopPageUrl, workshopTheme } from './workshopSurface'
import { canonicalizePanelTarget } from '../stores/panelsStore'
import { fetchWorkshopReviewCount } from './workshopReviewCount'

describe('Workshop 页面地址', () => {
  it('三个页面都走同源 /bside 代理并去掉 Workshop 自己的外壳', () => {
    expect(workshopPageUrl('projects', { base: 'dark' })).toBe('/bside/?embed=1&theme=omni')
    expect(workshopPageUrl('canvas', { base: 'dark' })).toBe('/bside/canvas?embed=1&theme=omni')
    expect(workshopPageUrl('review', { base: 'light' })).toBe('/bside/review?embed=1&theme=paper')
  })

  it('页面内定位: 画布带画布与卡片, 项目带项目', () => {
    expect(workshopPageUrl('canvas', { base: 'dark', target: 'canvas-1#card-9' }))
      .toBe('/bside/canvas?embed=1&theme=omni&canvas=canvas-1&card=card-9')
    expect(workshopPageUrl('projects', { base: 'dark', target: 'demogame' }))
      .toBe('/bside/?embed=1&theme=omni&project=demogame')
  })

  it('主题按皮肤明暗选', () => {
    expect(workshopTheme('dark')).toBe('omni')
    expect(workshopTheme('light')).toBe('paper')
    expect(isWorkshopPage('review')).toBe(true)
    expect(isWorkshopPage('main')).toBe(false)
  })
})

describe('退役界面的旧地址', () => {
  it.each([
    ['project_board', 'projects'],
    ['quest_board', 'projects'],
    ['authored', 'canvas'],
    ['review_queue', 'review'],
  ] as const)('%s 落到 Workshop 的 %s 页', (type, page) => {
    const target = canonicalizePanelTarget({ type, id: 'main' }, '旧标题', 'some-facet')
    expect(target.ref).toEqual({ type: 'workshop', id: page })
    expect(target.facet).toBeUndefined()
  })

  it('没退役的地址原样保留', () => {
    const target = canonicalizePanelTarget({ type: 'cc_session', id: 'abc' }, '会话', undefined)
    expect(target.ref).toEqual({ type: 'cc_session', id: 'abc' })
  })
})

describe('审阅角标', () => {
  const respond = (body: unknown, ok = true) => (async () => ({ ok, json: async () => body })) as unknown as typeof fetch

  it('等我处理 = 指派给我的待审 + 未读提审', async () => {
    expect(await fetchWorkshopReviewCount(respond({ unread: [1, 2], own_pending: [3] }))).toBe(3)
  })

  it('读不到按 0 算, 不抛错', async () => {
    expect(await fetchWorkshopReviewCount(respond({}, false))).toBe(0)
    expect(await fetchWorkshopReviewCount(respond({ unread: 'bad' }))).toBe(0)
    expect(await fetchWorkshopReviewCount((async () => { throw new Error('offline') }) as unknown as typeof fetch)).toBe(0)
  })
})

describe('Workshop 登录门', () => {
  it('没登录(401/403)才拦; 其余情况交给页面自己', async () => {
    const { checkWorkshopAccess } = await import('./workshopAccess')
    const status = (code: number) => (async () => ({ status: code })) as unknown as typeof fetch
    expect(await checkWorkshopAccess(status(200))).toBe('ok')
    expect(await checkWorkshopAccess(status(401))).toBe('login-required')
    expect(await checkWorkshopAccess(status(403))).toBe('login-required')
    expect(await checkWorkshopAccess(status(502))).toBe('ok')
    expect(await checkWorkshopAccess((async () => { throw new Error('offline') }) as unknown as typeof fetch)).toBe('ok')
  })

  it('登录地址带上登完要回的页面, 不带嵌入参数', async () => {
    const { workshopLoginUrl } = await import('./workshopAccess')
    expect(workshopLoginUrl('/bside/review?embed=1&theme=omni')).toBe('/bside/login?next=%2Freview%3Ftheme%3Domni')
    expect(workshopLoginUrl('/bside/?embed=1&theme=omni')).toBe('/bside/login?next=%2F%3Ftheme%3Domni')
  })

  it('嵌入框不允许换掉顶层页面', async () => {
    const { WORKSHOP_FRAME_SANDBOX } = await import('../entities/workshop')
    expect(WORKSHOP_FRAME_SANDBOX).not.toContain('allow-top-navigation')
    expect(WORKSHOP_FRAME_SANDBOX).toContain('allow-same-origin')
    expect(WORKSHOP_FRAME_SANDBOX).toContain('allow-scripts')
  })
})
