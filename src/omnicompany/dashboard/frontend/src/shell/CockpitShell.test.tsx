import React from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CockpitShell from './CockpitShell'
import { CONTROLLER_TAB_ID, saveTabSnapshot, usePanels, withDefaultTabs } from '../stores/panelsStore'
import { reviewstageApi } from '../api/reviewstageClient'
import { clearConsoleLogRing } from './consoleLogRing'

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

// P1.2: 剪贴板统一走 mock(快照 URL 化/日志复制全部的断言入口)。
const copyTextMock = vi.hoisted(() => vi.fn(async () => true))
vi.mock('../lib/copyText', () => ({
  copyText: (text: string) => copyTextMock(text),
}))

// P1.2: 记录 POST /api/boss-sight/captures 的请求体, 断言 logs/url/圈选联动快照。
const captureRequests: Array<Record<string, any>> = []

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
        captureRequests.push(body)
        return Promise.resolve(new Response(JSON.stringify({
          saved_path: `E:/ws/data/boss_sight/captures/pending/x_${body.capture_kind}.md`,
          url: `http://127.0.0.1:8210/api/boss-sight/captures/file?path=pending/x_${body.capture_kind}.md`,
          share_id: `${body.capture_kind === 'page_snapshot' ? 'pg' : 'ui'}-capture-test01`,
          canonical_url: `http://192.0.2.10:8210/s/${body.capture_kind === 'page_snapshot' ? 'pg' : 'ui'}-capture-test01`,
          share_status: 'ready',
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
    window.sessionStorage.clear()
    window.history.replaceState(null, '', '/')
    delete window.__OMNICOMPANY_HOST__
    delete (window as any).__OMNI_CODEX_DEBUG_HANDOFF__
    // R4: 壳挂载即订阅审阅 WS 流(urgent 角标 + 推送 toast); 单测里不真连。
    vi.spyOn(reviewstageApi, 'openStream').mockReturnValue(() => {})
    mockBossSightFetch()
    // P1.2: 每个测试从干净的日志环形缓冲/捕获请求记录/剪贴板 mock 开始。
    clearConsoleLogRing()
    captureRequests.length = 0
    copyTextMock.mockClear()
  })

  afterEach(() => {
    delete window.__OMNICOMPANY_HOST__
    vi.restoreAllMocks()
  })


  it('starts a detached deep link with only the requested tab', async () => {
    const inherited = { id: 'note:from-opener', ref: { type: 'note' as const, id: 'from-opener' }, title: 'Inherited opener tab' }
    saveTabSnapshot(withDefaultTabs([inherited]), inherited.id)
    window.history.replaceState(
      null,
      '',
      '/?open_type=cc_session&open_id=pty-detached&open_title=Detached+CLI&omni_detached=1',
    )

    render(<CockpitShell />)

    await waitFor(() => {
      expect(usePanels.getState().tabs).toHaveLength(1)
      expect(usePanels.getState().tabs[0]).toMatchObject({
        id: 'cc_session:pty-detached',
        ref: { type: 'cc_session', id: 'pty-detached' },
        title: 'Detached CLI',
      })
      expect(usePanels.getState().tabs[0].returnTo).toBeUndefined()
      expect(usePanels.getState().activeId).toBe('cc_session:pty-detached')
    })
    expect(usePanels.getState().tabs.some((tab) => tab.id === inherited.id)).toBe(false)
  })

  it('renders the cockpit shell without a legacy top bar (快照/圈选入口在 ⋯ 菜单)', async () => {
    render(<CockpitShell />)

    expect(screen.getByTestId('cockpit-shell')).toBeTruthy()
    // 2026-08-16 用户裁决: dashboard 不加薄顶栏, 圈选/整页快照的既有位置(⋯ 菜单)不动。
    expect(screen.queryByTestId('cockpit-topbar')).toBeNull()
    expect(screen.queryByTestId('cockpit-thintop')).toBeNull()
    const rail = screen.getByTestId('cockpit-rail')
    expect(screen.getByTestId('mock-editor-area')).toBeTruthy()
    // rail 槽位: ⌘K 命令面板(顶槽) / 通知 bell / ⋯ 菜单(底槽) 都在 rail 内。
    expect(rail.contains(screen.getByTestId('cockpit-cmdk'))).toBe(true)
    expect(rail.contains(screen.getByTestId('cockpit-notifications-toggle'))).toBe(true)
    expect(rail.contains(screen.getByTestId('cockpit-more'))).toBe(true)
    expect(rail.textContent).not.toContain('最近')
    expect(rail.querySelector('[data-testid^="cockpit-rail-recent-"]')).toBeNull()

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
    expect(screen.getByTestId('cockpit-more-close-other-tabs')).toBeTruthy()
    expect(screen.getByTestId('cockpit-more-page-snapshot')).toBeTruthy()
    expect(screen.queryByTestId('cockpit-capture-moved-to-canvas')).toBeNull()
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

  // ── P1.2 薄顶栏 + 快照/圈选统一 ────────────────────────────────────────────

  it('sends logs with page snapshot and surfaces the returned URL in toast + clipboard', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })
    act(() => { window.dispatchEvent(new ErrorEvent('error', { message: 'snap-err-marker' })) })

    fireEvent.click(screen.getByTestId('cockpit-more'))
    fireEvent.click(screen.getByTestId('cockpit-more-page-snapshot'))
    fireEvent.change(screen.getByTestId('cockpit-capture-comment'), { target: { value: '备份这一版' } })
    fireEvent.click(screen.getByTestId('cockpit-capture-submit'))

    await waitFor(() => expect(captureRequests.length).toBe(1))
    const body = captureRequests[0]
    expect(body.capture_kind).toBe('page_snapshot')
    expect(Array.isArray(body.logs)).toBe(true)
    expect(body.logs.some((entry: any) => String(entry.text).includes('snap-err-marker'))).toBe(true)

    // URL 化: 提示条与剪贴板给中心确认过的 canonical URL, 不再是本地路径或 loopback。
    const url = 'http://192.0.2.10:8210/s/pg-capture-test01'
    await waitFor(() => {
      expect(screen.getByTestId('cockpit-capture-toast').textContent).toContain(url)
    })
    await waitFor(() => expect(copyTextMock).toHaveBeenCalledWith(url))
    expect(copyTextMock.mock.calls.some((call) => String(call[0]).startsWith('E:/ws/'))).toBe(false)
  })

  it('copies the snapshot URL instead of a local path from the copy action', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    fireEvent.click(screen.getByTestId('cockpit-more'))
    fireEvent.click(screen.getByTestId('cockpit-more-page-snapshot'))
    fireEvent.click(screen.getByTestId('cockpit-capture-copy'))

    await waitFor(() => expect(copyTextMock).toHaveBeenCalled())
    expect(String(copyTextMock.mock.calls[0][0])).toBe('http://192.0.2.10:8210/s/pg-capture-test01')
    await waitFor(() => {
      expect(screen.getByTestId('cockpit-capture-toast').textContent).toContain('已复制分享链接')
    })
  })

  it('silently attaches a page snapshot after submitting an element comment, linked by URL', async () => {
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    fireEvent.click(screen.getByTestId('cockpit-more'))
    fireEvent.click(screen.getByTestId('cockpit-element-comment'))
    const target = screen.getByTestId('mock-editor-area')
    fireEvent.mouseMove(target)
    fireEvent.click(target)
    fireEvent.change(screen.getByTestId('cockpit-capture-comment'), { target: { value: '这个按钮太挤' } })
    fireEvent.click(screen.getByTestId('cockpit-capture-submit'))

    await waitFor(() => expect(captureRequests.length).toBe(2))
    const [comment, snap] = captureRequests
    expect(comment.capture_kind).toBe('element_comment')
    expect(snap.capture_kind).toBe('page_snapshot')
    expect(snap.title).toBe('页面快照(圈选联动)')
    expect(snap.enqueue).toBe(false)
    // 两者以中心 canonical URL 互相关联，不再传 loopback 临时文件 URL。
    expect(snap.comment).toContain('http://192.0.2.10:8210/s/ui-capture-test01')
    expect(snap.comment).toContain('自动附带 · 关联圈选评论')
    // 快照同样附带日志环形缓冲。
    expect(Array.isArray(snap.logs)).toBe(true)
    // 静默: 不弹第二个对话框, 提示条追加自动快照的 URL。
    expect(screen.queryByTestId('cockpit-capture-modal')).toBeNull()
    await waitFor(() => {
      expect(screen.getByTestId('cockpit-capture-toast').textContent).toContain('已自动附带快照')
      expect(screen.getByTestId('cockpit-capture-toast').textContent).toContain('http://192.0.2.10:8210/s/pg-capture-test01')
    })
  })

  it('closes every other tab from the more menu and keeps the active one', async () => {
    const keep = { id: 'cc_session:keep', ref: { type: 'cc_session' as const, id: 'keep' }, title: 'keep' }
    const extra = { id: 'review_material:extra', ref: { type: 'review_material' as const, id: 'extra' }, title: 'extra' }
    usePanels.setState({ tabs: [keep, extra], activeId: keep.id })
    render(<CockpitShell />)

    await waitFor(() => {
      expect(screen.getByTestId('cockpit-status').textContent).toContain('blocked')
    })

    fireEvent.click(screen.getByTestId('cockpit-more'))
    fireEvent.click(screen.getByTestId('cockpit-more-close-other-tabs'))

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual([keep.id])
    expect(usePanels.getState().activeId).toBe(keep.id)
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

  it('adds non-overlay window fullscreen controls only in desktop host mode', async () => {
    const windowState = vi.fn().mockResolvedValue({
      fullscreen: false,
      maximized: false,
      titlebarHeight: 38,
      fullscreenGuardHotEdge: 8,
    })
    const toggleFullscreen = vi.fn().mockResolvedValue(true)
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      windowState,
      toggleFullscreen,
    }

    render(<CockpitShell />)

    const control = await screen.findByTestId('cockpit-desktop-fullscreen')
    await waitFor(() => expect(windowState).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('cockpit-rail').contains(control)).toBe(true)
    expect(control.getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByTestId('cockpit-shell').getAttribute('data-omnicompany-host')).toBe('desktop')

    fireEvent.click(control)
    await waitFor(() => expect(toggleFullscreen).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(control.getAttribute('aria-pressed')).toBe('true'))

    fireEvent.click(screen.getByTestId('cockpit-more'))
    expect(screen.getByTestId('cockpit-more-desktop-fullscreen').textContent).toContain('退出窗口全屏')
  })
})
