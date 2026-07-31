// 任务窗口复制 e2e — 真 UI 路径（用户 2026-07-03: 计划行「复制引用/复制路径」点了没反应）。
// whatnow(:8230) 是外部 Rust 服务, 这里 page.route stub 一块固定 board, 专测前端复制链路:
// kebab 菜单项 → copyText 三级降级 → 剪贴板内容 + 行内「已复制」反馈。
// 场景 B 模拟 VSCode webview/受限 iframe（navigator.clipboard 不存在）, 验证 execCommand
// 降级真的把文本放上剪贴板（用 copy 事件捕获 textarea 内容）且反馈可见 —— 旧实现在
// 该环境下静默无反应, 正是本次 bug。
import { test, expect, type Page } from '@playwright/test'

const PLAN_ID = 'agent-framework/[2026-05-26]MULTI-MACHINE-OMNICOMPANY'
const PLAN_PATH = 'E:\\WindowsWorkspace\\omnicompany\\docs\\plans\\agent-framework\\[2026-05-26]MULTI-MACHINE-OMNICOMPANY'

const BOARD = {
  clusters: [{
    id: 'c1', title: '测试域 · e2e', note: '',
    goals: [{
      id: 'g1', title: '测试任务线', kind: '北极星', line: 'main', status: 'in_progress',
      objective: '', detail: '', source: '', cluster_id: 'c1', plan_id: PLAN_ID, archived_count: 0,
      tasks: [{
        id: 't1', title: '【框架】将Omnicompany改造为多机互联底座', status: 'in_progress', completion: 25,
        line: 'main', channel: 'local', external_refs: [], plan_id: PLAN_ID,
        latest_progress: '', updated_at: 1751500000000, archived: false, subtasks: [], progress: [],
      }],
      progress: [],
    }],
  }],
  orphan_goals: [], loose_tasks: [], pins: [],
  counts: { clusters: 1, goals: 1, tasks: 1 }, updated_at: 1751500000000,
}

async function openQuestBoard(page: Page) {
  await page.route('http://127.0.0.1:8230/**', async (route) => {
    const url = route.request().url()
    if (url.includes('/api/board')) return route.fulfill({ json: BOARD })
    return route.fulfill({ json: { ok: true, synced: 0 } })
  })
  await page.goto('/')
  await page.locator('.dv-default-tab-content', { hasText: '任务窗口' }).first().click()
  await expect(page.getByTestId('quest-board')).toBeVisible()
  await expect(page.getByTestId('task-row').first()).toBeVisible()
}

test.describe('正常浏览器（navigator.clipboard 可用）', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

  test('计划行 ⋯ → 复制引用: 剪贴板=plan id + 行内「已复制」', async ({ page }) => {
    await openQuestBoard(page)
    await page.getByTestId('task-more').first().click()
    await expect(page.getByTestId('task-more-menu')).toBeVisible()
    await page.getByTestId('task-jump').click()
    await expect(page.getByTestId('task-more-menu')).toBeHidden()
    await expect(page.getByTestId('task-copy-toast')).toHaveText('已复制')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(PLAN_ID)
  })

  test('计划行 ⋯ → 复制路径（计划目录）: 剪贴板=绝对路径', async ({ page }) => {
    await openQuestBoard(page)
    await page.getByTestId('task-more').first().click()
    await page.getByTestId('task-copy-path').click()
    await expect(page.getByTestId('task-copy-toast')).toHaveText('已复制')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(PLAN_PATH)
  })

  test('任务线卡头 ⋯ → 复制路径: 剪贴板=绝对路径 + 卡头「已复制」', async ({ page }) => {
    await openQuestBoard(page)
    await page.getByTestId('goal-more').first().click()
    await page.getByTestId('goal-copy-path').click()
    await expect(page.getByTestId('goal-copy-toast')).toHaveText('已复制')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(PLAN_PATH)
  })
})

test.describe('受限环境（无 navigator.clipboard, 如 VSCode webview iframe）', () => {
  test('复制引用走 execCommand 降级: 真的复制了 + 有可见反馈(不再静默)', async ({ page }) => {
    await page.addInitScript(() => {
      // 模拟受限 webview: clipboard API 整个不存在
      Object.defineProperty(navigator, 'clipboard', { get: () => undefined })
      // execCommand('copy') 会对 focus 的 textarea 触发 copy 事件 — 借此捕获实际复制的文本
      document.addEventListener('copy', () => {
        const el = document.activeElement as HTMLTextAreaElement | null
        ;(window as unknown as { __copied?: string }).__copied = el?.value ?? ''
      })
    })
    await openQuestBoard(page)
    await page.getByTestId('task-more').first().click()
    await page.getByTestId('task-jump').click()
    // 反馈必须可见 — 旧实现(裸 navigator.clipboard?.writeText)在此环境下点了毫无反应
    await expect(page.getByTestId('task-copy-toast')).toBeVisible()
    await expect(page.getByTestId('task-copy-toast')).toHaveText('已复制')
    expect(await page.evaluate(() => (window as unknown as { __copied?: string }).__copied)).toBe(PLAN_ID)
  })
})
