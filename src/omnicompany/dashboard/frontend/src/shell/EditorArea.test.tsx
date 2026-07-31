import React from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EditorArea, { compactCliTabTitle } from './EditorArea'
import { CONTROLLER_TAB_ID, usePanels, withDefaultTabs } from '../stores/panelsStore'

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
          isActive: false,
          close: vi.fn(),
          setActive: vi.fn(() => {
            panel.api.isActive = true
          }),
          moveTo: vi.fn((moveOptions: any) => {
            panel.moveOptions = moveOptions
            panel.group = { id: `moved-${options.id}` }
          }),
        },
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

describe('EditorArea dock placement', () => {
  beforeEach(() => {
    dockMock.panels.splice(0, dockMock.panels.length)
    vi.clearAllMocks()
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

    // 固定页签 3 个(项目工作板 + 任务窗口 + 总控) + material = 4
    await waitFor(() => {
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(4)
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

    // 固定页签 3 个(项目工作板 + 任务窗口 + 总控) + material = 4
    await waitFor(() => {
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(4)
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
      expect(dockMock.api.addPanel).toHaveBeenCalledTimes(3)
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
})
