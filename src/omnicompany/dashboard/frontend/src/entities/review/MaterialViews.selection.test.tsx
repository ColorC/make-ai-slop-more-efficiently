import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Material } from '../../api/reviewstageClient'
import { MaterialContentView } from './MaterialViews'

function material(): Material {
  return {
    id: 'selection-material', kind: 'markdown', tier: 'important', title: '选区测试', status: 'pending',
    source_subagent_id: null, source_plan_id: null, file_relpath: null,
    inline_content: 'Selectable content for a review comment.', annotations: [], comments: [],
    annotations_allowed: true, created_at: '', updated_at: '', history: [], pushed_to_user: false,
    pushed_reason: null, pushed_at: null, extra: {},
  } as Material
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('document selection comments', () => {
  it('keeps a visible hard selection frame while the themed composer is open', async () => {
    render(<MaterialContentView m={material()} onElementSelect={() => {}} onTextSelection={() => {}} />)

    const surface = screen.getByTestId('material-selection-surface')
    const textNode = screen.getByTestId('material-markdown').querySelector('p')?.firstChild
    expect(textNode).toBeTruthy()
    const rect = {
      left: 40, top: 50, right: 200, bottom: 70, width: 160, height: 20,
      x: 40, y: 50, toJSON: () => ({}),
    } as DOMRect
    vi.spyOn(window, 'getSelection').mockReturnValue({
      anchorNode: textNode,
      rangeCount: 1,
      toString: () => 'Selectable content',
      getRangeAt: () => ({
        getBoundingClientRect: () => rect,
        getClientRects: () => [rect],
      }),
      removeAllRanges: vi.fn(),
    } as unknown as Selection)

    fireEvent.mouseUp(surface)
    await waitFor(() => expect(screen.getByTestId('selection-comment-btn')).toBeTruthy())
    expect(screen.queryByTestId('selection-agent-action')).toBeNull()
    const mark = screen.getByTestId('selection-comment-mark')
    expect(mark.classList.contains('rf-selection-mark')).toBe(true)
    expect(surface.contains(mark)).toBe(false)
    expect(mark.parentElement).toBe(document.getElementById('root') ?? document.body)

    fireEvent.click(screen.getByTestId('selection-comment-btn'))
    const composer = screen.getByTestId('selection-comment-composer')
    expect(composer.classList.contains('rf-selection-composer')).toBe(true)
    expect(screen.getByTestId('selection-comment-mark')).toBeTruthy()

    // 在输入框里松开鼠标不应被 surface 的 mouseup 误判为一次新选区。
    fireEvent.mouseUp(screen.getByTestId('selection-comment-input'))
    expect(screen.getByTestId('selection-comment-composer')).toBeTruthy()
  })

})
