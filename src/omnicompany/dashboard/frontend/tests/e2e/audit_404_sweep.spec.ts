import { test, expect, type Page } from '@playwright/test'
import { gotoModule } from './helpers'

/**
 * Long-term safety-net audit (per user 2026-05-01 directive: "后续要对所有功能特性进行测试,
 * 避免有链接点出来是 404"). This spec does NOT exhaustively test feature behavior — it only
 * sweeps every reasonably-clickable affordance and asserts:
 *   1. no 4xx/5xx network responses  (excluding the allow-list below)
 *   2. no console.error messages     (excluding the allow-list below)
 *
 * Add new affordances as the dashboard grows. If a new harmless error shows up,
 * widen the allow-list with justification. Goal: this spec stays green.
 */

interface Failure {
  kind: 'http' | 'console'
  detail: string
  source: string
}

const HTTP_ALLOW = [
  // Favicon may legitimately not exist in dev — not a real bug.
  /\/favicon\.ico$/,
  // Some assistant context endpoints intentionally 404 when feature is off.
  /\/api\/v2\/assistant\/config\/work-until\b.*404/,
  // KB note resolver: clicking some links may target non-existent docs (we route them,
  // but the target lookup is allowed to 404 — UI shows a "not found" panel rather than crashing).
  /\/api\/notes\//,
]

const CONSOLE_ALLOW = [
  /\[Sidebar\] resolver\.list\(session\) failed/,  // session backend optional in dev
  /Failed to fetch/,                                // SSE/WS reconnect noise
  /sourcemap/i,                                     // dev-mode sourcemap noise
  /Failed to load resource: the server responded with a status of 404/,  // covered by HTTP allow above
]

function makeRecorder(page: Page): { failures: Failure[] } {
  const failures: Failure[] = []
  page.on('response', (resp) => {
    const status = resp.status()
    if (status < 400) return
    const url = resp.url()
    if (HTTP_ALLOW.some((rx) => rx.test(url) || rx.test(`${url} ${status}`))) return
    failures.push({ kind: 'http', detail: `${status} ${url}`, source: 'response' })
  })
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return
    const txt = msg.text()
    if (CONSOLE_ALLOW.some((rx) => rx.test(txt))) return
    failures.push({ kind: 'console', detail: txt, source: 'console' })
  })
  return { failures }
}

function reportFailures(label: string, failures: Failure[]) {
  if (failures.length === 0) return
  const lines = failures.map((f) => `  [${f.kind}] ${f.detail}`).join('\n')
  throw new Error(`${label} produced ${failures.length} unexpected failures:\n${lines}`)
}

// ─── 1. Module navigation sweep ───────────────────────────────────────────────

test('AUDIT.1 5 模块切换零 404 / 零 console.error', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await page.waitForTimeout(800)
  for (const m of ['kb', 'pm', 'agent', 'system', 'settings'] as const) {
    await gotoModule(page, m)
    await page.waitForTimeout(500)
  }
  reportFailures('Module switching', failures)
})

// ─── 2. Sidebar item open sweep (one per type per module) ─────────────────────

async function clickFirst(page: Page, locator: string, label: string): Promise<boolean> {
  const el = page.locator(locator).first()
  if (!(await el.isVisible().catch(() => false))) return false
  await el.click()
  await page.waitForTimeout(800)
  return true
}

test('AUDIT.2 KB 模块 — 树+第一条 note 开启零 404', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await gotoModule(page, 'kb')
  await page.waitForTimeout(800)
  // pick first markdown leaf — they have title attr containing the note id
  const noteLeaf = page.locator('div[title]').filter({ hasText: /[A-Za-z]/ }).first()
  if (await noteLeaf.isVisible().catch(() => false)) {
    await noteLeaf.click()
    await page.waitForTimeout(1500)
  }
  reportFailures('KB sidebar open', failures)
})

test('AUDIT.3 PM 模块 — 第一条 plan 开启 + plan.md 打开零 404', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await gotoModule(page, 'pm')
  await page.waitForTimeout(800)
  // first plan in sidebar; topic-y text
  const planRow = page.locator('div').filter({ hasText: /WEB-FOUNDATION|TABLE|voxelcraft|demogame/ }).first()
  if (await planRow.isVisible().catch(() => false)) {
    await planRow.click()
    await page.waitForTimeout(800)
    const openBtn = page.getByRole('button', { name: /打开 plan\.md/ })
    if (await openBtn.isVisible().catch(() => false)) {
      await openBtn.click()
      await page.waitForTimeout(1200)
    }
  }
  reportFailures('PM open', failures)
})

test('AUDIT.4 System 模块 — Worker / Team / Material 各开第一条零 404', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await gotoModule(page, 'system')
  await page.waitForTimeout(800)
  // each tree wrapper has data-tree=<entityType>
  for (const tree of ['worker', 'team', 'material']) {
    const top = page.locator(`[data-tree="${tree}"] div[title]`).first()
    if (await top.isVisible().catch(() => false)) {
      await top.click()  // expand
      await page.waitForTimeout(300)
      const child = page.locator(`[data-tree="${tree}"] div[title]`).nth(1)
      if (await child.isVisible().catch(() => false)) {
        await child.click()
        await page.waitForTimeout(300)
      }
    }
  }
  reportFailures('System sidebar open', failures)
})

test('AUDIT.5 Settings 模块零 404', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await gotoModule(page, 'settings')
  await page.waitForTimeout(1200)
  reportFailures('Settings module', failures)
})

// ─── 3. Bottom panel + Trace tab sweep ────────────────────────────────────────

test('AUDIT.6 Trace 列表点开第一条零 404', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Trace 列表' }).click()
  await page.waitForTimeout(1500)
  const row = page.locator('div').filter({ hasText: /^\d{2}\/\d{2} \d{2}:\d{2}/ }).first()
  if (await row.isVisible().catch(() => false)) {
    await row.click()
    await page.waitForTimeout(1000)
  }
  reportFailures('Trace tab open', failures)
})

// ─── 4. KB demo: every clickable link works (this is what bit us last round) ──

test('AUDIT.7 kb_markdown_demo 内每个 .md 链接都不 404', async ({ page }) => {
  const { failures } = makeRecorder(page)
  await page.goto('/')
  await page.waitForTimeout(600)
  await page.keyboard.press('Control+k')
  await page.waitForTimeout(300)
  await page.getByRole('combobox').fill('kb_markdown_demo')
  await page.waitForTimeout(400)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(800)
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 8000 })

  // every .md routed link should have data-md-link (i.e. interception works);
  // assert there are no raw <a href="...md"> escaping back into the browser.
  const rawLeak = page.locator('.markdown-body a[href$=".md"][target="_blank"]')
  const leakCount = await rawLeak.count()
  expect(leakCount, '原始 .md 链接没被拦截 (会触发 404)').toBe(0)

  // also make sure our demo's data-md-link anchors all parse to non-empty ids
  const mdLinks = page.locator('.markdown-body a[data-md-link]')
  const n = await mdLinks.count()
  for (let i = 0; i < n; i++) {
    const id = await mdLinks.nth(i).getAttribute('data-md-link')
    expect(id, `链接 #${i} data-md-link 应非空`).toBeTruthy()
  }

  reportFailures('KB demo links', failures)
})
