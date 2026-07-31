#!/usr/bin/env node
/**
 * v2-blueprint-wave4-lofa-shots.mjs — V2 阶段四第五波「材质裁决」LOFA 侧视觉验证截图。
 *
 * 验证点:统一网格层(#ambient 唯一真源,卡片间连续)/ 蓝图玻璃行卡(.58/hover .68/text .72)
 * / 页头刻度尺(.bp-ruler-top)/ 平板网格露出带。供文件法照 triform-shots.mjs:
 * playwright 路由拦截 http://localhost 源供本地 www(CORS 白名单),API 走 Caddy HTTPS
 * 单端口,注入 localStorage lofa.baseUrl;Chromium 关 LocalNetworkAccessChecks。
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/v2-blueprint-wave4-lofa-shots.mjs [--out <目录>] [--lofa-api https://10.3.43.246:12443]
 *
 * 视口:手机 390×844(会话 + 审阅详情)/ 平板竖 834×1194(会话 + 审阅双栏)/ 平板横 1194×834(会话 + 审阅双栏)。
 */
import { chromium } from 'playwright';
import { readFile, mkdir } from 'fs/promises';
import { extname, join, normalize } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave4-lofa'));
const LOFA_API = arg('lofa-api', 'https://10.3.43.246:12443');
const WWW = arg('lofa-www', 'E:/WindowsWorkspace/lofa/app/www');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ args: ['--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests'] });

async function lofaPage(w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true });
  await ctx.route('http://localhost/**', async (route) => {
    let p = new URL(route.request().url()).pathname;
    if (p === '/' || p === '') p = '/index.html';
    try { await route.fulfill({ status: 200, contentType: MIME[extname(p).toLowerCase()] || 'application/octet-stream', body: await readFile(normalize(join(WWW, p))) }); }
    catch { await route.fulfill({ status: 404, body: 'nf' }); }
  });
  await ctx.addInitScript((api) => { try { localStorage.setItem('lofa.baseUrl', api); } catch (e) {} }, LOFA_API);
  const page = await ctx.newPage();
  await page.goto('http://localhost/', { waitUntil: 'load', timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(5000);
  return { ctx, page };
}
const shot = (page, name) => page.screenshot({ path: join(OUT, `${name}.png`) }).then(() => console.log('ok', name));

/** 网格/玻璃/尺条探针:容器透明 + #ambient 是唯一网格源 + 行卡玻璃参数 + 尺条在位。 */
async function probe(page, tag) {
  const r = await page.evaluate(() => {
    const cs = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el) : null; };
    const amb = cs('#ambient');
    const view = cs('.view.show');
    const scroll = cs('.view.show .scroll') || cs('.view.show');
    const row = cs('.view.show .lg-row');
    const ruler = document.querySelector('.view.show .bp-ruler-top');
    return {
      ambientFixed: amb && amb.position === 'fixed',
      ambientHasGrid: amb ? /repeating-linear-gradient/.test(amb.backgroundImage) : false,
      viewBg: view ? view.backgroundColor : null,
      scrollBg: scroll ? scroll.backgroundColor : null,
      rowBg: row ? row.backgroundColor : null,
      rowBackdrop: row ? (row.backdropFilter || row.webkitBackdropFilter) : null,
      rulerMounted: !!ruler,
      rulerAriaHidden: ruler ? ruler.getAttribute('aria-hidden') : null,
      rulerTicks: ruler ? /repeating-linear-gradient/.test(getComputedStyle(ruler).backgroundImage) : false,
    };
  });
  console.log(`probe[${tag}]`, JSON.stringify(r));
  return r;
}

async function gotoReview(page) {
  await page.click('#bottomNav .lg-tab[data-tab="review"]').catch(() => {});
  await page.waitForTimeout(2000);
}
async function openReviewDetail(page) {
  await page.click('#reviewView .lg-row').catch(() => {});
  await page.waitForTimeout(2500);
}

// ── 手机 390×844:会话 + 审阅详情 ──
{
  const { ctx, page } = await lofaPage(390, 844);
  await probe(page, 'phone-sessions');
  await shot(page, 'lofa-phone-sessions');
  await gotoReview(page);
  await openReviewDetail(page);
  await probe(page, 'phone-review-detail');
  await shot(page, 'lofa-phone-review-detail');
  await ctx.close();
}
// ── 平板竖 834×1194:会话 + 审阅双栏 ──
{
  const { ctx, page } = await lofaPage(834, 1194);
  await probe(page, 'tport-sessions');
  await shot(page, 'lofa-tport-sessions');
  await gotoReview(page);
  await openReviewDetail(page);
  await shot(page, 'lofa-tport-review-split');
  await ctx.close();
}
// ── 平板横 1194×834:会话 + 审阅双栏 ──
{
  const { ctx, page } = await lofaPage(1194, 834);
  await probe(page, 'tland-sessions');
  await shot(page, 'lofa-tland-sessions');
  await gotoReview(page);
  await openReviewDetail(page);
  await shot(page, 'lofa-tland-review-split');
  await ctx.close();
}
await browser.close();
console.log(`done → ${OUT}`);
