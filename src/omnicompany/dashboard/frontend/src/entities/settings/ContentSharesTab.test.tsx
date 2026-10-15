import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ContentSharesTab from './ContentSharesTab'
import { settingsRegistration } from './index'

const READY_SHARE = {
  protocol_version: '1.0.0',
  share_id: 'ui-alpha01',
  type: 'ui',
  status: 'ready',
  title: '界面快照',
  canonical_url: 'http://192.168.1.8:8210/s/ui-alpha01',
  access: 'lan-readonly',
  producer: {
    product: 'WebTwin',
    adapter: 'spark-webtwin.ui',
    resolver: 'omni-content-share-manager',
    source_ref: 'webtwin:pick:alpha',
    mode: 'frozen',
  },
  created_at: '2026-09-09T01:00:00Z',
  updated_at: '2026-09-09T02:03:04Z',
  sections: [],
  extras: [],
  aliases: [],
}

const REVOKED_SHARE = {
  ...READY_SHARE,
  share_id: 'pg-bravo02',
  type: 'pg',
  status: 'revoked',
  title: '页面记录',
  canonical_url: 'http://192.168.1.8:8210/s/pg-bravo02',
  producer: { ...READY_SHARE.producer, product: 'Spark Workshop' },
}

function response(payload: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => payload } as Response
}

describe('ContentSharesTab', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ items: [READY_SHARE, REVOKED_SHARE], count: 2 })))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn(async () => undefined) },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('is available from settings and loads the unified share list', async () => {
    const SettingsEditor = settingsRegistration.renderer.Editor
    render(<SettingsEditor entity={{ type: 'settings', id: 'main', title: '设置' } as any} />)

    fireEvent.click(screen.getByRole('radio', { name: '内容分享' }))

    expect(await screen.findByTestId('content-shares-tab')).toBeTruthy()
    expect(screen.getByTestId('content-shares-total').textContent).toContain('共 2 项')
    expect(screen.getByText('ui-alpha01')).toBeTruthy()
    expect(screen.getByText('界面快照')).toBeTruthy()
    expect(screen.getByText('WebTwin')).toBeTruthy()
    expect(screen.getAllByText('ui').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('可用').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(READY_SHARE.canonical_url)).toBeTruthy()
    expect(screen.getAllByText(/2026/)).toHaveLength(2)
  })

  it('refreshes, copies a canonical URL, and only offers revoke for active shares', async () => {
    const fetchMock = vi.mocked(fetch)
    render(<ContentSharesTab />)
    await screen.findByText('界面快照')

    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const readyCard = screen.getByTestId('content-share-ui-alpha01')
    fireEvent.click(screen.getAllByRole('button', { name: '复制链接' })[0])
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(READY_SHARE.canonical_url))
    expect(await screen.findByText('链接已复制')).toBeTruthy()

    expect(readyCard.textContent).toContain('撤销')
    expect(screen.getByTestId('content-share-pg-bravo02').querySelectorAll('button')).toHaveLength(1)
  })

  it('posts revoke and reloads the list', async () => {
    const revoked = { ...READY_SHARE, status: 'revoked' }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ items: [READY_SHARE], count: 1 }))
      .mockResolvedValueOnce(response(revoked))
      .mockResolvedValueOnce(response({ items: [revoked], count: 1 }))
    vi.stubGlobal('fetch', fetchMock)
    render(<ContentSharesTab />)
    await screen.findByText('界面快照')

    fireEvent.click(screen.getByRole('button', { name: '撤销' }))

    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/content-share/v1/shares/ui-alpha01/revoke',
      { method: 'POST' },
    ))
    await waitFor(() => expect(screen.getByTestId('content-share-ui-alpha01').textContent).toContain('已撤销'))
    expect(screen.queryByRole('button', { name: '撤销' })).toBeNull()
  })

  it('shows explicit empty and request error states', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ items: [], count: 0 })))
    const view = render(<ContentSharesTab />)
    expect(await screen.findByText('还没有内容分享')).toBeTruthy()
    view.unmount()

    vi.stubGlobal('fetch', vi.fn(async () => response({}, false, 503)))
    render(<ContentSharesTab />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('读取失败')
    expect(alert.textContent).toContain('HTTP 503')
  })
})
