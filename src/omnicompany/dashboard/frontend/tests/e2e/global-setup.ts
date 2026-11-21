/**
 * Playwright global setup — 起 ccdaemon + dashboard 两个 uvicorn 进程.
 *
 * 跟 [2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE 阶段 8 配套. 跑 e2e 不再要求用户先手动
 * 起两进程, runner 启动时自启, teardown 自杀.
 *
 * 约定:
 * - 如果 8200 (dashboard) 已经 listen → 假定用户已手动起, 跳过 spawn 不接管 (避免冲突)
 * - 如果 8200 未 listen → 自启两进程, 把 pid 写到临时文件给 globalTeardown 用
 *
 * 实现细节:
 * - 用 Node 的 child_process.spawn 起 python -m uvicorn ...
 * - daemon 端口 8201 (默认), dashboard 端口 8200
 * - waitFor 用 fetch /health + /api/cc/health
 */

import { spawn, type ChildProcess } from 'child_process'
import { writeFileSync, mkdirSync, openSync } from 'fs'
import { delimiter, join } from 'path'
import { tmpdir } from 'os'
import * as net from 'net'

// Playwright cwd = src/omnicompany/dashboard/frontend/ → 4 层 .. 到项目根 omnicompany/
const REPO_ROOT = join(process.cwd(), '..', '..', '..', '..')
const DASHBOARD_PORT = Number(process.env.OMNI_E2E_DASHBOARD_PORT || '8200')
const DAEMON_PORT = Number(process.env.OMNI_E2E_DAEMON_PORT || process.env.OMNI_CC_DAEMON_PORT || '8201')
const PID_FILE = join(tmpdir(), 'omni_e2e_pids.json')
const DAEMON_STATE_DIR = join(tmpdir(), `omni-e2e-ccdaemon-${DASHBOARD_PORT}-${DAEMON_PORT}`)

async function isPortOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = new net.Socket()
    sock.setTimeout(800)
    sock.once('connect', () => { sock.destroy(); resolve(true) })
    sock.once('error', () => { sock.destroy(); resolve(false) })
    sock.once('timeout', () => { sock.destroy(); resolve(false) })
    sock.connect(port, '127.0.0.1')
  })
}

async function waitForHttp(url: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url)
      if (r.status >= 200 && r.status < 500) return true
    } catch { /* */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  return false
}

async function globalSetup() {
  const dashboardAlive = await isPortOpen(DASHBOARD_PORT)
  // 即使 dashboard 在 (用户外部起), 也要确认 daemon 在 — 否则 chat e2e 全 skip.
  // 用 /api/cc/health 经反向代理探 daemon. 200 = daemon alive; 503 = daemon dead.
  let daemonReachable = false
  if (dashboardAlive) {
    try {
      const r = await fetch(`http://127.0.0.1:${DASHBOARD_PORT}/api/cc/health`)
      daemonReachable = r.status === 200
    } catch { /* */ }
  }

  if (dashboardAlive && daemonReachable) {
    console.log(`[e2e] dashboard already on :${DASHBOARD_PORT} + daemon reachable, skip spawn`)
    writeFileSync(PID_FILE, JSON.stringify({ external: true }))
    return
  }

  if (dashboardAlive && !daemonReachable) {
    console.log('[e2e] dashboard external alive but daemon dead — spawning daemon only')
    // 这种情况下我们启 daemon, 不动 dashboard
  } else {
    console.log('[e2e] dashboard not running; spawning daemon + dashboard...')
  }

  // 复用外部 daemon(端口已被真 daemon 占)时, 状态目录必须跟它一致(默认 <repo>/data) —
  // 指到空的 e2e 临时目录会让 spawn 的 dashboard cc_proxy 按临时目录状态文件找 daemon,
  // 永远 503 "ccdaemon not running" (2026-07-05 实锤: 孤儿 dashboard + daemon 换 pid)。
  const daemonExternal = await isPortOpen(DAEMON_PORT)
  const env = {
    ...process.env,
    OMNI_CC_DAEMON_PORT: String(DAEMON_PORT),
    ...(daemonExternal ? {} : {
      OMNI_CC_DAEMON_STATE_DIR: DAEMON_STATE_DIR,
      OMNICOMPANY_DB_DIR: DAEMON_STATE_DIR,
    }),
    PYTHONPATH: [join(REPO_ROOT, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter),
  }
  const isWin = process.platform === 'win32'
  const detached = true

  // log files for spawn'd processes — bei FAIL 时方便排查
  const logDir = process.env.OMNI_E2E_LOG_DIR || tmpdir()
  mkdirSync(logDir, { recursive: true })
  mkdirSync(DAEMON_STATE_DIR, { recursive: true })
  const daemonLogFd = openSync(join(logDir, 'e2e_daemon.log'), 'w')
  const dashboardLogFd = openSync(join(logDir, 'e2e_dashboard.log'), 'w')

  // 1) ccdaemon. Reuse an existing daemon instead of spawning a duplicate.
  let daemon: ChildProcess | null = null
  if (daemonExternal) {
    console.log(`[e2e] daemon already listening on :${DAEMON_PORT}, skip spawn`)
  } else {
    daemon = spawn('python', [
      '-m', 'uvicorn',
      'omnicompany.dashboard.ccdaemon.main:app',
      '--host', '127.0.0.1', '--port', String(DAEMON_PORT),
      '--log-level', 'info',
    ], {
      cwd: REPO_ROOT, env, detached,
      windowsHide: true,
      stdio: ['ignore', daemonLogFd, daemonLogFd],
    })
    daemon.unref?.()
  }

  if (!await waitForHttp(`http://127.0.0.1:${DAEMON_PORT}/health`, 25000)) {
    throw new Error('ccdaemon did not become ready in 25s')
  }

  if (dashboardAlive && !daemonReachable) {
    // 外部 dashboard 已活, daemon 我们补起来即可, 不再 spawn dashboard
    console.log(`[e2e] daemon ready on :${DAEMON_PORT}; using external dashboard on :${DASHBOARD_PORT}`)
    writeFileSync(PID_FILE, JSON.stringify({
      daemonPid: daemon?.pid,
      external: false,  // we own the daemon, but dashboard is external
      dashboardExternal: true,
      daemonExternal,
    }))
    return
  }

  // 2) dashboard (with --reload — e2e 也是 dogfood 主路, 本来就要测 reload 韧性)
  const dashboard = spawn('python', [
    '-m', 'uvicorn',
    'omnicompany.dashboard.app:app',
    '--host', '127.0.0.1', '--port', String(DASHBOARD_PORT),
    '--reload',
    '--reload-dir', join(REPO_ROOT, 'src', 'omnicompany', 'dashboard'),
    '--reload-exclude', 'src/omnicompany/dashboard/ccdaemon',
    '--log-level', 'info',
  ], {
    cwd: REPO_ROOT, env, detached,
    windowsHide: true,
    stdio: ['ignore', dashboardLogFd, dashboardLogFd],
  })
  dashboard.unref?.()

  if (!await waitForHttp(`http://127.0.0.1:${DASHBOARD_PORT}/api/teams`, 45000)) {
    if (daemon && !daemonExternal) daemon.kill()
    dashboard.kill()
    throw new Error('dashboard /api/teams did not respond in 45s')
  }

  console.log(`[e2e] dashboard ready on :${DASHBOARD_PORT}, daemon on :${DAEMON_PORT}`)
  mkdirSync(join(tmpdir()), { recursive: true })
  writeFileSync(PID_FILE, JSON.stringify({
    daemonPid: daemon?.pid,
    dashboardPid: dashboard.pid,
    external: false,
    daemonExternal,
  }))
}

export default globalSetup
