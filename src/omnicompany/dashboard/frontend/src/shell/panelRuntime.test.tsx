import React from 'react'
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PanelRuntimeProvider, useBackgroundPoll, usePanelVisible } from './panelRuntime'

function Probe({ onTick }: { onTick: () => void }) {
  const visible = usePanelVisible()
  useBackgroundPoll(onTick, 1_000)
  return <span data-testid="probe" data-visible={visible ? 'true' : 'false'} />
}

function Panel({ visible, onTick }: { visible: boolean; onTick: () => void }) {
  return (
    <PanelRuntimeProvider value={{ tabId: 'note:probe', visible }}>
      <Probe onTick={onTick} />
    </PanelRuntimeProvider>
  )
}

describe('panelRuntime background throttling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('treats a panel-less surface as visible so standalone views keep polling', () => {
    const onTick = vi.fn()
    const view = render(<Probe onTick={onTick} />)

    expect(view.getByTestId('probe').getAttribute('data-visible')).toBe('true')
    expect(onTick).toHaveBeenCalledTimes(1)
  })

  it('suspends polling for hidden tabs and refreshes once on the way back', () => {
    const onTick = vi.fn()
    const view = render(<Panel visible onTick={onTick} />)
    expect(onTick).toHaveBeenCalledTimes(1)

    act(() => { vi.advanceTimersByTime(2_000) })
    expect(onTick).toHaveBeenCalledTimes(3)

    // 页签切走: 常驻不等于常轮询 —— 定时器停掉。
    view.rerender(<Panel visible={false} onTick={onTick} />)
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(onTick).toHaveBeenCalledTimes(3)

    // 切回来: 立刻补一次, 用户不会看到一屏陈旧数据。
    view.rerender(<Panel visible onTick={onTick} />)
    expect(onTick).toHaveBeenCalledTimes(4)
    act(() => { vi.advanceTimersByTime(1_000) })
    expect(onTick).toHaveBeenCalledTimes(5)
  })

  it('stops polling when the whole window goes to the background', () => {
    const onTick = vi.fn()
    const visibilityState = vi.spyOn(document, 'visibilityState', 'get')
    visibilityState.mockReturnValue('visible')
    render(<Panel visible onTick={onTick} />)
    expect(onTick).toHaveBeenCalledTimes(1)

    visibilityState.mockReturnValue('hidden')
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    act(() => { vi.advanceTimersByTime(5_000) })
    expect(onTick).toHaveBeenCalledTimes(1)

    visibilityState.mockReturnValue('visible')
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(onTick).toHaveBeenCalledTimes(2)
    visibilityState.mockRestore()
  })

  it('skips a tick while the previous one is still in flight', async () => {
    let resolveFirst: (() => void) | null = null
    const onTick = vi.fn(() => new Promise<void>((resolve) => { resolveFirst = resolve }))
    render(<Panel visible onTick={onTick} />)
    expect(onTick).toHaveBeenCalledTimes(1)

    act(() => { vi.advanceTimersByTime(3_000) })
    expect(onTick).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveFirst?.()
      await Promise.resolve()
    })
    act(() => { vi.advanceTimersByTime(1_000) })
    expect(onTick).toHaveBeenCalledTimes(2)
  })
})
