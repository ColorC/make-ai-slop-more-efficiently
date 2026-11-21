// 草稿设施的行为测试 —— 守的是三条纪律, 不是实现细节。
//
//   1. 本地先落(不等服务端)
//   2. **服务端确认前不清本地** ← 保存失败、关页面、崩溃都不清
//   3. 重开时摆出来让人选, 不自动覆盖当前内容
//
// jsdom 没有 IndexedDB, 所以这里跑的是 localStorage 回退路径 —— 对外 API 与断言
// 完全相同, 正好顺带证明了"IndexedDB 不可用时草稿功能不整个失效"。

import React, { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetDraftGuardForTests, deleteDraft, getDraft, listDrafts, markUnsynced, putDraft,
  putDraftSync, unsyncedDrafts,
} from './draftStore'
import { useDraft } from './useDraft'

beforeEach(() => {
  window.localStorage.clear()
  __resetDraftGuardForTests()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('draftStore', () => {
  it('存了能取回来, 删了就没了', async () => {
    await putDraft('k1', '方案', { body: '写了一半' })
    expect((await getDraft<{ body: string }>('k1'))?.value.body).toBe('写了一半')
    await deleteDraft('k1')
    expect(await getDraft('k1')).toBeNull()
  })

  it('同步写是给 unload 用的 —— 异步写来不及的那一刻也要留下东西', async () => {
    putDraftSync('k2', '方案', { body: '关页面前最后一下' })
    expect((await getDraft<{ body: string }>('k2'))?.value.body).toBe('关页面前最后一下')
  })

  it('列得出所有没清掉的草稿, 新的在前', async () => {
    await putDraft('a', 'A', 1)
    await new Promise((r) => setTimeout(r, 2))
    await putDraft('b', 'B', 2)
    const keys = (await listDrafts()).map((r) => r.key)
    expect(keys).toContain('a')
    expect(keys).toContain('b')
    expect(keys[0]).toBe('b')
  })

  it('有未同步草稿时才拦关页面', () => {
    const clean = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(clean)
    expect(clean.defaultPrevented).toBe(false)

    markUnsynced('k3', '方案')
    expect(unsyncedDrafts()).toEqual([{ key: 'k3', label: '方案' }])
    const dirty = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirty)
    expect(dirty.defaultPrevented).toBe(true)
  })
})

/** 一个最小的编辑器: 有草稿、能保存、保存可能失败。 */
function Editor({ onSave, initial = '' }: { onSave: () => Promise<boolean>; initial?: string }) {
  const [text, setText] = useState(initial)
  const draft = useDraft({
    key: 'editor:1',
    label: '方案',
    value: { text },
    clean: text === initial,
    debounceMs: 10,
  })
  return (
    <div>
      <input data-testid="in" value={text} onChange={(e) => setText(e.target.value)} />
      <button
        type="button"
        data-testid="save"
        onClick={() => { void onSave().then((ok) => { if (ok) draft.markSynced() }) }}
      >
        保存
      </button>
      {draft.recovered && (
        <div>
          <span data-testid="recovered">{(draft.recovered as { text: string }).text}</span>
          <button
            type="button"
            data-testid="restore"
            onClick={() => { setText((draft.recovered as { text: string }).text); draft.acceptRecovered() }}
          >
            恢复
          </button>
          <button type="button" data-testid="discard" onClick={draft.discardRecovered}>丢弃</button>
        </div>
      )}
    </div>
  )
}

describe('useDraft', () => {
  it('改了就落本地, 不等保存', async () => {
    render(<Editor onSave={async () => true} />)
    fireEvent.change(screen.getByTestId('in'), { target: { value: '写了一半的方案' } })
    await waitFor(async () => {
      expect((await getDraft<{ text: string }>('editor:1'))?.value.text).toBe('写了一半的方案')
    })
  })

  it('保存失败绝不清本地 —— 这是整套设施存在的理由', async () => {
    render(<Editor onSave={async () => false} />)
    fireEvent.change(screen.getByTestId('in'), { target: { value: '别弄丢我' } })
    await waitFor(async () => { expect(await getDraft('editor:1')).not.toBeNull() })

    fireEvent.click(screen.getByTestId('save'))
    await act(async () => { await Promise.resolve() })
    expect((await getDraft<{ text: string }>('editor:1'))?.value.text).toBe('别弄丢我')
    expect(unsyncedDrafts().map((d) => d.key)).toContain('editor:1')
  })

  it('保存成功才清本地, 并解除关页面拦截', async () => {
    render(<Editor onSave={async () => true} />)
    fireEvent.change(screen.getByTestId('in'), { target: { value: '这次成了' } })
    await waitFor(async () => { expect(await getDraft('editor:1')).not.toBeNull() })

    fireEvent.click(screen.getByTestId('save'))
    await waitFor(async () => { expect(await getDraft('editor:1')).toBeNull() })
    expect(unsyncedDrafts()).toEqual([])
  })

  it('重开时摆出来让人选, 不自动覆盖当前内容', async () => {
    await putDraft('editor:1', '方案', { text: '崩之前写的' })
    render(<Editor onSave={async () => true} />)

    const recovered = await screen.findByTestId('recovered')
    expect(recovered.textContent).toBe('崩之前写的')
    // 关键: 输入框还是原样, 没被草稿悄悄盖掉
    expect((screen.getByTestId('in') as HTMLInputElement).value).toBe('')

    fireEvent.click(screen.getByTestId('restore'))
    expect((screen.getByTestId('in') as HTMLInputElement).value).toBe('崩之前写的')
  })

  it('丢弃就真的删掉, 下次不再打扰', async () => {
    await putDraft('editor:1', '方案', { text: '不要了' })
    render(<Editor onSave={async () => true} />)
    fireEvent.click(await screen.findByTestId('discard'))
    await waitFor(async () => { expect(await getDraft('editor:1')).toBeNull() })
  })

  it('草稿和当前内容一样时不打扰 —— 没什么可"恢复"的', async () => {
    await putDraft('editor:1', '方案', { text: '一样的' })
    render(<Editor onSave={async () => true} initial="一样的" />)
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByTestId('recovered')).toBeNull()
  })

  it('内容退回干净态时清掉草稿 —— 免得下次误报可恢复', async () => {
    render(<Editor onSave={async () => true} />)
    const input = screen.getByTestId('in')
    fireEvent.change(input, { target: { value: '临时打的字' } })
    await waitFor(async () => { expect(await getDraft('editor:1')).not.toBeNull() })
    fireEvent.change(input, { target: { value: '' } })
    await waitFor(async () => { expect(await getDraft('editor:1')).toBeNull() })
  })

  it('卸载不删草稿 —— 关掉抽屉不等于放弃内容', async () => {
    const view = render(<Editor onSave={async () => true} />)
    fireEvent.change(screen.getByTestId('in'), { target: { value: '关了再回来' } })
    await waitFor(async () => { expect(await getDraft('editor:1')).not.toBeNull() })
    view.unmount()
    expect((await getDraft<{ text: string }>('editor:1'))?.value.text).toBe('关了再回来')
    // 但拦截要注销, 否则会留下永远拦人的幽灵条目
    expect(unsyncedDrafts()).toEqual([])
  })
})
