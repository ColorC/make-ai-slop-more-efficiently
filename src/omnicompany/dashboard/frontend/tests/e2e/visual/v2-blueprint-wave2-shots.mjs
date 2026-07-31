#!/usr/bin/env node
/**
 * v2-blueprint-wave2-shots.mjs — V2 阶段四第二波「蓝图精制」LOFA 页面层接线视觉验证。
 *
 * 方法照 triform-shots.mjs 的 LOFA 部分:www 无构建,经 playwright 路由拦截以
 * http://localhost 源供本地文件(CORS 白名单);API 走 Caddy HTTPS 单端口(12443),
 * 注入 localStorage lofa.baseUrl;Chromium 关 LocalNetworkAccessChecks(PNA)。
 *
 * 安全:reviewstage 的一切写方法(verdict/mark_pushed/comment/archive/batch)本地 fulfill,
 *   不触达真实后端(截图只验证表现层,不污染真实审阅数据)。
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/v2-blueprint-wave2-shots.mjs [--out <目录>] [--lofa-api https://10.3.43.246:12443]
 */
import { chromium } from 'playwright';
import { readFile, mkdir } from 'fs/promises';
import { extname, join, normalize } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave2'));
const LOFA_API = arg('lofa-api', 'https://10.3.43.246:12443');
const WWW = arg('lofa-www', 'E:/WindowsWorkspace/lofa/app/www');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ args: ['--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests'] });

async function lofaPage(w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true });
  // www 静态文件 → http://localhost 源
  await ctx.route('http://localhost/**', async (route) => {
    let p = new URL(route.request().url()).pathname;
    if (p === '/' || p === '') p = '/index.html';
    try { await route.fulfill({ status: 200, contentType: MIME[extname(p).toLowerCase()] || 'application/octet-stream', body: await readFile(normalize(join(WWW, p))) }); }
    catch { await route.fulfill({ status: 404, body: 'nf' }); }
  });
  // 审阅写操作本地 fulfill(不污染真实数据):GET 全放行,非 GET 直接 200。
  await ctx.route('**/api/boss-sight/reviewstage/**', (route) => {
    const req = route.request();
    if (req.method() === 'GET') return route.continue();
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, mocked: true }) });
  });
  await ctx.addInitScript((api) => { try { localStorage.setItem('lofa.baseUrl', api); } catch (e) {} }, LOFA_API);
  const page = await ctx.newPage();
  await page.goto('http://localhost/', { waitUntil: 'load', timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(5000);
  return { ctx, page };
}
const shot = (page, name) => page.screenshot({ path: join(OUT, `${name}.png`) }).then(() => console.log('ok', name));
const visible = (page, sel) => page.locator(sel).first().isVisible().catch(() => false);

// ── phone 390×844:sessions(filterbar/picker/ⓘsheet/批量) + review(队列/批量/详情/盖章/目录) + me ──
{
  const { ctx, page } = await lofaPage(390, 844);
  await shot(page, 'phone-sessions');

  if (await visible(page, '#sessionsSrc .tpk-btn')) {
    await page.click('#sessionsSrc .tpk-btn').catch(() => {});
    await page.waitForTimeout(400);
    await shot(page, 'phone-sessions-picker');
    await page.keyboard.press('Escape').catch(() => {});
    await page.click('#sessionsFilter').catch(() => {});
    await page.waitForTimeout(200);
  }
  if (await visible(page, '#sessionsView .lg-row .lr-info')) {
    await page.locator('#sessionsView .lg-row .lr-info').first().click().catch(() => {});
    await page.waitForTimeout(500);
    await shot(page, 'phone-sessions-info-sheet');
    await page.locator('.lg-ov').first().click({ position: { x: 10, y: 10 } }).catch(() => {});
    await page.waitForTimeout(300);
  }
  if (await visible(page, '#sessionsSelect')) {
    await page.click('#sessionsSelect').catch(() => {});
    await page.waitForTimeout(300);
    const row = page.locator('#sessionsView .lg-row').first();
    if (await row.count()) await row.click().catch(() => {});
    await page.waitForTimeout(300);
    await shot(page, 'phone-sessions-batch');
    await page.click('#sessionsSelect').catch(() => {});
    await page.waitForTimeout(200);
  }

  await page.click('#bottomNav .lg-tab[data-tab="review"]').catch(() => {});
  await page.waitForTimeout(2500);
  await shot(page, 'phone-review-queue');

  if (await visible(page, '#reviewSelect')) {
    await page.click('#reviewSelect').catch(() => {});
    await page.waitForTimeout(300);
    const row = page.locator('#reviewView .rv-row').first();
    if (await row.count()) await row.click().catch(() => {});
    await page.waitForTimeout(300);
    await shot(page, 'phone-review-batch');
    await page.click('#reviewSelect').catch(() => {});
    await page.waitForTimeout(200);
  }
  if (await visible(page, '#reviewView .rv-row .lr-info')) {
    await page.locator('#reviewView .rv-row .lr-info').first().click().catch(() => {});
    await page.waitForTimeout(500);
    await shot(page, 'phone-review-info-sheet');
    await page.locator('.lg-ov').first().click({ position: { x: 10, y: 10 } }).catch(() => {});
    await page.waitForTimeout(300);
  }
  const qrow = page.locator('#reviewView .rv-row').first();
  if (await qrow.count()) {
    await qrow.click().catch(() => {});
    await page.waitForTimeout(2500);
    await shot(page, 'phone-review-detail');
    if (await visible(page, '#reviewDetailBar .vd-i[data-v="accepted"]')) {
      await page.click('#reviewDetailBar .vd-i[data-v="accepted"]').catch(() => {});
      await page.waitForTimeout(700);
      await shot(page, 'phone-review-detail-stamp');
    }
    await page.locator('#reviewDetailView .lg-nav-back').click().catch(() => {});
    await page.waitForTimeout(500);
  }
  // TOC:「目录」钮只在正文有 h2/h3 时出现——跨 seg 逐行找一条带章节的材料(最多 2 seg × 5 行)。
  {
    let found = false;
    outer: for (const seg of ['pending', 'all']) {
      await page.locator('#reviewSeg button[data-v="' + seg + '"]').click().catch(() => {});
      await page.waitForTimeout(2000);
      const rows = page.locator('#reviewView .rv-row');
      const n = Math.min(await rows.count(), 5);
      for (let i = 0; i < n; i++) {
        await rows.nth(i).click().catch(() => {});
        await page.waitForTimeout(2000);
        if (await visible(page, '#reviewToc')) { found = true; break outer; }
        await page.locator('#reviewDetailView .lg-nav-back').click().catch(() => {});
        await page.waitForTimeout(500);
      }
    }
    if (found) {
      await page.click('#reviewToc').catch(() => {});
      await page.waitForTimeout(400);
      await shot(page, 'phone-review-toc');
      await page.locator('#reviewDetailScroll').click({ position: { x: 40, y: 300 } }).catch(() => {});
      await page.waitForTimeout(200);
      await page.locator('#reviewDetailView .lg-nav-back').click().catch(() => {});
      await page.waitForTimeout(400);
    } else console.log('skip phone-review-toc(队列前 10 条均无 h2/h3 章节)');
    // 恢复默认 seg,不污染后续形态。
    await page.locator('#reviewSeg button[data-v="pending"]').click().catch(() => {});
    await page.waitForTimeout(1200);
  }

  await page.click('#bottomNav .lg-tab[data-tab="me"]').catch(() => {});
  await page.waitForTimeout(1200);
  await shot(page, 'phone-me');
  await ctx.close();
}
// ── tablet 834×1194:sessions 限宽 + review split 双栏(盖章落 detail 列) ──
{
  const { ctx, page } = await lofaPage(834, 1194);
  await shot(page, 'tablet-sessions');
  await page.click('#bottomNav .lg-tab[data-tab="review"]').catch(() => {});
  await page.waitForTimeout(2500);
  await shot(page, 'tablet-review-queue');
  const qrow = page.locator('#reviewView .rv-row').first();
  if (await qrow.count()) {
    await qrow.click().catch(() => {});
    await page.waitForTimeout(2500);
    await shot(page, 'tablet-review-split');
    if (await visible(page, '#reviewDetailBar .vd-i[data-v="accepted"]')) {
      await page.click('#reviewDetailBar .vd-i[data-v="accepted"]').catch(() => {});
      await page.waitForTimeout(700);
      await shot(page, 'tablet-review-split-stamp');
    }
  }
  await ctx.close();
}
// ── tablet-land 1194×834:横屏 split + 底部胶囊 nav 形态 ──
{
  const { ctx, page } = await lofaPage(1194, 834);
  await page.click('#bottomNav .lg-tab[data-tab="review"]').catch(() => {});
  await page.waitForTimeout(2500);
  const qrow = page.locator('#reviewView .rv-row').first();
  if (await qrow.count()) {
    await qrow.click().catch(() => {});
    await page.waitForTimeout(2500);
  }
  await shot(page, 'tablet-land-review-split');
  await ctx.close();
}
await browser.close();
console.log(`done → ${OUT}`);
