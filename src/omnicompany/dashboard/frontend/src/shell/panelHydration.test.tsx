import React from 'react'
import { act, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PanelHydrationQueue, usePanelHydration } from './panelHydration'
import { reportFrontendEvent } from '../lib/frontendTelemetry'

vi.mock('../lib/frontendTelemetry', () => ({
  reportFrontendEvent: vi.fn(),
}))

/** 手动驱动的 idle 调度器: 让"空闲时放行下一批"在测试里变成显式的一步。 */
function manualScheduler() {
  const tasks: Array<() => void> = []
  return {
    schedule: (task: () => void) => {
      tasks.push(task)
      return () => {
        const index = tasks.indexOf(task)
        if (index >= 0) tasks.splice(index, 1)
      }
    },
    /** 跑完当前排着的调度(每跑一次可能又排出新的一轮)。 */
    flush(rounds = 1) {
      for (let round = 0; round < rounds; round += 1) {
        const pending = tasks.splice(0, tasks.length)
        for (const task of pending) task()
      }
    },
    get size() { return tasks.length },
  }
}

function makeQueue(concurrency = 2) {
  const scheduler = manualScheduler()
  const timers: Array<() => void> = []
  const queue = new PanelHydrationQueue({
    scheduler: scheduler.schedule,
    concurrency,
    setTimer: (task) => {
      timers.push(task)
      return timers.length - 1
    },
    clearTimer: () => undefined,
    settleTimeoutMs: 1_000,
  })
  return { queue, scheduler, timers }
}

describe('PanelHydrationQueue', () => {
  it('hydrates the visible panel immediately without waiting for idle time', () => {
    const { queue, scheduler } = makeQueue()
    const hydrate = vi.fn()

    queue.request('review_material:visible', true, hydrate)

    expect(hydrate).toHaveBeenCalledTimes(1)
    expect(scheduler.size).toBe(0)
    expect(queue.inFlight).toBe(1)
  })

  it('holds hidden panels back and releases them in bounded batches', () => {
    const { queue, scheduler } = makeQueue(2)
    const calls: string[] = []
    for (const id of ['a', 'b', 'c', 'd']) {
      queue.request(id, false, () => calls.push(id))
    }

    // 还没到空闲: 一个都没放行 —— 刷新驾驶舱时首屏不会被十几个后台页签一起挤掉。
    expect(calls).toEqual([])

    scheduler.flush()
    expect(calls).toEqual(['a', 'b'])

    // 名额满着, 再多的空闲也不会超发。
    scheduler.flush()
    expect(calls).toEqual(['a', 'b'])

    queue.settle('a')
    scheduler.flush()
    expect(calls).toEqual(['a', 'b', 'c'])

    queue.settle('b')
    queue.settle('c')
    scheduler.flush(2)
    expect(calls).toEqual(['a', 'b', 'c', 'd'])
  })

  it('promotes a queued panel the moment the user switches to it', () => {
    const { queue, scheduler } = makeQueue(1)
    const calls: string[] = []
    queue.request('slow', false, () => calls.push('slow'))
    queue.request('wanted', false, () => calls.push('wanted'))
    scheduler.flush()
    expect(calls).toEqual(['slow'])

    // 用户切到还在排队的那个页签: 立刻放行, 不再等空闲也不受并发上限约束。
    queue.promote('wanted')
    expect(calls).toEqual(['slow', 'wanted'])
  })

  it('frees the slot when a panel never settles so the queue cannot deadlock', () => {
    const { queue, scheduler, timers } = makeQueue(1)
    const calls: string[] = []
    queue.request('stuck', false, () => calls.push('stuck'))
    queue.request('next', false, () => calls.push('next'))
    scheduler.flush()
    expect(calls).toEqual(['stuck'])

    // watchdog 到时: 卡住的那个让出名额。
    timers[0]()
    scheduler.flush()
    expect(calls).toEqual(['stuck', 'next'])
  })

  it('drops a closed tab from both the queue and the in-flight budget', () => {
    const { queue, scheduler } = makeQueue(1)
    const calls: string[] = []
    queue.request('closing', false, () => calls.push('closing'))
    queue.request('other', false, () => calls.push('other'))

    queue.release('closing')
    scheduler.flush()

    expect(calls).toEqual(['other'])
    expect(queue.pending).toBe(0)
  })
})

function HydrationProbe({ visible, queue }: { visible: boolean; queue: PanelHydrationQueue }) {
  const { hydrated } = usePanelHydration('note:probe', visible, queue)
  return <span data-testid="probe" data-hydrated={hydrated ? 'true' : 'false'} />
}

describe('usePanelHydration', () => {
  it('shows content immediately for the tab the user is looking at', () => {
    const { queue } = makeQueue()
    const view = render(<HydrationProbe visible queue={queue} />)
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('true')
  })

  it('keeps a background tab on the skeleton until the queue releases it, then never goes back', () => {
    const { queue, scheduler } = makeQueue()
    const view = render(<HydrationProbe visible={false} queue={queue} />)
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('false')

    act(() => { scheduler.flush() })
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('true')

    // 水合是单向的: 切走再切回来都不会退回骨架 —— 这就是"切换页签不再加载"。
    view.rerender(<HydrationProbe visible queue={queue} />)
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('true')
    view.rerender(<HydrationProbe visible={false} queue={queue} />)
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('true')
  })

  it('hydrates a queued tab as soon as the user switches to it', () => {
    const { queue } = makeQueue()
    const view = render(<HydrationProbe visible={false} queue={queue} />)
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('false')

    act(() => { view.rerender(<HydrationProbe visible queue={queue} />) })
    expect(view.getByTestId('probe').getAttribute('data-hydrated')).toBe('true')
  })

  it('gives the slot back when the panel unmounts', () => {
    const { queue } = makeQueue()
    const view = render(<HydrationProbe visible queue={queue} />)
    expect(queue.inFlight).toBe(1)
    view.unmount()
    expect(queue.inFlight).toBe(0)
  })
})

function CcSessionHydrationProbe({ tabId, visible, queue }: { tabId: string; visible: boolean; queue: PanelHydrationQueue }) {
  const { hydrated } = usePanelHydration(tabId, visible, queue)
  return <span data-testid="probe" data-hydrated={hydrated ? 'true' : 'false'} />
}

describe('panel_hydration_wait telemetry', () => {
  it('reports session_id and queued=false for a visible cc_session tab', () => {
    vi.mocked(reportFrontendEvent).mockClear()
    const { queue } = makeQueue()
    render(<CcSessionHydrationProbe tabId="cc_session:abc123" visible queue={queue} />)

    expect(reportFrontendEvent).toHaveBeenCalledWith(
      'panel_hydration_wait',
      expect.any(String),
      expect.objectContaining({ session_id: 'abc123', queued: false }),
    )
  })

  it('reports queued=true and only after the queue releases a hidden cc_session tab', () => {
    vi.mocked(reportFrontendEvent).mockClear()
    const { queue, scheduler } = makeQueue()
    render(<CcSessionHydrationProbe tabId="cc_session:hidden-one" visible={false} queue={queue} />)
    expect(reportFrontendEvent).not.toHaveBeenCalled()

    act(() => { scheduler.flush() })
    expect(reportFrontendEvent).toHaveBeenCalledWith(
      'panel_hydration_wait',
      expect.any(String),
      expect.objectContaining({ session_id: 'hidden-one', queued: true }),
    )
  })

  it('never reports for non-terminal panel types', () => {
    vi.mocked(reportFrontendEvent).mockClear()
    const { queue } = makeQueue()
    render(<HydrationProbe visible queue={queue} />)
    expect(reportFrontendEvent).not.toHaveBeenCalled()
  })
})
