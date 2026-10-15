import { expect, test } from '@playwright/test'
import { setupErrorLogging } from './helpers'

const dashboardApi = `http://127.0.0.1:${process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`

test('会话右栏概览复用项目首页，并在窄栏中完整滚动宽内容', async ({ page, request }) => {
  setupErrorLogging(page)
  await page.setViewportSize({ width: 780, height: 820 })
  const response = await request.post(`${dashboardApi}/api/cc/sessions`, {
    data: {
      cmd: ['powershell.exe', '-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 120'],
      cwd: 'C:/workspace/',
      cols: 100,
      rows: 30,
    },
  })
  expect(response.ok()).toBeTruthy()
  const session = await response.json() as { id: string }

  try {
    await page.route(`**/api/cc/sessions/${session.id}/context`, (route) => route.fulfill({
      json: {
        session_id: session.id,
        context: { project: 'omnidashboard', active_plan: 'plans/[2026-08-11]SIDEBAR' },
        modified_files: [], bash_writes: [], added_workers: [], added_materials: [], event_count: 0,
      },
    }))
    await page.route('**/lofa/overlay/invoke', async (route) => {
      const body = route.request().postDataJSON() as { cmd?: string } | null
      if (body?.cmd === 'notes_canvas_get') {
        await route.fulfill({
          json: {
            ok: true,
            result: JSON.stringify({
              cards: [{
                id: 'card:new', kind: 'text', title: '最新提交',
                material: { project: 'omnidashboard', kind: 'agent-workflow-report', tier: 'important' },
                submission: { state: 'unreviewed', submittedAt: '2026-08-14T10:00:00.000Z', submittedBy: 'cli' },
              }],
              relations: [],
            }),
          },
        })
        return
      }
      await route.fulfill({ json: { ok: true, result: null } })
    })
    await page.route('**/api/projects/omnidashboard/index', (route) => route.fulfill({
      json: {
        ok: true,
        path: 'C:/workspace/omnicompany/docs/plans/omnidashboard-os/PROJECT_INDEX.md',
        content: '# OmniDashboard 项目首页\n\n==唯一 Wiki Core==\n\n## 容器大纲验收\n\n| 很长的第一列 | 很长的第二列 | 很长的第三列 |\n|---|---|---|\n| AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA | BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB | CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC |',
      },
    }))

    await page.goto(`/?open_type=cc_session&open_id=${session.id}&open_title=会话侧栏验收`)
    await expect(page.locator(`[data-cc-session-id="${session.id}"]`)).toBeVisible({ timeout: 15_000 })
    const companion = page.getByTestId('session-companion')
    await expect(companion.getByTestId('companion-nav-multiagent')).toHaveText('会话')
    await expect(companion.getByTestId('companion-nav-materials')).toHaveCount(0)

    // 绑定项目后，概览复用项目页同一个 Wiki 工作区；只隐藏非 Wiki 域入口。
    await expect(companion).toHaveAttribute('data-page', 'overview', { timeout: 10_000 })
    const workspace = companion.getByTestId('project-detail')
    await expect(workspace).toBeVisible()
    await expect(workspace).toHaveAttribute('data-wiki-only', '1')
    const project = companion.getByTestId('project-wiki-home')
    await expect(project).toBeVisible()
    await expect(project).toHaveAttribute('data-project-id', 'omnidashboard')
    await expect(project.getByTestId('pd-home').getByRole('heading', { name: 'OmniDashboard 项目首页' })).toBeVisible()
    await expect(project.locator('.wiki-core-markdown mark')).toHaveText('唯一 Wiki Core')
    await expect(companion.getByText('omnidashboard 材料 Wiki')).toHaveCount(0)
    await expect(companion.getByTestId('review-board')).toHaveCount(0)
    await expect(companion.getByTestId('pd-entry-canvas')).toHaveCount(0)

    // 左栏收起后节点、边线与布局占位一起消失，但正文顶栏的展开按钮始终保留。
    await expect(workspace.getByTestId('pd-rail')).toBeVisible()
    await workspace.getByTestId('pd-toggle-rail-mobile').click()
    await expect(workspace.getByTestId('pd-rail')).toHaveCount(0)
    const railToggle = workspace.getByTestId('pd-toggle-rail')
    await expect(railToggle).toBeVisible()
    await expect(railToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(railToggle).toHaveAttribute('aria-label', '展开项目 Wiki 导航')
    await railToggle.click()
    await expect(workspace.getByTestId('pd-rail')).toBeVisible()
    await workspace.getByTestId('pd-toggle-rail-mobile').click()

    // 大纲按 Wiki 容器宽度响应：Companion 窄时隐藏，即便浏览器视口本身仍较宽。
    await expect(project.getByTestId('pd-outline')).toBeHidden()

    // 宽表格保持原尺寸，由项目正文容器横向滑动，不裁掉最后一列。
    const article = project.locator('.pd-article')
    await expect(article.locator('table')).toBeVisible()
    const sizing = await article.evaluate((node) => ({
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth,
      overflowX: getComputedStyle(node).overflowX,
    }))
    expect(sizing.overflowX).toBe('auto')
    expect(sizing.scrollWidth).toBeGreaterThan(sizing.clientWidth)
  } finally {
    await request.delete(`${dashboardApi}/api/cc/sessions/${session.id}`).catch(() => undefined)
  }
})
