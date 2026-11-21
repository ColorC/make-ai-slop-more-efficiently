// 项目工作板首页 e2e — 真 UI 路径: 首页(无页签时的水印层)即项目卡片工作板;
// 点卡进详情(材料轨迹/计划/对话/管线/技能/文件/审阅/札记 八内页签); 卡片右上角一键复制 index 路径。
// 数据前提: 注册表里至少有一个项目(data/registry/projects.json)。
// 2026-07-06 页签断言更新: 旧断言含早已删除的 index 页签(过时未随重构更新); 新增 skills(技能)
// 页签与文件目录树(fs-all-toggle)断言。

import { test, expect } from '@playwright/test'

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test('首页即项目工作板: 分组卡片 → 详情 → 复制 index 路径', async ({ page }) => {
  await page.goto('/')

  // 首页水印层 = 项目工作板
  const board = page.getByTestId('project-board')
  await expect(board).toBeVisible()
  const cards = page.getByTestId('project-card')
  // 冷启动时 enrich 要读外部盘上的 index 文件(有 TTL 缓存但首回合慢), 放宽首屏等待
  await expect(cards.first()).toBeVisible({ timeout: 15_000 })

  // 卡片有最后活跃时间
  await expect(page.getByTestId('project-card-last-active').first()).toContainText(/活跃/)

  // 一键复制 index 路径(有 index 的卡才有按钮; 允许部分项目暂未绑定)
  const copyBtns = page.getByTestId('project-card-copy-index')
  if (await copyBtns.count() > 0) {
    await copyBtns.first().click()
    await expect(copyBtns.first()).toContainText('已复制')
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    expect(clip.length).toBeGreaterThan(3)
  }

  // 点开卡片 → 项目详情页签
  await cards.first().click()
  const detail = page.getByTestId('project-detail')
  await expect(detail).toBeVisible()
  for (const t of ['canvas', 'plans', 'convos', 'teams', 'skills', 'files', 'reviews', 'authored']) {
    await expect(page.getByTestId(`project-tab-${t}`)).toBeVisible()
  }
  // 已删除的低频区块/按钮不再出现(2026-07-06 用户裁决)
  await expect(page.getByTestId('project-quick-actions')).toHaveCount(0)
  await expect(page.getByTestId('project-new-plan')).toHaveCount(0)

  // 技能页签: 集合 atlas 技能 + omni run 管线
  await page.getByTestId('project-tab-skills').click()
  await expect(page.getByTestId('project-skills')).toBeVisible()
  await expect(page.getByTestId('project-skills-filter')).toBeVisible()

  // 文件页签 = 真目录树: 有"展示所有目录"开关; 树根可见后能展开一层
  await page.getByTestId('project-tab-files').click()
  await expect(page.getByTestId('project-files')).toBeVisible()
  await expect(page.getByTestId('fs-all-toggle')).toBeVisible()
})
