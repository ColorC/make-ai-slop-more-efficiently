import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import WebFileContextMenu from './WebFileContextMenu'

afterEach(() => cleanup())

describe('WebFileContextMenu', () => {
  const target = {
    name: 'PROJECT_INDEX.md',
    path: 'C:/workspace/PROJECT_INDEX.md',
    kind: 'file',
    open_token: 'signed-token',
  }

  it('只提供网页打开与复制动作，不调用本机应用', () => {
    const onOpen = vi.fn()
    const onClose = vi.fn()
    render(
      <WebFileContextMenu
        menu={{ x: 100, y: 120, target }}
        onClose={onClose}
        onOpen={onOpen}
      />,
    )

    expect(screen.getByRole('menu', { name: '文件操作' })).toBeTruthy()
    expect(screen.getByText('在 Dashboard 网页中打开')).toBeTruthy()
    expect(screen.getByText('在新浏览器标签打开')).toBeTruthy()
    expect(screen.getByText('复制网页链接')).toBeTruthy()
    expect(screen.getByText('复制文件路径')).toBeTruthy()
    expect(screen.queryByText(/VSCode|本机打开/)).toBeNull()

    fireEvent.click(screen.getByTestId('web-file-menu-open'))
    expect(onOpen).toHaveBeenCalledWith(target)
    expect(onClose).toHaveBeenCalled()
  })
})
