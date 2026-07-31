// @ts-expect-error Vitest executes this source-level assertion under Node;
// the browser bundle intentionally excludes Node type declarations.
import { existsSync, readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanels, withDefaultTabs } from '../../stores/panelsStore'
import { settingsRegistration } from './index.tsx'

declare const process: { cwd(): string }

vi.mock('./BossSightControlCard', () => ({
  default: () => <div data-testid="boss-sight-card">Boss Sight</div>,
}))
vi.mock('./CcInstallCard', () => ({
  default: () => <div data-testid="cc-install-card">CLI integration</div>,
}))
vi.mock('./TokenStatsTab', () => ({
  default: () => <div data-testid="token-stats-tab">Token statistics</div>,
}))

const SYSTEM_INFO = {
  version: 'test-version',
  project_root: 'C:/workspace/omnicompany',
  packages_root: 'C:/workspace/omnicompany/packages',
  stats: { worker_count: 7, package_count: 11 },
  databases: {
    events: { path: 'data/events.db', exists: true, size: 2048 },
  },
  endpoints: { dashboard: 'http://127.0.0.1:8210' },
}

describe('settings runtime entrypoint', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: 'settings:main' })
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => SYSTEM_INFO,
    })))
  })

  it('resolves the settings folder to the recovered TSX module without a legacy index.ts shadow', () => {
    const registrationSource = readFileSync(
      resolve(process.cwd(), 'src/shell/registerEntities.ts'),
      'utf8',
    )
    expect(registrationSource).toContain("from '../entities/settings'")
    expect(existsSync(resolve(process.cwd(), 'src/entities/settings/index.ts'))).toBe(false)
  })

  it('preserves system controls and exposes the recovered Token statistics view', async () => {
    const SettingsEditor = settingsRegistration.renderer.Editor
    render(<SettingsEditor entity={{ type: 'settings', id: 'main', title: '设置' } as any} />)

    expect(screen.getByTestId('settings-page')).toBeTruthy()
    expect(await screen.findByText('test-version')).toBeTruthy()
    expect(screen.getByTestId('boss-sight-card')).toBeTruthy()
    expect(screen.getByTestId('cc-install-card')).toBeTruthy()

    fireEvent.click(screen.getByTestId('settings-file-bridge'))
    expect(usePanels.getState().activeId).toBe('file_bridge:main')

    fireEvent.click(screen.getByRole('radio', { name: 'Token 统计' }))
    expect(screen.getByTestId('token-stats-tab')).toBeTruthy()
  })
})
