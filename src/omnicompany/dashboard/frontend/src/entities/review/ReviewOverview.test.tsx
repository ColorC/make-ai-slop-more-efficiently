import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import ReviewOverview from './ReviewOverview'
import { mdExcerpt, MaterialOverviewCard, cardSize } from './MaterialOverviewCard'
import type { Material } from '../../api/reviewstageClient'

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
    inline_content: null,
    annotations: [],
    comments: [],
    annotations_allowed: true,
    created_at: '',
    updated_at: '',
    history: [],
    pushed_to_user: false,
    pushed_reason: null,
    pushed_at: null,
    extra: {},
    ...over,
  }
}

describe('mdExcerpt', () => {
  it('strips markdown and truncates', () => {
    expect(mdExcerpt('# Title\n\nsome **bold** body', 100)).toBe('Title some bold body')
    expect(mdExcerpt(null)).toBe('')
    const long = 'a'.repeat(200)
    expect(mdExcerpt(long, 10)).toBe(`${'a'.repeat(10)}…`)
  })
})

describe('MaterialOverviewCard preview dispatch', () => {
  it('renders the right preview per kind', () => {
    const { rerender } = render(<MaterialOverviewCard m={mat('a', { kind: 'markdown', inline_content: 'hello body' })} />)
    expect(screen.getByTestId('card-preview-markdown')).toBeTruthy()
    rerender(<MaterialOverviewCard m={mat('b', { kind: 'image', file_relpath: 'x.png' })} />)
    expect(screen.getByTestId('card-preview-image')).toBeTruthy()
    rerender(<MaterialOverviewCard m={mat('c', { kind: 'video' })} />)
    expect(screen.getByTestId('card-preview-video')).toBeTruthy()
    rerender(<MaterialOverviewCard m={mat('d', { kind: 'html', extra: { live_url: 'http://x' } })} />)
    expect(screen.getByTestId('card-preview-html')).toBeTruthy()
  })

  it('retries a queued cover without asking the overview to generate it', () => {
    vi.useFakeTimers()
    try {
      render(<MaterialOverviewCard m={mat('queued', { kind: 'markdown', updated_at: 'now' })} />)
      const cover = screen.getByTestId('card-cover') as HTMLImageElement
      const initial = cover.src
      fireEvent.error(cover)
      act(() => vi.advanceTimersByTime(1_000))
      expect(cover.src).not.toBe(initial)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('cardSize: tier × visualizability', () => {
  it('maps importance and visual kind to size', () => {
    expect(cardSize({ kind: 'image', tier: 'mandatory' })).toBe('feature')   // never spans the full row
    expect(cardSize({ kind: 'markdown', tier: 'mandatory' })).toBe('feature') // 3+0
    expect(cardSize({ kind: 'image', tier: 'important' })).toBe('feature')    // 2+1
    expect(cardSize({ kind: 'markdown', tier: 'important' })).toBe('normal')  // 2+0
    expect(cardSize({ kind: 'video', tier: 'processual' })).toBe('normal')    // 1+1
    expect(cardSize({ kind: 'markdown', tier: 'processual' })).toBe('compact')// 1+0
    expect(cardSize({ kind: 'plan', tier: 'ignored' })).toBe('compact')       // 0+0
  })
})

describe('ReviewOverview', () => {
  // 时间乱序传入,组件默认按优先级聚合,同优先级内保持 updated_at 倒序。
  const sample = [
    mat('old', { kind: 'markdown', tier: 'processual', updated_at: '2026-06-25T08:00:00Z' }), // compact
    mat('new', { kind: 'image', tier: 'mandatory', file_relpath: 'x.png', updated_at: '2026-06-25T10:00:00Z' }), // feature
    mat('mid', { kind: 'image', tier: 'important', file_relpath: 'y.png', updated_at: '2026-06-25T09:00:00Z' }), // feature
  ]

  it('packs every tier into one waterfall without a full-width mandatory card', async () => {
    render(<ReviewOverview fetcher={async () => sample} pollMs={0} />)
    await waitFor(() => expect(screen.getByTestId('review-overview')).toBeTruthy())
    const cards = screen.getAllByTestId('overview-card')
    expect(cards.length).toBe(3)
    // newest first
    expect(cards[0].getAttribute('data-size')).toBe('feature')
    expect(cards[0].getAttribute('data-hero')).toBe('0')
    expect(cards[1].getAttribute('data-size')).toBe('feature')
    expect(cards[2].getAttribute('data-size')).toBe('compact')
    expect(screen.getAllByTestId('overview-waterfall')).toHaveLength(1)
    expect(screen.queryByTestId('overview-meta')).toBeNull()
    expect(screen.getByTestId('overview-actions')).toBeTruthy()
  })

  it('uses one compact button to return to review', async () => {
    const onOpenReview = vi.fn()
    render(<ReviewOverview fetcher={async () => sample} pollMs={0} onOpenReview={onOpenReview} />)
    await screen.findByTestId('overview-view-review')
    expect(screen.queryByTestId('overview-view-overview')).toBeNull()
    expect(screen.getByTestId('overview-view-review').getAttribute('aria-label')).toBe('返回审阅')
    fireEvent.click(screen.getByTestId('overview-view-review'))
    expect(onOpenReview).toHaveBeenCalledOnce()
  })

  it('shows error on fetch failure', async () => {
    render(
      <ReviewOverview
        fetcher={async () => {
          throw new Error('nope')
        }}
        pollMs={0}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('overview-error')).toBeTruthy())
    expect(screen.getByTestId('overview-error').textContent).toContain('nope')
  })
})
