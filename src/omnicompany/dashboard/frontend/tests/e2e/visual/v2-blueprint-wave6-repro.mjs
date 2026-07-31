#!/usr/bin/env node
/**
 * v2-blueprint-wave6-repro.mjs — V2 阶段四第七波 · 页签策略问题复现(只读,不改源码)。
 *
 * 复现三条:
 *  A. 全新 profile(无 localStorage)开 BASE: 记录默认 tabs/groups/active + 截图。
 *  B. 手工开 2 个页签(审阅队列 + 一条材料)、切 active、刷新: 记录恢复行为。
 *  C. 脏快照模拟(用户真实状态): 种 v2 快照含 project:detail 页签 + controllerRight=1,
 *     刷新后看是否出现"双项目/多 dock 组"。
 *  D. 两组同屏时(手动拖出分屏)再刷新: 看恢复是否收敛回单组。
 *
 * 用法(frontend/ 目录): node tests/e2e/visual/v2-blueprint-wave6-repro.mjs [--base http://localhost:8210]
 */
import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';
import { join } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave6'));
const BASE = arg('base', 'http://localhost:8210');

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const shot = (page, name) =>
  page.screenshot({ path: join(OUT, `${name}.png`) }).then(() => console.log('ok', name));

/** 读出 dockview 组结构 + 每组页签标题 + active + 组内实际渲染的页面标志。 */
async function dumpLayout(page, tag) {
  const r = await page.evaluate(() => {
    const groups = Array.from(document.querySelectorAll('.dv-groupview'));
    const g = groups.map((gv) => {
      const tabs = Array.from(gv.querySelectorAll('.dv-tab')).map((t) => ({
        title: (t.textContent || '').trim(),
        active: t.classList.contains('dv-active-tab'),
      }));
      // 组内可见内容的标志: 项目板/任务窗口/总控各自根标志
      const content = gv.querySelector('.dv-content-container');
      const flags = {
        projectBoard: !!gv.querySelector('[data-testid="project-board"], .pb, [class*="project-board"]'),
        questBoard: !!gv.querySelector('.qb, [class*="quest"]'),
        controller: !!gv.querySelector('[data-testid*="controller"], [data-cc-chat-panel]'),
        text: content ? (content.textContent || '').replace(/\s+/g, ' ').slice(0, 80) : '',
      };
      const rect = gv.getBoundingClientRect();
      return { tabs, flags, x: Math.round(rect.x), w: Math.round(rect.width) };
    });
    return {
      groupCount: groups.length,
      groups: g,
      ls: Object.keys(localStorage).filter((k) => k.startsWith('omni.')),
      snapshot: localStorage.getItem('omni.cockpit.tabSnapshot.v2'),
    };
  });
  console.log(`\n== ${tag} ==`);
  console.log(JSON.stringify(r, null, 1).slice(0, 2400));
  return r;
}

// ── A. 全新 profile 默认态 ─────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
  await page.waitForSelector('.dv-tab', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(2500);
  await dumpLayout(page, 'A 全新默认态');
  await shot(page, 'repro-a-fresh-default');

  // ── B. 开 2 个页签 + 切 active + 刷新 ──
  await page.click('[data-testid="cockpit-nav-review"]').catch((e) => console.log('nav-review click fail', e.message));
  await page.waitForTimeout(2200);
  const row = page.locator('.rf-qrow').first();
  if (await row.count()) {
    await row.click().catch(() => {});
    await page.mouse.move(1300, 500);
    await page.waitForTimeout(2500);
  } else console.log('warn: 无 .rf-qrow, 材料页签开不了');
  await dumpLayout(page, 'B 开了审阅+材料后(刷新前)');
  await shot(page, 'repro-b-before-refresh');
  await page.reload({ waitUntil: 'load' }).catch(() => {});
  await page.waitForSelector('.dv-tab', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(3000);
  await dumpLayout(page, 'B 刷新后恢复态');
  await shot(page, 'repro-b-after-refresh');
  await ctx.close();
}

// ── C. 脏快照: v2 快照含 project 详情页 + 重复 id + controllerRight ──
{
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('omni.cockpit.tabSnapshot.v2', JSON.stringify([
        { id: 'project:demo-p1', ref: { type: 'project', id: 'demo-p1' }, title: '项目:demo-p1' },
        // 旧 bug 面: 快照里混入与固定页签同 id 但 ref 不同的脏条目
        { id: 'quest_board:main', ref: { type: 'project_board', id: 'main' }, title: '项目' },
        { id: 'review_queue:main', ref: { type: 'review_queue', id: 'main' }, title: '审阅' },
      ]));
      localStorage.setItem('omni.cockpit.controllerRight', '1');
    } catch (e) {}
  });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
  await page.waitForSelector('.dv-tab', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(3000);
  await dumpLayout(page, 'C 脏快照恢复态');
  await shot(page, 'repro-c-dirty-snapshot');
  await ctx.close();
}

await browser.close();
console.log(`done → ${OUT}`);
