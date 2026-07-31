#!/usr/bin/env node
/**
 * v2-blueprint-wave5-shots.mjs — V2 阶段四第六波「两条用户裁决」视觉验证。
 *
 * 裁决一:审阅列表栏软衬底 rgba(9,26,62,.45)(dashboard .rf-side / LOFA split master 列)
 *        ——网格隐约可见 + 文字有床。
 * 裁决二:rail/页签条透明化(dashboard .sha-rail + dockview 页签条 / LOFA ≥1024 侧 rail)
 *        ——材质只留 icon 底衬与页签/按钮本体;展开态两版对比(A=纯透明+text-shadow,
 *        B=展开态给 .45 软衬底)各截一张供选型。
 *
 * dashboard 直打真实 8210 服务(frontend/static = vite outDir, npm run build 后即为新产物);
 * LOFA 供文件法照 v2-blueprint-wave4-lofa-shots.mjs(route 拦截本地 www,API 走 Caddy HTTPS)。
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/v2-blueprint-wave5-shots.mjs [--out <目录>] [--base http://localhost:8210] [--lofa-api https://10.3.43.246:12443]
 */
import { chromium } from 'playwright';
import { readFile, mkdir } from 'fs/promises';
import { extname, join, normalize } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave5'));
const BASE = arg('base', 'http://localhost:8210');
const LOFA_API = arg('lofa-api', 'https://10.3.43.246:12443');
const WWW = arg('lofa-www', 'E:/WindowsWorkspace/lofa/app/www');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ args: ['--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests'] });
const shot = (page, name, opts = {}) =>
  page.screenshot({ path: join(OUT, `${name}.png`), ...opts }).then(() => console.log('ok', name));

async function openDash(url, w, h, waitSel, mobile = false) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
  if (waitSel) await page.waitForSelector(waitSel, { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1800); // 字体/徽章/玻璃 backdrop 二次渲染稳定
  return { ctx, page };
}

/** 裁决探针:软衬底/透明化落在计算样式上的证据。 */
async function dashProbe(page, tag) {
  const r = await page.evaluate(() => {
    const cs = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el) : null; };
    const side = cs('.rf-side');
    const rail = cs('.sha-rail');
    const tile = cs('.sha-rail .ico-c') || cs('.sha-rail .ico-s');
    const bar = cs('.dv-tabs-and-actions-container');
    const tab = cs('.dv-tab');
    const atab = cs('.dv-tab.dv-active-tab');
    return {
      sideBg: side ? side.backgroundColor : null,
      railBg: rail ? rail.backgroundColor : null,
      railBorderRight: rail ? rail.borderRight : null,
      railBackdrop: rail ? (rail.backdropFilter || rail.webkitBackdropFilter) : null,
      tileBg: tile ? tile.backgroundColor : null,
      tileBackdrop: tile ? (tile.backdropFilter || tile.webkitBackdropFilter) : null,
      barBg: bar ? bar.backgroundColor : null,
      barBackdrop: bar ? (bar.backdropFilter || bar.webkitBackdropFilter) : null,
      tabBg: tab ? tab.backgroundColor : null,
      tabBackdrop: tab ? (tab.backdropFilter || tab.webkitBackdropFilter) : null,
      activeTabBgImage: atab ? atab.backgroundImage.slice(0, 60) : null,
    };
  });
  console.log(`probe[${tag}]`, JSON.stringify(r));
  return r;
}

// ══ dashboard · desktop 1600×950:审阅队列(软衬底)/ rail 收起/展开两版 / 页签条特写 / 详情米卡 ══
{
  const { ctx, page } = await openDash(`${BASE}/?open_type=review_queue&open_id=main`, 1600, 950, '.rf-qrow');
  await dashProbe(page, 'desktop-review');
  await shot(page, 'desktop-review-queue');                       // 软衬底+文字可读+网格隐约(整页)

  // rail 收起态特写(透明露网格;56px rail + 右侧接缝)
  await shot(page, 'desktop-rail-collapsed', { clip: { x: 0, y: 0, width: 240, height: 950 } });

  // 展开态 A 版(当前 CSS:纯透明 + text-shadow 软垫)
  await page.hover('.sha-rail').catch(() => {});
  await page.waitForTimeout(700);
  await shot(page, 'desktop-rail-expanded-a-transparent', { clip: { x: 0, y: 0, width: 320, height: 950 } });

  // 展开态 B 版(候选:展开态给 .45 软衬底一档;addStyleTag 临时注入,不改源码)
  await page.addStyleTag({ content: '.sha-rail:hover { background: rgba(9,26,62,.45) !important; }' }).catch(() => {});
  await page.mouse.move(900, 500); await page.waitForTimeout(300);
  await page.hover('.sha-rail').catch(() => {});
  await page.waitForTimeout(700);
  await shot(page, 'desktop-rail-expanded-b-softbed', { clip: { x: 0, y: 0, width: 320, height: 950 } });
  await page.mouse.move(900, 500); await page.waitForTimeout(400);

  // 页签条特写(页签 chip + 间隙露网格 + active hatch 下划线/白竖标)
  const bar = await page.locator('.dv-tabs-and-actions-container').first().boundingBox().catch(() => null);
  if (bar) {
    await shot(page, 'desktop-tabstrip-closeup', {
      clip: { x: Math.max(0, bar.x - 20), y: Math.max(0, bar.y - 14), width: Math.min(900, bar.width + 40), height: bar.height + 40 },
    });
  } else console.log('skip desktop-tabstrip-closeup(无页签条)');

  // 审阅详情米卡(不受影响证据:米卡整列满铺,蓝边露出网格)
  // 点行后鼠标必须移开行区——否则 hover 预览卡(HoverCard)会盖在详情上。
  // 米卡只给文档型材料(resolveMaterialRenderer().document===true);占满型(iframe/视频)裸铺。
  // 逐行试点,直到撞上出米卡的文档材料(最多 8 行)。
  let gotDoc = false;
  const rows = page.locator('.rf-qrow');
  const n = Math.min(8, await rows.count());
  for (let i = 0; i < n && !gotDoc; i++) {
    await rows.nth(i).click().catch(() => {});
    await page.mouse.move(1350, 500);
    gotDoc = await page.waitForSelector('.rf-docwrap', { timeout: 6000 }).then(() => true).catch(() => false);
    if (!gotDoc) await page.waitForTimeout(400);
  }
  await page.waitForTimeout(1200);
  if (!gotDoc) console.log('warn: 前 8 行无文档型材料,米卡未拍到(详情区为 bleed/iframe)');
  await shot(page, 'desktop-review-detail-doc');
  await ctx.close();
}
// ══ dashboard · tablet 834×1194:审阅队列(软衬底 + 收起 rail 透明) ══
{
  const { ctx, page } = await openDash(`${BASE}/?open_type=review_queue&open_id=main`, 834, 1194, '.rf-qrow', true);
  await dashProbe(page, 'tablet-review');
  await shot(page, 'tablet-review-queue');
  // 点开详情(tablet 双栏):逐行试点直到文档型材料出米卡;占满型(iframe/视频)裸铺,如实记录
  const tRows = page.locator('.rf-qrow');
  const tn = Math.min(8, await tRows.count());
  let tGot = false;
  for (let i = 0; i < tn && !tGot; i++) {
    await tRows.nth(i).click().catch(() => {});
    await page.mouse.move(700, 600).catch(() => {});
    tGot = await page.waitForSelector('.rf-docwrap', { timeout: 6000 }).then(() => true).catch(() => false);
    if (!tGot) await page.waitForTimeout(400);
  }
  await page.waitForTimeout(1200);
  await shot(page, 'tablet-review-detail');
  await ctx.close();
}

// ══ LOFA:phone 会话(胶囊 nav 不受影响证据)/ tablet-land(rail 透明 + 审阅 master 软衬底) ══
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
async function lofaProbe(page, tag) {
  const r = await page.evaluate(() => {
    const cs = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el) : null; };
    const nav = cs('#bottomNav');
    const tab = cs('#bottomNav .lg-tab');
    const master = cs('#reviewView.split-master');
    return {
      navBg: nav ? nav.backgroundColor : null,
      navBackdrop: nav ? (nav.backdropFilter || nav.webkitBackdropFilter) : null,
      navBorderRight: nav ? nav.borderRight : null,
      tabBg: tab ? tab.backgroundColor : null,
      tabBackdrop: tab ? (tab.backdropFilter || tab.webkitBackdropFilter) : null,
      masterBg: master ? master.backgroundColor : null,
    };
  });
  console.log(`probe[${tag}]`, JSON.stringify(r));
  return r;
}
{
  const { ctx, page } = await lofaPage(390, 844);
  await lofaProbe(page, 'lofa-phone-sessions');
  await shot(page, 'lofa-phone-sessions');                        // 胶囊 nav 保持玻璃(不受影响)
  await ctx.close();
}
{
  const { ctx, page } = await lofaPage(1194, 834);
  await lofaProbe(page, 'lofa-tland-sessions');
  await shot(page, 'lofa-tland-sessions');                        // 侧 rail 透明 + tab 玻璃 chip
  await page.click('#bottomNav .lg-tab[data-tab="review"]').catch(() => {});
  await page.waitForTimeout(2000);
  await page.click('#reviewView .lg-row').catch(() => {});        // 点开详情 → body.split-active(master/detail 双栏)
  await page.waitForTimeout(2500);
  await lofaProbe(page, 'lofa-tland-review');
  await shot(page, 'lofa-tland-review-split');                    // 审阅 master 列软衬底
  await ctx.close();
}
await browser.close();
console.log(`done → ${OUT}`);
