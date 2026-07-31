import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CockpitShell from './CockpitShell'
import { CONTROLLER_TAB_ID, usePanels, withDefaultTabs } from '../stores/panelsStore'
import { reviewstageApi } from '../api/reviewstageClient'

vi.mock('./EditorArea', () => ({
  default: () => <div data-testid="mock-editor-area">Dockview work surface</div>,
}))

vi.mock('./BottomPanel', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="mock-bottom-panel">
      <button type="button" onClick={onClose}>close</button>
    </div>
  ),
}))

vi.mock('./useBossSightObservability', () => ({
  useBossSightObservability: vi.fn(),
}))

const briefing = {
  generated_at: '2026-06-03T00:00:00Z',
  severity: 'attention',
  headline: 'Pilot needs review',
  all_green: false,
  summary: {
    plans_total: 4,
    plans_active: 1,
    plans_done: 2,
    review_total: 3,
    review_pending: 2,
    mandatory_unaccepted: 1,
    pushed_unread: 1,
    subagents_total: 3,
    subagents_running: 1,
    subagents_blocked: 1,
  },
  review: {
    available: true,
    total: 3,
    by_status: { delivered: 1, todo_open: 1 },
    by_tier: { mandatory: 1 },
    mandatory_unaccepted: 1,
    pushed_unread: 1,
    recent: [{
      id: 'plan-review',
      title: 'Plan review material',
      kind: 'markdown',
      tier: 'important',
      status: 'pending',
      source_plan_id: 'v2-11',
      source_subagent_id: null,
      pushed_to_user: false,
      updated_at: '2026-06-03T00:00:00Z',
    }, {
      id: 'agent-review',
      title: 'Agent review material',
      kind: 'markdown',
      tier: 'mandatory',
      status: 'pending',
      source_plan_id: null,
      source_subagent_id: 'agent-a',
      pushed_to_user: false,
      updated_at: '2026-06-03T00:00:00Z',
    }],
  },
  plans: {
    total: 4,
    active: [{
      plan_id: 'v2-11',
      title: 'web cockpit endpoint',
      status: 'in_progress',
      open_ref: { type: 'plan', id: 'v2-11', facet: 'summary' },
    }],
  },
  subagents: {
    total: 3,
    running: [{ id: 'agent-a' }],
    blocked: [{ id: 'agent-b' }],
  },
  next_actions: [],
  secretary: {
    title: 'Needs attention',
    body: 'There is work pending.',
  },
}

const ctxSummary = {
  status: 'blocked',
  headline: '1 critical item requires attention',
  summary: {
    status: 'blocked',
    headline: '1 critical item requires attention',
    unresolved_count: 2,
    critical_count: 1,
    comment_unresolved_count: 1,
    comment_todo_done_count: 1,
    blocked_agent_count: 1,
    action_failed_count: 1,
    action_succeeded_count: 3,
  },
  unresolved: [{
    id: 'mandatory_material_unaccepted',
    title: 'Mandatory material is unaccepted',
    priority: 'critical',
    reason: 'mandatory_material_unaccepted',
    kind: 'review',
    open_ref: { type: 'material', id: 'attention-mat' },
  }],
  comment_feedback: {
    by_status: { delivered: 1, read: 1, todo_open: 1, todo_done: 1 },
    unresolved_count: 1,
    todo_done_count: 1,
    unresolved: [],
    recent_resolved: [],
  },
  action_history: {
    recent: [],
    failed_count: 1,
    succeeded_count: 3,
    last_failed: {
      id: 'action-1',
      kind: 'open_review',
      actor: 'controller',
      target: {},
      note: 'open review',
      status: 'failed',
      result: {},
      error: 'missing route',
      created_at: '2026-06-03T00:00:00Z',
    },
  },
  blocked_agents: [{ id: 'agent-b', status: 'blocked' }],
}

function mockBossSightFetch() {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/boss-sight/briefing') {
      return Promise.resolve(new Response(JSON.stringify(briefing), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    }
    if (url === '/api/boss-sight/workflow-summary') {
      return Promise.resolve(new Response(JSON.stringify({
        generated_at: '2026-06-03T00:00:00Z',
        status: 'blocked',
        headline: ctxSummary.headline,
        summary: ctxSummary.summary,
        unresolved: {
          count: 2,
          critical_count: 1,
          attention_count: 1,
          by_reason: { mandatory_material_unaccepted: 1 },
          by_kind: { review: 1 },
          items: ctxSummary.unresolved,
        },
        comment_feedback: { ...ctxSummary.comment_feedback, total: 4 },
        blocked_agents: ctxSummary.blocked_agents,
        action_history: { ...ctxSummary.action_history, count: 4, by_status: { failed: 1 }, by_kind: { open_review: 1 } },
        ctx_summary: ctxSummary,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    }
    if (url.startsWith('/api/boss-sight/material-registry')) {
      return Promise.resolve(new Response(JSON.stringify({
        generated_at: '2026-06-03T00:00:00Z',
        items: [{
          uri: 'omni://material/search-mat',
          id: 'search-mat',
          title: 'Boundary guard material',
          kind: 'guard',
          role: 'boundary',
          layer: 'context',
          status: 'active',
          display: 'Boundary guard material',
          source: 'test',
          snippet: 'search result',
          open_ref: { type: 'material', id: 'search-mat' },
          relations: [],
          tags: ['guard'],
        }],
        total: 1,
        returned: 1,
        counts: { by_kind: {}, by_role: {}, by_layer: {}, by_status: {} },
        filters: {},
        summary: { total: 1, counts: {}, highlighted_items: [], execution_boundaries: [], executors: [] },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    }
    if (url === '/api/projects') {
      // 侧栏项目面板 + 首页项目工作板共用的唯一权威注册表 (core/projects_registry)
      return Promise.resolve(new Response(JSON.stringify({
        projects: [], groups_order: ['demogame', 'omnicompany', 'indie-game', 'other'], group_labels: {},
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }
    if (url === '/api/boss-sight/captures') {
      // 2026-06-03: 提交 = 保存到文件(POST), 进场拉计数(GET)。不再建审阅材料。
      const method = String(init?.method || 'GET').toUpperCase()
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body || '{}'))
        return Promise.resolve(new Response(JSON.stringify({
          saved_path: `E:/ws/data/boss_sight/captures/pending/x_${body.capture_kind}.md`,
          pending_count: 1,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      }
      return Promise.resolve(new Response(JSON.stringify({ pending_count: 0, items: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
    }
    return Promise.resolve(new Response(JSON.stringify({ recorded: true, skipped: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  })
}

describe('CockpitShell', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: CONTROLLER_TAB_ID })
    window.localStorage.clear()
    delete (window as any).__OMNI_CODEX_DEBUG_HANDOFF__
    // R4: 壳挂载即订阅审阅 WS 流(urgent 角标 + 推送 toast); 单测里不真连。
    vi.spyOn(reviewstageApi, 'openStream').mockReturnValue(() => {})
    mockBossSightFetch()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the zero-topbar cockpit shell with everything tucked into the rail', async () => {
    render(<CockpitShell />)

    expect(screen.getByTestId('cockpit-shell')).toBeTruthy()
    // 零顶栏(2026-07-19 用户指令): 薄顶栏整体删除,全件收编进左 rail 槽位。
    expect(screen.queryByTestId('cockpit-topbar')).toBeNull()
    const rail = screen.getByTestId('cockpit-rail')
    expect(screen.getByTestId('mock-editor-area')).toBeTruthy()
    // rail 槽位: ⌘K 命令面板(顶槽) / 通知 bell / ⋯ 菜单(底槽) 都在 rail 内。
    expect(rail.contains(screen.getByTestId('cockpit-cmdk'))).toBe(true)
    expect(rail.contains(screen.getByTestId('cockpit-notifications-toggle'))).toBe(true)
    expect(rail.contains(screen.getByTestId('cockpit-more'))).toBe(true)
    expect(rail.textContent).not.toContain('最近')
    expect(rail.querySelector('[data-testid^="cockpit-rail-recent-"]')).toBeNull()

    // blocked 状态细点在 rail 底部(同 testid, 文案=状态标签)。
    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })
    // 右侧检视面板已删除(与控制台+通知重复); 工作流细节走通知铃 + 控制台。
  })

  // 网页审阅闭环: 整页快照与精准圈选并列，二者都在 ⋯ 菜单(全档同构)。
  it('keeps page snapshot and element selection adjacent inside the more menu', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    fireEvent.click(screen.getByTestId('cockpit-more'))
    expect(screen.getByTestId('cockpit-more-page-snapshot')).toBeTruthy()
    expect(screen.queryByTestId('cockpit-capture-moved-to-poof')).toBeNull()
    const elementComment = screen.getByTestId('cockpit-element-comment')
    expect(elementComment).toBeTruthy()
    fireEvent.click(elementComment)
    expect(screen.getByTestId('cockpit-capture-banner').textContent).toContain('点击要评论的元素')

    const target = screen.getByTestId('mock-editor-area')
    fireEvent.mouseMove(target)
    expect(screen.getByTestId('cockpit-capture-outline')).toBeTruthy()
    fireEvent.click(target)
    expect(screen.getByTestId('cockpit-capture-modal')).toBeTruthy()
    // 点击后进入评论态仍保留被选元素的硬描边，不再随 hover/captureMode 一起消失。
    expect(screen.getByTestId('cockpit-capture-selected-outline')).toBeTruthy()
    expect(screen.getByTestId('cockpit-capture-comment').classList.contains('omni-capture-textarea')).toBe(true)
  })

  it('does not mount a cockpit-global comments rail above the active tab content', async () => {
    const queueTab = { id: 'review_queue:main', ref: { type: 'review_queue' as const, id: 'main' }, title: 'Review' }
    usePanels.setState({ tabs: withDefaultTabs([queueTab]), activeId: queueTab.id })
    render(<CockpitShell />)

    expect(screen.queryByTestId('cockpit-comments-rail')).toBeNull()
    expect(screen.queryByTestId('cockpit-toggle-comments')).toBeNull()
  })

  it('keeps spine destinations wired to the work surface via the rail', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    // rail 目的地 = 切主区视图；Multiagent 是一等入口，不藏在总控菜单。
    fireEvent.click(screen.getByTestId('cockpit-nav-review'))
    expect(usePanels.getState().activeId).toBe('review_queue:main')

    fireEvent.click(screen.getByTestId('cockpit-nav-home'))
    expect(usePanels.getState().activeId).toBe('project_board:main')

    fireEvent.click(screen.getByTestId('cockpit-nav-multiagent'))
    expect(usePanels.getState().activeId).toBe('multiagent:main')

    // 暂存区不属于导航；上传走全窗口拖放/文件粘贴，记录入口下沉到设置。
    expect(screen.queryByTestId('cockpit-nav-files')).toBeNull()
    expect(screen.queryByTestId('cockpit-file-bridge')).toBeNull()

    fireEvent.click(screen.getByTestId('cockpit-nav-settings'))
    expect(usePanels.getState().activeId).toBe('settings:main')
  })

  // (已删除)"关联审阅材料"链接随右侧检视面板一并移除; 关联材料改由控制台/本对话材料卡呈现。
  // 壳层 A: 顶栏全局搜索输入框退役, 统一走 ⌘K 命令面板(kbar CommandPalette, App 侧挂载);
  // 壳内只留提示钮 → window 事件桥(PaletteToggleBridge 收听)。
  it('opens the command palette via the ⌘K hint button (window event bridge)', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    const seen: string[] = []
    const onToggle = () => seen.push('toggle')
    window.addEventListener('omni:toggle-command-palette', onToggle)
    try {
      fireEvent.click(screen.getByTestId('cockpit-cmdk'))
      expect(seen.length).toBe(1)
    } finally {
      window.removeEventListener('omni:toggle-command-palette', onToggle)
    }
    // 旧居中搜索输入框已退役。
    expect(screen.queryByTestId('cockpit-global-search')).toBeNull()
  })

  it('opens notification refs in the work surface', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    fireEvent.click(screen.getByTestId('cockpit-notifications-toggle'))
    expect(screen.getByTestId('cockpit-notification-panel')).toBeTruthy()
    fireEvent.click(screen.getByTestId('cockpit-notification-item-0'))
    expect(usePanels.getState().activeId).toBe('material:attention-mat')
    // (右侧检视面板的 cockpit-attention-item 已随面板删除; attention 仍可经通知铃进入。)
  })
})
