import React, { type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EditorArea, {
  PanelSwapIn,
  compactCliTabTitle,
} from './EditorArea'
import { CONTROLLER_TAB_ID, usePanels, withDefaultTabs } from '../stores/panelsStore'
import { TAB_TITLE_LIMITS, useUiSettings } from '../stores/uiSettingsStore'

const dockMock = vi.hoisted(() => {
  const panels: any[] = []
  const defaultGroup = { id: 'group-main' }
  const api: any = {
    panels,
    addPanel: vi.fn((options: any) => {
      // 对齐真实 dockview: 只有带 direction 的 position 才新劈组;
      // referencePanel(+index) 无 direction = 插入参照面板所在组(同组页签)。
      const group = options.position?.direction ? { id: `split-${options.id}` } : defaultGroup
      const panel: any = {
        id: options.id,
        group,
        options,
        api: {
          isActive: !options.inactive,
          isVisible: !options.inactive,
          title: options.title,
          renderer: options.renderer,
          close: vi.fn(),
          onDidVisibilityChange: vi.fn(() => ({ dispose: vi.fn() })),
          setTitle: vi.fn((title: string) => {
            panel.api.title = title
            panel.options.title = title
          }),
          setRenderer: vi.fn((renderer: 'always' | 'onlyWhenVisible') => {
            panel.api.renderer = renderer
            panel.options.renderer = renderer
          }),
          setActive: vi.fn(() => {
            for (const candidate of panels) {
              if (candidate.group === panel.group) candidate.api.isActive = false
            }
            panel.api.isActive = true
          }),
          moveTo: vi.fn((moveOptions: any) => {
            panel.moveOptions = moveOptions
            panel.group = { id: `moved-${options.id}` }
          }),
        },
      }
      if (!options.inactive) {
        for (const candidate of panels) {
          if (candidate.group === group) candidate.api.isActive = false
        }
      }
      panels.push(panel)
      return panel
    }),
    getPanel: vi.fn((id: string) => panels.find((panel) => panel.id === id)),
    onDidActivePanelChange: vi.fn(),
    onDidRemovePanel: vi.fn(),
    onDidMaximizedGroupChange: vi.fn(),
    hasMaximizedGroup: vi.fn(() => false),
    exitMaximizedGroup: vi.fn(),
  }
  return { api, panels }
})

vi.mock('dockview', async () => {
  const ReactModule = await import('react')
  return {
    DockviewReact: ({ onReady }: { onReady: (event: any) => void }) => {
      ReactModule.useEffect(() => {
        onReady({ api: dockMock.api })
      }, [])
      return ReactModule.createElement('div', { 'data-testid': 'dockview-mock' })
    },
  }
})

function dispatchDrag(target: EventTarget, type: 'dragstart' | 'dragend', point?: { clientX: number; clientY: number; screenX: number; screenY: number }): void {
  const event = new Event(type, { bubbles: true, cancelable: true })
  if (point) {
    Object.defineProperties(event, {
      clientX: { value: point.clientX },
      clientY: { value: point.clientY },
      screenX: { value: point.screenX },
      screenY: { value: point.screenY },
    })
  }
  target.dispatchEvent(event)
}

vi.mock('../entities/cc_session/SessionCompanion' , async () => {
  const ReactModule = await import('react')
  return {
    default: ({ sessionId, headerActions }: { sessionId: string; headerActions?: ReactNode }) => {
      const [page, setPage] = ReactModule.useState('multiagent')
      return ReactModule.createElement(
        'div',
        {
          'data-testid': 'session-companion',
          'data-session-id': sessionId,
          'data-page': page,
        },
        headerActions,
        ReactModule.createElement('button', {
          'data-testid': 'mock-companion-page',
          onClick: () => setPage('overview'),
        }),
      )
    },
  }
})

describe('EditorArea bounded heavy-tab retention', () => {
  const tab = (type: string, id: string) => ({ id: `${type}:${id}`, ref: { type, id }, title: id }) as any

  beforeEach(() => {
    dockMock.panels.splice(0, dockMock.panels.length)
    vi.clearAllMocks()
    window.localStorage.clear()
    useUiSettings.setState({ tabLayout: 'single-row', tabTitleMaxLength: TAB_TITLE_LIMITS.default })
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: CONTROLLER_TAB_ID })
  })

  // 2026-08-07 用户改定: 所有打开的页签一并常驻, 不再有"最近 N 个"热集 ——
  // 热集外的页签每次切走都被 dockview 卸载, 那正是"切换任何页签都要加载"的根因。
  it('creates every panel with the always renderer', async () => {
    const heavy = tab('review_material', 'review-1')
    const web = tab('web_review', 'web-1')
    const note = tab('note', 'note-1')
    const cli = tab('cc_session', 'pty-1')
    usePanels.setState({ tabs: withDefaultTabs([heavy, web, note, cli]), activeId: note.id })

    render(<EditorArea />)

    await waitFor(() => expect(dockMock.api.getPanel(cli.id)).toBeTruthy())
    expect(dockMock.panels).toHaveLength(6) // 2 个固定页签(M4 退役任务窗口) + 4 个
    for (const panel of dockMock.panels) {
      expect(panel.options.renderer).toBe('always')
      expect(panel.api.renderer).toBe('always')
    }
  })

  it('never downgrades a panel to onlyWhenVisible when other tabs are activated', async () => {
    const tabs = Array.from({ length: 8 }, (_, index) => tab('web_review', `web-${index + 1}`))
    usePanels.setState({ tabs: tabs as any, activeId: tabs[0].id })

    render(<EditorArea />)
    await waitFor(() => expect(dockMock.api.addPanel).toHaveBeenCalledTimes(tabs.length))

    for (const current of tabs) {
      act(() => usePanels.getState().activate(current.id))
    }

    for (const current of tabs) {
      expect(dockMock.api.getPanel(current.id).api.renderer).toBe('always')
    }
    expect(dockMock.api.getPanel(tabs[0].id).api.setRenderer).not.toHaveBeenCalled()
  })
})

describe('PanelSwapIn under always-mounted panels', () => {
  // ⚠ 带 animation 的元素会建立 backdrop root, 里面的 backdrop-filter 会糊成一片虚无。
  // 在 display:none 的隐藏页签里动画根本不播 → animationend 不来 → class 永远摘不掉,
  // 于是那个页签切回来时全站玻璃材质集体失效。所以隐藏期间一律不挂 class。
  it('never attaches the animation class while the panel is hidden', () => {
    const { container, rerender } = render(<PanelSwapIn visible={false}><i /></PanelSwapIn>)
    const shell = container.firstElementChild as HTMLElement
    expect(shell.className).toBe('')

    rerender(<PanelSwapIn visible={false}><i /></PanelSwapIn>)
    expect(shell.className).toBe('')
  })

  it('replays the fade once when the tab comes back and cleans the class up afterwards', () => {
    const { container, rerender } = render(<PanelSwapIn visible={false}><i /></PanelSwapIn>)
    const shell = container.firstElementChild as HTMLElement

    rerender(<PanelSwapIn visible><i /></PanelSwapIn>)
    expect(shell.className).toBe('mo-swap-in')

    // 播完必须把 class 摘掉(带 animation 的元素是 backdrop root, 留着就废掉面板内的玻璃材质)。
    // jsdom 没有 AnimationEvent, React 因此监听 webkit 前缀名(见 getVendorPrefixedEventName);
    // 两个名字都发一遍, 换环境也不会假绿。
    for (const type of ['animationend', 'webkitAnimationEnd']) {
      fireEvent(shell, new Event(type, { bubbles: true }))
    }
    expect(shell.className).toBe('')
  })
})

describe('EditorArea dock placement', () => {
  beforeEach(() => {
    dockMock.panels.splice(0, dockMock.panels.length)
    vi.clearAllMocks()
    window.localStorage.clear()
    useUiSettings.setState({ tabLayout: 'single-row', tabTitleMaxLength: TAB_TITLE_LIMITS.default })
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: CONTROLLER_TAB_ID })
  })

  it('passes right-side placement to Dockview when adding a new panel', async () => {
    usePanels.getState().openTab(
      { type: 'material', id: 'mat-1' },
      'material 1',
      undefined,
      { direction: 'right', referenceTabId: CONTROLLER_TAB_ID },
    )

    render(<EditorArea />)

    // 固定页签 2 个(项目工作板 + 总控, M4 退役任务窗口) + material = 3
    await waitFor(() => {
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(3)
    })
    expect(dockMock.api.getPanel(CONTROLLER_TAB_ID).options.renderer).toBe('always')
    // openTab 把 material 置为 active → 它第一个加入, 此刻参照(总控)尚未挂载,
    // 分裂由 add 后的 moveTo 兜底完成(参照在 post-loop 必然已存在), 视觉等价。
    const materialPanel = dockMock.api.getPanel('material:mat-1')
    await waitFor(() => {
      expect(materialPanel.api.moveTo).toHaveBeenCalledWith({
        group: dockMock.api.getPanel(CONTROLLER_TAB_ID).group,
        position: 'right',
      })
    })
    await waitFor(() => {
      expect(usePanels.getState().tabs.find((tab) => tab.id === 'material:mat-1')?.placement).toBeUndefined()
    })
  })

  it('moves an existing panel when a split placement is requested later', async () => {
    usePanels.getState().openTab({ type: 'material', id: 'mat-2' }, 'material 2')

    render(<EditorArea />)

    // 固定页签 2 个(项目工作板 + 总控, M4 退役任务窗口) + material = 3
    await waitFor(() => {
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(3)
    })
    const materialPanel = dockMock.api.getPanel('material:mat-2')
    expect(materialPanel).toBeTruthy()

    act(() => {
      usePanels.getState().requestDockPlacement('material:mat-2', {
        direction: 'right',
        referenceTabId: CONTROLLER_TAB_ID,
      })
    })

    await waitFor(() => {
      expect(materialPanel.api.moveTo).toHaveBeenCalledWith({
        group: dockMock.api.getPanel(CONTROLLER_TAB_ID).group,
        position: 'right',
      })
    })
    expect(usePanels.getState().tabs.find((tab) => tab.id === 'material:mat-2')?.placement).toBeUndefined()
  })

  it('adds the active panel first and keeps fixed tabs in one group (修三 dock 组同屏)', async () => {
    render(<EditorArea />)

    await waitFor(() => {
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(2)
    })
    const calls = dockMock.api.addPanel.mock.calls.map(([options]: any[]) => options)
    // active(总控) 第一个加入 —— dockview 有 activeGroup, 后续 inactive 面板落同组,
    // 不再出现"每个 inactive 面板各建一个 dock 组"的三区域同屏。
    expect(calls[0].id).toBe(CONTROLLER_TAB_ID)
    expect(calls[0].inactive).toBe(false)
    // 其余固定页签: inactive 挂载但只带 referencePanel+index(同组插入), 不带 direction(不劈组)
    const rest = calls.slice(1)
    expect(rest.every((options: any) => options.inactive === true)).toBe(true)
    expect(rest.every((options: any) => !options.position?.direction)).toBe(true)
    for (const panel of dockMock.panels) {
      expect(panel.group.id).toBe('group-main')
    }
  })

  it('inserts a newly opened child panel directly after its active parent', async () => {
    usePanels.setState({
      tabs: [
        { id: 'note:source', ref: { type: 'note', id: 'source' }, title: 'Source' },
        { id: 'note:later', ref: { type: 'note', id: 'later' }, title: 'Later' },
      ],
      activeId: 'note:source',
    })

    render(<EditorArea />)
    await waitFor(() => expect(dockMock.api.addPanel).toHaveBeenCalledTimes(2))
    dockMock.api.addPanel.mockClear()

    act(() => {
      usePanels.getState().openTab({ type: 'note', id: 'child' }, 'Child')
    })

    await waitFor(() => expect(dockMock.api.addPanel).toHaveBeenCalledTimes(1))
    const options = dockMock.api.addPanel.mock.calls[0][0]
    expect(options.id).toBe('note:child')
    expect(options.position).toEqual({
      referencePanel: dockMock.api.getPanel('note:source'),
      index: 1,
    })
    expect(options.position.direction).toBeUndefined()
  })

  it('keeps every mounted panel on the always renderer regardless of visit order', async () => {
    const heavyTabs = Array.from({ length: 5 }, (_, index) => ({
      id: `review_material:review-${index + 1}`,
      ref: { type: 'review_material', id: `review-${index + 1}` },
      title: `Review ${index + 1}`,
    }))
    const ordinaryTab = { id: 'note:ordinary', ref: { type: 'note', id: 'ordinary' }, title: 'Ordinary' }
    usePanels.setState({ tabs: [...heavyTabs, ordinaryTab] as any, activeId: heavyTabs[0].id })

    render(<EditorArea />)
    await waitFor(() => expect(dockMock.api.addPanel).toHaveBeenCalledTimes(heavyTabs.length + 1))

    expect(dockMock.api.getPanel(heavyTabs[0].id).api.renderer).toBe('always')
    expect(dockMock.api.getPanel(ordinaryTab.id).api.renderer).toBe('always')

    for (const current of heavyTabs.slice(1)) {
      act(() => usePanels.getState().activate(current.id))
    }

    // 曾经的 LRU 会把最旧的重型页签降回 onlyWhenVisible(= 卸载 + 切回重载)。现在一个都不降。
    for (const current of [...heavyTabs, ordinaryTab]) {
      expect(dockMock.api.getPanel(current.id).api.renderer).toBe('always')
    }
  })

  it('keeps the whole dock mounted when switching between a CLI tab and a web tab', async () => {
    // 实证根因(2026-08-07): 包着 DockviewReact 的那层原来在 Fragment 与 TabSidecarLayout
    // 之间来回变(有没有伴随视图), React 因此把整个 dock 卸载重挂 —— 所有面板重建、
    // iframe 从头加载、终端重放历史。用户报的"每次访问重型网页还是要加载"就是这条路。
    const cli = { id: 'cc_session:pty-keep', ref: { type: 'cc_session' as const, id: 'pty-keep' }, title: 'CLI' }
    const web = { id: 'web_review:web-page:http%3A%2F%2Fhost%3A8210%2Fspark-webtwin%2F', ref: { type: 'web_review' as const, id: 'web-page:http%3A%2F%2Fhost%3A8210%2Fspark-webtwin%2F' }, title: 'webtwin' }
    usePanels.setState({ tabs: [cli, web] as any, activeId: cli.id })

    const { container } = render(<EditorArea />)
    await waitFor(() => expect(dockMock.api.getPanel(web.id)).toBeTruthy())
    // onReady 只该发生一次: 它每跑一次就意味着 dockview 实例被重建过。
    expect(dockMock.api.onDidActivePanelChange).toHaveBeenCalledTimes(1)
    const dockNode = container.querySelector('[data-testid="dockview-mock"]')
    const webPanel = dockMock.api.getPanel(web.id)

    act(() => usePanels.getState().activate(web.id))       // CLI(有伴随视图) → 网页(没有)
    act(() => usePanels.getState().activate(cli.id))       // 再切回来

    expect(dockMock.api.onDidActivePanelChange).toHaveBeenCalledTimes(1)
    // 同一个 DOM 节点、同一个 panel 实例 —— 里面的 iframe/终端不可能被重新创建。
    expect(container.querySelector('[data-testid="dockview-mock"]')).toBe(dockNode)
    expect(dockMock.api.getPanel(web.id)).toBe(webPanel)
    // 布局外壳全程在场(没有伴随视图时只是不出 aside), children 的 DOM 祖先链因此不变。
    expect(container.querySelectorAll('[data-testid="tab-sidecar-layout"]')).toHaveLength(1)
  })

  it('switches the tab strip between single-row and stacked layouts', async () => {
    const { container } = render(<EditorArea />)
    expect(container.querySelector('[data-tab-layout="single-row"]')).toBeTruthy()

    act(() => useUiSettings.getState().setTabLayout('stacked'))

    await waitFor(() => {
      expect(container.querySelector('[data-tab-layout="stacked"]')).toBeTruthy()
    })
  })

  it('updates existing Dockview titles when the configured maximum length changes', async () => {
    const fullTitle = 'Codex - ' + 'long title '.repeat(8)
    usePanels.getState().openTab({ type: 'cc_session', id: 'pty-title-setting' }, fullTitle)

    render(<EditorArea />)
    await waitFor(() => expect(dockMock.api.getPanel('cc_session:pty-title-setting')).toBeTruthy())
    const panel = dockMock.api.getPanel('cc_session:pty-title-setting')
    expect(panel.options.title).toBe(compactCliTabTitle(fullTitle, TAB_TITLE_LIMITS.default))

    act(() => useUiSettings.getState().setTabTitleMaxLength(10))

    await waitFor(() => {
      expect(panel.options.title).toBe(compactCliTabTitle(fullTitle, 10))
    })
    expect(panel.api.setTitle).toHaveBeenCalledWith(compactCliTabTitle(fullTitle, 10))
  })
  it('caps a huge CLI conversation title without changing the stored full title', async () => {
    const fullTitle = `Codex · ${'超长会话标题'.repeat(20)}`
    usePanels.getState().openTab({ type: 'cc_session', id: 'pty-long-title' }, fullTitle)

    render(<EditorArea />)

    await waitFor(() => {
      expect(dockMock.api.getPanel('cc_session:pty-long-title')).toBeTruthy()
    })
    const panel = dockMock.api.getPanel('cc_session:pty-long-title')
    expect(panel.options.title).toBe(compactCliTabTitle(fullTitle))
    expect(panel.options.title.endsWith('…')).toBe(true)
    expect(usePanels.getState().tabs.find((tab) => tab.id === 'cc_session:pty-long-title')?.title)
      .toBe(fullTitle)
  })

  it('keeps one shared session companion mounted while switching CLI tabs', async () => {
    window.localStorage.setItem('omni.tabSidecar.collapsed.v2', '0')
    const first = {
      id: 'cc_session:pty-first',
      ref: { type: 'cc_session', id: 'pty-first' },
      title: 'first CLI',
    } as const
    const second = {
      id: 'cc_session:pty-second',
      ref: { type: 'cc_session', id: 'pty-second' },
      title: 'second CLI',
    } as const
    usePanels.setState({ tabs: withDefaultTabs([first, second]), activeId: first.id })

    render(<EditorArea />)

    await waitFor(() => {
      expect(screen.getByTestId('session-companion').getAttribute('data-session-id')).toBe('pty-first')
    })
    fireEvent.click(screen.getByTestId('mock-companion-page'))
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('overview')

    act(() => usePanels.getState().activate(second.id))

    await waitFor(() => {
      expect(screen.getByTestId('session-companion').getAttribute('data-session-id')).toBe('pty-second')
    })
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('overview')
    expect(screen.getAllByTestId('session-companion')).toHaveLength(1)
    expect(screen.getByTestId('tab-sidecar')).toBeTruthy()
  })

  it('keeps the source session material directory beside a review material opened from that session', async () => {
    window.localStorage.setItem('omni.tabSidecar.collapsed.v2', '0')
    const session = {
      id: 'cc_session:pty-source',
      ref: { type: 'cc_session', id: 'pty-source' },
      title: 'source CLI',
    } as const
    const material = {
      id: 'review_material:material-1',
      ref: { type: 'review_material', id: 'material-1' },
      title: 'material',
      returnTo: { id: session.id, ref: session.ref, title: session.title },
    } as const
    usePanels.setState({ tabs: withDefaultTabs([session, material]), activeId: material.id })

    render(<EditorArea />)

    await waitFor(() => {
      expect(screen.getByTestId('session-companion').getAttribute('data-session-id')).toBe('pty-source')
    })
    expect(screen.getByTestId('tab-sidecar').getAttribute('data-testid')).toBe('tab-sidecar')
  })

  it('opens the session companion expanded on a cold CLI tab (札记入口在侧栏)', async () => {
    const cli = {
      id: 'cc_session:pty-cold',
      ref: { type: 'cc_session', id: 'pty-cold' },
      title: 'cold CLI',
    } as const
    usePanels.setState({ tabs: withDefaultTabs([cli]), activeId: cli.id })

    render(<EditorArea />)

    await waitFor(() => {
      expect(screen.getByTestId('tab-sidecar-layout').getAttribute('data-sidecar-collapsed')).toBe('0')
    })
    expect(screen.getByTestId('session-companion')).toBeTruthy()
  })

  it('can collapse the expanded session sidebar from its own header', async () => {
    window.localStorage.setItem('omni.tabSidecar.collapsed.v2', '0')
    const cli = {
      id: 'cc_session:pty-collapse',
      ref: { type: 'cc_session', id: 'pty-collapse' },
      title: 'collapse CLI',
    } as const
    usePanels.setState({ tabs: withDefaultTabs([cli]), activeId: cli.id })

    render(<EditorArea />)

    const toggle = await screen.findByTestId('session-ctx-toggle')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(toggle.getAttribute('aria-label')).toBe('收起会话信息')
    fireEvent.click(toggle)

    await waitFor(() => {
      expect(screen.getByTestId('tab-sidecar-layout').getAttribute('data-sidecar-collapsed')).toBe('1')
    })
    expect(screen.queryByTestId('tab-sidecar')).toBeNull()
    expect(window.localStorage.getItem('omni.tabSidecar.collapsed.v2')).toBe('1')
  })

  it('re-opens content when the initially active CLI tab is re-indexed behind default tabs', async () => {
    const cliTab = {
      id: 'cc_session:pty-deeplink',
      ref: { type: 'cc_session', id: 'pty-deeplink' },
      title: 'CLI deep link',
    } as const
    usePanels.setState({ tabs: withDefaultTabs([cliTab]), activeId: cliTab.id })

    render(<EditorArea />)

    await waitFor(() => {
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(3)
    })
    const cliPanel = dockMock.api.getPanel(cliTab.id)
    const projectPanel = dockMock.api.getPanel('project_board:main')
    expect(projectPanel.api.setActive).toHaveBeenCalledTimes(1)
    expect(cliPanel.api.setActive).toHaveBeenCalledTimes(1)
    expect(cliPanel.api.isActive).toBe(true)
  })

  it('tears a tab off only after the drag ends outside the viewport', async () => {
    const tab = { id: 'cc_session:pty-tear-off', ref: { type: 'cc_session' as const, id: 'pty-tear-off' }, title: 'CLI tear-off' }
    usePanels.setState({ tabs: withDefaultTabs([tab]), activeId: tab.id })
    const originalOpen = window.open
    const originalMatchMedia = window.matchMedia
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
    const child = { opener: window } as unknown as Window
    const nativeOpen = vi.fn(() => child)
    window.open = nativeOpen
    try {
      const { container } = render(<EditorArea />)
      await waitFor(() => expect(dockMock.api.getPanel(tab.id)).toBeTruthy())
      const root = container.querySelector<HTMLElement>('[data-tab-layout]')!
      const dragSource = document.createElement('span')
      dragSource.dataset.omniTabId = tab.id
      root.appendChild(dragSource)

      dispatchDrag(dragSource, 'dragstart')
      dispatchDrag(window, 'dragend', {
        clientX: window.innerWidth + 20,
        clientY: 200,
        screenX: 1400,
        screenY: 400,
      })

      expect(nativeOpen).toHaveBeenCalledWith(
        expect.stringContaining('omni_detached=1'),
        '_blank',
        expect.stringContaining('popup=yes'),
      )
      expect(usePanels.getState().tabs.some((candidate) => candidate.id === tab.id)).toBe(false)
    } finally {
      window.open = originalOpen
      Object.defineProperty(window, 'matchMedia', { configurable: true, value: originalMatchMedia })
    }
  })

  it('keeps the source tab when a tear-off popup is blocked', async () => {
    const tab = { id: 'cc_session:pty-blocked', ref: { type: 'cc_session' as const, id: 'pty-blocked' }, title: 'Blocked CLI' }
    usePanels.setState({ tabs: withDefaultTabs([tab]), activeId: tab.id })
    const originalOpen = window.open
    const originalMatchMedia = window.matchMedia
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
    window.open = vi.fn(() => null)
    try {
      const { container } = render(<EditorArea />)
      await waitFor(() => expect(dockMock.api.getPanel(tab.id)).toBeTruthy())
      const root = container.querySelector<HTMLElement>('[data-tab-layout]')!
      const dragSource = document.createElement('span')
      dragSource.dataset.omniTabId = tab.id
      root.appendChild(dragSource)

      dispatchDrag(dragSource, 'dragstart')
      dispatchDrag(window, 'dragend', {
        clientX: window.innerWidth + 20,
        clientY: 200,
        screenX: 1400,
        screenY: 400,
      })

      expect(window.open).toHaveBeenCalledTimes(1)
      expect(usePanels.getState().tabs.some((candidate) => candidate.id === tab.id)).toBe(true)
    } finally {
      window.open = originalOpen
      Object.defineProperty(window, 'matchMedia', { configurable: true, value: originalMatchMedia })
    }
  })

  it('does not mistake an in-viewport or cancelled drag for a tear-off', async () => {
    const tab = { id: 'cc_session:pty-cancelled', ref: { type: 'cc_session' as const, id: 'pty-cancelled' }, title: 'Cancelled CLI' }
    usePanels.setState({ tabs: withDefaultTabs([tab]), activeId: tab.id })
    const originalOpen = window.open
    const originalMatchMedia = window.matchMedia
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
    window.open = vi.fn(() => ({ opener: window } as unknown as Window))
    try {
      const { container } = render(<EditorArea />)
      await waitFor(() => expect(dockMock.api.getPanel(tab.id)).toBeTruthy())
      const root = container.querySelector<HTMLElement>('[data-tab-layout]')!
      const dragSource = document.createElement('span')
      dragSource.dataset.omniTabId = tab.id
      root.appendChild(dragSource)

      dispatchDrag(dragSource, 'dragstart')
      dispatchDrag(window, 'dragend', { clientX: 300, clientY: 200, screenX: 500, screenY: 400 })
      dispatchDrag(dragSource, 'dragstart')
      dispatchDrag(window, 'dragend', { clientX: 0, clientY: 0, screenX: 0, screenY: 0 })

      expect(window.open).not.toHaveBeenCalled()
      expect(usePanels.getState().tabs.some((candidate) => candidate.id === tab.id)).toBe(true)
    } finally {
      window.open = originalOpen
      Object.defineProperty(window, 'matchMedia', { configurable: true, value: originalMatchMedia })
    }
  })

})
