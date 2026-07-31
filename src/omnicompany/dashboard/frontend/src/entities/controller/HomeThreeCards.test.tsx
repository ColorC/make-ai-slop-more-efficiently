import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ccApi } from '../../api/ccClient'
import { ccChatApi } from '../../api/ccChatClient'
import { projectsApi } from '../../api/projectsClient'
import { reviewstageApi } from '../../api/reviewstageClient'
import { CONTROLLER_TAB_ID, usePanels, withDefaultTabs } from '../../stores/panelsStore'
import HomeThreeCards from './HomeThreeCards'

describe('HomeThreeCards CLI-only conversation semantics', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: CONTROLLER_TAB_ID })
    vi.restoreAllMocks()
    vi.spyOn(ccChatApi, 'list').mockResolvedValue([])
    vi.spyOn(ccChatApi, 'activeSessions').mockResolvedValue([])
    vi.spyOn(projectsApi, 'list').mockResolvedValue({ projects: [] } as any)
    vi.spyOn(reviewstageApi, 'list').mockResolvedValue({ items: [] } as any)
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ plans: [], items: [] }),
    })))
  })

  it('opens a recent conversation by resuming it in a real CLI', async () => {
    vi.mocked(ccChatApi.activeSessions).mockResolvedValue([
      {
        provider: 'codex',
        session_id: 'native-codex-session',
        cwd: '/path/to/workspace',
        mtime: 1785463000,
        status: 'done',
        digest: { title: '历史 Codex 会话' },
      },
    ] as any)
    const resumeProvider = vi.spyOn(ccApi, 'resumeProvider').mockResolvedValue({
      id: 'pty-resumed-home',
      cmd: ['codex', 'resume', 'native-codex-session'],
      provider: 'codex',
      provider_session_id: 'native-codex-session',
      cwd: '/path/to/workspace',
      cols: 120,
      rows: 30,
      started_at: 1785463100,
      alive: true,
    })

    render(<HomeThreeCards />)
    fireEvent.click(await screen.findByTestId('home-filter-conv'))
    fireEvent.click(await screen.findByText('历史 Codex 会话'))

    await waitFor(() => {
      expect(resumeProvider).toHaveBeenCalledWith({
        provider: 'codex',
        provider_session_id: 'native-codex-session',
        cwd: '/path/to/workspace',
      })
    })
    expect(usePanels.getState().activeId).toBe('cc_session:pty-resumed-home')
  })

  it('creates a PTY-backed CLI instead of a web chat', async () => {
    const create = vi.spyOn(ccApi, 'create').mockResolvedValue({
      id: 'pty-home-new',
      cmd: ['claude'],
      provider: 'claude_code',
      cwd: '/path/to/workspace',
      cols: 120,
      rows: 30,
      started_at: 1785463200,
      alive: true,
    })
    const createWebChat = vi.spyOn(ccChatApi, 'create')

    render(<HomeThreeCards />)
    fireEvent.click(await screen.findByTestId('home-new-session'))

    await waitFor(() => expect(create).toHaveBeenCalledWith({}))
    expect(createWebChat).not.toHaveBeenCalled()
    expect(usePanels.getState().activeId).toBe('cc_session:pty-home-new')
  })
})
