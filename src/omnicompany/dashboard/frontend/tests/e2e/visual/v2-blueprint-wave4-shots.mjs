#!/usr/bin/env node
/**
 * v2-blueprint-wave4-shots.mjs — V2 阶段四第四波「G.7 统一连续网格 + 蓝图玻璃面板 + 压缩长宽」视觉验证。
 *
 * 直打真实 8210 服务(frontend/static = vite outDir, npm run build 后即为新产物),
 * 只读截图,不写任何数据(捕获/审阅写操作一律不触发)。
 *
 * 截图清单(合同):
 *   desktop 1600×950: 项目板(网格在卡片间/边缘连续不断开) / rail 悬停展开 /
 *                     审阅队列 / 材料库 / 网格连续性特写(卡片边界处网格对齐)
 *   tablet  834×1194: 项目板(收边 12px)
 *   phone   390×844:  项目板(收边 8px,标尺自动隐藏)
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/v2-blueprint-wave4-shots.mjs [--out <目录>] [--base http://localhost:8210]
 */
import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';
import { join } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave4'));
const BASE = arg('base', 'http://localhost:8210');
const URLS = {
  board: `${BASE}/?open_type=project_board&open_id=main`,
  review: `${BASE}/?open_type=review_queue&open_id=main`,
  registry: `${BASE}/?open_type=material_registry&open_id=main`,
};

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const shot = (page, name, opts = {}) =>
  page.screenshot({ path: join(OUT, `${name}.png`), ...opts }).then(() => console.log('ok', name));

async function openPage(url, w, h, waitSel, mobile = false) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
  if (waitSel) await page.waitForSelector(waitSel, { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1800); // 字体/徽章/玻璃 backdrop 二次渲染稳定
  return { ctx, page };
}

// ── desktop 1600×950: 项目板 / rail 展开 / 网格特写 / 审阅队列 / 材料库 ──
{
  const { ctx, page } = await openPage(URLS.board, 1600, 950, '.pb-card');
  await shot(page, 'desktop-board');

  // rail 悬停展开(56 → 200px,过渡 220ms + label 渐显;描图纸玻璃透出统一网格)
  await page.hover('.sha-rail').catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'desktop-rail-expanded');
  await page.mouse.move(900, 500);
  await page.waitForTimeout(400);

  // 网格连续性特写: 跨「卡片边界 + 卡间隙 + 页内左缘露出带」的裁切——
  // 会话恢复可能多 dock 组并存,统一取最右组的前两张卡,向左含 30px 露出网格带。
  const boxes = await page.locator('.pb-card').evaluateAll((els) =>
    els.map((e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; }));
  boxes.sort((a, b) => b.x - a.x || a.y - b.y);
  const pane = boxes.filter((b) => Math.abs(b.x - (boxes[0] || { x: 0 }).x) < 4).slice(0, 2);
  if (pane.length >= 2) {
    const top = Math.max(0, pane[0].y - 44);
    const bottom = pane[1].y + Math.min(pane[1].h, 44);
    await shot(page, 'desktop-grid-continuity-closeup', {
      clip: { x: Math.max(0, pane[0].x - 30), y: top, width: 620, height: bottom - top },
    });
  } else console.log('skip desktop-grid-continuity-closeup(.pb-card 不足两张)');
  await ctx.close();
}
{
  const { ctx, page } = await openPage(URLS.review, 1600, 950, '.rf-qrow');
  await shot(page, 'desktop-review-queue');
  await ctx.close();
}
{
  const { ctx, page } = await openPage(URLS.registry, 1600, 950, '.mr-card');
  await shot(page, 'desktop-material-registry');
  await ctx.close();
}
// ── tablet 834×1194: rail 收起态默认 + 项目板(收边 12px) ──
{
  const { ctx, page } = await openPage(URLS.board, 834, 1194, '.pb-card', true);
  await shot(page, 'tablet-board');
  await ctx.close();
}
// ── phone 390×844: 收边 8px + 标尺自动隐藏 ──
{
  const { ctx, page } = await openPage(URLS.board, 390, 844, '.pb-card', true);
  await shot(page, 'phone-board');
  await ctx.close();
}
await browser.close();
console.log(`done → ${OUT}`);
