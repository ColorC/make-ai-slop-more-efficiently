import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TabSidecarLayout, { TabSidecarToggleButton, useTabSidecar } from './TabSidecar'

function RequestWideButton() {
  const sidecar = useTabSidecar()
  return <button type="button" onClick={sidecar?.requestWide}>展开阅读</button>
}

describe('TabSidecarLayout', () => {
  beforeEach(() => window.localStorage.clear())

  it('keeps the toggle in the existing header areas and never renders a button-only rail', () => {
    window.localStorage.setItem('omni.tabSidecar.width', '280')
    render(
      <TabSidecarLayout
        sidecar={(
          <header data-testid="sidecar-header">
            <TabSidecarToggleButton label="评价" showWhen="open" />
            <RequestWideButton />
          </header>
        )}
      >
        <header data-testid="main-header">
          <TabSidecarToggleButton label="评价" showWhen="collapsed" />
        </header>
      </TabSidecarLayout>,
    )

    expect(screen.getByTestId('tab-sidecar')).toBeTruthy()
    expect(screen.getByTestId('tab-sidecar').getAttribute('style')).toContain('width: 520px')
    expect(screen.getByTestId('sidecar-header').contains(screen.getByTestId('tab-sidecar-toggle'))).toBe(true)
    expect(document.querySelector('.tab-sidecar-rail')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '展开阅读' }))
    expect(screen.getByTestId('tab-sidecar').getAttribute('style')).toContain('width: 600px')

    fireEvent.click(screen.getByTestId('tab-sidecar-toggle'))
    expect(screen.queryByTestId('tab-sidecar')).toBeNull()
    expect(screen.getByTestId('main-header').contains(screen.getByTestId('tab-sidecar-toggle'))).toBe(true)
  })

  it('captures one touch gesture and applies its full resize distance', () => {
    render(
      <TabSidecarLayout sidecar={<div>札记</div>}>
        <div>会话</div>
      </TabSidecarLayout>,
    )

    const handle = screen.getByTestId('tab-sidecar-resize')
    const setPointerCapture = vi.fn()
    const releasePointerCapture = vi.fn()
    Object.assign(handle, { setPointerCapture, releasePointerCapture })

    fireEvent.pointerDown(handle, {
      button: 0,
      clientX: 900,
      pointerId: 7,
      pointerType: 'touch',
      isPrimary: true,
    })
    fireEvent.pointerMove(window, { clientX: 840, pointerId: 7, pointerType: 'touch' })
    fireEvent.pointerUp(window, { clientX: 780, pointerId: 7, pointerType: 'touch' })

    expect(setPointerCapture).toHaveBeenCalledWith(7)
    expect(releasePointerCapture).toHaveBeenCalledWith(7)
    expect(screen.getByTestId('tab-sidecar').getAttribute('style')).toContain('width: 640px')
  })

  it('手机档不并排: 默认只留主区, 展开后侧栏整幅接管并自带返回入口', () => {
    // 390px 屏上并排是假的(实测主区只剩 47px)。页签声明的「默认展开」也要被压回收起。
    const rect = { width: 390, height: 800, top: 0, left: 0, right: 390, bottom: 800, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
    const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(rect)

    render(
      <TabSidecarLayout sidecar={<div>札记</div>} defaultCollapsed={false}>
        <header data-testid="main-header">
          <TabSidecarToggleButton label="伴随" showWhen="collapsed" testId="open-sidecar" />
        </header>
      </TabSidecarLayout>,
    )

    const layout = screen.getByTestId('tab-sidecar-layout')
    expect(layout.getAttribute('data-sidecar-stacked')).toBe('1')
    expect(layout.getAttribute('data-sidecar-collapsed')).toBe('1')
    expect(screen.queryByTestId('tab-sidecar')).toBeNull()

    fireEvent.click(screen.getByTestId('open-sidecar'))
    const aside = screen.getByTestId('tab-sidecar')
    // 不再钉死宽度(交给 flex 撑满), 也不再给拖宽把手
    expect(aside.getAttribute('style')).toBeFalsy()
    expect(screen.queryByTestId('tab-sidecar-resize')).toBeNull()
    expect(document.querySelector('.tab-sidecar-scrim')).toBeNull()
    expect(layout.getAttribute('data-sidecar-collapsed')).toBe('0')

    fireEvent.click(screen.getByTestId('tab-sidecar-back'))
    expect(screen.queryByTestId('tab-sidecar')).toBeNull()

    rectSpy.mockRestore()
  })

  it('keeps the aside in the flex flow on narrow hosts — never an overlay, never a scrim', () => {
    // 2026-08-21 用户裁决: 侧栏任何宽度下都占布局位置; drawer 只剩 0.88 宽度钳制。
    window.localStorage.setItem('omni.tabSidecar.collapsed.v2', '0')
    window.localStorage.setItem('omni.tabSidecar.width.v2', '600')
    const rect = { width: 600, height: 400, top: 0, left: 0, right: 600, bottom: 400, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
    const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(rect)

    render(
      <TabSidecarLayout sidecar={<div>札记</div>}>
        <div>会话</div>
      </TabSidecarLayout>,
    )

    const layout = screen.getByTestId('tab-sidecar-layout')
    expect(layout.getAttribute('data-sidecar-drawer')).toBe('1')
    expect(document.querySelector('.tab-sidecar-scrim')).toBeNull()
    const aside = screen.getByTestId('tab-sidecar')
    // 600 * 0.88 = 528 的窄屏钳制仍生效
    expect(aside.getAttribute('style')).toContain('width: 528px')
    // aside 与主区同为 flex 子项: 主区在前, aside 紧随其后占位
    expect(layout.firstElementChild?.className).toBe('tab-sidecar-main')
    expect(layout.firstElementChild?.nextElementSibling).toBe(aside)

    rectSpy.mockRestore()
  })
})
