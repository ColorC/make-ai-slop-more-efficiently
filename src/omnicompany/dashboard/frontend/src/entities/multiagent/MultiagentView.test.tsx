import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { providerDisplayName } from './MultiagentView'

const PAYLOAD = {
  pty: [
    {
      id: 'pty-work', alive: true, working: true, has_user_turn: true, status: 'alive',
      provider: 'codex', cwd: 'E:\\work\\alpha', provider_title: 'raw first prompt',
      display_title: '修终端闪烁', started_at: Date.now() / 1000 - 3600,
    },
    {
      id: 'pty-idle', alive: true, working: false, has_user_turn: true, status: 'alive',
      provider: 'claude_code', cwd: 'E:\\work\\beta', provider_title: null,
    },
  ],
  chat: [
    {
      id: 'chat-run', alive: true, running: true, status: 'alive',
      provider: 'claude_code', cwd: 'E:\\work\\gamma', name: '审阅改造',
      started_at: Date.now() / 1000 - 120,
      token_usage: {
        total: 12_400, input: 8_000, output: 2_000,
        cache_creation_input: 400, cache_read_input: 2_000, source: 'provider_reported',
      },
    },
  ],
}

function stubTabStates(payload: unknown) {
  const fetchMock = vi.fn(async (url: unknown) => {
    if (String(url).includes('/api/cc/tab-states')) return { ok: true, json: async () => payload }
    if (String(url).includes('/api/boss-sight/residents/') && String(url).includes('/tail')) {
      return { ok: true, json: async () => ({ lines: [] }) }
    }
    if (String(url).includes('/api/boss-sight/residents')) {
      return { ok: true, json: async () => ({ source: 'test', count: 0, now: Date.now() / 1000, residents: [] }) }
    }
    throw new Error(`unexpected fetch: ${String(url)}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
})

async function renderView(tabs: Array<{ id: string; refId: string; title: string }>) {
  stubTabStates(PAYLOAD)
  const { default: MultiagentView } = await import('./MultiagentView')
  const { usePanels } = await import('../../stores/panelsStore')
  usePanels.setState({
    tabs: tabs.map((tab) => ({
      id: tab.id,
      ref: { type: tab.id.startsWith('cc_session:') ? 'cc_session' : 'controller', id: tab.refId },
      title: tab.title,
    })),
    activeId: tabs[0]?.id ?? null,
  } as never)
  render(<MultiagentView />)
  return { usePanels }
}

const OPEN_TABS = [
  { id: 'controller:main', refId: 'main', title: '总控' },
  { id: 'cc_session:pty-idle', refId: 'pty-idle', title: 'Claude · beta' },
  { id: 'cc_session:pty-work', refId: 'pty-work', title: 'Codex · alpha' },
  { id: 'cc_session:chat-run', refId: 'chat-run', title: '审阅改造 · chat' },
  // 快照里没有这个 id(进程已死): 仍要列出来, 标为已结束。
  { id: 'cc_session:pty-dead', refId: 'pty-dead', title: 'Claude · dead' },
]

describe('MultiagentView 列表', () => {
  it('只列真实活 runtime，过滤已死页签，并保持已打开页签顺序', async () => {
    await renderView(OPEN_TABS)

    const rows = await screen.findAllByTestId('multiagent-row')
    expect(rows.map((row) => row.getAttribute('data-session-id')))
      .toEqual(['pty-idle', 'pty-work', 'chat-run'])
    expect(rows.map((row) => row.getAttribute('data-state')))
      .toEqual(['done', 'working', 'working'])
    expect(rows.map((row) => row.getAttribute('data-kind')))
      .toEqual(['pty', 'pty', 'chat'])
    expect(rows[0].textContent).toContain('待命')
    expect(rows[1].textContent).toContain('进行中')
  })

  it('没有重复厚标题栏，工具区保留新会话、Full 和刷新', async () => {
    await renderView(OPEN_TABS)
    await screen.findAllByTestId('multiagent-row')
    expect(document.querySelector('.ma-head')).toBeNull()
    expect(screen.getByTestId('multiagent-new-session')).toBeTruthy()
    expect(screen.getByTestId('multiagent-full-mode')).toBeTruthy()
    expect(screen.getByTestId('multiagent-refresh')).toBeTruthy()
  })

  it('小模型标题到达后覆盖页签创建时的占位标题', async () => {
    await renderView(OPEN_TABS)
    expect(await screen.findByText('修终端闪烁')).toBeTruthy()
    expect(screen.queryByText('Codex · alpha')).toBeNull()
  })

  it('点一行切到该会话页签', async () => {
    const { usePanels } = await renderView(OPEN_TABS)
    const rows = await screen.findAllByTestId('multiagent-row')
    fireEvent.click(rows[1].querySelector('.ma-row-hit') as HTMLElement)
    expect(usePanels.getState().activeId).toBe('cc_session:pty-work')
  })

  it('Full mode 拉高行并显示 provider 回传的 Token', async () => {
    await renderView(OPEN_TABS)
    await screen.findAllByTestId('multiagent-row')
    fireEvent.click(screen.getByTestId('multiagent-full-mode'))
    const chatRow = screen.getAllByTestId('multiagent-row').find((row) => row.dataset.sessionId === 'chat-run')
    expect(chatRow?.getAttribute('data-full')).toBe('1')
    expect(chatRow?.textContent).toContain('12.4k tok')
  })

  it('后端没有活 runtime 时给出空态', async () => {
    stubTabStates({ pty: [], chat: [] })
    const { default: MultiagentView } = await import('./MultiagentView')
    const { usePanels } = await import('../../stores/panelsStore')
    usePanels.setState({ tabs: [], activeId: null } as never)
    render(<MultiagentView />)
    await screen.findByTestId('multiagent-empty')
    expect(screen.queryAllByTestId('multiagent-row')).toHaveLength(0)
  })
})

describe('providerDisplayName', () => {
  it('maps provider ids to friendly names with a safe fallback', () => {
    expect(providerDisplayName('claude_code')).toBe('Claude')
    expect(providerDisplayName('codex')).toBe('Codex')
    expect(providerDisplayName('controller')).toBe('总控')
    expect(providerDisplayName(undefined)).toBe('Agent')
  })
})
