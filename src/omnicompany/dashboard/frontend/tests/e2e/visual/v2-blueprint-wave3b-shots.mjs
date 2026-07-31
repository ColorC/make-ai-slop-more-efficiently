#!/usr/bin/env node
/**
 * v2-blueprint-wave3b-shots.mjs — 零顶栏(壳层 A 收编)视觉验证。
 * 用户指令(2026-07-19): "不要顶栏了,任何都不要,想办法塞进其他地方" ——
 * 30px 薄顶栏整体删除,⌘K/通知/⋯/评论/全屏/调试/状态全部收进左 rail 槽位;
 * <600 浮动汉堡 + V1 抽屉(抽屉内补 ⌘K/通知/⋯ 项)。
 *
 * 直打真实 8210 服务(npm run build 后的产物),只读截图不写数据。
 * 截图清单: desktop 全壳 / desktop rail 展开(完整功能布局: 顶槽 ⌘K → 目的地 → bell
 *   → 最近 → 底槽 ⋯+状态) / desktop ⋯ 菜单弹层 / tablet 834 / phone 390(浮动汉堡) / phone 抽屉(补件)。
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/v2-blueprint-wave3b-shots.mjs [--out <目录>] [--base http://localhost:8210]
 */
import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';
import { join } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave3b'));
const BASE = arg('base', 'http://localhost:8210');
const BOARD_URL = `${BASE}/?open_type=project_board&open_id=main`;

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const shot = (page, name) => page.screenshot({ path: join(OUT, `${name}.png`) }).then(() => console.log('ok', name));

async function boardPage(w, h, mobile = false) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile });
  const page = await ctx.newPage();
  await page.goto(BOARD_URL, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
  await page.waitForSelector('.pb-card', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1800);
  return { ctx, page };
}

// ── desktop 1600×950: 零顶栏全壳 + rail 展开完整功能布局 + ⋯ 弹层 ──
{
  const { ctx, page } = await boardPage(1600, 950);
  await shot(page, 'desktop-board');

  // rail 悬停展开(顶槽 ⌘K → 5 目的地 → bell → 最近 → 底槽 ⋯+状态细点)
  await page.hover('.sha-rail').catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'desktop-rail-expanded');
  // 展开态直接点底槽 ⋯(rail hover 保持, 菜单弹层在 rail 旁)
  await page.click('[data-testid="cockpit-more"]').catch(() => {});
  await page.waitForTimeout(400);
  await shot(page, 'desktop-more-menu');
  await page.keyboard.press('Escape').catch(() => {});
  await page.mouse.move(900, 500);
  await page.waitForTimeout(400);

  // ⌘K 命令面板(rail 顶槽触发)
  await page.click('[data-testid="cockpit-cmdk"]').catch(() => {});
  await page.waitForTimeout(1200);
  await shot(page, 'desktop-cmdk-palette');
  await page.keyboard.press('Escape').catch(() => {});
  await ctx.close();
}
// ── tablet 834×1194: rail 收起态(槽位图标全列) ──
{
  const { ctx, page } = await boardPage(834, 1194, true);
  await shot(page, 'tablet-board');
  await ctx.close();
}
// ── phone 390×844: 零顶栏 + 浮动汉堡 + 抽屉补件 ──
{
  const { ctx, page } = await boardPage(390, 844, true);
  await shot(page, 'phone-board');
  await page.click('[data-testid="cockpit-nav-drawer-toggle"]').catch(() => {});
  await page.waitForTimeout(500);
  await shot(page, 'phone-nav-drawer');
  await ctx.close();
}
await browser.close();
console.log(`done → ${OUT}`);
