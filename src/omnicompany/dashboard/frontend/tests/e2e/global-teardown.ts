/**
 * Playwright global teardown — 杀 globalSetup spawn 的两进程.
 * 若 setup 时 dashboard 是 external (用户手动起) 则不动.
 */

import { readFileSync, unlinkSync } from 'fs'
import { join } from 'path'
import { tmpdir } from 'os'

const PID_FILE = join(tmpdir(), 'omni_e2e_pids.json')

function killPid(pid: number) {
  if (process.platform === 'win32') {
    // Windows: process.kill SIGTERM 不可靠, 用 taskkill /F /T (force + tree)
    try {
      const { execSync } = require('child_process')
      execSync(`taskkill /F /T /PID ${pid}`, { stdio: 'ignore' })
    } catch { /* */ }
    return
  }
  try { process.kill(pid, 'SIGTERM') } catch { /* */ }
  setTimeout(() => {
    try { process.kill(pid, 'SIGKILL') } catch { /* */ }
  }, 2000)
}

async function globalTeardown() {
  let info: {
    external?: boolean
    dashboardExternal?: boolean
    daemonPid?: number
    dashboardPid?: number
  }
  try {
    info = JSON.parse(readFileSync(PID_FILE, 'utf-8'))
  } catch {
    return
  }
  if (info.external) {
    console.log('[e2e] dashboard + daemon both external, skip teardown')
    return
  }
  console.log(`[e2e] tearing down dashboard pid=${info.dashboardPid} daemon pid=${info.daemonPid}`)
  if (info.dashboardPid && !info.dashboardExternal) killPid(info.dashboardPid)
  if (info.daemonPid) killPid(info.daemonPid)
  try { unlinkSync(PID_FILE) } catch { /* */ }
  await new Promise((r) => setTimeout(r, 1500))
}

export default globalTeardown
