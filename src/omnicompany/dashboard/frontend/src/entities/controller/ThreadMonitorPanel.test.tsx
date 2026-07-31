import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ccApi } from '../../api/ccClient'
import { ccChatApi } from '../../api/ccChatClient'
import { CONTROLLER_TAB_ID, usePanels, withDefaultTabs } from '../../stores/panelsStore'
import ThreadMonitorPanel from './ThreadMonitorPanel'

describe('ThreadMonitorPanel', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: CONTROLLER_TAB_ID })
    vi.restoreAllMocks()
  })

  it('opens a legacy web chat as a real resumed CLI tab', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([
      {
        id: 'chat-abc123',
        kind: 'chat',
        provider: 'codex',
        cwd: 'C:/workspace/omnicompany',
        cmd: [],
        cols: 0,
        rows: 0,
        started_at: 1780272000,
        alive: true,
        status: 'alive',
        claude_session_id: 'codex-native-thread',
        active_plan: 'dashboard/[2026-05-31]v2-09',
        model: 'gpt-5.4-codex',
        last_message: 'working on monitor',
      },
    ])
    vi.spyOn(ccApi, 'list').mockResolvedValue([
      {
        id: 'pty-1234567890',
        cmd: [],
        cwd: 'C:/workspace/omnicompany',
        cols: 120,
        rows: 30,
        started_at: 1780271000,
        alive: false,
        status: 'recoverable',
        active_plan: null,
      },
    ])
    const resumeProvider = vi.spyOn(ccApi, 'resumeProvider').mockResolvedValue({
      id: 'pty-resumed-chat',
      cmd: ['codex', 'resume', 'codex-native-thread'],
      provider: 'codex',
      provider_session_id: 'codex-native-thread',
      cwd: 'C:/workspace/omnicompany',
      cols: 120,
      rows: 30,
      started_at: 1780273000,
      alive: true,
    })

    render(<ThreadMonitorPanel />)

    await waitFor(() => {
      expect(screen.getAllByTestId('thread-monitor-row')).toHaveLength(2)
    })
    expect(screen.getAllByText(/Codex/).length).toBeGreaterThan(0)
    expect(screen.getByText('可续接')).toBeTruthy()

    fireEvent.click(screen.getByText('打开 CLI'))

    await waitFor(() => {
      expect(resumeProvider).toHaveBeenCalledWith({
        provider: 'codex',
        provider_session_id: 'codex-native-thread',
        cwd: 'C:/workspace/omnicompany',
      })
    })
    const tabs = usePanels.getState().tabs
    expect(tabs.some((t) => t.id === 'cc_session:pty-resumed-chat')).toBe(true)
    expect(usePanels.getState().activeId).toBe('cc_session:pty-resumed-chat')
  })

  it('creates and opens a remote Codex CLI session inside the dashboard', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([])
    const create = vi.spyOn(ccApi, 'create').mockResolvedValue({
      id: 'pty-codex-1', cmd: ['codex'], cwd: 'C:/workspace/omnicompany',
      cols: 120, rows: 30, started_at: 1780272000, alive: true,
    })

    render(<ThreadMonitorPanel />)
    await waitFor(() => expect(screen.getByTestId('thread-new-session')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('选择执行者'), { target: { value: 'codex_cli' } })
    fireEvent.click(screen.getByTestId('thread-new-session'))

    await waitFor(() => expect(create).toHaveBeenCalledWith({ cmd: ['codex'] }))
    expect(usePanels.getState().activeId).toBe('cc_session:pty-codex-1')
  })

  it('creates a plain CLI shell that waits for a manually entered agent command', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([])
    const create = vi.spyOn(ccApi, 'create').mockResolvedValue({
      id: 'pty-shell-1', cmd: ['powershell', '-NoLogo'], provider: 'shell',
      cwd: 'C:/workspace/omnicompany', cols: 120, rows: 30,
      started_at: 1780272000, alive: true,
    })

    render(<ThreadMonitorPanel />)
    fireEvent.change(await screen.findByLabelText('选择执行者'), { target: { value: 'plain_cli' } })
    fireEvent.click(screen.getByTestId('thread-new-session'))

    await waitFor(() => expect(create).toHaveBeenCalledWith({ cmd: ['powershell', '-NoLogo'] }))
    expect(usePanels.getState().activeId).toBe('cc_session:pty-shell-1')
  })

  it('opens only detached live CLI processes and never resumes historical records', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([
      {
        id: 'pty-live-a', cmd: ['codex'], provider: 'codex', cwd: 'E:/work/a', cols: 120, rows: 30,
        started_at: 100, alive: true, subscribers: 0, status: 'alive',
      },
      {
        id: 'pty-live-b', cmd: ['kimi'], provider: 'kimi', cwd: 'E:/work/b', cols: 120, rows: 30,
        started_at: 90, alive: true, subscribers: 0, status: 'alive',
      },
      {
        id: 'pty-old-history', cmd: ['claude'], provider: 'claude_code', cwd: 'E:/work/old', cols: 120, rows: 30,
        started_at: 80, alive: false, status: 'recoverable',
      },
      {
        id: 'pty-plain-shell', cmd: ['powershell', '-NoLogo'], provider: 'shell', cwd: 'E:/work/shell', cols: 120, rows: 30,
        started_at: 70, alive: true, subscribers: 0, status: 'alive',
      },
    ])
    const resume = vi.spyOn(ccApi, 'resume')
    usePanels.getState().openTabBackground({ type: 'cc_session', id: 'pty-live-b' }, '已打开 CLI')

    render(<ThreadMonitorPanel />)
    const button = await screen.findByTestId('thread-restore-all')
    expect(button.textContent).toContain('打开后台 CLI 1')
    fireEvent.click(button)

    await waitFor(() => {
      const ids = usePanels.getState().tabs.map((tab) => tab.id)
      expect(ids).toContain('cc_session:pty-live-a')
      expect(ids).toContain('cc_session:pty-live-b')
      expect(ids).not.toContain('cc_session:pty-old-history')
      expect(ids).not.toContain('cc_session:pty-plain-shell')
    })
    expect(resume).not.toHaveBeenCalled()
  })

  it('surfaces contentful remote OpenCode sessions and opens both on a fresh device', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([
      {
        id: '5da8c392-newer', cmd: ['opencode'], provider: 'opencode', cwd: 'C:/workspace/',
        cols: 120, rows: 30, started_at: 200, last_output_at: 240, alive: true,
        has_user_turn: true, subscribers: 0, status: 'alive',
      },
      {
        id: '607b1d85-older', cmd: ['opencode'], provider: 'opencode', cwd: 'C:/workspace/',
        cols: 120, rows: 30, started_at: 100, last_output_at: 180, alive: true,
        has_user_turn: true, subscribers: 1, status: 'alive',
      },
      {
        id: '0340ee96-empty', cmd: ['opencode'], provider: 'opencode', cwd: 'C:/workspace/',
        cols: 120, rows: 30, started_at: 50, alive: true,
        has_user_turn: false, subscribers: 0, status: 'alive',
      },
    ])

    render(<ThreadMonitorPanel />)

    expect(await screen.findByTestId('remote-opencode-sessions')).toBeTruthy()
    expect(screen.getAllByTestId('remote-opencode-row')).toHaveLength(2)
    const openBoth = screen.getByTestId('thread-open-remote-opencode')
    expect(openBoth.textContent).toContain('打开远程 OpenCode 2')
    fireEvent.click(openBoth)

    await waitFor(() => {
      const state = usePanels.getState()
      const ids = state.tabs.map((tab) => tab.id)
      expect(ids).toContain('cc_session:5da8c392-newer')
      expect(ids).toContain('cc_session:607b1d85-older')
      expect(ids).not.toContain('cc_session:0340ee96-empty')
      expect(state.activeId).toBe('cc_session:5da8c392-newer')
      const titles = state.tabs.map((tab) => tab.title)
      expect(titles).toContain('OpenCode · WindowsWorkspace · 5da8c392')
      expect(titles).toContain('OpenCode · WindowsWorkspace · 607b1d85')
    })
  })

  it('cleans up only confirmed plain background CLIs and leaves agent sessions alive', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([
      {
        id: 'pty-shell-a', cmd: ['powershell', '-NoLogo'], provider: 'shell', cwd: 'E:/work/a',
        cols: 120, rows: 30, started_at: 100, alive: true, subscribers: 0, status: 'alive',
      },
      {
        id: 'pty-shell-b', cmd: ['powershell', '-NoLogo'], provider: 'shell', cwd: 'E:/work/b',
        cols: 120, rows: 30, started_at: 90, alive: true, subscribers: 0, status: 'alive',
      },
      {
        id: 'pty-codex', cmd: ['codex'], provider: 'codex', cwd: 'E:/work/codex',
        cols: 120, rows: 30, started_at: 80, alive: true, subscribers: 0, status: 'alive',
      },
    ])
    const kill = vi.spyOn(ccApi, 'kill').mockResolvedValue()

    render(<ThreadMonitorPanel />)
    fireEvent.click(await screen.findByTestId('thread-actions'))
    fireEvent.click(await screen.findByTestId('thread-cleanup-plain-cli'))
    expect(await screen.findByTestId('cleanup-plain-cli-modal')).toBeTruthy()
    expect(kill).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('cleanup-plain-cli-confirm'))

    await waitFor(() => {
      expect(kill).toHaveBeenCalledTimes(2)
    })
    expect(kill).toHaveBeenCalledWith('pty-shell-a')
    expect(kill).toHaveBeenCalledWith('pty-shell-b')
    expect(kill).not.toHaveBeenCalledWith('pty-codex')
  })

  it('highlights a detached live CLI and shows full recent messages on hover', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([{
      id: 'pty-live-codex',
      cmd: ['codex'],
      cwd: 'C:/workspace/omnicompany',
      cols: 120,
      rows: 30,
      started_at: 1780272000,
      alive: true,
      working: true,
      subscribers: 0,
      status: 'alive',
      claude_session_id: 'codex-transcript-1',
    }])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([{
      provider: 'codex',
      session_id: 'codex-transcript-1',
      cwd: 'C:/workspace/omnicompany',
      mtime: Date.now() / 1000,
      preview: '修复页签状态',
      file: 'session.jsonl',
      status: 'working',
      last_did: '已经完成最后一轮验证并保留完整回复。',
      recent_messages: [
        { role: 'user', text: '请继续处理这个很长的任务描述，不要裁掉。' },
        { role: 'assistant', text: '已经完成最后一轮验证并保留完整回复。' },
      ],
    }])

    render(<ThreadMonitorPanel />)
    const card = await screen.findByTestId('active-session-row')
    expect(card.getAttribute('data-runtime')).toBe('detached')
    expect(screen.getByText('后台保活')).toBeTruthy()

    fireEvent.mouseEnter(card)
    const popup = await screen.findByTestId('session-quick-preview')
    expect(popup.textContent).toContain('请继续处理这个很长的任务描述，不要裁掉。')
    expect(popup.textContent).toContain('最后回复')
    expect(popup.textContent).toContain('已经完成最后一轮验证并保留完整回复。')
  })

  it('opens an unmanaged transcript by starting its provider CLI with resume', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([{
      provider: 'claude_code',
      session_id: 'external-claude-session',
      cwd: 'C:/workspace/',
      mtime: Date.now() / 1000,
      preview: 'resume existing session',
      file: 'external.jsonl',
      status: 'idle',
    }])
    const resumeProvider = vi.spyOn(ccApi, 'resumeProvider').mockResolvedValue({
      id: 'pty-resumed-session',
      cmd: ['claude', '--resume', 'external-claude-session'],
      provider: 'claude_code',
      provider_session_id: 'external-claude-session',
      cwd: 'C:/workspace/',
      cols: 120,
      rows: 30,
      started_at: Date.now() / 1000,
      alive: true,
    })

    render(<ThreadMonitorPanel />)
    fireEvent.click(await screen.findByTestId('active-session-open'))

    await waitFor(() => {
      expect(resumeProvider).toHaveBeenCalledWith({
        provider_session_id: 'external-claude-session',
        provider: 'claude_code',
        cwd: 'C:/workspace/',
      })
      expect(usePanels.getState().tabs.map((tab) => tab.id)).toContain('cc_session:pty-resumed-session')
      expect(usePanels.getState().activeId).toBe('cc_session:pty-resumed-session')
    })
  })

  it('marks a working session not owned by the dashboard as running elsewhere', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([{
      provider: 'claude_code', session_id: 'external-1', cwd: 'E:/elsewhere',
      mtime: Date.now() / 1000, preview: 'external task', file: 'external.jsonl', status: 'working',
    }])

    render(<ThreadMonitorPanel />)
    expect(await screen.findByText('其他软件运行')).toBeTruthy()
  })

  it('searches historical native sessions and resumes the match as CLI', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'importable').mockResolvedValue([{
      provider: 'codex',
      session_id: '019f-search-native-session',
      cwd: 'C:/workspace/search-target',
      mtime: Date.now() / 1000 - 1000,
      preview: 'unique historical needle',
      file: 'historical.jsonl',
      status: 'idle',
    }])
    const resumeProvider = vi.spyOn(ccApi, 'resumeProvider').mockResolvedValue({
      id: 'pty-search-result',
      cmd: ['codex', 'resume', '019f-search-native-session'],
      provider: 'codex',
      provider_session_id: '019f-search-native-session',
      cwd: 'C:/workspace/search-target',
      cols: 120,
      rows: 30,
      started_at: Date.now() / 1000,
      alive: true,
    })

    render(<ThreadMonitorPanel />)
    const search = await screen.findByTestId('thread-session-search')
    fireEvent.change(search, { target: { value: 'historical needle' } })

    expect(await screen.findByTestId('historical-session-row')).toBeTruthy()
    fireEvent.click(screen.getByTestId('historical-session-open'))
    await waitFor(() => {
      expect(resumeProvider).toHaveBeenCalledWith({
        provider_session_id: '019f-search-native-session',
        provider: 'codex',
        cwd: 'C:/workspace/search-target',
      })
      expect(usePanels.getState().activeId).toBe('cc_session:pty-search-result')
    })
  })

  it('renders sessions in bounded batches until the user asks for more', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'importable').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue(Array.from({ length: 50 }, (_, index) => ({
      provider: 'codex' as const,
      session_id: `native-${index}`,
      cwd: 'C:/workspace/',
      mtime: Date.now() / 1000 - index,
      preview: `session ${index}`,
      file: `session-${index}.jsonl`,
      status: 'idle' as const,
    })))

    render(<ThreadMonitorPanel />)
    expect(await screen.findAllByTestId('active-session-row')).toHaveLength(36)
    fireEvent.click(screen.getByTestId('thread-session-load-more'))
    await waitFor(() => expect(screen.getAllByTestId('active-session-row')).toHaveLength(50))
  })

  it('keeps a readable title and the full session id permanently visible on every card', async () => {
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([{
      provider: 'codex',
      session_id: '019f9dee-b673-7212-b948-3baa72c242f2',
      cwd: 'C:/workspace/',
      mtime: Date.now() / 1000,
      preview: 'preview fallback',
      file: 'active.jsonl',
      status: 'working',
      digest: { title: 'Mobile session title\nthis line must not replace the title' },
    }])
    vi.spyOn(ccApi, 'list').mockResolvedValue([
      {
        id: 'pty-title-123456', cmd: ['kimi'], provider: 'kimi', cwd: 'E:/work/alpha', cols: 120, rows: 30,
        started_at: Date.now() / 1000, alive: true, status: 'alive', provider_title: 'First title line\nsecond line',
      },
      {
        id: 'pty-fallback-7890', cmd: ['codex'], provider: 'codex', cwd: 'E:/work/beta', cols: 120, rows: 30,
        started_at: Date.now() / 1000 - 10, alive: true, status: 'alive', provider_title: null, active_plan: null,
      },
    ])

    render(<ThreadMonitorPanel />)

    const titles = await screen.findAllByTestId('session-title')
    const ids = await screen.findAllByTestId('session-id')
    expect(titles.map((node) => node.textContent)).toEqual(expect.arrayContaining([
      'Mobile session title',
      'First title line',
      'Codex \u00b7 beta',
    ]))
    expect(ids.map((node) => node.textContent)).toEqual(expect.arrayContaining([
      'ID019f9dee-b673-7212-b948-3baa72c242f2',
      'IDpty-title-123456',
      'IDpty-fallback-7890',
    ]))
    for (const id of ids) {
      expect(id.classList.contains('tm-session-id')).toBe(true)
      expect(id.getAttribute('title')).toBeTruthy()
    }
  })

})
