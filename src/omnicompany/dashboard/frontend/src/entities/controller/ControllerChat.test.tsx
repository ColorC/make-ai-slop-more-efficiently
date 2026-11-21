import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ccApi } from '../../api/ccClient'
import ControllerChat from './ControllerChat'

vi.mock('../cc_session/Editor', () => ({
  default: ({ entity }: { entity: { id: string } }) => <div data-testid="controller-cli-editor">{entity.id}</div>,
}))

describe('ControllerChat (assistant)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('does not create a worker merely because the surface mounted', () => {
    const create = vi.spyOn(ccApi, 'createAgentWorker')
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([])

    render(<ControllerChat />)

    expect(screen.getByTestId('controller-cli-idle')).toBeTruthy()
    expect(screen.getByText('assistant 未启动')).toBeTruthy()
    expect(create).not.toHaveBeenCalled()
    expect(list).not.toHaveBeenCalled()
  })

  it('starts the assistant (codex_the_company, hidden) only after an explicit click', async () => {
    window.localStorage.clear()
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([])
    const create = vi.spyOn(ccApi, 'createAgentWorker').mockResolvedValue({
      id: 'pty-assistant-manual',
      cmd: ['codex'],
      provider: 'codex',
      topic: 'assistant',
      cwd: 'C:/workspace/',
      cols: 120,
      rows: 30,
      started_at: 1786400000,
      alive: true,
    })

    render(<ControllerChat />)
    expect(create).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('controller-cli-start'))

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(1)
      expect(create).toHaveBeenCalledWith({
        provider: 'codex_the_company',
        topic: 'assistant',
        visibility: 'hidden',
      })
    })
    expect((await screen.findByTestId('controller-cli-editor')).textContent).toBe('pty-assistant-manual')
  })

  it('reuses the live assistant session instead of creating a second one', async () => {
    window.localStorage.clear()
    const live = {
      id: 'pty-assistant-live',
      cmd: ['codex'],
      provider: 'codex',
      topic: 'assistant',
      cwd: 'C:/workspace/',
      cols: 120,
      rows: 30,
      started_at: 1786400000,
      alive: true,
    }
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([live])
    const create = vi.spyOn(ccApi, 'createAgentWorker')

    render(<ControllerChat />)

    fireEvent.click(screen.getByTestId('controller-cli-start'))

    await waitFor(() => expect(screen.getByTestId('controller-cli-editor')).toBeTruthy())
    expect(screen.getByTestId('controller-cli-editor').textContent).toBe('pty-assistant-live')
    expect(create).not.toHaveBeenCalled()
  })

  it('reuses the remembered assistant id even before the backend reports the topic', async () => {
    window.localStorage.setItem('omni.assistant.sessionId', 'pty-assistant-remembered')
    const live = {
      id: 'pty-assistant-remembered',
      cmd: ['codex'],
      provider: 'codex',
      cwd: 'C:/workspace/',
      cols: 120,
      rows: 30,
      started_at: 1786400000,
      alive: true,
    }
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([live])
    const create = vi.spyOn(ccApi, 'createAgentWorker')

    render(<ControllerChat />)

    fireEvent.click(screen.getByTestId('controller-cli-start'))

    await waitFor(() => expect(screen.getByTestId('controller-cli-editor')).toBeTruthy())
    expect(screen.getByTestId('controller-cli-editor').textContent).toBe('pty-assistant-remembered')
    expect(create).not.toHaveBeenCalled()
  })
})
