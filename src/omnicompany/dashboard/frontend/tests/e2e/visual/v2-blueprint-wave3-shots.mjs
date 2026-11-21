#!/usr/bin/env node
/**
 * v2-blueprint-wave3-shots.mjs — V2 阶段四第三波「壳层 A + 项目板蓝图 G」视觉验证。
 *
 * 直打真实 8210 服务(frontend/static = vite outDir, npm run build 后即为新产物),
 * 只读截图,不写任何数据(捕获/审阅写操作一律不触发)。
 *
 * 截图清单(合同要求: 三形态项目板 + rail 展开态 + hover 预览卡):
 *   desktop 1600×950: 项目板(壳层 A 全形态) / rail 悬停展开 200px / 行 hover 预览卡(300ms)
 *   tablet  834×1194: rail 收起态默认 + 项目板
 *   phone   390×844:  rail 隐藏 + 汉堡抽屉(V1 降级收编)
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/v2-blueprint-wave3-shots.mjs [--out <目录>] [--base http://localhost:8210]
 */
import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';
import { join } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave3'));
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
  await page.waitForTimeout(1800); // 字体/活跃徽章/图钉等二次渲染稳定
  return { ctx, page };
}

// ── desktop 1600×950: 项目板全形态 + rail 展开 + hover 预览卡 ──
{
  const { ctx, page } = await boardPage(1600, 950);
  await shot(page, 'desktop-board');

  // rail 悬停展开(56 → 200px,过渡 220ms + label 渐显)
  await page.hover('.sha-rail').catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'desktop-rail-expanded');
  await page.mouse.move(900, 500);
  await page.waitForTimeout(400);

  // 行 hover 预览卡(300ms enter 延迟 + 250ms 落下动画)
  const card = page.locator('.pb-card').first();
  if (await card.count()) {
    await card.hover();
    await page.waitForTimeout(900);
    await shot(page, 'desktop-board-hover-preview');
  } else console.log('skip desktop-board-hover-preview(无 .pb-card)');

  // ⌘K 命令面板(壳层 A 收编全局搜索;顺手留证)
  await page.click('[data-testid="cockpit-cmdk"]').catch(() => {});
  await page.waitForTimeout(1200);
  await shot(page, 'desktop-cmdk-palette');
  await page.keyboard.press('Escape').catch(() => {});
  await ctx.close();
}
// ── tablet 834×1194: rail 收起态默认 ──
{
  const { ctx, page } = await boardPage(834, 1194, true);
  await shot(page, 'tablet-board');
  await ctx.close();
}
// ── phone 390×844: rail 隐藏 + 汉堡抽屉 ──
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
