import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { ReviewThumb, type ReviewItemData } from '@overlay-notes/regions/reviewShared'

// 2026-09-01 用户反馈: 审阅台里 markdown 卡没按预览显示. 详情主角位(variant="full")必须渲成 HTML,
// 列表缩略(thumb)保持纯文本摘要.
const item: ReviewItemData = {
  key: 'k', cardId: 'review-x', canvasKey: 'c', canvasTitle: 't', sessionId: 's', sessionLabel: 's',
  title: 'C 盘审计', kind: 'text', adapter: 'text', fileToken: null, url: null, mime: 'text/markdown',
  editableId: 'review-x',
  submission: { state: 'unreviewed', submittedAt: '2026-09-01T00:00:00.000Z', submittedBy: 'cli' },
} as unknown as ReviewItemData

const md = '# 标题一\n\n| 列 | 值 |\n|---|---|\n| a | **粗** |\n\n<script>alert(1)</script>'

describe('ReviewThumb markdown', () => {
  afterEach(() => cleanup())

  it('full 变体把 markdown 渲成 HTML 并消毒', async () => {
    const { container } = render(<ReviewThumb item={item} fetchText={async () => md} variant="full" />)
    await waitFor(() => expect(container.querySelector('[data-material-renderer="material-note-markdown"]')).toBeTruthy())
    const el = container.querySelector('.material-markdown-preview') as HTMLElement
    expect(el.querySelector('h1')?.textContent).toBe('标题一')
    expect(el.querySelector('table')).toBeTruthy()
    expect(el.querySelector('strong')?.textContent).toBe('粗')
    expect(el.querySelector('script')).toBeNull()
    expect(el.textContent).not.toContain('# 标题一')
  })

  it('thumb 变体仍是纯文本摘要', async () => {
    const { container } = render(<ReviewThumb item={item} fetchText={async () => md} />)
    await waitFor(() => expect(container.textContent).toContain('# 标题一'))
    expect(container.querySelector('h1')).toBeNull()
  })
})
