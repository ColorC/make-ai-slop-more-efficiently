import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { reviewstageApi, type Material, type StreamEvent } from '../../api/reviewstageClient'
import { useReviewStream } from './streamStore'

function mat(id: string, over: Partial<Material> = {}): Material {
  return {
    id,
    kind: 'markdown',
    tier: 'important',
    title: `material ${id}`,
    status: 'pending',
    source_subagent_id: null,
    source_plan_id: null,
    file_relpath: null,
    inline_content: 'hello',
    annotations: [],
    comments: [],
    annotations_allowed: true,
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    history: [],
    pushed_to_user: false,
    pushed_reason: null,
    pushed_at: null,
    extra: {},
    ...over,
  }
}

describe('review streamStore (R3 WS 流上移)', () => {
  let onEvents: Array<(e: StreamEvent) => void>
  let onErrors: Array<(e: Event) => void>
  let closeSpies: Array<ReturnType<typeof vi.fn>>
  let openSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    onEvents = []
    onErrors = []
    closeSpies = []
    openSpy = vi.spyOn(reviewstageApi, 'openStream').mockImplementation((onEvent, onError) => {
      onEvents.push(onEvent)
      if (onError) onErrors.push(onError)
      const close = vi.fn()
      closeSpies.push(close)
      return close
    })
    useReviewStream.setState({ version: 0, materials: {}, connected: false, pushed: null, pushedNonce: 0 })
  })

  afterEach(() => {
    // 跑掉 linger/重连定时器, 让模块级连接状态归零, 不串测试
    vi.runAllTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('引用计数: 两个订阅者共享一条连接, 全部释放后延迟关闭, release 幂等', () => {
    const release1 = useReviewStream.getState().acquire()
    const release2 = useReviewStream.getState().acquire()
    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(useReviewStream.getState().connected).toBe(true)

    release1()
    vi.advanceTimersByTime(10_000)
    expect(closeSpies[0]).not.toHaveBeenCalled() // 还有订阅者, 不关

    release2()
    release2() // 幂等: 重复 release 不把 refs 减成负
    expect(closeSpies[0]).not.toHaveBeenCalled() // linger 窗口内未关
    vi.advanceTimersByTime(1_500)
    expect(closeSpies[0]).toHaveBeenCalledTimes(1)
    expect(useReviewStream.getState().connected).toBe(false)
  })

  it('linger 窗口内重新 acquire 不断连(dockview 切页签场景)', () => {
    const release1 = useReviewStream.getState().acquire()
    release1()
    const release2 = useReviewStream.getState().acquire() // 旧面板卸载→新面板挂载
    vi.advanceTimersByTime(10_000)
    expect(closeSpies[0]).not.toHaveBeenCalled()
    expect(openSpy).toHaveBeenCalledTimes(1) // 还是同一条连接
    release2()
    vi.advanceTimersByTime(1_500)
    expect(closeSpies[0]).toHaveBeenCalledTimes(1)
  })

  it('事件合并: snapshot 重建 / 增量更新 / deleted 移除 / ping 不动 version', () => {
    const release = useReviewStream.getState().acquire()
    const emit = onEvents[0]

    emit({ event_type: 'snapshot', items: [mat('a'), mat('b')] })
    expect(useReviewStream.getState().version).toBe(1)
    expect(Object.keys(useReviewStream.getState().materials).sort()).toEqual(['a', 'b'])

    emit({ event_type: 'comment_added', material: mat('a', { title: 'updated a' }) })
    expect(useReviewStream.getState().version).toBe(2)
    expect(useReviewStream.getState().materials['a'].title).toBe('updated a')

    emit({ event_type: 'deleted', material: mat('b') })
    expect(useReviewStream.getState().version).toBe(3)
    expect(useReviewStream.getState().materials['b']).toBeUndefined()

    emit({ event_type: 'ping' })
    expect(useReviewStream.getState().version).toBe(3)

    // pushed 事件: 除了合并材料, 还透出给驾驶舱推送 toast (nonce 自增)
    emit({ event_type: 'pushed', material: mat('a', { pushed_to_user: true, pushed_reason: '看一下' }) })
    expect(useReviewStream.getState().version).toBe(4)
    expect(useReviewStream.getState().pushed?.id).toBe('a')
    expect(useReviewStream.getState().pushedNonce).toBe(1)
    emit({ event_type: 'comment_added', material: mat('a') })
    expect(useReviewStream.getState().pushedNonce).toBe(1) // 非 pushed 事件不动 nonce

    release()
  })

  it('断线后仍有订阅者 → 自动重连', () => {
    const release = useReviewStream.getState().acquire()
    expect(openSpy).toHaveBeenCalledTimes(1)

    onErrors[0](new Event('error')) // 连接挂掉
    expect(closeSpies[0]).toHaveBeenCalledTimes(1) // 收掉死连接
    expect(useReviewStream.getState().connected).toBe(false)

    vi.advanceTimersByTime(3_000)
    expect(openSpy).toHaveBeenCalledTimes(2) // 重连
    expect(useReviewStream.getState().connected).toBe(true)

    release()
    vi.advanceTimersByTime(1_500)
    expect(closeSpies[1]).toHaveBeenCalledTimes(1)
  })
})
