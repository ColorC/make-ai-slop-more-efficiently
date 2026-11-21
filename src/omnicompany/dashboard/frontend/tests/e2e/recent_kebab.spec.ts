// 三点「…更多」菜单 + 最近访问表兜底 e2e — 真 UI 路径。
// 覆盖: (1) 项目卡片 kebab 复制项目 id 且 stopPropagation 不误开卡片;
//       (2) captures 兜底 — 对话行不再显示「信息不足」, 回退到 prompt;
//       (3) 对话行 kebab 复制 session id。
import { test, expect } from '@playwright/test'

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test('项目卡片三点菜单: 复制项目 id, 点菜单不误开卡片', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('project-board')).toBeVisible()
  const kebab = page.getByTestId('project-kebab').first()
  await expect(kebab).toBeVisible({ timeout: 15_000 })
  await kebab.click()
  const menu = page.getByTestId('project-kebab-menu')
  await expect(menu).toBeVisible()
  await expect(page.getByTestId('project-kebab-copy-id').first()).toBeVisible()
  await page.getByTestId('project-kebab-copy-id').first().click()
  // 选项后菜单关闭, 且仍停在工作板(stopPropagation 生效, 没被卡片 openProps 顶进项目详情)
  await expect(menu).toBeHidden()
  await expect(page.getByTestId('project-board')).toBeVisible()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip.length).toBeGreaterThan(0)
})

test('项目卡片三点菜单: 贴住触发钮且不被相邻卡片按钮盖住(悬浮层定位回归)', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('project-board')).toBeVisible()
  const kebab = page.getByTestId('project-kebab').first()
  await expect(kebab).toBeVisible({ timeout: 15_000 })
  const trigger = await kebab.boundingBox()
  await kebab.click()
  const menu = page.getByTestId('project-kebab-menu')
  await expect(menu).toBeVisible()
  const box = await menu.boundingBox()
  expect(trigger && box).toBeTruthy()
  if (!trigger || !box) throw new Error('no bbox')

  // (1) 贴住: 菜单顶边紧挨触发钮底边(默认下方 4px), 不是飘在别处。
  const gap = box.y - (trigger.y + trigger.height)
  expect(gap).toBeGreaterThanOrEqual(0)
  expect(gap).toBeLessThanOrEqual(12)

  // (2) 不被盖住: 菜单自身坐标处命中的最顶元素必须落在菜单内(portal 到 body + 够高 z-index),
  //     否则会被后续卡片右上角的复制钮等盖住 —— 正是用户反馈的 bug。
  const topmost = await page.evaluate(({ x, y, width }) => {
    const cx = x + Math.min(width / 2, 40)
    const cy = y + 16  // 第一个菜单项附近
    const el = document.elementFromPoint(cx, cy)
    const m = el?.closest('[data-testid="project-kebab-menu"]')
    return { hit: !!m, tag: el?.tagName || null, testid: (el as HTMLElement)?.dataset?.testid || null }
  }, box)
  expect(topmost.hit, `菜单被遮挡, 命中的是 ${topmost.tag}/${topmost.testid}`).toBe(true)
})

test('最近访问表: 对话行回退显示 prompt(无「信息不足」) + 行内 kebab 复制 session id', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('cockpit-nav-controller').click()
  await page.getByTestId('controller-view-home').click()
  await page.getByTestId('home-filter-conv').click()
  const rows = page.getByTestId('home-recent-row')
  await expect(rows.first()).toBeVisible({ timeout: 15_000 })

  // captures 兜底: 没有任何一行整体文本里出现「信息不足」占位
  const rowTexts = await rows.allInnerTexts()
  for (const t of rowTexts) expect(t).not.toContain('信息不足')

  // 对话行 kebab 复制 session id
  const kebab = page.getByTestId('recent-kebab-conv').first()
  await expect(kebab).toBeVisible()
  await kebab.click()
  // phase 五: 对话行菜单含「跑 plan audit」项(点它会 POST /api/plan-audit 起后台 job 并开审计页签)
  await expect(page.getByTestId('recent-kebab-audit-conv').first()).toBeVisible()
  await page.getByTestId('recent-kebab-copy-sid').first().click()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip.length).toBeGreaterThan(0)
})
