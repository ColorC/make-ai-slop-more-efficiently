// 任务窗口置顶 e2e — 真 UI 路径（台账 gap: quest_board.task_pin 长期存在功能无测试）。
// whatnow(:8230) 是外部 Rust 服务, 这里 page.route stub 一块固定 board（2 条任务, 排序按
// lastFollowup 降序: t2 本应排在 t1 前面）, 专测前端置顶链路:
// kebab 菜单「置顶」→ POST /api/pin(pinned=true) → 重新拉 board(pins 含该任务)→ 该行排到
// 全局最前 + 出现置顶高亮(qb-pinned-row class + 行内实心 Pin 图标)。
// 再点「取消置顶」→ POST /api/pin(pinned=false) → board 无 pins → 排序回到原有次序。
import { test, expect, type Page } from '@playwright/test'

const PLAN_ID = 'agent-framework/[2026-05-26]MULTI-MACHINE-OMNICOMPANY'

function board(pinned: boolean) {
  return {
    clusters: [{
      id: 'c1', title: '测试域 · e2e', note: '',
      goals: [{
        id: 'g1', title: '测试任务线', kind: '北极星', line: 'main', status: 'in_progress',
        objective: '', detail: '', source: '', cluster_id: 'c1', plan_id: PLAN_ID, archived_count: 0,
        tasks: [
          {
            id: 't1', title: '计划行一（原排第一, 跟进更晚）', status: 'in_progress', completion: 25,
            line: 'main', channel: 'local', external_refs: [], plan_id: PLAN_ID,
            latest_progress: '', updated_at: 1751500000000, archived: false, subtasks: [], progress: [],
          },
          {
            id: 't2', title: '计划行二（原排第二, 待置顶）', status: 'in_progress', completion: 10,
            line: 'main', channel: 'local', external_refs: [], plan_id: PLAN_ID,
            latest_progress: '', updated_at: 1751400000000, archived: false, subtasks: [], progress: [],
          },
        ],
        progress: [],
      }],
    }],
    orphan_goals: [], loose_tasks: [],
    pins: pinned ? [{ subject_kind: 'task', subject_id: 't2', note: '' }] : [],
    counts: { clusters: 1, goals: 1, tasks: 2 }, updated_at: 1751500000000,
  }
}

async function openQuestBoard(page: Page) {
  let pinned = false
  await page.route('http://127.0.0.1:8230/**', async (route) => {
    const req = route.request()
    const url = req.url()
    if (url.includes('/api/pin') && req.method() === 'POST') {
      const body = req.postDataJSON() as { subject_kind: string; subject_id: string; pinned: boolean }
      pinned = !!body.pinned && body.subject_id === 't2'
      return route.fulfill({ json: { ok: true } })
    }
    if (url.includes('/api/board')) return route.fulfill({ json: board(pinned) })
    return route.fulfill({ json: { ok: true, synced: 0 } })
  })
  await page.goto('/')
  await page.locator('.dv-default-tab-content', { hasText: '任务窗口' }).first().click()
  await expect(page.getByTestId('quest-board')).toBeVisible()
  await expect(page.getByTestId('task-row')).toHaveCount(2)
}

test.describe('任务窗口计划行置顶（kebab ⋯ → 置顶）', () => {
  test('点置顶后该行排到列表最前 + 带置顶高亮态; 再点取消置顶回到原排序', async ({ page }) => {
    await openQuestBoard(page)

    // 初始次序: t1(计划行一) 在前, t2(计划行二) 在后 — 与 lastFollowup 降序一致。
    const rows = page.getByTestId('task-row')
    await expect(rows.nth(0)).toContainText('计划行一')
    await expect(rows.nth(1)).toContainText('计划行二')
    await expect(rows.nth(1)).not.toHaveClass(/qb-pinned-row/)

    // 对第二行（计划行二）点 ⋯ → 置顶。
    await rows.nth(1).getByTestId('task-more').click()
    await expect(page.getByTestId('task-more-menu')).toBeVisible()
    await page.getByTestId('task-pin').click()
    await expect(page.getByTestId('task-more-menu')).toBeHidden()

    // 置顶后重拉 board: 计划行二排到最前 + 带高亮态(qb-pinned-row class + 实心 Pin 图标)。
    await expect(rows.nth(0)).toContainText('计划行二')
    await expect(rows.nth(0)).toHaveClass(/qb-pinned-row/)
    await expect(rows.nth(0).locator('svg.lucide-pin')).toBeVisible()
    await expect(rows.nth(1)).toContainText('计划行一')

    // 再次点击(取消置顶): 该行回到原有排序位置, 高亮消失。
    await rows.nth(0).getByTestId('task-more').click()
    await expect(page.getByTestId('task-more-menu')).toBeVisible()
    await page.getByTestId('task-pin').click()
    await expect(page.getByTestId('task-more-menu')).toBeHidden()

    await expect(rows.nth(0)).toContainText('计划行一')
    await expect(rows.nth(1)).toContainText('计划行二')
    await expect(rows.nth(1)).not.toHaveClass(/qb-pinned-row/)
  })
})
