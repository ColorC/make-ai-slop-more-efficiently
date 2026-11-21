// @ts-expect-error Vitest executes this source-level assertion under Node;
// the browser bundle intentionally excludes Node type declarations.
import { existsSync, readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { resolve } from 'node:path'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanels, withDefaultTabs } from '../../stores/panelsStore'
import { TAB_TITLE_LIMITS, UI_SETTINGS_STORAGE_KEYS, useUiSettings } from '../../stores/uiSettingsStore'
import { settingsRegistration } from './index.tsx'

declare const process: { cwd(): string }

const SYSTEM_INFO = { version: 'test-version' }
const LOFA_VERSION = {
  ok: true,
  manifest: {
    versionCode: 53,
    versionName: '1.0.53',
    size: 8_894_523,
    sha256: 'test-sha256',
  },
}
const CLIENT_CATALOG = {
  ok: true,
  desktop: {
    available: true,
    channel: 'canary',
    updaterSignatureRequired: true,
    latest: {
      version: '0.1.0',
      publishedAt: '2026-08-03T10:00:00Z',
      notes: 'first desktop release',
      assets: {
        nsis: {
          filename: 'Omnicompany.Desktop_0.1.0_x64-setup.exe',
          size: 5_242_880,
          sha256: 'a'.repeat(64),
          updaterSignature: 'signed',
          signatureVerifiedByClient: true,
          authenticode: { status: 'NotSigned', publisher: null, thumbprint: null },
          downloadUrl: '/api/clients/desktop/canary/0.1.0/nsis',
        },
      },
    },
    previous: null,
  },
}

describe('settings runtime entrypoint', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiSettings.setState({ tabLayout: 'single-row', tabTitleMaxLength: TAB_TITLE_LIMITS.default })
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: 'settings:main' })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const payload = url.includes('/api/android/apk/version')
        ? LOFA_VERSION
        : url.includes('/api/clients/catalog') ? CLIENT_CATALOG : SYSTEM_INFO
      return {
        ok: true,
        status: 200,
        json: async () => payload,
      }
    }))
  })

  afterEach(() => {
    delete window.__OMNICOMPANY_HOST__
  })

  it('resolves the settings folder to the TSX module without a legacy index.ts shadow', () => {
    const registrationSource = readFileSync(
      resolve(process.cwd(), 'src/shell/registerEntities.ts'),
      'utf8',
    )
    expect(registrationSource).toContain("from '../entities/settings'")
    expect(existsSync(resolve(process.cwd(), 'src/entities/settings/index.ts'))).toBe(false)
  })

  it('opens on real client downloads and keeps only the useful system entrypoint', async () => {
    const SettingsEditor = settingsRegistration.renderer.Editor
    render(<SettingsEditor entity={{ type: 'settings', id: 'main', title: '设置' } as any} />)

    expect(screen.getByTestId('settings-page')).toBeTruthy()
    expect(await screen.findByText('1.0.53')).toBeTruthy()
    expect(screen.getByTestId('lofa-download-link').getAttribute('href')).toBe('/api/android/apk/latest')
    expect(await screen.findByText('0.1.0')).toBeTruthy()
    expect(screen.getByTestId('desktop-download-link').getAttribute('href'))
      .toBe('/api/clients/desktop/canary/0.1.0/nsis')
    expect(screen.getByText('更新签名强制校验')).toBeTruthy()
    expect(screen.queryByText('Token 统计')).toBeNull()
    expect(screen.queryByText('BOSS SIGHT')).toBeNull()
    expect(screen.getByRole('radio', { name: '订阅配额' })).toBeTruthy()

    fireEvent.click(screen.getByTestId('open-remote-node'))
    expect(screen.getByTestId('remote-node-browser-note')).toBeTruthy()

    fireEvent.click(screen.getByRole('radio', { name: '系统' }))
    expect(await screen.findByText('test-version')).toBeTruthy()
    fireEvent.click(screen.getByTestId('settings-file-bridge'))
    expect(usePanels.getState().activeId).toBe('file_bridge:main')
  })

  it('persists the tab layout and clamps the configurable title length', () => {
    const SettingsEditor = settingsRegistration.renderer.Editor
    render(<SettingsEditor entity={{ type: 'settings', id: 'main', title: '设置' } as any} />)

    fireEvent.click(screen.getByRole('radio', { name: '界面' }))
    expect(screen.getByRole('radio', { name: '单行' }).getAttribute('aria-checked')).toBe('true')

    fireEvent.click(screen.getByRole('radio', { name: '堆叠' }))
    expect(useUiSettings.getState().tabLayout).toBe('stacked')
    expect(window.localStorage.getItem(UI_SETTINGS_STORAGE_KEYS.tabLayout)).toBe('stacked')

    const input = screen.getByRole('spinbutton', { name: '页签标题最大长度' }) as HTMLInputElement
    expect(input.value).toBe(String(TAB_TITLE_LIMITS.default))
    fireEvent.change(input, { target: { value: '7' } })
    expect(useUiSettings.getState().tabTitleMaxLength).toBe(TAB_TITLE_LIMITS.min)
    expect(input.value).toBe(String(TAB_TITLE_LIMITS.min))

    fireEvent.change(input, { target: { value: '100' } })
    expect(useUiSettings.getState().tabTitleMaxLength).toBe(TAB_TITLE_LIMITS.max)
    expect(window.localStorage.getItem(UI_SETTINGS_STORAGE_KEYS.tabTitleMaxLength))
      .toBe(String(TAB_TITLE_LIMITS.max))
  })
  it('uses the native signed updater bridge only when running in Desktop', async () => {
    const checkDesktopUpdate = vi.fn(async () => ({
      currentVersion: '0.1.0',
      available: true,
      version: '0.2.0',
      notes: 'upgrade',
      publishedAt: '2026-08-03T11:00:00Z',
      rollback: false,
      signatureEnforced: true,
      automatic: true,
    }))
    const applyDesktopUpdate = vi.fn(async () => ({
      started: false,
      reason: 'cancelled' as const,
      version: '0.2.0',
    }))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      hostInfo: vi.fn(async () => ({
        runtime: 'desktop' as const,
        version: '0.1.0',
        dashboardUrl: 'https://omnicompany.example.test/',
        serverUrl: 'https://omnicompany.example.test/',
        webviewEngine: 'WebView2' as const,
        externalWebviewsIsolated: true,
      })),
      checkDesktopUpdate,
      applyDesktopUpdate,
    }

    const SettingsEditor = settingsRegistration.renderer.Editor
    render(<SettingsEditor entity={{ type: 'settings', id: 'main', title: '设置' } as any} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查更新' }))
    expect(await screen.findByText('发现可安装版本 0.2.0')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '安装 0.2.0' }))

    await waitFor(() => expect(applyDesktopUpdate).toHaveBeenCalledWith('0.2.0'))
    expect(await screen.findByText('已取消安装')).toBeTruthy()
  })

  it('opens the real Remote Node setup directly from client downloads', async () => {
    const nodeStatus = vi.fn(async () => ({
      configured: false,
      grantId: null,
      generation: null,
      root: null,
      tools: [],
      limits: {
        timeoutMs: 30_000,
        maxMemoryMb: 512,
        maxOutputBytes: 512 * 1024,
        maxProcesses: 4,
        cpuRatePercent: 25,
      },
      mode: 'readonly-l1' as const,
      networkPolicy: 'deny' as const,
      enabled: false,
      paused: false,
      selfTestedAtMs: null,
      activeTasks: [],
      auditPath: 'C:/test/audit.jsonl',
    }))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => null),
      inspectNodeRoot: vi.fn(async () => { throw new Error('not used') }),
      nodeStatus,
      saveNodeGrant: vi.fn(async () => nodeStatus()),
      selfTestAndEnableNode: vi.fn(async () => ({ passed: true, enabled: true, checks: [] })),
      setNodePaused: vi.fn(async () => nodeStatus()),
      revokeNode: vi.fn(async () => nodeStatus()),
      transportStatus: vi.fn(async () => ({
        nodeId: 'node-test',
        nodeFingerprint: 'fingerprint',
        paired: false,
        revoked: false,
        controllerUrl: null,
        controllerFingerprint: null,
        label: null,
        connected: false,
        lastPollAtMs: null,
        lastHeartbeatLatencyMs: null,
        lastQueueLatencyMs: null,
        pendingReceipts: 0,
        lastError: null,
      })),
    }

    const SettingsEditor = settingsRegistration.renderer.Editor
    render(<SettingsEditor entity={{ type: 'settings', id: 'main', title: '设置' } as any} />)
    fireEvent.click(screen.getByTestId('open-remote-node'))

    expect(await screen.findByTestId('remote-node-settings')).toBeTruthy()
    await waitFor(() => expect(nodeStatus).toHaveBeenCalled())
  })
})
