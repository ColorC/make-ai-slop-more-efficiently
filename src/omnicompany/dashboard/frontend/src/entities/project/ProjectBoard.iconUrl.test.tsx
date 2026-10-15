import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectItem, ProjectsBoard } from '../../api/projectsClient'
import ProjectBoard from './ProjectBoard'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  views: vi.fn(),
  activeBindings: vi.fn(),
}))

vi.mock('../../api/projectsClient', async () => {
  const actual = await vi.importActual<typeof import('../../api/projectsClient')>('../../api/projectsClient')
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      list: mocks.list,
      views: mocks.views,
    },
  }
})

vi.mock('../../api/bossSightClient', async () => {
  const actual = await vi.importActual<typeof import('../../api/bossSightClient')>('../../api/bossSightClient')
  return {
    ...actual,
    bossSightApi: {
      ...actual.bossSightApi,
      activeBindings: mocks.activeBindings,
    },
  }
})

const baseProject: ProjectItem = {
  id: 'omnidashboard',
  name: 'OmniDashboard',
  group: 'omnicompany',
  icon: 'layout-dashboard',
  icon_url: '/api/project-assets/omnidashboard-icon.png?v=1',
  bg: '/api/project-assets/omnidashboard.png',
}

function boardWith(project: ProjectItem): ProjectsBoard {
  return {
    projects: [project],
    groups_order: ['omnicompany'],
    group_labels: { omnicompany: 'OmniCompany' },
  }
}

describe('ProjectBoard project icon_url', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.list.mockReset().mockResolvedValue(boardWith(baseProject))
    mocks.views.mockReset().mockResolvedValue({ apps: [] })
    mocks.activeBindings.mockReset().mockResolvedValue({ by_project: {} })
  })

  it('renders icon_url in a project list row and falls back to its lucide icon on error', async () => {
    render(<ProjectBoard />)

    const row = await screen.findByTestId('project-list-row')
    const image = within(row).getByTestId('project-icon-image')
    expect(image.getAttribute('src')).toBe('/api/project-assets/omnidashboard-icon.png?v=1&w=320')
    expect(image.getAttribute('loading')).toBe('lazy')
    expect(image.getAttribute('fetchpriority')).toBe('low')
    expect(image.getAttribute('alt')).toBe('OmniDashboard icon')

    fireEvent.error(image)

    await waitFor(() => expect(within(row).queryByTestId('project-icon-image')).toBeNull())
    expect(within(row).getByTestId('project-card-icon')).toBeTruthy()
  })

  it('keeps the launcher cover while its icon overlay falls back to lucide on error', async () => {
    localStorage.setItem('omni.projectBoard.view', 'apps')
    render(<ProjectBoard />)

    const tile = await screen.findByTestId('project-app-project-tile')
    const cover = within(tile).getByTestId('project-app-tile-img')
    const coverStyle = cover.getAttribute('style')
    expect(coverStyle).toContain('/api/project-assets/omnidashboard.png?w=320')
    expect(within(tile).getByTestId('project-app-tile-icon-overlay')).toBeTruthy()
    const image = within(tile).getByTestId('project-icon-image')
    expect(image.getAttribute('src')).toBe('/api/project-assets/omnidashboard-icon.png?v=1&w=320')
    expect(image.getAttribute('alt')).toBe('OmniDashboard icon')

    fireEvent.error(image)

    await waitFor(() => expect(within(tile).queryByTestId('project-icon-image')).toBeNull())
    expect(within(tile).getByTestId('project-card-icon')).toBeTruthy()
    expect(within(tile).getByTestId('project-app-tile-img').getAttribute('style')).toBe(coverStyle)
  })

  it('keeps the existing project cover when icon_url is absent', async () => {
    localStorage.setItem('omni.projectBoard.view', 'apps')
    mocks.list.mockResolvedValue(boardWith({ ...baseProject, icon_url: null }))
    render(<ProjectBoard />)

    const tile = await screen.findByTestId('project-app-project-tile')
    expect(within(tile).queryByTestId('project-icon-image')).toBeNull()
    expect(within(tile).getByTestId('project-app-tile-img')).toBeTruthy()
  })
})
