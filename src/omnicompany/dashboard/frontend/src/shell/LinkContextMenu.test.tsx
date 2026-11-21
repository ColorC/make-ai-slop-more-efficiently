import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LinkContextMenu from './LinkContextMenu'
import { usePanels, withDefaultTabs } from '../stores/panelsStore'

describe('LinkContextMenu', () => {
  const openSystemUrl = vi.fn(async () => undefined)

  beforeEach(() => {
    vi.clearAllMocks()
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: null })
    window.__OMNICOMPANY_HOST__ = { runtime: 'desktop', openSystemUrl }
  })

  afterEach(() => {
    cleanup()
    delete window.__OMNICOMPANY_HOST__
  })

  it('opens a right-clicked link in the system browser on request', async () => {
    render(<><LinkContextMenu /><a href="https://example.com/docs">参考文档</a></>)
    fireEvent.contextMenu(screen.getByText('参考文档'), { clientX: 40, clientY: 50 })
    expect(screen.getByTestId('link-context-menu')).toBeTruthy()

    fireEvent.click(screen.getByTestId('link-menu-open-system'))

    expect(openSystemUrl).toHaveBeenCalledWith('https://example.com/docs')
  })

  it('keeps the explicit internal-tab choice available', () => {
    render(<><LinkContextMenu /><a href="/bside/">BSide</a></>)
    fireEvent.contextMenu(screen.getByText('BSide'), { clientX: 20, clientY: 30 })
    fireEvent.click(screen.getByTestId('link-menu-open-workspace'))

    expect(usePanels.getState().tabs.some((tab) => tab.ref.type === 'web_review')).toBe(true)
    expect(openSystemUrl).not.toHaveBeenCalled()
  })
})
