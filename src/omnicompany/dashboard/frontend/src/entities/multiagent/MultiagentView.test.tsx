import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  canonicalResidentProject,
  canonicalSessionProject,
  groupSessionRows,
  isFoldedSession,
  isListedInMultiagent,
  lineageParentLabel,
  mergePendingRuntimeIds,
  multiagentSortRank,
  planDisplayName,
  providerDisplayName,
  residentFor,
  resolveSessionVisibility,
  runtimeTabs,
  sessionDisplayTitle,
  sessionLineageBadge,
  stableMultiagentSessionIds,
  sessionProjectGroupLabel,
  summaryDisplayText,
  readNewSessionPref,
} from './MultiagentView'
import { providerIconSource } from './ProviderIcon'
import { useSessionCompletionAttention } from '../cc_session/sessionAttention'

const PAYLOAD = {
  pty: [
    {
      id: 'pty-work', alive: true, working: true, has_user_turn: true, status: 'alive',
      provider: 'codex', cwd: 'E:\\work\\alpha', provider_title: 'raw first prompt',
      display_title: '修终端闪烁', started_at: Date.now() / 1000 - 3600,
    },
    {
      id: 'pty-idle', alive: true, working: false, has_user_turn: true, status: 'alive',
      provider: 'claude_code', origin: 'agent', cwd: 'E:\\work\\beta', provider_title: null,
      runtime_host: 'litework',
      started_at: Date.now() / 1000 - 600, last_submit_at: Date.now() / 1000 - 90,
      last_output_at: Date.now() / 1000 - 60,
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

const SESSION_CATALOG = {
  schema_version: 1,
  options: [
    {
      id: 'provider:codex', label: 'Codex from server', kind: 'agent_worker', provider: 'codex',
      default_model: 'gpt-5.6-luna', models: ['gpt-5.6-luna', 'gpt-5.6-terra'],
      model_labels: { 'gpt-5.6-luna': 'Luna from server', 'gpt-5.6-terra': 'Terra from server' },
      model_efforts: { 'gpt-5.6-luna': ['low', 'medium', 'high'], 'gpt-5.6-terra': ['medium', 'high', 'xhigh'] },
      model_default_efforts: { 'gpt-5.6-luna': 'medium', 'gpt-5.6-terra': 'high' },
      default_effort: 'medium', efforts: ['low', 'medium', 'high', 'xhigh'], surface: 'pty',
    },
    {
      id: 'provider:qoder', label: 'Qoder from server', kind: 'agent_worker', provider: 'qoder',
      default_model: null, models: ['qwen3.8-max'], model_labels: { 'qwen3.8-max': 'Qwen from server' },
      model_efforts: { 'qwen3.8-max': [] }, model_default_efforts: { 'qwen3.8-max': null },
      efforts: [], surface: 'pty',
    },
    { id: 'shell:powershell', label: 'PowerShell', kind: 'shell', cmd: ['pwsh'] },
  ],
  assistant: { label: 'the_company from server', provider: 'codex_the_company', model: 'deepseek-v4-flash', effort: 'medium' },
}

function stubTabStates(payload: unknown, ptySessions?: unknown) {
  const projectedPty = (payload as { pty?: unknown[] })?.pty || []
  const sessionPayload = ptySessions ?? {
    items: projectedPty, alive_count: projectedPty.length,
    recoverable: [], recoverable_count: 0, remembered: [], remembered_count: 0,
  }
  const fetchMock = vi.fn(async (url: unknown, init?: RequestInit) => {
    if (init?.method === 'DELETE' && String(url).includes('/api/cc/sessions/')) {
      return { ok: true, json: async () => ({}) }
    }
    if (String(url).includes('/api/cc/agent-workers/catalog')) return { ok: true, json: async () => SESSION_CATALOG }
    if (String(url).includes('/api/cc/tab-states')) return { ok: true, json: async () => payload }
    if (String(url).includes('/api/cc/sessions?')) return { ok: true, json: async () => sessionPayload }
    if (String(url).includes('/api/cc/chat/sessions')) {
      // 结构化 chat 的存活位来自这个全量接口: 快照里有的会话这里必须也在, 否则
      // hydrate 一回来就把它判成已死清出列表(测试里会表现为行随机消失)。
      const items = ((payload as { chat?: Array<Record<string, unknown>> })?.chat || [])
        .map((item) => ({ ...item, runtime_alive: true }))
      return { ok: true, json: async () => ({ items, total: items.length, limit: 500, offset: 0, has_more: false }) }
    }
    if (String(url).includes('/api/boss-sight/session-placements')) {
      return { ok: true, json: async () => ({ placements: {}, count: 0 }) }
    }
    if (String(url).includes('/api/boss-sight/residents/') && String(url).includes('/tail')) {
      return { ok: true, json: async () => ({ lines: [] }) }
    }
    if (String(url).includes('/api/boss-sight/residents')) {
      return { ok: true, json: async () => ({
        source: 'test', count: 3, now: Date.now() / 1000,
        residents: [
          {
            key: 'codex:pty-work', provider: 'codex', session_id: 'pty-work', name: 'Codex',
            project: 'omnidashboard', active_plan: 'dashboard/[2026-08-09]MULTIAGENT-CARDS',
            workstream: 'omnidashboard.multiagent-coordination', workstream_name: '多 Agent 协作与会话治理',
            active_plan_title: 'Multiagent 会话卡片', authoritative: true,
            role: 'agent', identity: 'codex', location: 'local', run_status: 'working', running: true,
          },
          {
            key: 'claude_code:pty-idle', provider: 'claude_code', session_id: 'pty-idle', name: 'Claude',
            project: 'omnidashboard', role: 'agent', identity: 'claude', location: 'local',
            run_status: 'done', running: false,
            origin: 'agent',
            lineage: {
              kind: 'derived', origin: 'ai', relation: 'subagent',
              parent_session_id: 'pty-work', parent_title: '修终端闪烁',
              agent_name: 'Kierkegaard', agent_path: '/root/lineage_review',
              task: '验证派生会话身份标识', depth: 1,
            },
          },
          {
            key: 'claude_code:chat-run', provider: 'claude_code', session_id: 'chat-run', name: 'Claude',
            project: 'omnidashboard', role: 'agent', identity: 'claude', location: 'local',
            run_status: 'working', running: true,
          },
        ],
      }) }
    if (String(url).includes('/api/projects')) {
      return { ok: true, json: async () => ({
        projects: [{
          id: 'omnidashboard', name: 'Omni Dashboard 驾驶舱', short: '驾驶舱', group: 'core',
          roots: ['C:/workspace/omnicompany/src/omnicompany/dashboard'],
        }],
        groups_order: [], group_labels: {},
      }) }
    }
    }
    throw new Error(`unexpected fetch: ${String(url)}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
  // 新建成功会把执行者/模型/推理强度记进 localStorage(下次默认沿用上一次建的),
  // 不清掉的话上一个用例建过的会话会改掉下一个用例的初始选择。
  window.localStorage.clear()
})

async function renderView(tabs: Array<{ id: string; refId: string; title: string }>) {
  useSessionCompletionAttention.setState({ seen: {} })
  const fetchMock = stubTabStates(PAYLOAD)
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
  return { usePanels, fetchMock }
}

const OPEN_TABS = [
  { id: 'controller:main', refId: 'main', title: '总控' },
  { id: 'cc_session:pty-idle', refId: 'pty-idle', title: 'Claude · beta' },
  { id: 'cc_session:pty-work', refId: 'pty-work', title: 'Codex · alpha' },
  { id: 'cc_session:chat-run', refId: 'chat-run', title: '审阅改造 · chat' },
  // 快照里没有这个 id(进程已死): 仍要列出来, 标为已结束。
  { id: 'cc_session:pty-dead', refId: 'pty-dead', title: 'Claude · dead' },
]

/** LiTeWork / AI 创建的会话默认折叠, 需要看它们的用例先把组展开。 */
function expandFolded() {
  const toggle = screen.queryByTestId('multiagent-fold-toggle')
  if (toggle && toggle.getAttribute('data-open') !== '1') fireEvent.click(toggle)
}

describe('会话后置', () => {
  it('keeps resume aliases usable for old tabs but omits them from the live-session list', () => {
    const snapshot = {
      states: { canonical: 'done', legacy: 'done' },
      metas: {
        canonical: { kind: 'pty', providerSessionId: 'native-1', startedAt: 1 },
        legacy: { kind: 'pty', providerSessionId: 'native-1', resumeAliasFor: 'canonical', startedAt: 1 },
      },
      ptyLoaded: true,
      chatLoaded: true,
    } as const

    const listed = runtimeTabs(
      [{ id: 'cc_session:legacy', ref: { type: 'cc_session', id: 'legacy' }, title: 'legacy tab' }],
      snapshot,
      new Set(['canonical']),
      new Set(),
    )

    expect(listed.map((tab) => tab.ref.id)).toEqual(['canonical'])
  })

  it('background 排到工作中和已完成之后，hidden 不进列表', () => {
    expect(isListedInMultiagent('hidden')).toBe(false)
    expect(isListedInMultiagent('background')).toBe(true)
    expect(multiagentSortRank('background', 'working')).toBeGreaterThan(multiagentSortRank('visible', 'done'))
    expect(resolveSessionVisibility('pty-probe', { visibility: 'hidden' }, { 'pty-probe': 'background' }))
      .toBe('background')
  })

  it('折叠组只吃 LiTeWork 与 AI 创建, 人工开的会话留在主列表', () => {
    expect(isFoldedSession({ runtimeHost: 'litework' })).toBe(true)
    expect(isFoldedSession({ origin: 'agent' })).toBe(true)
    expect(isFoldedSession(undefined, { lineage: { kind: 'derived', origin: 'ai', relation: 'subagent' } })).toBe(true)
    expect(isFoldedSession(undefined, { origin: 'agent' })).toBe(true)
    expect(isFoldedSession({ runtimeHost: 'omnicompany', origin: 'human' })).toBe(false)
    expect(isFoldedSession(undefined, { lineage: { kind: 'derived', origin: 'human', relation: 'fork' } })).toBe(false)
    expect(isFoldedSession()).toBe(false)
    expect(isFoldedSession({ origin: 'agent' }, {
      origin: 'human', lineage: { kind: 'derived', origin: 'ai', relation: 'worker' },
    })).toBe(false)
    expect(isFoldedSession({ runtimeHost: 'litework' }, { origin: 'human' })).toBe(true)
    expect(isFoldedSession({ origin: 'human' }, {
      lineage: { kind: 'derived', origin: 'ai', relation: 'worker' },
    })).toBe(false)
  })

  it('忽略快照输入顺序与非运行态抖动，只在工作组边界移动', () => {
    const snapshot = {
      states: { old: 'working', idle: 'done', recent: 'working' },
      metas: {
        old: { kind: 'pty', startedAt: 100 },
        idle: { kind: 'pty', startedAt: 200 },
        recent: { kind: 'pty', startedAt: 300 },
      },
      ptyLoaded: true,
      chatLoaded: true,
    } as const

    expect(stableMultiagentSessionIds(['recent', 'idle', 'old'], snapshot)).toEqual(['old', 'recent', 'idle'])
    expect(stableMultiagentSessionIds(['idle', 'old', 'recent'], snapshot)).toEqual(['old', 'recent', 'idle'])

    const uncertainSnapshot = { ...snapshot, states: { ...snapshot.states, idle: 'unknown' as const } }
    expect(stableMultiagentSessionIds(['recent', 'idle', 'old'], uncertainSnapshot)).toEqual(['old', 'recent', 'idle'])

    const stoppedSnapshot = { ...snapshot, states: { ...snapshot.states, recent: 'done' as const } }
    expect(stableMultiagentSessionIds(['recent', 'idle', 'old'], stoppedSnapshot)).toEqual(['old', 'idle', 'recent'])
  })
})

describe('MultiagentView 列表', () => {
  it('把 background 探测会话排到列表最后并标后置', async () => {
    stubTabStates(PAYLOAD)
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/cc/agent-workers/catalog')) return { ok: true, json: async () => SESSION_CATALOG }
      if (String(url).includes('/api/cc/tab-states')) return { ok: true, json: async () => PAYLOAD }
      if (String(url).includes('/api/boss-sight/session-placements')) {
        return { ok: true, json: async () => ({ placements: { 'pty-work': 'background' }, count: 1 }) }
      }
      if (String(url).includes('/api/boss-sight/residents/') && String(url).includes('/tail')) {
        return { ok: true, json: async () => ({ lines: [] }) }
      }
      if (String(url).includes('/api/boss-sight/residents')) {
        return { ok: true, json: async () => ({ source: 'test', count: 0, now: Date.now() / 1000, residents: [] }) }
      }
      if (String(url).includes('/api/projects')) {
        return { ok: true, json: async () => ({ projects: [], groups_order: [], group_labels: {} }) }
      }
      throw new Error(`unexpected fetch: ${String(url)}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { default: MultiagentView } = await import('./MultiagentView')
    const { usePanels } = await import('../../stores/panelsStore')
    usePanels.setState({
      tabs: OPEN_TABS.map((tab) => ({
        id: tab.id,
        ref: { type: tab.id.startsWith('cc_session:') ? 'cc_session' : 'controller', id: tab.refId },
        title: tab.title,
      })),
      activeId: OPEN_TABS[0]?.id ?? null,
    } as never)
    render(<MultiagentView />)
    await waitFor(() => {
      const rows = screen.getAllByTestId('multiagent-row')
      expect(rows.map((row) => row.getAttribute('data-session-id')))
        .toEqual(['chat-run', 'pty-work'])
    })
    expandFolded()
    const rows = screen.getAllByTestId('multiagent-row')
    expect(rows.map((row) => row.getAttribute('data-session-id')))
      .toEqual(['chat-run', 'pty-work', 'pty-idle'])
    expect(rows[1].getAttribute('data-visibility')).toBe('background')
    expect(rows[1].textContent).toContain('后置')
  })

  it('只列真实活 runtime，运行中优先，并隐藏右侧运行状态标签', async () => {
    await renderView(OPEN_TABS)

    await waitFor(() => {
      expect(screen.getAllByTestId('multiagent-row')).toHaveLength(2)
    })
    expandFolded()
    const rows = screen.getAllByTestId('multiagent-row')
    expect(rows.map((row) => row.getAttribute('data-session-id')))
      .toEqual(['pty-work', 'chat-run', 'pty-idle'])
    expect(rows.map((row) => row.getAttribute('data-state')))
      .toEqual(['working', 'working', 'done'])
    expect(rows.map((row) => row.getAttribute('data-kind')))
      .toEqual(['pty', 'chat', 'pty'])
    expect(rows[0].querySelector('.ma-run-state')).toBeNull()
    expect(rows[1].querySelector('.ma-run-state')).toBeNull()
    expect(rows[2].querySelector('.ma-run-state')).toBeNull()
    expect(rows[2].querySelector('[data-testid="session-litework-badge"]')?.textContent).toBe('LiTeWork')
    expect(document.querySelector('.ma-state')).toBeNull()
    expect(document.querySelector('.ma-state-dot')).toBeNull()
    expect(document.querySelector('.ma-runtime')).toBeNull()
    expect(document.querySelector('.ma-provider')).toBeNull()
    expect(rows[0].querySelector('.ma-cli-mark img')?.getAttribute('src')).toBe('/assets/cli/codex-color.svg')
    expect(rows[0].querySelector('.ma-cli-mark img')?.getAttribute('width')).toBe('16')
    expect(rows[0].querySelector('.ma-project-mark [data-project-icon="omnidashboard"]')).toBeTruthy()
  })

  it('把 LiTeWork 与 AI 创建的会话折进一组, 默认不占主列表', async () => {
    await renderView(OPEN_TABS)
    await waitFor(() => {
      expect(screen.getAllByTestId('multiagent-row').map((row) => row.getAttribute('data-session-id')))
        .toEqual(['pty-work', 'chat-run'])
    })

    const toggle = screen.getByTestId('multiagent-fold-toggle')
    expect(toggle.textContent).toContain('LiTeWork 与 AI 创建')
    expect(toggle.querySelector('.ma-fold-count')?.textContent).toBe('1')

    fireEvent.click(toggle)
    expect(screen.getAllByTestId('multiagent-row').map((row) => row.getAttribute('data-session-id')))
      .toEqual(['pty-work', 'chat-run', 'pty-idle'])

    fireEvent.click(toggle)
    expect(screen.getAllByTestId('multiagent-row').map((row) => row.getAttribute('data-session-id')))
      .toEqual(['pty-work', 'chat-run'])
  })

  it('双排显示会话、项目与绑定计划', async () => {
    await renderView(OPEN_TABS)
    expect(await screen.findByTestId('multiagent-project-group-omnidashboard')).toBeTruthy()
    const workRow = (await screen.findAllByTestId('multiagent-row'))
      .find((row) => row.dataset.sessionId === 'pty-work')
    expect(workRow?.querySelector('.ma-row-copy')).toBeTruthy()
    expect(workRow?.querySelector('.ma-project-name')?.textContent).toBe('omnidashboard')
    expect(workRow?.querySelector('.ma-workstream-name')?.textContent).toBe('多 Agent 协作与会话治理')
    expect(workRow?.querySelector('.ma-plan-name')?.textContent).toBe('Multiagent 会话卡片')
  })

  it('给已完成但尚未查看的会话显示待看边条语义', async () => {
    await renderView(OPEN_TABS)
    await waitFor(() => {
      expect(screen.queryByTestId('multiagent-fold-toggle')).toBeTruthy()
    })
    expandFolded()
    const idleRow = screen.getAllByTestId('multiagent-row')
      .find((row) => row.dataset.sessionId === 'pty-idle')
    expect(idleRow?.getAttribute('data-attention')).toBe('1')
    expect(idleRow?.querySelector('.ma-row-hit')?.getAttribute('aria-label')).toContain('有未查看结果')
  })

  it('明确点选已完成会话后立即确认已读', async () => {
    await renderView(OPEN_TABS)
    await waitFor(() => {
      expect(screen.queryByTestId('multiagent-fold-toggle')).toBeTruthy()
    })
    expandFolded()
    const idleRow = screen.getAllByTestId('multiagent-row')
      .find((row) => row.dataset.sessionId === 'pty-idle')
    expect(idleRow?.getAttribute('data-attention')).toBe('1')

    fireEvent.click(idleRow?.querySelector('.ma-row-hit') as HTMLElement)

    await waitFor(() => expect(idleRow?.getAttribute('data-attention')).toBe('0'))
  })

  it('在派生会话开头标出 AI 来源、父会话和独立职责', async () => {
    await renderView(OPEN_TABS)
    await waitFor(() => {
      expect(screen.queryByTestId('multiagent-fold-toggle')).toBeTruthy()
    })
    expandFolded()
    const idleRow = screen.getAllByTestId('multiagent-row')
      .find((row) => row.dataset.sessionId === 'pty-idle')

    expect(idleRow?.getAttribute('data-lineage-kind')).toBe('derived')
    expect(idleRow?.getAttribute('data-lineage-origin')).toBe('ai')
    expect(idleRow?.querySelector('.ma-lineage-badge')?.textContent).toBe('AI 派生')
    expect(idleRow?.querySelector('.ma-row-title')?.textContent).toBe('Claude · pty-idle')
    expect(idleRow?.querySelector('.ma-lineage-context')?.textContent)
      .toBe('来自 修终端闪烁 · Kierkegaard')
    expect(idleRow?.querySelector('.ma-row-hit')?.getAttribute('aria-label'))
      .toContain('AI 派生，来自 修终端闪烁 · Kierkegaard')
  })

  it('没有重复厚标题栏，工具区保留新会话、assistant 和刷新', async () => {
    await renderView(OPEN_TABS)
    await screen.findAllByTestId('multiagent-row')
    expect(document.querySelector('.ma-head')).toBeNull()
    expect(screen.getByTestId('multiagent-new-session')).toBeTruthy()
    expect(screen.getByTestId('multiagent-assistant')).toBeTruthy()
    expect(screen.queryByTestId('multiagent-full-mode')).toBeNull()
    expect(screen.getByTestId('multiagent-refresh')).toBeTruthy()
  })

  it('模型与推理强度完全由服务端目录驱动并随新会话提交', async () => {
    const { usePanels } = await renderView(OPEN_TABS)
    await screen.findByText('Luna from server')
    expect(screen.getByTestId('multiagent-assistant').getAttribute('title'))
      .toContain('the_company from server · deepseek-v4-flash')

    fireEvent.click(screen.getByRole('combobox', { name: '新会话模型' }))
    fireEvent.click(screen.getByRole('option', { name: 'Terra from server' }))
    fireEvent.click(screen.getByRole('combobox', { name: '新会话推理强度' }))
    fireEvent.click(screen.getByRole('option', { name: 'xhigh' }))

    const inheritedFetch = globalThis.fetch
    const launches: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
      const target = String(url)
      if (target.includes('/api/cc/agent-workers') && init?.method === 'POST') {
        launches.push(JSON.parse(String(init.body || '{}')))
        return {
          ok: true,
          json: async () => ({ id: 'new-dynamic', provider: 'codex', alive: true, status: 'alive' }),
        }
      }
      return inheritedFetch(url, init)
    }))

    fireEvent.click(screen.getByTestId('multiagent-new-session'))
    await waitFor(() => {
      expect(launches).toContainEqual(expect.objectContaining({
        provider: 'codex', model: 'gpt-5.6-terra', effort: 'xhigh', visibility: 'visible',
      }))
      expect(usePanels.getState().activeId).toBe('cc_session:new-dynamic')
    })
  })

  it('服务端未声明安全默认模型时保留 CLI 默认且不偷选目录第一项', async () => {
    await renderView(OPEN_TABS)
    await screen.findByText('Luna from server')
    fireEvent.click(screen.getByRole('combobox', { name: '新会话执行者' }))
    fireEvent.click(screen.getByRole('option', { name: 'Qoder from server' }))

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: '新会话模型' }).textContent).toContain('CLI 默认')
      expect(screen.queryByTestId('multiagent-new-effort')).toBeNull()
    })

    const inheritedFetch = globalThis.fetch
    const launches: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
      const target = String(url)
      if (target.includes('/api/cc/agent-workers') && init?.method === 'POST') {
        launches.push(JSON.parse(String(init.body || '{}')))
        return { ok: true, json: async () => ({ id: 'new-default', provider: 'qoder', alive: true }) }
      }
      return inheritedFetch(url, init)
    }))
    fireEvent.click(screen.getByTestId('multiagent-new-session'))
    await waitFor(() => {
      expect(launches).toHaveLength(1)
      expect(launches[0]).toMatchObject({ provider: 'qoder', visibility: 'visible' })
      expect(launches[0]).not.toHaveProperty('model')
      expect(launches[0]).not.toHaveProperty('effort')
    })
  })

  it('新建成功后记住执行者与模型，下次默认沿用上一次建的', async () => {
    await renderView(OPEN_TABS)
    await screen.findByText('Luna from server')

    fireEvent.click(screen.getByRole('combobox', { name: '新会话模型' }))
    fireEvent.click(screen.getByRole('option', { name: 'Terra from server' }))
    fireEvent.click(screen.getByRole('combobox', { name: '新会话推理强度' }))
    fireEvent.click(screen.getByRole('option', { name: 'xhigh' }))

    const inheritedFetch = globalThis.fetch
    let sessionListFetches = 0
    vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
      const target = String(url)
      if (target.includes('/api/cc/sessions?')) sessionListFetches += 1
      if (target.includes('/api/cc/agent-workers') && init?.method === 'POST') {
        return { ok: true, json: async () => ({ id: 'new-remembered', provider: 'codex', alive: true }) }
      }
      return inheritedFetch(url, init)
    }))

    const before = sessionListFetches
    fireEvent.click(screen.getByTestId('multiagent-new-session'))
    // 新建当场就去对账存活集合, 不再等 30s 那一轮(否则新会话要过好久才进列表)。
    await waitFor(() => expect(sessionListFetches).toBeGreaterThan(before))
    await waitFor(() => expect(readNewSessionPref()).toMatchObject({
      kind: 'provider:codex',
      model: { 'provider:codex': 'gpt-5.6-terra' },
      effort: { 'provider:codex:gpt-5.6-terra': 'xhigh' },
    }))

    cleanup()
    await renderView(OPEN_TABS)
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: '新会话模型' }).textContent).toContain('Terra from server')
      expect(screen.getByRole('combobox', { name: '新会话推理强度' }).textContent).toContain('xhigh')
    })
  })

  it('companion summary title overrides provider title and tab placeholder', async () => {
    await renderView(OPEN_TABS)
    expect((await screen.findAllByText('修终端闪烁')).length).toBeGreaterThan(0)
    expect(screen.queryByText('raw first prompt')).toBeNull()
    expect(screen.queryByText('Codex · alpha')).toBeNull()
  })

  it('摘要没有有效内容时不渲染信息不足的替代文案', async () => {
    await renderView(OPEN_TABS)
    await waitFor(() => {
      expect(screen.queryByTestId('multiagent-fold-toggle')).toBeTruthy()
    })
    expandFolded()
    const idleRow = screen.getAllByTestId('multiagent-row')
      .find((row) => row.dataset.sessionId === 'pty-idle')
    expect(idleRow?.textContent).not.toContain('暂无可解析动态')
    expect(idleRow?.textContent).not.toContain('信息不足')
  })

  it('点一行切到该会话页签', async () => {
    const { usePanels } = await renderView(OPEN_TABS)
    const rows = await screen.findAllByTestId('multiagent-row')
    const workRow = rows.find((row) => row.dataset.sessionId === 'pty-work')
    fireEvent.click(workRow?.querySelector('.ma-row-hit') as HTMLElement)
    expect(usePanels.getState().activeId).toBe('cc_session:pty-work')
  })

  it('从更多菜单终止会话后自动关闭对应窗口', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { usePanels, fetchMock } = await renderView(OPEN_TABS)
    const rows = await screen.findAllByTestId('multiagent-row')
    const workRow = rows.find((row) => row.dataset.sessionId === 'pty-work')

    fireEvent.click(workRow?.querySelector('[data-testid="active-process-more"]') as HTMLElement)
    fireEvent.click(screen.getByTestId('active-process-kill'))

    await waitFor(() => {
      expect(usePanels.getState().tabs.some((tab) => tab.id === 'cc_session:pty-work')).toBe(false)
    })
    expect(fetchMock.mock.calls.some(([url, init]) => (
      String(url).endsWith('/api/cc/sessions/pty-work')
      && (init as RequestInit | undefined)?.method === 'DELETE'
    ))).toBe(true)
  })

  it('没有活着的 assistant 会话时, 点 assistant 用 codex_the_company 新建并打开页签(隐藏于会话列表)', async () => {
    window.localStorage.clear()
    stubTabStates(PAYLOAD)
    const requests: Array<{ url: string; body: unknown }> = []
    const fetchMock = vi.fn(async (url: unknown, init?: unknown) => {
      const target = String(url)
      if (target.includes('/api/cc/agent-workers/catalog')) return { ok: true, json: async () => SESSION_CATALOG }
      if (target.includes('/api/cc/sessions?')) {
        return { ok: true, json: async () => ({ items: [], recoverable: [] }) }
      }
      if (target.includes('/api/cc/agent-workers')) {
        const body = JSON.parse(String((init as { body?: string } | undefined)?.body || '{}'))
        requests.push({ url: target, body })
        return {
          ok: true,
          json: async () => ({ id: 'assist-new', provider: 'codex', topic: 'assistant', alive: true, visibility: 'hidden', cwd: 'E:\\work' }),
        }
      }
      if (target.includes('/api/cc/tab-states')) return { ok: true, json: async () => PAYLOAD }
      if (target.includes('/api/boss-sight/session-placements')) {
        return { ok: true, json: async () => ({ placements: {}, count: 0 }) }
      }
      if (target.includes('/api/boss-sight/residents/') && target.includes('/tail')) {
        return { ok: true, json: async () => ({ lines: [] }) }
      }
      if (target.includes('/api/boss-sight/residents')) {
        return { ok: true, json: async () => ({ source: 'test', count: 0, now: Date.now() / 1000, residents: [] }) }
      }
      if (target.includes('/api/projects')) {
        return { ok: true, json: async () => ({ projects: [], groups_order: [], group_labels: {} }) }
      }
      throw new Error(`unexpected fetch: ${target}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { default: MultiagentView } = await import('./MultiagentView')
    const { usePanels } = await import('../../stores/panelsStore')
    usePanels.setState({ tabs: OPEN_TABS.map((tab) => ({
      id: tab.id,
      ref: { type: tab.id.startsWith('cc_session:') ? 'cc_session' : 'controller', id: tab.refId },
      title: tab.title,
    })), activeId: OPEN_TABS[0]?.id ?? null } as never)
    render(<MultiagentView />)
    await screen.findAllByTestId('multiagent-row')

    fireEvent.click(screen.getByTestId('multiagent-assistant'))

    await waitFor(() => {
      const worker = requests.find((req) => req.url.includes('/api/cc/agent-workers'))
      expect(worker).toBeTruthy()
      expect(worker?.body).toMatchObject({
        provider: 'codex_the_company', model: 'deepseek-v4-flash', effort: 'medium',
        topic: 'assistant', visibility: 'hidden',
      })
      expect(usePanels.getState().activeId).toBe('cc_session:assist-new')
    })
  })

  it('已有活着的 assistant 会话时直接聚焦, 不再新建', async () => {
    window.localStorage.clear()
    stubTabStates(PAYLOAD)
    const liveAssistant = {
      id: 'assist-live', provider: 'codex', topic: 'assistant', alive: true, status: 'alive', cwd: 'E:\\work',
    }
    const fetchMock = vi.fn(async (url: unknown) => {
      const target = String(url)
      if (target.includes('/api/cc/agent-workers/catalog')) return { ok: true, json: async () => SESSION_CATALOG }
      if (target.includes('/api/cc/sessions?')) return { ok: true, json: async () => ({ items: [liveAssistant], recoverable: [] }) }
      if (target.includes('/api/cc/tab-states')) return { ok: true, json: async () => PAYLOAD }
      if (target.includes('/api/boss-sight/session-placements')) {
        return { ok: true, json: async () => ({ placements: {}, count: 0 }) }
      }
      if (target.includes('/api/boss-sight/residents/') && target.includes('/tail')) {
        return { ok: true, json: async () => ({ lines: [] }) }
      }
      if (target.includes('/api/boss-sight/residents')) {
        return { ok: true, json: async () => ({ source: 'test', count: 0, now: Date.now() / 1000, residents: [] }) }
      }
      if (target.includes('/api/projects')) {
        return { ok: true, json: async () => ({ projects: [], groups_order: [], group_labels: {} }) }
      }
      throw new Error(`unexpected fetch: ${target}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { default: MultiagentView } = await import('./MultiagentView')
    const { usePanels } = await import('../../stores/panelsStore')
    usePanels.setState({ tabs: OPEN_TABS.map((tab) => ({
      id: tab.id,
      ref: { type: tab.id.startsWith('cc_session:') ? 'cc_session' : 'controller', id: tab.refId },
      title: tab.title,
    })), activeId: OPEN_TABS[0]?.id ?? null } as never)
    render(<MultiagentView />)
    await screen.findAllByTestId('multiagent-row')

    fireEvent.click(screen.getByTestId('multiagent-assistant'))

    await waitFor(() => expect(usePanels.getState().activeId).toBe('cc_session:assist-live'))
    expect(usePanels.getState().tabs.some((tab) => tab.ref.type === 'cc_session' && tab.ref.id === 'assist-live')).toBe(true)
  })

  it('后端未重启(topic 缺失)时, 靠本地记住的 id 也能复用同一个 assistant 会话', async () => {
    window.localStorage.setItem('omni.assistant.sessionId', 'assist-remembered')
    stubTabStates(PAYLOAD)
    const rememberedLive = {
      id: 'assist-remembered', provider: 'codex', alive: true, status: 'alive', cwd: 'E:\\work',
    }
    const fetchMock = vi.fn(async (url: unknown) => {
      const target = String(url)
      if (target.includes('/api/cc/agent-workers/catalog')) return { ok: true, json: async () => SESSION_CATALOG }
      if (target.includes('/api/cc/sessions?')) return { ok: true, json: async () => ({ items: [rememberedLive], recoverable: [] }) }
      if (target.includes('/api/cc/tab-states')) return { ok: true, json: async () => PAYLOAD }
      if (target.includes('/api/boss-sight/session-placements')) {
        return { ok: true, json: async () => ({ placements: {}, count: 0 }) }
      }
      if (target.includes('/api/boss-sight/residents/') && target.includes('/tail')) {
        return { ok: true, json: async () => ({ lines: [] }) }
      }
      if (target.includes('/api/boss-sight/residents')) {
        return { ok: true, json: async () => ({ source: 'test', count: 0, now: Date.now() / 1000, residents: [] }) }
      }
      if (target.includes('/api/projects')) {
        return { ok: true, json: async () => ({ projects: [], groups_order: [], group_labels: {} }) }
      }
      throw new Error(`unexpected fetch: ${target}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { default: MultiagentView } = await import('./MultiagentView')
    const { usePanels } = await import('../../stores/panelsStore')
    usePanels.setState({ tabs: OPEN_TABS.map((tab) => ({
      id: tab.id,
      ref: { type: tab.id.startsWith('cc_session:') ? 'cc_session' : 'controller', id: tab.refId },
      title: tab.title,
    })), activeId: OPEN_TABS[0]?.id ?? null } as never)
    render(<MultiagentView />)
    await screen.findAllByTestId('multiagent-row')

    fireEvent.click(screen.getByTestId('multiagent-assistant'))

    await waitFor(() => expect(usePanels.getState().activeId).toBe('cc_session:assist-remembered'))
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

describe('mergePendingRuntimeIds', () => {
  it('刚建的会话在存活接口还没报出它时也留在列表里', () => {
    const pending = new Map([['new-pty', 1_000]])
    const merged = mergePendingRuntimeIds(new Set(['old-pty']), pending, 2_000)
    expect([...merged].sort()).toEqual(['new-pty', 'old-pty'])
    expect(pending.has('new-pty')).toBe(true)
  })

  it('接口自己报出这个 id 之后就不再认领', () => {
    const pending = new Map([['new-pty', 1_000]])
    const merged = mergePendingRuntimeIds(new Set(['new-pty']), pending, 2_000)
    expect([...merged]).toEqual(['new-pty'])
    expect(pending.size).toBe(0)
  })

  it('认领超时后把存活判断交还给接口，不把死会话永远钉在列表上', () => {
    const pending = new Map([['dead-pty', 1_000]])
    const merged = mergePendingRuntimeIds(new Set(['old-pty']), pending, 1_000 + 60_001)
    expect([...merged]).toEqual(['old-pty'])
    expect(pending.size).toBe(0)
  })
})

describe('providerDisplayName', () => {
  it('maps provider ids to friendly names with a safe fallback', () => {
    expect(providerDisplayName('claude_code')).toBe('Claude')
    expect(providerDisplayName('codex')).toBe('Codex')
    expect(providerDisplayName('codex_the_company')).toBe('the_company API')
    expect(providerDisplayName('kiro')).toBe('Kiro')
    expect(providerDisplayName('controller')).toBe('总控')
    expect(providerDisplayName(undefined)).toBe('Agent')
  })

  it('主号不带后缀(会话标题复用这个名字), 只有第二个官方账号标出来', () => {
    expect(providerDisplayName('codex')).toBe('Codex')
    expect(providerDisplayName('codex_2')).toBe('Codex official 2')
  })

  it('has local icons for the supported branded CLIs', () => {
    for (const provider of ['codex', 'codex_2', 'claude_code', 'kiro', 'qoder', 'kimi', 'codebuddy', 'opencode']) {
      expect(providerIconSource(provider)).toMatch(/^\/assets\/cli\/.+\.svg$/)
    }
    expect(providerIconSource('codex_the_company')).toBe('/assets/cli/the_company-color.png')
    // 第二个账号仍然是 Codex, 用同一个标; 区分靠名字不靠图标。
    expect(providerIconSource('codex_2')).toBe(providerIconSource('codex'))
  })

  it('derives a compact readable plan name when the backend has no title', () => {
    expect(planDisplayName('dashboard/[2026-08-09]MULTIAGENT-CARDS')).toBe('MULTIAGENT CARDS')
  })
})

describe('summaryDisplayText', () => {
  it('keeps the previous or transcript fallback instead of displaying insufficient-information placeholders', () => {
    expect(summaryDisplayText('信息不足', '原来的有效摘要')).toBe('原来的有效摘要')
    expect(summaryDisplayText('信息不足 · 已完成回归测试')).toBe('已完成回归测试')
    expect(summaryDisplayText('资料不足，无法判断', '初始任务')).toBe('初始任务')
    expect(summaryDisplayText('证据不足')).toBe('')
    expect(summaryDisplayText('无可用信息 · 无可用信息')).toBe('')
  })
})

describe('sessionDisplayTitle', () => {
  it('uses only a declared topic, first message, or stable default', () => {
    expect(sessionDisplayTitle({
      provider: 'codex', sessionId: 'e965d53edab1411c', topic: '真实评测主题',
      nativeTitle: '首条消息', firstMessage: '另一条首条消息',
    })).toBe('真实评测主题')
    expect(sessionDisplayTitle({
      provider: 'codex', sessionId: 'e965d53edab1411c', firstMessage: '第一条用户消息',
    })).toBe('第一条用户消息')
    expect(sessionDisplayTitle({
      provider: 'codex', sessionId: 'e965d53edab1411c', firstMessage: '(无)',
    })).toBe('Codex · e965d53e')
  })

  it('never promotes a random resident name or digest summary into a title', () => {
    expect(sessionDisplayTitle({
      provider: 'codex', sessionId: 'e965d53edab1411c',
      topic: '信息不足', nativeTitle: '无可用信息', firstMessage: '(无)', preview: '暂无信息',
    })).toBe('Codex · e965d53e')
  })
})

describe('session lineage labels', () => {
  it('distinguishes AI-derived, human-derived, and AI-created sessions', () => {
    expect(sessionLineageBadge({ kind: 'derived', origin: 'ai', relation: 'subagent' })).toBe('AI 派生')
    expect(sessionLineageBadge({ kind: 'derived', origin: 'human', relation: 'fork' })).toBe('人工派生')
    expect(sessionLineageBadge({ kind: 'root', origin: 'unknown', relation: 'unknown' }, 'agent')).toBe('AI 创建')
    expect(sessionLineageBadge({ kind: 'root', origin: 'human', relation: 'created' }, 'human')).toBe('')
  })

  it('uses a parent title and falls back to a compact stable id', () => {
    expect(lineageParentLabel({
      kind: 'derived', origin: 'ai', relation: 'worker',
      parent_session_id: '019ff05012345678', parent_title: '主会话：列表优化',
    })).toBe('主会话：列表优化')
    expect(lineageParentLabel({
      kind: 'derived', origin: 'ai', relation: 'worker',
      parent_session_id: '019ff05012345678',
    })).toBe('019ff0501234')
  })
})

describe('residentFor', () => {
  it('prefers the exact provider session over an earlier record sharing the PTY', () => {
    const nested = {
      session_id: 'nested-child', pty_id: 'e965d53edab1411c', project: 'wrong-project',
    } as never
    const owner = {
      session_id: 'owner-session', pty_id: 'e965d53edab1411c', project: 'ai-learning-observatory',
    } as never

    expect(residentFor(
      'e965d53edab1411c',
      { kind: 'pty', providerSessionId: 'owner-session' },
      [nested, owner],
    )).toBe(owner)
  })
})

describe('canonicalResidentProject', () => {
  const projects = [{
    id: 'omnidashboard', name: 'Omni Dashboard 驾驶舱', short: '驾驶舱', group: 'core',
    roots: ['C:/workspace/omnicompany/src/omnicompany/dashboard'],
  }]

  it('never presents workspace folders or arbitrary digest text as projects', () => {
    expect(canonicalResidentProject({ project: 'WindowsWorkspace 工具' } as never, projects)).toBe('')
    expect(canonicalResidentProject({ project: 'Spark WebTwin' } as never, projects)).toBe('')
  })

  it('canonicalizes explicit catalog aliases and ignores cwd-only guesses', () => {
    expect(canonicalResidentProject({ project: 'omnicompany 驾驶舱' } as never, projects)).toBe('omnidashboard')
    expect(canonicalResidentProject({
      project: 'WindowsWorkspace',
      cwd: 'C:/workspace/omnicompany\\src\\omnicompany\\dashboard\\frontend',
    } as never, projects)).toBe('')
  })
})

describe('会话项目分组', () => {
  it('按 canonical project 聚合并把未绑定会话放进独立组', () => {
    const rows = groupSessionRows([
      { id: 'a', projectId: 'demogame-config' },
      { id: 'b', projectId: '' },
      { id: 'c', projectId: 'demogame-config' },
      { id: 'd', projectId: null },
    ])

    expect(rows.map((group) => [group.projectId, group.rows.map((row) => row.id)])).toEqual([
      ['demogame-config', ['a', 'c']],
      ['', ['b', 'd']],
    ])
  })

  it('优先显示注册项目名称，未绑定组使用稳定的中文标签', () => {
    const projects = [{ id: 'demogame-config', name: 'demogame 配置', short: '配置', group: 'core' }]
    expect(sessionProjectGroupLabel('demogame-config', projects)).toBe('demogame 配置')
    expect(sessionProjectGroupLabel('', projects)).toBe('未绑定项目')
  })

  it('没有显式绑定时不按会话目录猜项目', () => {
    const projects = [{
      id: 'litework', name: 'LiTeWork', group: 'product',
      roots: ['C:/workspace/LiWork/workspaces/litework-main'],
    }]
    expect(canonicalSessionProject({
      cwd: 'C:/workspace/LiWork/workspaces/litework-main/internal-task',
    }, undefined, projects)).toBe('')
  })
})
