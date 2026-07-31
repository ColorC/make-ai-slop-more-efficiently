#!/usr/bin/env node
/**
 * triform-shots.mjs — 三形态视觉回归截图(dashboard 网页 / LOFA 手机 / LOFA 平板)。
 *
 * 用途:前端升级(见 docs/plans/frontend-design/[2026-07-18]UNIFIED-FRONTEND-UPGRADE)
 * 每个里程碑的改前改后成对截图基建。输出 PNG 到 --out 目录(默认 ./_shots)。
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/triform-shots.mjs [--out <目录>] [--dash http://127.0.0.1:8210] [--lofa-api https://10.3.43.246:12443]
 *
 * 原理:
 * - dashboard:直连 8210(服务 frontend/dist,改完先 npm run build)。
 * - LOFA:www 无构建,经 playwright 路由拦截以 http://localhost 源供本地文件
 *   (CORS 白名单仅 capacitor://localhost 与 http://localhost);API 走 Caddy HTTPS
 *   单端口,注入 localStorage lofa.baseUrl;Chromium 需关 LocalNetworkAccessChecks
 *   (localhost→LAN 的 PNA 拦截)。
 * - 视口:手机 390×844 / 平板竖 834×1194 / 平板横 1194×834 / 桌面 1600×1000。
 */
import { chromium } from 'playwright';
import { readFile, mkdir } from 'fs/promises';
import { extname, join, normalize } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots'));
const DASH = arg('dash', 'http://127.0.0.1:8210');
const LOFA_API = arg('lofa-api', 'https://10.3.43.246:12443');
const WWW = arg('lofa-www', 'C:/workspace/lofa/app/www');
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
async function dashPage(w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  await page.goto(`${DASH}/`, { waitUntil: 'networkidle', timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(4500);
  return { ctx, page };
}
const shot = (page, name) => page.screenshot({ path: join(OUT, `${name}.png`) }).then(() => console.log('ok', name));

// ── dashboard:桌面回归 + 平板竖/横 + 手机宽 ──
for (const [tag, w, h] of [['desktop', 1600, 1000], ['tablet-port', 834, 1194], ['tablet-land', 1194, 834], ['phone', 390, 844]]) {
  const { ctx, page } = await dashPage(w, h);
  await shot(page, `dash-${tag}`);
  await ctx.close();
}
// ── LOFA:手机会话 + 平板竖会话 + 平板竖审阅双栏 + 旋转横屏 + 平板横侧 rail ──
{
  const { ctx, page } = await lofaPage(390, 844);
  await shot(page, 'lofa-phone-sessions');
  await ctx.close();
}
{
  const { ctx, page } = await lofaPage(834, 1194);
  await shot(page, 'lofa-tport-sessions');
  await page.click('#bottomNav .lg-tab[data-tab="review"]').catch(() => {});
  await page.waitForTimeout(2000);
  await page.click('#reviewView .lg-row').catch(() => {});
  await page.waitForTimeout(2500);
  await shot(page, 'lofa-tport-review-split');
  await page.setViewportSize({ width: 1194, height: 834 });
  await page.waitForTimeout(1500);
  await shot(page, 'lofa-tland-review-split-rotated');
  await ctx.close();
}
{
  const { ctx, page } = await lofaPage(1194, 834);
  await shot(page, 'lofa-tland-sessions');
  await ctx.close();
}
await browser.close();
console.log(`done → ${OUT}`);
