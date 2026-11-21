import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SubscriptionsTab, {
  providerIconSrc,
  remainingPct,
  sortProviders,
  usedPct,
  type SubscriptionProvider,
} from './SubscriptionsTab'

const PROVIDERS: SubscriptionProvider[] = [
  {
    id: 'claude',
    display_name: 'Anthropic Claude',
    method: 'official_api',
    next_reset_in_sec: 7200,
    observed_at: '2026-08-14T01:00:00Z',
    windows: [{ id: 'five_hour', label: '5小时', remaining_pct: 40, reset_in_sec: 7200 }],
  },
  {
    id: 'kimi',
    display_name: 'Kimi Code',
    method: 'webpage',
    next_reset_in_sec: 300,
    observed_at: '2026-08-13T01:00:00Z',
    usage_url: 'https://www.kimi.com/code/console',
    windows: [{ id: 'weekly', label: '周', remaining_pct: 12, reset_in_sec: 300 }],
  },
  {
    id: 'codex',
    display_name: 'OpenAI Codex',
    reason: 'diagnostic text must not be rendered',
    windows: [],
  },
]

describe('SubscriptionsTab', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ providers: PROVIDERS }),
    })))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('maps pty cli icons and fills usage bars from remaining', () => {
    expect(providerIconSrc('claude')).toBe('/assets/cli/claudecode-color.svg')
    expect(providerIconSrc('the_company-api')).toBe('/assets/cli/the_company-color.png')
    expect(providerIconSrc('codex_the_company')).toBe('/assets/cli/the_company-color.png')
    expect(providerIconSrc('codex-official-2')).toBe('/assets/cli/codex-color.svg')
    expect(usedPct({ id: 'w', label: '5小时', remaining_pct: 40 })).toBe(60)
    expect(remainingPct({ id: 'w', label: '5小时', used_pct: 60 })).toBe(40)
  })

  it('sorts soonest reset first', () => {
    expect(sortProviders(PROVIDERS, 'reset_at').map((row) => row.id)).toEqual(['kimi', 'claude', 'codex'])
  })

  it('renders cards without a manual calibration form', async () => {
    render(<SubscriptionsTab />)
    expect(await screen.findByTestId('settings-subscriptions')).toBeTruthy()
    expect(screen.getByText('Anthropic Claude')).toBeTruthy()
    expect(screen.queryByText('Kimi Code')).toBeNull()
    expect(screen.getByText('剩余 40%')).toBeTruthy()
    expect(screen.getByText('diagnostic text must not be rendered')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '记一笔校准' })).toBeNull()
    expect(screen.queryByLabelText('Kimi Code 剩余')).toBeNull()
    const cards = screen.getAllByTestId(/subscription-card-/)
    expect(cards[0].getAttribute('data-testid')).toBe('subscription-card-claude')
  })

  it('shows cached accounts immediately and picks up each background completion', async () => {
    vi.useFakeTimers()
    const first = { id: 'claude', display_name: 'Claude-1', available: true,
      observed_at: '2026-09-07T01:00:00Z', windows: [{ id: 'h', label: '5小时', remaining_pct: 64 }],
      account_home: '.claude', refreshing: true }
    const second = { id: 'claude-official-2', display_name: 'Claude-2', windows: [], refreshing: true }
    const responses = [
      { refreshing: true, providers: [first, second] },
      { refreshing: true, providers: [{ ...first, refreshing: false }, second] },
      { refreshing: false, providers: [{ ...first, refreshing: false },
        { ...second, refreshing: false, refresh_error: '账号认证失败' }] },
    ]
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => responses.shift() })))
    const view = render(<SubscriptionsTab />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('Claude-1')).toBeTruthy()
    expect(screen.getByText('Claude-2')).toBeTruthy()
    expect(screen.getByText('剩余 64%')).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(750) })
    expect(screen.getByTestId('subscription-status-claude').textContent).not.toContain('正在刷新')
    expect(screen.getByTestId('subscription-status-claude-official-2').textContent).toContain('正在刷新')
    await act(async () => { await vi.advanceTimersByTimeAsync(750) })
    expect(screen.getByText('账号认证失败')).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(fetch).toHaveBeenCalledTimes(3)
    view.unmount()
    vi.useRealTimers()
  })

  it('keeps previous values and observation time during manual refresh', async () => {
    render(<SubscriptionsTab />)
    await screen.findByText('剩余 40%')
    let resolve!: (value: unknown) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise((done) => { resolve = done })))
    fireEvent.click(screen.getByTestId('subscriptions-refresh'))
    expect(screen.getByText('剩余 40%')).toBeTruthy()
    expect(screen.getByTestId('subscription-status-claude').textContent).toContain('2026')
    await act(async () => resolve({ ok: true, json: async () => ({ providers: PROVIDERS }) }))
    await waitFor(() => expect(screen.getByTestId('subscriptions-refresh').textContent).toContain('刷新'))
  })

  it('bounds automatic polling even when a provider never finishes', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true,
      json: async () => ({ refreshing: true, providers: PROVIDERS }) })))
    const view = render(<SubscriptionsTab />)
    await act(async () => { await vi.advanceTimersByTimeAsync(46_000) })
    expect(screen.getByTestId('subscriptions-poll-paused').textContent).toContain('已有读数保留')
    const calls = vi.mocked(fetch).mock.calls.length
    expect(calls).toBeLessThanOrEqual(61)
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000) })
    expect(fetch).toHaveBeenCalledTimes(calls)
    expect(screen.getByText('剩余 40%')).toBeTruthy()
    view.unmount()
  })
})
