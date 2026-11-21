// 任务窗口「点击直达 plan.md 正文」e2e — 真 UI 路径（TASK-SSOT-UNIFICATION §N-ui-plan-direct,
// 用户 2026-07-05: 点开要见正文, 历史记录和文件信息挪进更多选项）。
// whatnow(:8230) 与同源 KB/plans API 全部 page.route stub, 专测前端两条链路:
// 1) 点任务标题 → 直接开 note 页签(plans/<plan_id>/plan), plan.md 正文可见, 不再是 plan-folder;
// 2) 行内 ⋯ →「历史与文件信息」→ 开 plan-folder 视图(时间线+文件列表)。
import { test, expect, type Page } from '@playwright/test'

const PLAN_ID = 'agent-orchestration/[2026-07-05]TASK-SSOT-UNIFICATION'
const NOTE_ID = `plans/${PLAN_ID}/plan`
const PLAN_BODY_MARK = '这是 plan md 的正文内容标记'

const board = {
  clusters: [{
    id: 'c1', title: '测试域 · e2e', note: '',
    goals: [{
      id: 'g1', title: '测试任务线', kind: '北极星', line: 'main', status: 'in_progress',
      objective: '', detail: '', source: '', cluster_id: 'c1', plan_id: '', archived_count: 0,
      tasks: [{
        id: 'p_task_ssot', title: '任务唯一真源统一', status: 'in_progress', completion: 40,
        line: 'main', channel: 'local', external_refs: [], plan_id: PLAN_ID,
        latest_progress: '', updated_at: 1751500000000, archived: false, subtasks: [],
        // 带一条进度 → 行首出现「展开」箭头(悬浮提示 e2e 需要这个按钮在场)
        progress: [{ ts: 1751400000000, text: '一条进度记录', source: 'test' }],
      }],
      progress: [],
    }],
  }],
  orphan_goals: [], loose_tasks: [], pins: [],
  counts: { clusters: 1, goals: 1, tasks: 1 }, updated_at: 1751500000000,
}

async function openQuestBoard(page: Page) {
  await page.route('http://127.0.0.1:8230/**', async (route) => {
    if (route.request().url().includes('/api/board')) return route.fulfill({ json: board })
    return route.fulfill({ json: { ok: true } })
  })
  // 同源 KB note API: plan.md 正文
  await page.route('**/api/notes/**', async (route) => {
    const url = route.request().url()
    if (url.includes('/links')) return route.fulfill({ json: { outgoing: [], outgoing_unresolved: [], backlinks: [] } })
    return route.fulfill({ json: { id: NOTE_ID, title: 'plan', path: `docs/plans/${PLAN_ID}/plan.md`, content: `# 计划正文\n\n${PLAN_BODY_MARK}\n` } })
  })
  await page.route('**/api/annotations**', async (route) => route.fulfill({ json: { items: [] } }))
  // plan-folder 视图的三个数据源
  await page.route('**/api/plans/**', async (route) => route.fulfill({
    json: {
      id: PLAN_ID, topic: 'TASK-SSOT-UNIFICATION', date: '2026-07-05',
      folder_path: `docs/plans/${PLAN_ID}`, archived: false, meta: {},
      files: [{ path: 'plan.md', is_md: true, size: 100, mtime: 1, note_id_if_md: NOTE_ID, summary: '计划书' }],
    },
  }))
  await page.route('**/api/cc/sessions**', async (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/boss-sight/progress**', async (route) => route.fulfill({ json: { entries: [] } }))

  await page.goto('/')
  await page.locator('.dv-default-tab-content', { hasText: '任务窗口' }).first().click()
  await expect(page.getByTestId('quest-board')).toBeVisible()
  await expect(page.getByTestId('task-row')).toHaveCount(1)
}

test.describe('任务窗口计划行打开行为（点击直达正文 / 历史信息在更多选项）', () => {
  test('点任务标题直接打开 plan.md 正文(note 渲染), 不是历史记录和文件信息', async ({ page }) => {
    await openQuestBoard(page)
    await page.getByTestId('task-open').click()
    // 正文标记可见 = 直达 plan.md 内容
    await expect(page.getByText(PLAN_BODY_MARK)).toBeVisible()
    // 不该出现 plan-folder 视图的骨架元素(「打开 plan.md」按钮 / 「全部文件」列表头)
    await expect(page.getByText('全部文件', { exact: false })).toHaveCount(0)
  })

  test('行内 ⋯ 菜单「历史与文件信息」打开 plan-folder 视图', async ({ page }) => {
    await openQuestBoard(page)
    await page.getByTestId('task-row').first().getByTestId('task-more').click()
    await expect(page.getByTestId('task-more-menu')).toBeVisible()
    await page.getByTestId('task-open-plan-folder').click()
    // plan-folder 视图骨架: 「打开 plan.md」按钮 + 全部文件列表
    await expect(page.getByText('📋 打开 plan.md')).toBeVisible()
    await expect(page.getByText('全部文件', { exact: false })).toBeVisible()
  })

  // 用户 2026-07-05: 分不清点哪是展开、点哪是跳转 → 标题与展开箭头悬浮各有说明。
  test('悬浮提示: 标题说明跳转、展开箭头说明原地展开', async ({ page }) => {
    await openQuestBoard(page)
    // 悬浮标题 → 说明这是跳转(打开正文)
    await page.getByTestId('task-open').hover()
    await expect(page.getByText('打开计划正文 plan.md')).toBeVisible()
    // 悬浮展开箭头 → 说明这是原地展开, 不跳转
    await page.getByTestId('task-expand').hover()
    await expect(page.getByText('原地展开：进度历史与执行子任务')).toBeVisible()
    // 点开后箭头提示变「收起进度与子任务」, 且确实原地展开(进度历史出现, 没有跳走)。
    // 提示文案刻意全局唯一, 避免与 GoalCard 底部「收起」等字样撞车(审查意见)。
    await page.getByTestId('task-expand').click()
    await expect(page.getByTestId('task-progress-history')).toBeVisible()
    await page.getByTestId('task-open').hover() // 先移开再回来, 触发重新悬浮
    await page.getByTestId('task-expand').hover()
    await expect(page.getByText('收起进度与子任务', { exact: true })).toBeVisible()
  })
})
