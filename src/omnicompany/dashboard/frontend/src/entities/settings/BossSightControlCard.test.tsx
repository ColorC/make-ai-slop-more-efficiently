import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { bossSightApi, type BossSightControlResponse } from '../../api/bossSightClient'
import BossSightControlCard from './BossSightControlCard'

const controlResponse: BossSightControlResponse = {
  count: 4,
  items: [
    {
      key: 'controller.auto_wake',
      label: 'Controller auto wake',
      description: '',
      value: true,
      updated_by: 'system',
      updated_at: '2026-05-31T00:00:00Z',
      reason: 'default',
      history: [],
    },
    {
      key: 'reviewstage.push_to_user',
      label: 'Review push',
      description: '',
      value: true,
      updated_by: 'system',
      updated_at: '2026-05-31T00:00:00Z',
      reason: 'default',
      history: [],
    },
    {
      key: 'spawn.hard_block',
      label: 'Hard block',
      description: '',
      value: true,
      updated_by: 'system',
      updated_at: '2026-05-31T00:00:00Z',
      reason: 'default',
      history: [],
    },
    {
      key: 'observability.enabled',
      label: 'Observability',
      description: '',
      value: true,
      updated_by: 'system',
      updated_at: '2026-05-31T00:00:00Z',
      reason: 'default',
      history: [],
    },
  ],
  by_key: {},
}
controlResponse.by_key = Object.fromEntries(controlResponse.items.map((item) => [item.key, item]))

const settings = {
  dimensions: { click: true, selection: true, toggle_change: true, view_dwell: true },
  updated_by: 'system',
  updated_at: '2026-05-31T00:00:00Z',
  reason: 'default',
  history: [],
}

describe('BossSightControlCard', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders controls and updates switches, dimensions, and user prefs', async () => {
    vi.spyOn(bossSightApi, 'getControl').mockResolvedValue(controlResponse)
    vi.spyOn(bossSightApi, 'getObservabilitySettings').mockResolvedValue(settings)
    vi.spyOn(bossSightApi, 'getUserPrefs').mockResolvedValue({ version: 1, permanent_allow: [] })
    vi.spyOn(bossSightApi, 'recentObservations').mockResolvedValue({
      count: 1,
      items: [{
        id: 'obs_1',
        dimension: 'click',
        surface: 'settings',
        target: 'control:controller.auto_wake',
        value: null,
        meta: {},
        actor: 'human',
        recorded_at: '2026-05-31T00:00:00Z',
      }],
    })
    const setControl = vi.spyOn(bossSightApi, 'setControl').mockResolvedValue({
      ...controlResponse.by_key['controller.auto_wake'],
      value: false,
      updated_by: 'human',
      history: [{ id: 'ctrl_1', actor: 'human', updated_at: '2026-05-31T00:00:01Z', reason: 'settings panel toggle', previous: true, next: false }],
    })
    const setObservabilitySettings = vi.spyOn(bossSightApi, 'setObservabilitySettings').mockResolvedValue({
      ...settings,
      dimensions: { ...settings.dimensions, selection: false },
      updated_by: 'human',
    })
    const addPermanentAllow = vi.spyOn(bossSightApi, 'addPermanentAllow').mockResolvedValue({
      id: 'allow_1',
      scope: 'user',
      tool: 'Bash',
      pattern: 'npm run build',
      reason: 'local build',
      actor: 'human',
      created_at: '2026-05-31T00:00:02Z',
    })
    vi.spyOn(bossSightApi, 'recordObservation').mockResolvedValue({ recorded: true, skipped: false })

    render(<BossSightControlCard />)

    await screen.findByText('总控自动唤起')
    expect(screen.getByTestId('recent-observations').textContent).toContain('click')

    fireEvent.click(screen.getByLabelText('总控自动唤起'))
    await waitFor(() => {
      expect(setControl).toHaveBeenCalledWith('controller.auto_wake', false, 'human', 'settings panel toggle')
    })

    fireEvent.click(screen.getByLabelText('圈选'))
    await waitFor(() => {
      expect(setObservabilitySettings).toHaveBeenCalledWith({ selection: false }, 'human', 'settings panel toggle')
    })

    fireEvent.change(screen.getByLabelText('tool'), { target: { value: 'Bash' } })
    fireEvent.change(screen.getByLabelText('pattern'), { target: { value: 'npm run build' } })
    fireEvent.change(screen.getByLabelText('reason'), { target: { value: 'local build' } })
    fireEvent.click(screen.getByText('写入偏好'))
    await waitFor(() => {
      expect(addPermanentAllow).toHaveBeenCalledWith({
        scope: 'user',
        tool: 'Bash',
        pattern: 'npm run build',
        reason: 'local build',
      })
    })
    expect(screen.getByTestId('permanent-allow-list').textContent).toContain('Bash')
  })
})
