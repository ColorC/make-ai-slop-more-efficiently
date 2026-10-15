import { useCallback, useEffect, useMemo, useState } from 'react'
import { useBackgroundPoll } from '../../shell/panelRuntime'
import {
  CheckCircle2,
  FolderOpen,
  Pause,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Trash2,
  XCircle,
} from 'lucide-react'
import {
  desktopHostBridge,
  type RemoteNodeMode,
  type RemoteNodeResourceLimits,
  type RemoteNodeRootInspection,
  type RemoteNodeSelfTestReport,
  type RemoteNodeStatus,
  type RemoteNodeTool,
  type RemoteNodeTransportStatus,
} from '../../lib/workspaceNavigation'

const READONLY: RemoteNodeMode = 'readonly-l1'
const TRUSTED_EXEC: RemoteNodeMode = 'trusted-exec-l2'
const MODE_ORDER: RemoteNodeMode[] = [READONLY, TRUSTED_EXEC]

interface LimitBound {
  min: number
  max: number
}

interface ModeProfile {
  label: string
  tagline: string
  defaultLimits: RemoteNodeResourceLimits
  bounds: Record<keyof RemoteNodeResourceLimits, LimitBound>
  policy: [string, string][]
  /** Shown before the mode is enabled. Only execution has one. */
  caution: string | null
  toolHint: string
  /**
   * 这一档的自检真的会执行的 program_id, 抄自节点侧 `remote_node.rs:807-846`
   * 那个 `match (mode, program_id)`：执行档只认 pwsh / powershell / cmd / bash
   * (`:811-825`, 其余 `:826` 一律 `continue`), 只读档只认 git / rg
   * (`:827-844`, 其余 `:845` 一律 `continue`)。
   *
   * 节点要求至少有一条工具执行检查跑起来才判通过(`remote_node.rs:897-898`),
   * 所以不在这张表里的工具授权了也是空授权 —— 一键开启据此决定每一档要哪些工具,
   * 不去问用户, 也不让某一档继承另一档已配的工具集。顺序即偏好序。
   */
  selfTestPrograms: string[]
}

/** 探测到的 program_id 对应的人话名字, 与 `remote_node.rs:1394-1404` 的 display_name 对齐。 */
const PROGRAM_LABELS: Record<string, string> = {
  git: 'Git',
  rg: 'ripgrep',
  pwsh: 'PowerShell 7 (pwsh)',
  powershell: 'Windows PowerShell (powershell.exe)',
  cmd: '命令提示符 (cmd.exe)',
  bash: 'Bash (bash.exe)',
}

const MODE_PROFILES: Record<RemoteNodeMode, ModeProfile> = {
  [READONLY]: {
    label: '只读节点',
    tagline: '只读 L1 · 默认拒绝 · 无入站端口 · 完整 Job Object 进程树治理',
    defaultLimits: {
      timeoutMs: 30_000,
      maxMemoryMb: 512,
      maxOutputBytes: 512 * 1024,
      maxProcesses: 4,
      cpuRatePercent: 25,
    },
    bounds: {
      timeoutMs: { min: 1, max: 120 },
      maxMemoryMb: { min: 64, max: 2_048 },
      maxOutputBytes: { min: 16, max: 2_048 },
      maxProcesses: { min: 1, max: 16 },
      cpuRatePercent: { min: 5, max: 75 },
    },
    policy: [
      ['目录能力', 'fs.read'],
      ['进程能力', 'process.spawn'],
      ['网络', 'deny'],
      ['写入 / 删除 / shell', '禁止'],
    ],
    caution: null,
    toolHint: '选择并检查目录后，客户端会探测 Git 与 ripgrep 的绝对路径并计算哈希。',
    selfTestPrograms: ['git', 'rg'],
  },
  [TRUSTED_EXEC]: {
    label: '执行节点',
    tagline: '任意命令 · 可写 · 等价于 ssh host \'cmd\' · 独立授权，可单独吊销',
    // 上限由节点侧的 `validate_limits`(windows_job.rs:388) 说了算, 不是这里说了算。
    // 原先这一档默认 600s / 4GiB / 8MiB / 128 进程 / 90% CPU —— 五项全部超出校验范围,
    // 于是执行档自检必然报 "resource limits are outside the safe range", 从落地起就没通过过。
    // 现在取校验器允许的**上限**: 「一键开满」= 开到真正允许的顶, 不是开到一个会被拒的数。
    defaultLimits: {
      timeoutMs: 120_000,
      maxMemoryMb: 2_048,
      maxOutputBytes: 2 * 1024 * 1024,
      maxProcesses: 16,
      cpuRatePercent: 75,
    },
    // 和 validate_limits 逐项对齐, 免得设置页放行一个节点侧必拒的值。
    bounds: {
      timeoutMs: { min: 1, max: 120 },
      maxMemoryMb: { min: 64, max: 2_048 },
      maxOutputBytes: { min: 16, max: 2_048 },
      maxProcesses: { min: 1, max: 16 },
      cpuRatePercent: { min: 5, max: 75 },
    },
    policy: [
      ['目录能力', 'fs.read + fs.write'],
      ['进程能力', 'process.exec（任意命令）'],
      ['网络', 'inherit（无法阻止）'],
      ['命令过滤', '无 —— 挡 rm 却放行 python -c 是自欺'],
    ],
    caution:
      '执行节点下，授权目录只是工作目录，不是隔离边界。任意命令一旦运行，就能读写这台机器上该 Windows 账户能碰的一切，也能联网。'
      + 'Job Object 仍然限住 CPU、内存、进程数和运行时长，并保证整棵进程树可被杀死——但它约束的是资源，不是可达范围。',
    toolHint: '执行节点批准的是启动器（PowerShell / cmd / bash），npm、cargo 这些通过 shell 触达，不逐个登记。',
    // 一键默认只批 Windows 原生启动器。bash 不进默认集是实测结论：Git Bash 的自检命令
    // 走 `bash -lc`, 会加载登录 profile, profile 里任何一句非零退出都让整条检查 exit=1
    // (实测 exit=1 / stdout=270B)。它坏不坏是它自己的事, 不该否决整个执行档 —— 而自检
    // 的判定是「逐条都要过」, 所以把它留在默认集里等于让一个可选启动器一票否决。
    // 需要 bash 的人仍可在「高级设置 → 2 · 工具授权」手工勾选。
    selfTestPrograms: ['pwsh', 'powershell', 'cmd'],
  },
}

/**
 * 探测结果里, 这一档自检认得的那些工具。
 *
 * 探测本身走 `inspectNodeRoot(root, mode)` —— 候选集本来就是 mode 的纯函数
 * (`remote_node.rs:1391-1405`: 只读档只找 git/rg, 执行档另加四个启动器),
 * 这里再按自检真的会跑的分支筛一遍, 免得把授权发给一个自检会跳过的工具。
 */
function selfTestablePrograms(target: RemoteNodeMode, tools: RemoteNodeTool[]): string[] {
  const detected = new Set(tools.map((tool) => tool.programId))
  return MODE_PROFILES[target].selfTestPrograms.filter((program) => detected.has(program))
}

/**
 * 存下来的上限是不是超出了这一档允许的范围。
 *
 * 节点侧 `validate_limits`(windows_job.rs:388) 是最终裁决者, 越界的 grant 自检必然报
 * "resource limits are outside the safe range"。这里用设置页声明的 `bounds` 判断 ——
 * 两者已逐项对齐, 所以设置页放行的值节点侧一定收。
 */
function limitsOutOfBounds(
  target: RemoteNodeMode,
  limits: RemoteNodeResourceLimits | null | undefined,
): boolean {
  if (!limits) return false
  const bounds = MODE_PROFILES[target].bounds
  return limits.timeoutMs / 1000 > bounds.timeoutMs.max
    || limits.maxMemoryMb > bounds.maxMemoryMb.max
    || limits.maxOutputBytes / 1024 > bounds.maxOutputBytes.max
    || limits.maxProcesses > bounds.maxProcesses.max
    || limits.cpuRatePercent > bounds.cpuRatePercent.max
}

/** 一条工具都探测不到时说人话：说清缺的是什么、去哪补, 而不是把人打发去设置页勾选。 */
function describeMissingTools(target: RemoteNodeMode): string {
  const profile = MODE_PROFILES[target]
  const names = profile.selfTestPrograms.map((program) => PROGRAM_LABELS[program] ?? program).join(' / ')
  const remedy = target === TRUSTED_EXEC
    ? '执行档批准的是启动器，自检必须能真的启动其中至少一个。'
      + 'Windows 自带的 powershell.exe 和 cmd.exe 通常在 C:\\Windows\\System32 下——'
      + '把该目录（或你想用的那个 shell 所在目录）加进 PATH 再点一次。'
    : '只读档需要 Git 或 ripgrep 之中至少一个。装上并确保它在 PATH 上，再点一次。'
  return `${profile.label}开不起来：这台机器上没找到 ${names}。${remedy}`
    + '（客户端只在自己启动时继承的 PATH 里找，改完 PATH 要重启客户端才看得到。）'
}

/**
 * 本机这一侧「能不能签出自证请求」的结局。
 *
 * 分三种是因为它们的下一步完全不同: 签出来了(问题在控制端)、这个客户端根本没有
 * 这个能力(浏览器 / 旧客户端)、有能力但调用失败(带原因)。以前三种一律吞成
 * `null`, 于是控制端的 `local_controller_required` 被前端译成"版本过旧"——
 * 而真实原因可能是签名调用被客户端自己的 ACL 拒了, 跟版本无关。
 */
type PairingSignAttempt =
  | { kind: 'signed' }
  | { kind: 'unavailable' }
  | { kind: 'failed', detail: string }

/** 首次注册的两条出路, 两句话都要给全: 带外一次, 或临时窗口。 */
const FIRST_ENROLLMENT_REMEDY =
  '两条路二选一：'
  + '① 带外一次——在控制端跑 `omni desktop-node pairing`，把 8 位码填进'
  + '「高级设置 → 4 · 控制端配对」，点「确认指纹并配对」；'
  + '② 临时窗口——在控制端跑 `omni desktop-node bootstrap --open --minutes 30`，'
  + '然后回来再点一次一键（窗口到点自动关，也可以 `--close` 立刻关）。'

/**
 * 控制端拒绝开配对窗口时说人话。
 *
 * 这里最要紧的一条是 `node_not_enrolled`: 它不是故障, 是**首次信任**那一次
 * 带外动作还没做过 —— 一台机器一辈子只需要做一次, 之后节点用自己的密钥自证,
 * 远端一键就够了。把它和"配对被拒"混成一句会让人以为是坏了。
 *
 * 第二要紧的是 `local_controller_required` 必须**分辨本机签名失败与控制端拒绝**:
 * 这一条只有在请求里根本没有自证信封时才会出现, 所以它真正的信息量在 `attempt`
 * 里, 不在 error 里。
 */
function describePairingOpenFailure(error: string, attempt: PairingSignAttempt): string {
  if (error === 'node_not_enrolled') {
    return `控制端还不认识这台机器（首次注册）。${FIRST_ENROLLMENT_REMEDY}`
      + '这一步每台机器只需要做一次——之后加档、换目录都能在这里一键完成。'
  }
  if (error === 'node_revoked') {
    return '这台节点已被控制端吊销，不能自己把自己放回来。'
      + '重新接纳必须由控制端发起：在控制端跑 `omni desktop-node pairing` 拿一个配对码。'
  }
  if (error === 'node_clock_skew') {
    return '本机时钟与控制端相差超过 5 分钟，签名被判过期。校准系统时间后再点一次。'
  }
  if (error === 'local_controller_required') {
    // 走到这里说明请求里没有自证信封——所以先说本机为什么没签出来，再说控制端。
    if (attempt.kind === 'unavailable') {
      return '这个客户端没有「节点自证」能力：本页是在浏览器里、或者桌面客户端太旧，'
        + `没有 signPairingOpen 这座桥。控制端于是只看到一个匿名请求并拒绝了。${FIRST_ENROLLMENT_REMEDY}`
    }
    if (attempt.kind === 'failed') {
      return `本机没能签出自证请求，控制端因此只收到一个匿名请求并拒绝了。签名失败的原因：${attempt.detail}。`
        + '（如果这句话里写着 not allowed / not in the allow list，那是桌面客户端自己的 ACL 没放行'
        + ' remote_node_sign_pairing_open，需要装新版客户端，不是控制端的问题。）'
        + FIRST_ENROLLMENT_REMEDY
    }
    return '控制端只接受来自本机（或持网关私密）的开窗请求，而这次请求没有带上节点自证信封。'
      + FIRST_ENROLLMENT_REMEDY
  }
  const signed = attempt.kind === 'signed'
    ? '本机签名是成功的，所以这是控制端那一侧的拒绝。'
    : ''
  return `控制端拒绝开配对窗口：${error}。${signed}`
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '未知'
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GiB`
}

function statusLabel(status: RemoteNodeStatus | null, transport: RemoteNodeTransportStatus | null): string {
  if (!status?.configured) return '未开启'
  if (!status.enabled) return '等待自检'
  if (status.paused) return '已暂停'
  if (transport?.connected) return '在线'
  if (transport?.paired && !transport.revoked) return '连接中'
  return '已开启 · 未连接'
}

function isOnline(status: RemoteNodeStatus | null, transport: RemoteNodeTransportStatus | null): boolean {
  return Boolean(status?.enabled && !status.paused && transport?.connected)
}

/** 把节点侧的 check id 翻成「哪一类检查」，好让一行错误就能指出该去修什么。 */
function selfTestCheckLabel(id: string): string {
  if (id === 'root') return '执行根目录'
  if (id.endsWith('.hash')) return `${id.slice(0, -'.hash'.length)} 哈希`
  if (id.endsWith('.job')) return `${id.slice(0, -'.job'.length)} 执行`
  return id
}

/**
 * 自检失败时把 `report.checks` 里的明细拼进异常，而不是只丢一句"未通过"。
 *
 * 三种失败彼此要修的东西完全不同：根目录变了要重选目录、工具哈希变了要重新授权、
 * 工具跑不起来要看那条命令本身。只说"未通过"等于把这三者的区别全扔掉。
 */
function describeSelfTestFailure(
  target: RemoteNodeMode,
  report: RemoteNodeSelfTestReport,
): string {
  const label = MODE_PROFILES[target].label
  const failed = report.checks.filter((check) => !check.passed)
  if (failed.length > 0) {
    const detail = failed
      .map((check) => `${selfTestCheckLabel(check.id)} —— ${check.message}`)
      .join('；')
    return `${label}本机安全自检未通过（${failed.length}/${report.checks.length} 项失败）：${detail}`
  }
  if (!report.passed) {
    // 每一条列出来的检查都过了却仍判失败, 只剩一种可能: 一条工具执行检查都没跑起来。
    // 节点侧要求至少有一条 `*.job`（remote_node.rs:866-867）, 否则"通过"只是空过。
    const seen = report.checks.map((check) => check.id).join('、') || '无'
    const need = target === TRUSTED_EXEC
      ? '执行档需要 PowerShell / cmd / bash 之中至少一个被授权且能启动'
      : '只读档需要 Git 或 ripgrep 之中至少一个被授权'
    return `${label}本机安全自检未通过：没有任何工具执行检查跑起来（已跑的检查：${seen}）。${need}`
      + '——请在「高级设置 → 2 · 工具授权」里确认勾选的工具，必要时重新检查目录。'
  }
  // passed 为真、enabled 为假 = 自检本身没问题, 是启用那一步没被确认。
  return `${label}自检已通过但没有启用：原生确认框被取消或没有确认。再点一次并选择「启用节点」。`
}

export default function SecurityNodeTab() {
  const host = useMemo(() => desktopHostBridge(), [])
  const desktopAvailable = Boolean(
    host?.selectNodeRoot
    && host.inspectNodeRoot
    && host.nodeStatus
    && host.saveNodeGrant
    && host.selfTestAndEnableNode
    && host.setNodePaused
    && host.revokeNode,
  )
  const quickAvailable = Boolean(
    host?.quickEnableNode
    && host.pairTrustedServer
    && host.transportStatus,
  )
  // A client that predates the two-mode bridge can still drive the read-only
  // node; it must not be offered an execution row it cannot reach.
  const execAvailable = Boolean(host?.nodeModes?.exec)

  const [mode, setMode] = useState<RemoteNodeMode>(READONLY)
  const [statuses, setStatuses] = useState<Partial<Record<RemoteNodeMode, RemoteNodeStatus>>>({})
  const [root, setRoot] = useState('')
  const [inspection, setInspection] = useState<RemoteNodeRootInspection | null>(null)
  const [selectedPrograms, setSelectedPrograms] = useState<string[]>([])
  const [limits, setLimits] = useState<RemoteNodeResourceLimits>(MODE_PROFILES[READONLY].defaultLimits)
  const [selfTest, setSelfTest] = useState<RemoteNodeSelfTestReport | null>(null)
  const [transport, setTransport] = useState<RemoteNodeTransportStatus | null>(null)
  const [controllerUrl, setControllerUrl] = useState(() => (
    typeof window === 'undefined' ? 'http://127.0.0.1:8210' : window.location.origin
  ))
  const [pairingId, setPairingId] = useState('')
  const [pairingCode, setPairingCode] = useState('')
  const [nodeLabel, setNodeLabel] = useState('Omnicompany Desktop')
  const [controllerFingerprint, setControllerFingerprint] = useState('')
  const [pairingExpiresAt, setPairingExpiresAt] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 控制端此刻是不是开着 bootstrap 临时窗口。开着 = 安全性降级, 必须在页面上看得见,
  // 而不是只有开窗的人自己知道。
  const [bootstrapWindow, setBootstrapWindow] = useState(false)
  const [bootstrapExpiresAt, setBootstrapExpiresAt] = useState<number | null>(null)

  const profile = MODE_PROFILES[mode]
  const status = statuses[mode] ?? null

  const applyStatus = useCallback((next: RemoteNodeStatus, forMode: RemoteNodeMode) => {
    setStatuses((current) => ({ ...current, [forMode]: next }))
    if (forMode !== mode) return
    setRoot(next.root ?? '')
    setLimits(next.configured ? next.limits : MODE_PROFILES[forMode].defaultLimits)
    setSelectedPrograms(next.tools.map((tool) => tool.programId))
  }, [mode])

  const refreshStatus = useCallback(async () => {
    if (!host?.nodeStatus) return
    const wanted = execAvailable ? MODE_ORDER : [READONLY]
    for (const candidate of wanted) {
      const next = await host.nodeStatus(candidate)
      setStatuses((current) => ({ ...current, [candidate]: next }))
      if (candidate === mode) {
        setRoot(next.root ?? '')
        setLimits(next.configured ? next.limits : MODE_PROFILES[candidate].defaultLimits)
        setSelectedPrograms(next.tools.map((tool) => tool.programId))
      }
    }
    if (host?.transportStatus) setTransport(await host.transportStatus())
  }, [host, execAvailable, mode])

  useEffect(() => {
    if (!desktopAvailable) return
    void refreshStatus().catch((reason) => setError(String(reason)))
  }, [desktopAvailable, refreshStatus])

  /**
   * 控制端的 bootstrap 窗口状态。
   *
   * 这个端点对"已经过了网关"的调用方都可读, 就是为了让降级状态在用的人眼前 ——
   * 只有开窗的人看得见的降级等于没人看得见。读不到(旧控制端没有这个端点 / 网络不通)
   * 时当作关着: 端点不存在就没有这条路。
   */
  const refreshBootstrap = useCallback(async () => {
    const normalized = controllerUrl.trim().replace(/\/$/, '')
    if (!normalized) return
    try {
      const response = await fetch(`${normalized}/api/desktop-nodes/bootstrap`, {
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) return
      const payload = await response.json() as { bootstrap?: { open?: boolean, expires_at_ms?: number } }
      setBootstrapWindow(Boolean(payload.bootstrap?.open))
      setBootstrapExpiresAt(Number(payload.bootstrap?.expires_at_ms) || null)
    } catch {
      // 读不到就不改显示 —— 猜"开着"会造成假警报, 猜"关着"会掩盖真降级。
    }
  }, [controllerUrl])

  useEffect(() => {
    void refreshBootstrap()
  }, [refreshBootstrap])
  // 后台节流(2026-08-07 页签常驻): 设置页签切走就停, 切回来立刻补一次。
  useBackgroundPoll(refreshBootstrap, 30_000, { leading: false })

  useBackgroundPoll(
    () => host?.transportStatus?.().then(setTransport).catch((reason) => setError(String(reason))),
    5_000,
    { enabled: desktopAvailable && Boolean(host?.transportStatus), leading: false },
  )

  const execute = useCallback(async (label: string, action: () => Promise<void>) => {
    setBusy(label)
    setError(null)
    try {
      await action()
    } catch (reason) {
      setError(String(reason))
    } finally {
      setBusy(null)
    }
  }, [])

  /**
   * 开一个 5 分钟配对窗口。
   *
   * 控制端不认网络位置以外的东西, 而这台桌面端多半不在控制端那台机器上 ——
   * 所以先让节点用自己的私钥签一个请求体(`signPairingOpen`), 再由这个页面发出去。
   * 页面发而不是 Rust 发, 是因为 `/pairing/open` 在网关的鉴权后面, 而只有页面
   * 持有这个 origin 的会话; Rust 直连会被 401。签名管"我是谁", 会话管"能不能进门"。
   *
   * 拿不到签名(旧客户端 / 客户端 ACL 没放行 / 这台机器从没被控制端认过)时仍然发
   * 裸 POST: 控制端本机的浏览器靠回环、bootstrap 窗口开着时靠窗口, 都还能走通。
   * 但**签名失败的原因要带到最后那句错误里** —— 控制端只会说"你不是本机",
   * 它并不知道本机这边为什么签不出来, 而那才是真正要修的东西。
   */
  const openControllerPairing = async (target?: RemoteNodeMode) => {
    const normalized = controllerUrl.trim().replace(/\/$/, '')
    let body: Record<string, unknown> | null = null
    let attempt: PairingSignAttempt = { kind: 'unavailable' }
    if (host?.signPairingOpen) {
      try {
        body = (await host.signPairingOpen(target ?? mode)).envelope
        attempt = { kind: 'signed' }
      } catch (reason) {
        attempt = { kind: 'failed', detail: String((reason as Error)?.message ?? reason) }
        body = null
      }
    }
    const response = await fetch(`${normalized}/api/desktop-nodes/pairing/open`, {
      method: 'POST',
      headers: body
        ? { Accept: 'application/json', 'Content-Type': 'application/json' }
        : { Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    const payload = await response.json().catch(() => ({})) as Record<string, unknown>
    if (!response.ok || payload.ok !== true) {
      throw new Error(
        describePairingOpenFailure(String(payload.error || `HTTP ${response.status}`), attempt),
      )
    }
    // 控制端说这个窗口是 bootstrap 窗口给的 —— 那就在页面上说出来, 别让降级无声。
    setBootstrapWindow(payload.bootstrap === true)
    setPairingId(String(payload.pairing_id || ''))
    setPairingCode(String(payload.code || ''))
    setControllerFingerprint(String(payload.controller_fingerprint || ''))
    setPairingExpiresAt(Number(payload.expires_at_ms) || null)
    return {
      pairingId: String(payload.pairing_id || ''),
      code: String(payload.code || ''),
    }
  }

  /**
   * 降级横幅。开着的时候两条渲染路径都要看得见 —— 浏览器版也是"能开窗的那一方"。
   */
  const bootstrapBanner = bootstrapWindow ? (
    <section className="st-node-message error" role="status" data-testid="remote-node-bootstrap-window">
      <ShieldAlert size={18} aria-hidden="true" />
      <span>
        控制端正处于 <b>bootstrap 临时窗口</b>（安全性降级中）：这期间任何已通过网关的调用方
        都能开一个配对窗口，不需要是控制端本机。
        {bootstrapExpiresAt ? `窗口有效至 ${new Date(bootstrapExpiresAt).toLocaleTimeString()}，到点自动关闭。` : '到点自动关闭。'}
        配对完成后请在控制端立刻跑 <code>omni desktop-node bootstrap --close</code>。
      </span>
    </section>
  ) : null

  if (!desktopAvailable) {
    return (
      <div className="st-node-page">
        {bootstrapBanner}
        <section className="st-node-browser" data-testid="remote-node-browser-note">
          <ShieldAlert size={22} aria-hidden="true" />
          <span className="st-row-t">
            <span className="t1">浏览器版没有本机执行权限</span>
            <span className="t2">本地目录、工具和进程树只能由桌面客户端授权；浏览器可以作为控制端创建一次性配对码。</span>
          </span>
        </section>
        <div className="st-sec">控制端 · 一次性配对</div>
        <section className="st-group st-node-pairing">
          <label>控制端地址<input value={controllerUrl} onChange={(event) => setControllerUrl(event.target.value)} /></label>
          <button className="st-btn" type="button" disabled={Boolean(busy)} onClick={() => void execute('pairing-window', async () => { await openControllerPairing() })}>生成 5 分钟配对码</button>
          {pairingCode && <div className="st-node-pair-code"><b>{pairingCode}</b><span>Pairing ID：{pairingId}</span><span>控制端指纹：{controllerFingerprint}</span></div>}
        </section>
        {error && <div className="st-node-message error" role="alert">{error}</div>}
      </div>
    )
  }

  const inspect = async (candidate: string) => {
    if (!host?.inspectNodeRoot || !candidate.trim()) return
    const result = await host.inspectNodeRoot(candidate.trim(), mode)
    setInspection(result)
    setRoot(result.canonicalPath)
    setSelectedPrograms(result.tools.map((tool) => tool.programId))
    setSelfTest(null)
  }

  const updateLimit = (key: keyof RemoteNodeResourceLimits, value: string) => {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) return
    setLimits((current) => ({ ...current, [key]: Math.round(parsed) }))
  }

  const selectMode = (next: RemoteNodeMode) => {
    setMode(next)
    setInspection(null)
    setSelfTest(null)
    const existing = statuses[next]
    setRoot(existing?.root ?? '')
    setLimits(existing?.configured ? existing.limits : MODE_PROFILES[next].defaultLimits)
    setSelectedPrograms(existing?.tools.map((tool) => tool.programId) ?? [])
  }

  /**
   * 把一档从「什么都没有」带到「已授权 → 已自检 → 已配对」。
   *
   * 这一档需要哪些工具由代码决定, 不要求用户预先在高级设置里勾好：
   * 每次都按 `target` 自己探测一遍(`inspectNodeRoot` 的候选集是 mode 的函数),
   * 再用 `selfTestablePrograms` 只留下这一档自检真的会执行的那些。
   * 所以执行档不会继承只读档的 git / ripgrep, 只读档也不会平白多出 PowerShell,
   * 而一个「配过但配错了工具」的旧草案会被就地纠正, 不需要用户去翻设置页。
   */
  const quickEnable = async (target: RemoteNodeMode, preferredRoot?: string) => {
    let nextStatus = statuses[target] ?? await host!.nodeStatus!(target)
    // 目录：已配置的那个优先, 其次调用方给的共享目录, 其次输入框, 实在没有才弹一次系统框。
    const candidateRoot = nextStatus.root?.trim()
      || preferredRoot
      || (target === mode ? root.trim() : '')
      || await host!.selectNodeRoot!()
    if (!candidateRoot) return

    let probe: RemoteNodeRootInspection
    try {
      probe = await host!.inspectNodeRoot!(candidateRoot, target)
    } catch (reason) {
      throw new Error(
        `${MODE_PROFILES[target].label}的授权目录 ${candidateRoot} 过不了安全检查（${String(reason)}）`
        + '——在「高级设置 → 1 · 执行根目录」重选一个项目目录再试。',
      )
    }
    if (!probe.safe) {
      throw new Error(`${MODE_PROFILES[target].label}的授权目录 ${candidateRoot} 没有通过安全检查`)
    }
    const approvedPrograms = selfTestablePrograms(target, probe.tools)
    if (approvedPrograms.length === 0) throw new Error(describeMissingTools(target))
    if (target === mode) {
      setInspection(probe)
      setRoot(probe.canonicalPath)
      setSelectedPrograms(approvedPrograms)
    }

    // 重存会 bump generation 并把这一档打回未启用, 所以只在真的开不起来时才重存：
    // 目录变了, 或者已配的工具里一个都不是这一档自检认得的(那种 grant 自检必然空过,
    // 再点多少次都不会自愈)。已经有可用工具的 grant 不动 —— 探测结果比它多一个
    // 启动器, 不构成作废一个正常 grant 的理由。
    // 还有一种「再点多少次都不会自愈」：存下来的上限本身超出节点侧 validate_limits 的
    // 允许范围。执行档曾经默认 600s/4GiB/128 进程, 全部越界, 那种 grant 自检必然报
    // "resource limits are outside the safe range"。默认值改对了也没用——不重存就永远
    // 用着旧值。所以越界也算「开不起来」, 要重存。
    // 存下来的工具集和这一档现在该批的不一致时也要重存。否则一旦某个启动器被移出默认集
    // (例如 bash 因为 profile 报错一票否决整档), 旧 grant 里那一条会一直留着继续否决。
    const configuredUsable = selfTestablePrograms(target, nextStatus.tools)
    // 比的是**原始存储**的工具集, 不是过滤后的 —— 被移出默认集的那条恰恰会被过滤掉,
    // 拿过滤后的集合去比永远比不出漂移, 那条也就永远清不掉。
    const configuredIds = (nextStatus.tools ?? []).map((tool) => tool.programId)
    const toolSetDrifted = configuredIds.length !== approvedPrograms.length
      || approvedPrograms.some((program) => !configuredIds.includes(program))
    const needsSave = !nextStatus.configured
      || nextStatus.root !== probe.canonicalPath
      || configuredUsable.length === 0
      || toolSetDrifted
      || limitsOutOfBounds(target, nextStatus.limits)
    if (needsSave) {
      nextStatus = await host!.saveNodeGrant!({
        root: probe.canonicalPath,
        approvedPrograms,
        limits: MODE_PROFILES[target].defaultLimits,
        mode: target,
      })
      applyStatus(nextStatus, target)
    }

    const report = await host!.quickEnableNode!(target)
    setSelfTest(report)
    if (!report.passed || !report.enabled) throw new Error(describeSelfTestFailure(target, report))
    applyStatus(await host!.nodeStatus!(target), target)

    // Each mode registers through its own pairing window, so enabling execution
    // never rides in on the read-only pairing.
    const normalizedController = controllerUrl.trim().replace(/\/$/, '')
    const pairing = await openControllerPairing(target)
    setTransport(await host!.pairTrustedServer!({
      controllerUrl: normalizedController,
      pairingId: pairing.pairingId,
      code: pairing.code,
      label: nodeLabel,
      mode: target,
    }))
    await refreshStatus()
  }

  const availableModes = execAvailable ? MODE_ORDER : [READONLY]

  // 「所有权限」= 这台机器上每一档授权都配好、自检通过、没暂停, 并且已经向控制端登记。
  // 它不是一个新档位, 也不新增任何能力 —— 只是把现有档位一次开满。
  const modeReady = (candidate: RemoteNodeMode): boolean => {
    const candidateStatus = statuses[candidate]
    return Boolean(candidateStatus?.configured && candidateStatus.enabled && !candidateStatus.paused)
  }
  const readyModes = availableModes.filter(modeReady)
  const allGranted = readyModes.length === availableModes.length && Boolean(transport?.paired && !transport.revoked)
  const grantLevel = allGranted ? 'all' : readyModes.length > 0 ? 'partial' : 'none'
  const grantSummary = allGranted
    ? `全部权限已开启 · ${availableModes.map((candidate) => MODE_PROFILES[candidate].label).join(' + ')}`
    : readyModes.length > 0
      ? `部分开启 · ${readyModes.map((candidate) => MODE_PROFILES[candidate].label).join(' + ')}`
      : '未开启任何权限'

  /**
   * 一次把所有档位开满。用户要做的只有一件事：目录最多问一次（还常常不问，
   * 因为会复用已配置那一档的根）。工具由每一档自己探测、自己挑，不复用别档的。
   */
  const grantAll = async () => {
    const sharedRoot = availableModes
      .map((candidate) => statuses[candidate]?.root)
      .find((value) => Boolean(value && value.trim()))
      ?? (root.trim() || await host!.selectNodeRoot!())
    if (!sharedRoot) return
    for (const target of availableModes) {
      if (modeReady(target) && transport?.paired && !transport.revoked) continue
      await quickEnable(target, sharedRoot)
    }
    await refreshStatus()
  }

  /** 一次收回所有档位。每一档仍各自弹一次原生确认——撤销要看得见。 */
  const revokeAll = async () => {
    for (const target of availableModes) {
      if (!statuses[target]?.configured) continue
      applyStatus(await host!.revokeNode!(target), target)
    }
    setInspection(null)
    setSelfTest(null)
    await refreshStatus()
  }

  const nodeOnline = isOnline(status, transport)
  const quickLabel = nodeOnline
    ? `${profile.label}已在线`
    : status?.configured
      ? '一键恢复并连接'
      : `一键开启${profile.label}`

  return (
    <div className="st-node-page" data-testid="remote-node-settings">
      {bootstrapBanner}
      <section className="st-node-summary">
        <span className="st-client-icon"><ShieldCheck size={20} aria-hidden="true" /></span>
        <span className="st-row-t">
          <span className="t1">Remote Execute Node</span>
          <span className="t2">两档授权彼此独立：分别配对、分别暂停、分别吊销。撤销执行节点不会影响只读节点。</span>
        </span>
      </section>

      <section className="st-node-grant-all" data-testid="remote-node-grant-all" data-level={grantLevel}>
        <span className="st-client-icon">
          {allGranted ? <ShieldAlert size={20} aria-hidden="true" /> : <ShieldCheck size={20} aria-hidden="true" />}
        </span>
        <span className="st-row-t">
          <span className="t1" data-testid="remote-node-grant-all-state">{grantSummary}</span>
          <span className="t2">
            {allGranted
              ? '这台机器上两档授权都开着。执行档等价于 ssh host \'cmd\'——随时可以一键收回。'
              : execAvailable
                ? '一次把只读与执行两档都开满：只问一次目录，其余自检、配对全自动。默认仍是收紧的，只有按下这里才会放开。'
                : '当前桌面客户端还没有执行档能力，这里只能开只读档。先更新客户端（设置 → 关于 → 检查更新）再开满。'}
          </span>
        </span>
        <div className="st-node-actions">
          <button
            type="button"
            className="st-btn primary"
            data-testid="remote-node-grant-all-button"
            disabled={Boolean(busy) || allGranted || !quickAvailable}
            onClick={() => void execute('一键开启所有权限', grantAll)}
          >
            <Play size={15} aria-hidden="true" />
            {allGranted ? '已全部开启' : '一键开启所有权限'}
          </button>
          <button
            type="button"
            className="st-btn danger"
            data-testid="remote-node-revoke-all-button"
            disabled={Boolean(busy) || readyModes.length === 0 || !host?.revokeNode}
            onClick={() => void execute('一键收回所有权限', revokeAll)}
          >
            <Trash2 size={15} aria-hidden="true" />一键收回全部
          </button>
        </div>
      </section>

      <section className="st-node-modes" data-testid="remote-node-modes" role="tablist" aria-label="节点授权档位">
        {availableModes.map((candidate) => {
          const candidateStatus = statuses[candidate] ?? null
          const candidateProfile = MODE_PROFILES[candidate]
          const online = isOnline(candidateStatus, transport)
          return (
            <button
              key={candidate}
              type="button"
              role="tab"
              aria-selected={candidate === mode}
              className={`st-node-mode-row${candidate === mode ? ' active' : ''}`}
              data-testid={`remote-node-mode-${candidate}`}
              onClick={() => selectMode(candidate)}
            >
              <span className="st-client-icon">
                {candidate === TRUSTED_EXEC
                  ? <Terminal size={18} aria-hidden="true" />
                  : <ShieldCheck size={18} aria-hidden="true" />}
              </span>
              <span className="st-row-t">
                <span className="t1">{candidateProfile.label}</span>
                <span className="t2">{candidateProfile.tagline}</span>
              </span>
              <span className={`st-status ${online ? 'ready' : 'planned'}`}>
                {statusLabel(candidateStatus, transport)}
              </span>
            </button>
          )
        })}
      </section>

      {profile.caution && !nodeOnline && (
        <section className="st-node-caution" data-testid="remote-node-caution" role="note">
          <ShieldAlert size={18} aria-hidden="true" />
          <span>{profile.caution}</span>
        </section>
      )}

      <section className="st-node-quick" data-testid="remote-node-quick-start">
        <span className="st-row-t">
          <span className="t1">{status?.configured ? (status.root || '已保存授权目录') : `首次开启${profile.label}只需选择一次目录`}</span>
          <span className="t2">
            {mode === TRUSTED_EXEC
              ? '自检会通过已批准的启动器跑一条最小命令，并单独向当前 Server 申请一次配对。'
              : '自动使用 Git / ripgrep、安全默认限额并连接当前 Server；写入、任意 shell 与入站端口仍然禁止。'}
          </span>
        </span>
        <button
          type="button"
          className="st-btn primary st-node-quick-button"
          disabled={Boolean(busy) || nodeOnline || !quickAvailable}
          onClick={() => void execute('一键开启', () => quickEnable(mode))}
        >
          {nodeOnline ? <CheckCircle2 size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          {quickAvailable ? quickLabel : '更新桌面客户端后可一键开启'}
        </button>
      </section>

      {busy && <div className="st-node-message">正在执行：{busy}…</div>}
      {error && <div className="st-node-message error" role="alert">{error}</div>}

      {/* 自检明细留在顶层, 不塞进折叠的高级设置里 —— 自检失败时它是唯一说得清
          "挂在哪一条"的东西, 收进 <details> 等于没有渲染。 */}
      {selfTest && (
        <section className="st-node-selftest" data-testid="remote-node-self-test">
          <strong>{selfTest.passed ? (selfTest.enabled ? '自检通过，节点已启用' : '自检通过，保持关闭') : '自检未通过'}</strong>
          {selfTest.checks.map((check) => (
            <span key={check.id} className={check.passed ? 'ok' : 'bad'}>
              {check.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
              {selfTestCheckLabel(check.id)}：{check.message}
              {check.durationMs == null ? '' : ` · ${check.durationMs} ms`}
            </span>
          ))}
          {selfTest.checks.length === 0 && (
            <span className="bad"><XCircle size={14} />节点没有回报任何检查项</span>
          )}
        </section>
      )}

      <details className="st-node-advanced">
        <summary>高级设置 · {profile.label}</summary>
        <div className="st-node-advanced-body">
      <div className="st-sec">1 · 执行根目录</div>
      <section className="st-group st-node-config">
        <div className="st-node-path-row">
          <input
            className="st-node-path"
            value={root}
            onChange={(event) => setRoot(event.target.value)}
            placeholder="选择一个项目目录；磁盘根、用户主目录、系统目录和 UNC 会被拒绝"
            aria-label="Remote Node 执行根目录"
          />
          <button
            type="button"
            className="st-btn"
            disabled={Boolean(busy)}
            onClick={() => void execute('select', async () => {
              const selected = await host!.selectNodeRoot!()
              if (selected) await inspect(selected)
            })}
          >
            <FolderOpen size={15} aria-hidden="true" />选择目录
          </button>
          <button
            type="button"
            className="st-btn"
            disabled={Boolean(busy) || !root.trim()}
            onClick={() => void execute('inspect', () => inspect(root))}
          >
            <RefreshCw size={14} aria-hidden="true" />重新检查
          </button>
        </div>

        {inspection && (
          <div className="st-node-inspection" data-testid="remote-node-inspection">
            <div className="st-node-facts">
              <span><b>Canonical</b>{inspection.canonicalPath}</span>
              <span><b>文件系统</b>{inspection.filesystem}</span>
              <span><b>剩余空间</b>{formatBytes(inspection.freeBytes)}</span>
              <span><b>项目类型</b>{inspection.projectTypes.join(' / ') || '未识别'}</span>
            </div>
            <div className="st-node-checks">
              {inspection.checks.map((check) => (
                <span key={check.id} className={check.passed ? 'ok' : 'bad'}>
                  {check.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                  {check.message}
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      <div className="st-sec">2 · {mode === TRUSTED_EXEC ? '启动器授权' : '只读工具授权'}</div>
      <section className="st-group st-node-tools">
        {(inspection?.tools ?? status?.tools ?? []).map((tool) => {
          const checked = selectedPrograms.includes(tool.programId)
          return (
            <label key={tool.programId} className="st-node-tool">
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) => setSelectedPrograms((current) => (
                  event.target.checked
                    ? [...new Set([...current, tool.programId])]
                    : current.filter((value) => value !== tool.programId)
                ))}
              />
              <span className="st-row-t">
                <span className="t1">{tool.displayName}</span>
                <span className="t2">{tool.path}</span>
                <span className="t2">允许：{tool.operations.join(' / ')} · SHA-256 {tool.sha256.slice(0, 12)}…</span>
              </span>
            </label>
          )
        })}
        {(inspection?.tools ?? status?.tools ?? []).length === 0 && (
          <span className="st-muted">{profile.toolHint}</span>
        )}
      </section>

      <div className="st-sec">3 · 资源与能力上限</div>
      <section className="st-group st-node-limits">
        <label>最长运行（秒）<input type="number" min={profile.bounds.timeoutMs.min} max={profile.bounds.timeoutMs.max} value={Math.round(limits.timeoutMs / 1000)} onChange={(event) => updateLimit('timeoutMs', String(Number(event.target.value) * 1000))} /></label>
        <label>单进程内存（MiB）<input type="number" min={profile.bounds.maxMemoryMb.min} max={profile.bounds.maxMemoryMb.max} value={limits.maxMemoryMb} onChange={(event) => updateLimit('maxMemoryMb', event.target.value)} /></label>
        <label>输出上限（KiB）<input type="number" min={profile.bounds.maxOutputBytes.min} max={profile.bounds.maxOutputBytes.max} value={Math.round(limits.maxOutputBytes / 1024)} onChange={(event) => updateLimit('maxOutputBytes', String(Number(event.target.value) * 1024))} /></label>
        <label>进程数<input type="number" min={profile.bounds.maxProcesses.min} max={profile.bounds.maxProcesses.max} value={limits.maxProcesses} onChange={(event) => updateLimit('maxProcesses', event.target.value)} /></label>
        <label>CPU 硬上限（%）<input type="number" min={profile.bounds.cpuRatePercent.min} max={profile.bounds.cpuRatePercent.max} value={limits.cpuRatePercent} onChange={(event) => updateLimit('cpuRatePercent', event.target.value)} /></label>
        <div className="st-node-fixed-policy">
          {profile.policy.map(([key, value]) => (
            <span key={key}><b>{key}</b>{value}</span>
          ))}
        </div>
      </section>

      <div className="st-node-actions">
        <button
          type="button"
          className="st-btn primary"
          disabled={Boolean(busy) || !inspection?.safe || selectedPrograms.length === 0}
          onClick={() => void execute('save', async () => {
            const next = await host!.saveNodeGrant!({
              root: inspection!.canonicalPath,
              approvedPrograms: selectedPrograms,
              limits,
              mode,
            })
            applyStatus(next, mode)
            setSelfTest(null)
          })}
        >
          保存授权草案
        </button>
        <button
          type="button"
          className="st-btn primary"
          disabled={Boolean(busy) || !status?.configured}
          onClick={() => void execute('selftest', async () => {
            const report = await host!.selfTestAndEnableNode!(mode)
            setSelfTest(report)
            await refreshStatus()
          })}
        >
          <Play size={14} aria-hidden="true" />本机自检并启用
        </button>
        {status?.enabled && (
          <button
            type="button"
            className="st-btn"
            disabled={Boolean(busy)}
            onClick={() => void execute('pause', async () => {
              applyStatus(await host!.setNodePaused!(!status.paused, mode), mode)
            })}
          >
            {status.paused ? <Play size={14} /> : <Pause size={14} />}
            {status.paused ? `恢复${profile.label}` : `暂停${profile.label}`}
          </button>
        )}
        {status?.configured && (
          <button
            type="button"
            className="st-btn danger"
            disabled={Boolean(busy)}
            onClick={() => void execute('revoke', async () => {
              applyStatus(await host!.revokeNode!(mode), mode)
              setInspection(null)
              setSelfTest(null)
            })}
          >
            <Trash2 size={14} aria-hidden="true" />撤销{profile.label}
          </button>
        )}
      </div>

      {status?.configured && (
        <section className="st-node-ledger">
          <span><b>Grant</b>{status.grantId} · generation {status.generation}</span>
          <span><b>档位</b>{status.mode} · 网络 {status.networkPolicy}</span>
          <span><b>活动任务</b>{status.activeTasks.length ? status.activeTasks.join('、') : '无'}</span>
          <span><b>审计账本</b>{status.auditPath}（两档共用，每条带 mode）</span>
        </section>
      )}

      <div className="st-sec">4 · 控制端配对与低成本心跳</div>
      <section className="st-group st-node-pairing">
        <div className="st-node-pair-grid">
          <label>控制端地址<input value={controllerUrl} onChange={(event) => setControllerUrl(event.target.value)} /></label>
          <label>节点名称<input value={nodeLabel} maxLength={80} onChange={(event) => setNodeLabel(event.target.value)} /></label>
          <label>Pairing ID<input value={pairingId} onChange={(event) => setPairingId(event.target.value)} /></label>
          <label>8 位一次性码<input value={pairingCode} inputMode="numeric" maxLength={8} onChange={(event) => setPairingCode(event.target.value.replace(/\D/g, ''))} /></label>
        </div>
        <div className="st-node-actions">
          <button type="button" className="st-btn" disabled={Boolean(busy)} onClick={() => void execute('pairing-window', async () => { await openControllerPairing() })}>本机控制端生成配对码</button>
          <button
            type="button"
            className="st-btn primary"
            disabled={Boolean(busy) || !status?.enabled || status.paused || pairingCode.length !== 8 || !pairingId.trim() || !host?.pairNode}
            onClick={() => void execute('pair', async () => {
              setTransport(await host!.pairNode!({ controllerUrl, pairingId, code: pairingCode, label: nodeLabel, mode }))
            })}
          >确认指纹并配对（{profile.label}）</button>
          {transport?.paired && <button type="button" className="st-btn danger" disabled={Boolean(busy) || !host?.unpairNode} onClick={() => void execute('unpair', async () => { setTransport(await host!.unpairNode!()) })}>解除配对</button>}
        </div>
        {pairingCode && (
          <div className="st-node-pair-code">
            <b>{pairingCode}</b>
            <span>有效至：{pairingExpiresAt ? new Date(pairingExpiresAt).toLocaleTimeString() : '未知'}</span>
            <span>控制端指纹：{controllerFingerprint || '将在原生确认框核对'}</span>
          </div>
        )}
        {transport && (
          <div className="st-node-transport" data-testid="remote-node-transport">
            <span><b>Node</b>{transport.nodeId}</span>
            <span><b>节点指纹</b>{transport.nodeFingerprint}</span>
            <span><b>控制端</b>{transport.controllerUrl || '未配对'}</span>
            <span><b>连接</b>{transport.revoked ? '已撤销' : transport.connected ? '长轮询在线' : transport.paired ? '等待心跳' : '未配对'}</span>
            <span><b>心跳延迟</b>{transport.lastHeartbeatLatencyMs == null ? '—' : `${transport.lastHeartbeatLatencyMs} ms`}</span>
            <span><b>排队延迟</b>{transport.lastQueueLatencyMs == null ? '—' : `${transport.lastQueueLatencyMs} ms`}</span>
            <span><b>待回执</b>{transport.pendingReceipts}</span>
            {transport.lastError && <span className="bad"><b>最近错误</b>{transport.lastError}</span>}
          </div>
        )}
      </section>
        </div>
      </details>
    </div>
  )
}
