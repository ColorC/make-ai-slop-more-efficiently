import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  OmnicompanyHostBridge,
  RemoteNodeRootInspection,
  RemoteNodeStatus,
  RemoteNodeTransportStatus,
} from '../../lib/workspaceNavigation'
import SecurityNodeTab from './SecurityNodeTab'

const ROOT = 'C:/workspace/omnicompany'
const LIMITS = {
  timeoutMs: 30_000,
  maxMemoryMb: 512,
  maxOutputBytes: 512 * 1024,
  maxProcesses: 4,
  cpuRatePercent: 25,
}
const TOOLS = [
  {
    programId: 'git',
    displayName: 'Git',
    path: 'C:\\Program Files\\Git\\cmd\\git.exe',
    sha256: 'a'.repeat(64),
    operations: ['status', 'rev-parse'],
  },
  {
    programId: 'rg',
    displayName: 'ripgrep',
    path: 'C:\\Tools\\rg.exe',
    sha256: 'b'.repeat(64),
    operations: ['files'],
  },
]
/**
 * 执行档探测到的东西和只读档不一样：`discover_tools` 的候选集是 mode 的函数
 * (`remote_node.rs:1391-1405`)，L2 才会去找启动器。这台样例机上没装 pwsh，
 * 和真机一致——只读工具照样在 PATH 上，但 L2 自检一条都不认。
 */
const EXEC_TOOLS = [
  ...TOOLS.map((tool) => ({ ...tool, operations: ['any'] })),
  {
    programId: 'powershell',
    displayName: 'Windows PowerShell',
    path: 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe',
    sha256: 'c'.repeat(64),
    operations: ['any'],
  },
  {
    programId: 'cmd',
    displayName: 'Command Prompt',
    path: 'C:\\Windows\\System32\\cmd.exe',
    sha256: 'd'.repeat(64),
    operations: ['any'],
  },
]

function inspection(tools = TOOLS): RemoteNodeRootInspection {
  return {
    safe: true,
    canonicalPath: ROOT,
    filesystem: 'NTFS',
    volumeSerial: 123,
    freeBytes: 500 * 1024 * 1024 * 1024,
    reparsePoints: [],
    projectTypes: ['git', 'python'],
    tools,
    checks: [
      { id: 'canonical', passed: true, message: 'canonical ok' },
      { id: 'reparse', passed: true, message: 'no reparse point' },
    ],
  }
}

const INSPECTION = inspection()

/** 和真 bridge 一样按档位给出不同的探测结果。 */
const inspectByMode = (_root: string, mode?: string) => (
  mode === 'trusted-exec-l2' ? inspection(EXEC_TOOLS) : INSPECTION
)

function nodeStatus(overrides: Partial<RemoteNodeStatus> = {}): RemoteNodeStatus {
  return {
    configured: false,
    grantId: null,
    generation: null,
    root: null,
    tools: [],
    limits: LIMITS,
    mode: 'readonly-l1',
    networkPolicy: 'deny',
    enabled: false,
    paused: true,
    selfTestedAtMs: null,
    activeTasks: [],
    auditPath: 'C:\\audit.jsonl',
    ...overrides,
  }
}

function transportStatus(overrides: Partial<RemoteNodeTransportStatus> = {}): RemoteNodeTransportStatus {
  return {
    nodeId: 'desktop-test',
    nodeFingerprint: 'c'.repeat(64),
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
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  delete window.__OMNICOMPANY_HOST__
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('SecurityNodeTab', () => {
  it('keeps browser settings explanatory without local execution controls', () => {
    render(<SecurityNodeTab />)
    expect(screen.getByTestId('remote-node-browser-note')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '选择目录' })).toBeNull()
  })

  it('pairs an enabled node through the native identity bridge', async () => {
    const pairNode = vi.fn(async () => transportStatus({
      paired: true,
      connected: true,
      controllerUrl: 'http://127.0.0.1:8210',
      controllerFingerprint: 'd'.repeat(64),
    }))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async () => nodeStatus({
        configured: true,
        grantId: 'grant-1',
        generation: 1,
        root: ROOT,
        tools: TOOLS,
        enabled: true,
        paused: false,
      })),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode,
      unpairNode: vi.fn(async () => transportStatus()),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    await screen.findByText('desktop-test')
    fireEvent.change(screen.getByLabelText('Pairing ID'), { target: { value: 'pair-1' } })
    fireEvent.change(screen.getByLabelText('8 位一次性码'), { target: { value: '12345678' } })
    fireEvent.click(screen.getByRole('button', { name: '确认指纹并配对（只读节点）' }))
    await waitFor(() => expect(pairNode).toHaveBeenCalledWith({
      controllerUrl: window.location.origin,
      pairingId: 'pair-1',
      code: '12345678',
      label: 'Omnicompany Desktop',
      mode: 'readonly-l1',
    }))
    expect(await screen.findByText('长轮询在线')).toBeTruthy()
  })

  it('configures, self-tests, and pairs the trusted Server from one primary action', async () => {
    let current = nodeStatus()
    const selectNodeRoot = vi.fn(async () => ROOT)
    const saveNodeGrant = vi.fn(async () => {
      current = nodeStatus({
        configured: true,
        grantId: 'grant-quick',
        generation: 1,
        root: ROOT,
        tools: TOOLS,
      })
      return current
    })
    const quickEnableNode = vi.fn(async () => {
      current = { ...current, enabled: true, paused: false, selfTestedAtMs: 12345 }
      return {
        passed: true,
        enabled: true,
        checks: [{ id: 'git.job', passed: true, message: 'Git exit=0', durationMs: 8 }],
      }
    })
    const pairTrustedServer = vi.fn(async () => transportStatus({
      paired: true,
      connected: true,
      controllerUrl: window.location.origin,
      controllerFingerprint: 'd'.repeat(64),
    }))
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        pairing_id: 'pair-quick',
        code: '12345678',
        controller_fingerprint: 'd'.repeat(64),
        expires_at_ms: Date.now() + 300_000,
      }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot,
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async () => current),
      saveNodeGrant,
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode,
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode: vi.fn(),
      pairTrustedServer,
      unpairNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键开启只读节点' }))

    await waitFor(() => expect(pairTrustedServer).toHaveBeenCalledWith({
      controllerUrl: window.location.origin,
      pairingId: 'pair-quick',
      code: '12345678',
      label: 'Omnicompany Desktop',
      mode: 'readonly-l1',
    }))
    expect(selectNodeRoot).toHaveBeenCalledTimes(1)
    expect(saveNodeGrant).toHaveBeenCalledWith({
      root: ROOT,
      approvedPrograms: ['git', 'rg'],
      limits: LIMITS,
      mode: 'readonly-l1',
    })
    expect(quickEnableNode).toHaveBeenCalledTimes(1)
  })

  it('inspects a native root, saves an explicit tool grant, and runs self-test', async () => {
    let current = nodeStatus()
    const saveNodeGrant = vi.fn(async (request) => {
      current = nodeStatus({
        configured: true,
        grantId: 'grant-1',
        generation: 1,
        root: request.root,
        tools: TOOLS,
      })
      return current
    })
    const selfTestAndEnableNode = vi.fn(async () => {
      current = { ...current, enabled: true, paused: false, selfTestedAtMs: 12345 }
      return {
        passed: true,
        enabled: true,
        checks: [{ id: 'git.job', passed: true, message: 'Git exit=0', durationMs: 12 }],
      }
    })
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async () => current),
      saveNodeGrant,
      selfTestAndEnableNode,
      setNodePaused: vi.fn(async (paused) => ({ ...current, paused })),
      revokeNode: vi.fn(async () => nodeStatus()),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '选择目录' }))
    expect(await screen.findByText(ROOT)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '保存授权草案' }))
    await waitFor(() => expect(saveNodeGrant).toHaveBeenCalledTimes(1))
    expect(saveNodeGrant.mock.calls[0][0]).toEqual(expect.objectContaining({
      root: ROOT,
      approvedPrograms: ['git', 'rg'],
    }))

    fireEvent.click(screen.getByRole('button', { name: '本机自检并启用' }))
    expect(await screen.findByText('自检通过，节点已启用')).toBeTruthy()
    expect(selfTestAndEnableNode).toHaveBeenCalledTimes(1)
  })

  it('offers only the read-only node to a client whose bridge predates execution', async () => {
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async () => nodeStatus()),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    expect(await screen.findByTestId('remote-node-mode-readonly-l1')).toBeTruthy()
    expect(screen.queryByTestId('remote-node-mode-trusted-exec-l2')).toBeNull()
  })

  it('shows each grant its own state rather than one shared switch', async () => {
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async (mode?: string) => (
        mode === 'trusted-exec-l2'
          ? nodeStatus({ mode: 'trusted-exec-l2', networkPolicy: 'inherit' })
          : nodeStatus({
            configured: true,
            grantId: 'grant-l1',
            generation: 1,
            root: ROOT,
            tools: TOOLS,
            enabled: true,
            paused: false,
          })
      )),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus({ paired: true, connected: true })),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    const readonlyRow = await screen.findByTestId('remote-node-mode-readonly-l1')
    await waitFor(() => expect(readonlyRow.textContent).toContain('在线'))
    expect(screen.getByTestId('remote-node-mode-trusted-exec-l2').textContent).toContain('未开启')
  })

  it('warns that the execution root is a working directory, not a boundary', async () => {
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async () => nodeStatus()),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    // The read-only grant makes no such claim, so the note must not be there yet.
    expect(screen.queryByTestId('remote-node-caution')).toBeNull()
    fireEvent.click(await screen.findByTestId('remote-node-mode-trusted-exec-l2'))
    expect((await screen.findByTestId('remote-node-caution')).textContent).toContain('不是隔离边界')
  })

  it('enables execution through its own grant, self-test and pairing', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus(),
      'trusted-exec-l2': nodeStatus({ mode: 'trusted-exec-l2', networkPolicy: 'inherit' }),
    }
    const saveNodeGrant = vi.fn(async (request) => {
      statuses[request.mode!] = nodeStatus({
        configured: true,
        grantId: 'grant-l2',
        generation: 1,
        root: request.root,
        tools: TOOLS,
        mode: 'trusted-exec-l2',
        networkPolicy: 'inherit',
      })
      return statuses[request.mode!]
    })
    const quickEnableNode = vi.fn(async (mode?: string) => {
      statuses[mode!] = { ...statuses[mode!], enabled: true, paused: false }
      return { passed: true, enabled: true, checks: [] }
    })
    const pairTrustedServer = vi.fn(async () => transportStatus({ paired: true, connected: true }))
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, pairing_id: 'pair-exec', code: '87654321' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant,
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode,
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode: vi.fn(),
      pairTrustedServer,
      unpairNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByTestId('remote-node-mode-trusted-exec-l2'))
    fireEvent.click(await screen.findByRole('button', { name: '一键开启执行节点' }))

    await waitFor(() => expect(quickEnableNode).toHaveBeenCalledWith('trusted-exec-l2'))
    expect(saveNodeGrant.mock.calls[0][0]).toEqual(expect.objectContaining({
      mode: 'trusted-exec-l2',
      root: ROOT,
    }))
    // Execution must not inherit the read-only ceilings.
    expect(saveNodeGrant.mock.calls[0][0].limits.timeoutMs).toBe(120_000)
    // Its own pairing window, never the read-only one.
    await waitFor(() => expect(pairTrustedServer).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'trusted-exec-l2', pairingId: 'pair-exec' }),
    ))
  })

  it('revokes one grant without touching the other', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus({
        configured: true,
        grantId: 'grant-l1',
        generation: 1,
        root: ROOT,
        tools: TOOLS,
        enabled: true,
        paused: false,
      }),
      'trusted-exec-l2': nodeStatus({
        configured: true,
        grantId: 'grant-l2',
        generation: 1,
        root: ROOT,
        tools: TOOLS,
        enabled: true,
        paused: false,
        mode: 'trusted-exec-l2',
        networkPolicy: 'inherit',
      }),
    }
    const revokeNode = vi.fn(async (mode?: string) => {
      statuses[mode!] = nodeStatus({ mode: (mode ?? 'readonly-l1') as RemoteNodeStatus['mode'] })
      return statuses[mode!]
    })
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode,
      transportStatus: vi.fn(async () => transportStatus({ paired: true, connected: true })),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByTestId('remote-node-mode-trusted-exec-l2'))
    fireEvent.click(await screen.findByRole('button', { name: '撤销执行节点' }))

    await waitFor(() => expect(revokeNode).toHaveBeenCalledWith('trusted-exec-l2'))
    expect(revokeNode).toHaveBeenCalledTimes(1)
    // The read-only row keeps the state it had.
    expect(screen.getByTestId('remote-node-mode-readonly-l1').textContent).toContain('在线')
    expect(screen.getByTestId('remote-node-mode-trusted-exec-l2').textContent).toContain('未开启')
  })

  it('opens every grant from one click and asks for the directory only once', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus(),
      'trusted-exec-l2': nodeStatus({ mode: 'trusted-exec-l2', networkPolicy: 'inherit' }),
    }
    const selectNodeRoot = vi.fn(async () => ROOT)
    const saveNodeGrant = vi.fn(async (request) => {
      statuses[request.mode!] = nodeStatus({
        configured: true,
        grantId: `grant-${request.mode}`,
        generation: 1,
        root: request.root,
        tools: TOOLS,
        mode: request.mode as RemoteNodeStatus['mode'],
      })
      return statuses[request.mode!]
    })
    const quickEnableNode = vi.fn(async (mode?: string) => {
      statuses[mode!] = { ...statuses[mode!], enabled: true, paused: false }
      return { passed: true, enabled: true, checks: [] }
    })
    const pairTrustedServer = vi.fn(async () => transportStatus({ paired: true, connected: true }))
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, pairing_id: 'pair-all', code: '11112222' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot,
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant,
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode,
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode: vi.fn(),
      pairTrustedServer,
      unpairNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    expect((await screen.findByTestId('remote-node-grant-all-state')).textContent)
      .toContain('未开启任何权限')
    fireEvent.click(screen.getByTestId('remote-node-grant-all-button'))

    await waitFor(() => expect(quickEnableNode).toHaveBeenCalledTimes(2))
    expect(quickEnableNode.mock.calls.map(([mode]) => mode))
      .toEqual(['readonly-l1', 'trusted-exec-l2'])
    // One click means one folder dialog, not one per grant.
    expect(selectNodeRoot).toHaveBeenCalledTimes(1)
    // Each grant still registers through its own pairing window.
    await waitFor(() => expect(pairTrustedServer).toHaveBeenCalledTimes(2))
    expect(saveNodeGrant.mock.calls.map(([request]) => request.mode))
      .toEqual(['readonly-l1', 'trusted-exec-l2'])
    // Execution keeps its own ceilings; grant-all is not a limits override.
    expect(saveNodeGrant.mock.calls[1][0].limits.timeoutMs).toBe(120_000)
  })

  it('gives each grant the tools its own self-test runs, not the other grant\'s', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus(),
      'trusted-exec-l2': nodeStatus({ mode: 'trusted-exec-l2', networkPolicy: 'inherit' }),
    }
    const saveNodeGrant = vi.fn(async (request) => {
      statuses[request.mode!] = nodeStatus({
        configured: true,
        grantId: `grant-${request.mode}`,
        generation: 1,
        root: request.root,
        tools: TOOLS,
        mode: request.mode as RemoteNodeStatus['mode'],
      })
      return statuses[request.mode!]
    })
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, pairing_id: 'pair-all', code: '11112222' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant,
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async (mode?: string) => {
        statuses[mode!] = { ...statuses[mode!], enabled: true, paused: false }
        return { passed: true, enabled: true, checks: [] }
      }),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode: vi.fn(),
      pairTrustedServer: vi.fn(async () => transportStatus({ paired: true, connected: true })),
      unpairNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByTestId('remote-node-grant-all-button'))

    await waitFor(() => expect(saveNodeGrant).toHaveBeenCalledTimes(2))
    // Read-only gets no launcher it has no business holding…
    expect(saveNodeGrant.mock.calls[0][0].approvedPrograms).toEqual(['git', 'rg'])
    // …and execution gets exactly the launchers its self-test can start —
    // git/rg would be `continue`d there, so granting them is empty authority.
    expect(saveNodeGrant.mock.calls[1][0].approvedPrograms).toEqual(['powershell', 'cmd'])
  })

  it('names the missing launcher instead of sending the user to advanced settings', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus(),
      'trusted-exec-l2': nodeStatus({ mode: 'trusted-exec-l2', networkPolicy: 'inherit' }),
    }
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, pairing_id: 'pair-all', code: '11112222' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(async () => ROOT),
      // No shell anywhere on PATH, so execution has nothing its self-test can start.
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant: vi.fn(async (request) => {
        statuses[request.mode!] = nodeStatus({
          configured: true,
          grantId: 'grant-l1',
          generation: 1,
          root: request.root,
          tools: TOOLS,
          mode: request.mode as RemoteNodeStatus['mode'],
        })
        return statuses[request.mode!]
      }),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async (mode?: string) => {
        statuses[mode!] = { ...statuses[mode!], enabled: true, paused: false }
        return { passed: true, enabled: true, checks: [] }
      }),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode: vi.fn(),
      pairTrustedServer: vi.fn(async () => transportStatus({ paired: true, connected: true })),
      unpairNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByTestId('remote-node-grant-all-button'))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('这台机器上没找到')
    expect(alert.textContent).toContain('Windows PowerShell (powershell.exe)')
    expect(alert.textContent).toContain('PATH')
    // The dead end this replaces: "go tick a box in advanced settings".
    expect(alert.textContent).not.toContain('工具授权')
  })

  it('repairs a grant that was saved with tools its self-test cannot run', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus({
        configured: true, grantId: 'g1', generation: 1, root: ROOT, tools: TOOLS,
        enabled: true, paused: false,
      }),
      // Saved earlier with the read-only tool set: every check would be skipped,
      // so the node refuses and no amount of re-clicking used to fix it.
      'trusted-exec-l2': nodeStatus({
        configured: true, grantId: 'g2', generation: 1, root: ROOT, tools: TOOLS,
        mode: 'trusted-exec-l2', networkPolicy: 'inherit',
      }),
    }
    const saveNodeGrant = vi.fn(async (request) => {
      statuses[request.mode!] = nodeStatus({
        configured: true,
        grantId: 'g2b',
        generation: 2,
        root: request.root,
        tools: EXEC_TOOLS.filter((tool) => request.approvedPrograms.includes(tool.programId)),
        mode: request.mode as RemoteNodeStatus['mode'],
      })
      return statuses[request.mode!]
    })
    const selectNodeRoot = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, pairing_id: 'pair-fix', code: '33334444' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot,
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant,
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async (mode?: string) => {
        statuses[mode!] = { ...statuses[mode!], enabled: true, paused: false }
        return { passed: true, enabled: true, checks: [] }
      }),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairNode: vi.fn(),
      pairTrustedServer: vi.fn(async () => transportStatus({ paired: true, connected: true })),
      unpairNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByTestId('remote-node-grant-all-button'))

    await waitFor(() => expect(saveNodeGrant).toHaveBeenCalledTimes(1))
    expect(saveNodeGrant.mock.calls[0][0]).toEqual(expect.objectContaining({
      mode: 'trusted-exec-l2',
      approvedPrograms: ['powershell', 'cmd'],
    }))
    // The already-correct read-only grant is left alone, and the repair asks
    // for nothing: it reuses the directory that grant already carries.
    expect(selectNodeRoot).not.toHaveBeenCalled()
  })

  it('states the open grant width and offers to take it all back', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus({
        configured: true, grantId: 'g1', generation: 1, root: ROOT, tools: TOOLS,
        enabled: true, paused: false,
      }),
      'trusted-exec-l2': nodeStatus({
        configured: true, grantId: 'g2', generation: 1, root: ROOT, tools: TOOLS,
        enabled: true, paused: false, mode: 'trusted-exec-l2', networkPolicy: 'inherit',
      }),
    }
    const revokeNode = vi.fn(async (mode?: string) => {
      statuses[mode!] = nodeStatus({ mode: (mode ?? 'readonly-l1') as RemoteNodeStatus['mode'] })
      return statuses[mode!]
    })
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      nodeModes: { readonly: 'readonly-l1', exec: 'trusted-exec-l2' },
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode,
      transportStatus: vi.fn(async () => transportStatus({ paired: true, connected: true })),
      pairTrustedServer: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    const banner = await screen.findByTestId('remote-node-grant-all')
    await waitFor(() => expect(banner.getAttribute('data-level')).toBe('all'))
    expect(screen.getByTestId('remote-node-grant-all-state').textContent)
      .toContain('全部权限已开启')
    // Nothing left to open, so the switch is spent — but taking it back is live.
    expect(screen.getByTestId('remote-node-grant-all-button').hasAttribute('disabled')).toBe(true)

    fireEvent.click(screen.getByTestId('remote-node-revoke-all-button'))
    await waitFor(() => expect(revokeNode).toHaveBeenCalledTimes(2))
    expect(revokeNode.mock.calls.map(([mode]) => mode))
      .toEqual(['readonly-l1', 'trusted-exec-l2'])
    await waitFor(() => expect(
      screen.getByTestId('remote-node-grant-all-state').textContent,
    ).toContain('未开启任何权限'))
  })

  it('names the failing self-test check instead of only saying it failed', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus({
        configured: true, grantId: 'g1', generation: 1, root: ROOT, tools: TOOLS,
      }),
    }
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async () => ({
        passed: false,
        enabled: false,
        checks: [
          { id: 'root', passed: true, message: '执行根目录检查通过', durationMs: null },
          { id: 'rg.hash', passed: false, message: 'ripgrep 已变化，必须重新授权', durationMs: null },
          { id: 'git.job', passed: false, message: 'Git：exit=128，completed', durationMs: 31 },
        ],
      })),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键恢复并连接' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('2/3 项失败')
    expect(alert.textContent).toContain('rg 哈希 —— ripgrep 已变化，必须重新授权')
    expect(alert.textContent).toContain('git 执行 —— Git：exit=128，completed')
    // The per-check panel has to be visible on its own, not buried in <details>.
    const report = screen.getByTestId('remote-node-self-test')
    expect(report.closest('details')).toBeNull()
    expect(report.textContent).toContain('执行根目录：执行根目录检查通过')
  })

  it('separates "no tool ran" from "a tool failed", since they are fixed differently', async () => {
    const statuses: Record<string, RemoteNodeStatus> = {
      'readonly-l1': nodeStatus({
        configured: true, grantId: 'g1', generation: 1, root: ROOT, tools: TOOLS,
      }),
    }
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async (mode?: string) => statuses[mode ?? 'readonly-l1']),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      // Every listed check passed, yet the node still refuses: no `*.job` ran.
      quickEnableNode: vi.fn(async () => ({
        passed: false,
        enabled: false,
        checks: [{ id: 'root', passed: true, message: '根目录检查通过', durationMs: null }],
      })),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键恢复并连接' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('没有任何工具执行检查跑起来')
    expect(alert.textContent).toContain('已跑的检查：root')
  })

  it('tells a pre-L2 client to update rather than pretending grant-all is complete', async () => {
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(),
      inspectNodeRoot: vi.fn(async () => INSPECTION),
      nodeStatus: vi.fn(async () => nodeStatus()),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    expect((await screen.findByTestId('remote-node-grant-all')).textContent)
      .toContain('先更新客户端')
  })

  /**
   * 远程一键的全部要害都在这三条里：控制端凭什么相信这次开窗请求、
   * 旧客户端会不会因此彻底开不了、以及"从没配对过"这件事有没有被说清楚。
   */
  it('asks the controller for a pairing window with the node-signed envelope', async () => {
    let current = nodeStatus()
    const signPairingOpen = vi.fn(async (mode?: string) => ({
      nodeId: 'desktop-test',
      envelope: {
        mode: mode ?? 'readonly-l1',
        auth: { node_id: 'desktop-test', path: '/api/desktop-nodes/pairing/open' },
        signature: 'sig',
      },
    }))
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        pairing_id: 'pair-signed',
        code: '87654321',
        controller_fingerprint: 'd'.repeat(64),
        expires_at_ms: Date.now() + 300_000,
        bound_node_id: 'desktop-test',
        bound_mode: 'readonly-l1',
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async () => current),
      saveNodeGrant: vi.fn(async () => {
        current = nodeStatus({
          configured: true,
          grantId: 'grant-signed',
          generation: 1,
          root: ROOT,
          tools: TOOLS,
        })
        return current
      }),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async () => {
        current = { ...current, enabled: true, paused: false }
        return { passed: true, enabled: true, checks: [{ id: 'git.job', passed: true, message: 'ok', durationMs: 4 }] }
      }),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(async () => transportStatus({ paired: true, connected: true })),
      signPairingOpen,
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键开启只读节点' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(signPairingOpen).toHaveBeenCalledWith('readonly-l1')
    // 页面还会轮询 /bootstrap 看降级状态，所以按 URL 挑出开窗那一次，而不是按顺序。
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][]
    const [url, init] = calls.find((call) => call[0].endsWith('/pairing/open'))!
    expect(url).toBe(`${window.location.origin}/api/desktop-nodes/pairing/open`)
    // 签名覆盖的是这个 body 的摘要，页面必须原样投递，多一个字段都不行。
    expect(JSON.parse(String(init.body))).toEqual({
      mode: 'readonly-l1',
      auth: { node_id: 'desktop-test', path: '/api/desktop-nodes/pairing/open' },
      signature: 'sig',
    })
  })

  it('still posts an unsigned open on a client that cannot sign yet', async () => {
    let current = nodeStatus()
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        pairing_id: 'pair-plain',
        code: '11112222',
        controller_fingerprint: 'd'.repeat(64),
        expires_at_ms: Date.now() + 300_000,
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async () => current),
      saveNodeGrant: vi.fn(async () => {
        current = nodeStatus({ configured: true, grantId: 'g', generation: 1, root: ROOT, tools: TOOLS })
        return current
      }),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async () => {
        current = { ...current, enabled: true, paused: false }
        return { passed: true, enabled: true, checks: [{ id: 'git.job', passed: true, message: 'ok', durationMs: 4 }] }
      }),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(async () => transportStatus({ paired: true, connected: true })),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键开启只读节点' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][]
    const [, init] = calls.find((call) => call[0].endsWith('/pairing/open'))!
    expect(init.body).toBeUndefined()
  })

  it('reads node_not_enrolled as the one-time first-pairing step, not a failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 403,
      json: async () => ({ ok: false, error: 'node_not_enrolled' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async () => nodeStatus({
        configured: true,
        grantId: 'g',
        generation: 1,
        root: ROOT,
        tools: TOOLS,
        enabled: true,
        paused: false,
      })),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async () => ({
        passed: true,
        enabled: true,
        checks: [{ id: 'git.job', passed: true, message: 'ok', durationMs: 4 }],
      })),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(),
      signPairingOpen: vi.fn(async () => ({ nodeId: 'desktop-test', envelope: { mode: 'readonly-l1' } })),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键恢复并连接' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('omni desktop-node pairing')
    expect(alert.textContent).toContain('每台机器只需要做一次')
  })

  /**
   * 0.1.22 上真实发生的那一幕：签名调用被客户端自己的 ACL 拒了，页面把异常吞掉、
   * 改发裸 POST，控制端回一句 `local_controller_required`，前端译成"版本过旧"。
   * 三层里没有一层说了实话。现在最终那句话必须带上签名失败的真实原因。
   */
  it('names the signing failure instead of blaming the client version', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 403,
      json: async () => ({ ok: false, error: 'local_controller_required' }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async () => nodeStatus({
        configured: true, grantId: 'g', generation: 1, root: ROOT, tools: TOOLS, enabled: true, paused: false,
      })),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(async () => ({
        passed: true,
        enabled: true,
        checks: [{ id: 'git.job', passed: true, message: 'ok', durationMs: 4 }],
      })),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(),
      signPairingOpen: vi.fn(async () => {
        throw new Error('remote_node_sign_pairing_open not allowed. Permissions associated with this command: none')
      }),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)
    fireEvent.click(await screen.findByRole('button', { name: '一键恢复并连接' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('not allowed')
    expect(alert.textContent).toContain('本机没能签出自证请求')
    // 这才是要害：不能再把签不出来说成版本问题以外的东西，也不能只留控制端那句。
    expect(alert.textContent).not.toContain('控制端只接受来自本机')
  })

  it('says out loud that the controller is in a bootstrap window', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => (String(url).endsWith('/bootstrap')
        ? { ok: true, bootstrap: { open: true, expires_at_ms: Date.now() + 600_000 } }
        : { ok: true }),
    })))
    window.__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      openSystemUrl: vi.fn(),
      selectNodeRoot: vi.fn(async () => ROOT),
      inspectNodeRoot: vi.fn(async (root: string, mode?: string) => inspectByMode(root, mode)),
      nodeStatus: vi.fn(async () => nodeStatus()),
      saveNodeGrant: vi.fn(),
      selfTestAndEnableNode: vi.fn(),
      quickEnableNode: vi.fn(),
      setNodePaused: vi.fn(),
      revokeNode: vi.fn(),
      transportStatus: vi.fn(async () => transportStatus()),
      pairTrustedServer: vi.fn(),
    } satisfies OmnicompanyHostBridge

    render(<SecurityNodeTab />)

    const banner = await screen.findByTestId('remote-node-bootstrap-window')
    expect(banner.textContent).toContain('安全性降级中')
    expect(banner.textContent).toContain('bootstrap --close')
  })
})
