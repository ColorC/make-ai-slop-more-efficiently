import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useReviewMaximize } from './reviewMaximizeStore'

// 最小化的 DockviewApi/面板桩, 只实现 store 用到的方法, 并让 maximized 状态可被读回,
// 以便验证"全屏审阅"状态机和与 dockview 自身状态的回收。
function makeApi() {
  let maximized = false
  const panel = {
    api: {
      setActive: vi.fn(),
      maximize: vi.fn(() => {
        maximized = true
      }),
    },
  }
  const api = {
    getPanel: vi.fn((id: string) => (id === 'tab-1' ? panel : undefined)),
    hasMaximizedGroup: vi.fn(() => maximized),
    exitMaximizedGroup: vi.fn(() => {
      maximized = false
    }),
  }
  return { api, panel, setMaximized: (v: boolean) => (maximized = v) }
}

describe('reviewMaximizeStore', () => {
  beforeEach(() => {
    useReviewMaximize.setState({ api: null, maximizedTabId: null, tabMenu: null })
  })

  it('maximizes a known tab: activates it, calls dockview maximize, records the id', () => {
    const { api, panel } = makeApi()
    useReviewMaximize.getState().registerApi(api as any)

    useReviewMaximize.getState().maximize('tab-1')

    expect(panel.api.setActive).toHaveBeenCalledTimes(1)
    expect(panel.api.maximize).toHaveBeenCalledTimes(1)
    expect(useReviewMaximize.getState().maximizedTabId).toBe('tab-1')
  })

  it('ignores maximize for an unknown tab without entering maximized state', () => {
    const { api } = makeApi()
    useReviewMaximize.getState().registerApi(api as any)

    useReviewMaximize.getState().maximize('missing')

    expect(useReviewMaximize.getState().maximizedTabId).toBeNull()
  })

  it('exit calls dockview exit and clears the maximized id', () => {
    const { api } = makeApi()
    useReviewMaximize.getState().registerApi(api as any)
    useReviewMaximize.getState().maximize('tab-1')

    useReviewMaximize.getState().exit()

    expect(api.exitMaximizedGroup).toHaveBeenCalledTimes(1)
    expect(useReviewMaximize.getState().maximizedTabId).toBeNull()
  })

  it('reconciles when dockview drops its maximized group externally (e.g. tab closed)', () => {
    const { api, setMaximized } = makeApi()
    useReviewMaximize.getState().registerApi(api as any)
    useReviewMaximize.getState().maximize('tab-1')
    expect(useReviewMaximize.getState().maximizedTabId).toBe('tab-1')

    // Dockview exits maximize on its own (panel removed / dragged out).
    setMaximized(false)
    useReviewMaximize.getState().syncFromDockview()

    expect(useReviewMaximize.getState().maximizedTabId).toBeNull()
  })

  it('opens and closes the tab context menu', () => {
    useReviewMaximize.getState().openTabMenu({ x: 10, y: 20, tabId: 'tab-1', title: '审阅' })
    expect(useReviewMaximize.getState().tabMenu).toMatchObject({ x: 10, y: 20, tabId: 'tab-1' })

    useReviewMaximize.getState().closeTabMenu()
    expect(useReviewMaximize.getState().tabMenu).toBeNull()
  })

  it('maximizing closes any open tab menu', () => {
    const { api } = makeApi()
    useReviewMaximize.getState().registerApi(api as any)
    useReviewMaximize.getState().openTabMenu({ x: 1, y: 2, tabId: 'tab-1', title: '审阅' })

    useReviewMaximize.getState().maximize('tab-1')

    expect(useReviewMaximize.getState().tabMenu).toBeNull()
  })
})
