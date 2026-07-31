#!/usr/bin/env node
/**
 * census.mjs — 前端全量页面普查(TRIFORM-UX-REDESIGN-V2 阶段一,只读+静态产物)。
 *
 * 对象:
 * - dashboard 23 个注册实体 × [desktop 1600×1000, tablet-port 834×1194]
 *   深链: http://127.0.0.1:8210/?open_type=<type>&open_id=<id>&open_title=<title>
 *   (CockpitShell.tsx 的 open_type/open_id useEffect; 列表类实体 id 运行时从 /api 拉真实值)
 * - LOFA 12 视图 × [phone 390×844, tablet-port 834×1194, tablet-land 1194×834]
 *   路由拦截 http://localhost 供本地 lofa/app/www(同 triform-shots.mjs),
 *   注入 localStorage lofa.baseUrl 指向真机 API。
 *
 * 每页三份快照: shot.png / dom.html(page.content()) / a11y.json。
 * a11y 口径回退链: page.accessibility.snapshot() → CDP Accessibility.getFullAXTree
 *   → 注入脚本收集 [role],button,a,input,select,textarea,[tabindex] 清单。
 *   每页记录实际口径,INDEX.md 汇总注明。
 *
 * 用法(在 frontend/ 目录下跑):
 *   node tests/e2e/visual/census.mjs [--out <census目录>] [--only dashboard|lofa]
 *     [--dash http://127.0.0.1:8210] [--lofa-api https://10.3.43.246:12443]
 */
import { chromium } from 'playwright';
import { readFile, mkdir, writeFile } from 'fs/promises';
import { extname, join, normalize } from 'path';

const args = process.argv.slice(2);
const arg = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = arg('out', 'E:/WindowsWorkspace/omnicompany/docs/plans/frontend-design/[2026-07-18]TRIFORM-UX-REDESIGN-V2/census');
const ONLY = arg('only', '');
const VIEWS = (arg('views', '') || '').split(',').map((s) => s.trim()).filter(Boolean);
const DASH = arg('dash', 'http://127.0.0.1:8210');
const LOFA_API = arg('lofa-api', 'https://10.3.43.246:12443');
const WWW = arg('lofa-www', 'E:/WindowsWorkspace/lofa/app/www');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };

const manifest = { generatedAt: new Date().toISOString(), dash: DASH, lofaApi: LOFA_API, captures: [], unreachable: [] };
const a11yMethods = new Set();

// ── 通用:三份快照 ────────────────────────────────────────────────────────────
async function a11ySnapshot(page) {
  try {
    if (page.accessibility && typeof page.accessibility.snapshot === 'function') {
      const tree = await page.accessibility.snapshot({ interestingOnly: false });
      if (tree) return { method: 'playwright.accessibility.snapshot(interestingOnly:false)', tree };
    }
  } catch (e) { /* fall through */ }
  try {
    const cdp = await page.context().newCDPSession(page);
    const r = await cdp.send('Accessibility.getFullAXTree');
    return { method: 'cdp Accessibility.getFullAXTree', tree: r };
  } catch (e) { /* fall through */ }
  const list = await page.evaluate(() => {
    const sel = '[role],button,a,input,select,textarea,[tabindex]';
    return Array.from(document.querySelectorAll(sel)).map((el) => ({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || undefined,
      type: el.getAttribute('type') || undefined,
      tabindex: el.getAttribute('tabindex') || undefined,
      ariaLabel: el.getAttribute('aria-label') || undefined,
      text: (el.textContent || '').trim().slice(0, 80) || undefined,
      href: el.getAttribute('href') || undefined,
      disabled: el.disabled || undefined,
    }));
  }).catch(() => []);
  return { method: 'injected interactive-elements list ([role],button,a,input,select,textarea,[tabindex])', tree: list };
}

async function capture(page, dir) {
  await mkdir(dir, { recursive: true });
  const a11y = await a11ySnapshot(page);
  a11yMethods.add(a11y.method);
  await writeFile(join(dir, 'a11y.json'), JSON.stringify({ method: a11y.method, capturedAt: new Date().toISOString(), url: page.url(), tree: a11y.tree }, null, 2));
  await writeFile(join(dir, 'dom.html'), await page.content());
  await page.screenshot({ path: join(dir, 'shot.png') });
  return a11y.method;
}

function rec(app, pageName, formfactor, data) {
  manifest.captures.push({ app, page: pageName, formfactor, ...data });
}

// ── dashboard ────────────────────────────────────────────────────────────────
async function fetchJson(url, fallback) {
  try { const r = await fetch(url); if (!r.ok) return fallback; return await r.json(); }
  catch { return fallback; }
}

async function resolveDashboardIds() {
  const first = (arr) => (Array.isArray(arr) && arr.length ? arr[0] : null);
  const [projects, plans, materials, notes, workers, teams, cc, traces, review, ide] = await Promise.all([
    fetchJson(`${DASH}/api/projects`, null),
    fetchJson(`${DASH}/api/plans`, null),
    fetchJson(`${DASH}/api/materials`, null),
    fetchJson(`${DASH}/api/notes`, null),
    fetchJson(`${DASH}/api/workers`, null),
    fetchJson(`${DASH}/api/teams`, null),
    fetchJson(`${DASH}/api/cc/sessions?include_recoverable=true`, null),
    fetchJson(`${DASH}/api/v2/trace-list?limit=5`, null),
    fetchJson(`${DASH}/api/boss-sight/reviewstage`, null),
    fetchJson(`${DASH}/api/v2/ide/sessions`, []),
  ]);
  const ccAlive = (cc && cc.items && cc.items.find((s) => s.alive)) || first(cc && cc.items) || first(cc && cc.recoverable);
  return {
    project: (first(projects && projects.projects) || {}).id || null,
    plan: (first(plans && plans.items) || {}).id || null,
    material: (first(materials && materials.items) || {}).id || null,
    note: (first(notes && notes.items) || {}).id || null,
    worker: (first(workers && workers.items) || {}).id || null,
    team: (first(teams && teams.items) || {}).id || null,
    cc_session: (ccAlive || {}).id || null,
    trace: (first(traces && traces.items) || {}).trace_id || null,
    review_material: (first(review && review.items) || {}).id || null,
    session: (first(ide) || {}).trace_id || null, // 已退役实体;空列表走回退
  };
}

// name=普查目录名/计划清单名; type=registry 真实 type 字符串; id 可为函数(运行时解析)
// wait=goto 后额外等待 ms; desc=一句话现状
const DASH_ENTITIES = [
  { name: 'project_board', type: 'project_board', id: 'main', desc: '项目工作板(首页默认页签,项目卡平铺)' },
  { name: 'project', type: 'project', id: (ids) => ids.project, desc: '项目详情(单项目工作区+材料轨迹)' },
  { name: 'quest_board', type: 'quest_board', id: 'main', desc: '任务窗口(主线/支线任务清单)' },
  { name: 'note', type: 'note', id: (ids) => ids.note, desc: 'Markdown 笔记视图(双链知识库)' },
  { name: 'graph', type: 'graph', id: 'main', wait: 7000, desc: 'KB 关系图谱(节点大图)' },
  { name: 'plan', type: 'plan', id: (ids) => ids.plan, desc: '计划文件夹详情(文件清单+关联会话)' },
  { name: 'worker', type: 'worker', id: (ids) => ids.worker, desc: 'worker 节点视图(设计/运行/历史 facets)' },
  { name: 'team', type: 'team', id: (ids) => ids.team, wait: 6000, desc: 'team 定义视图(代码+图)' },
  { name: 'team_board', type: 'team_board', id: 'main', wait: 6000, desc: '管线板(team DAG 总览)' },
  { name: 'material', type: 'material', id: (ids) => ids.material, desc: 'material 源码/契约视图' },
  { name: 'controller', type: 'controller', id: 'main', wait: 6000, desc: '总控(多 agent 会话监控)' },
  { name: 'material_registry', type: 'material_registry', id: 'main', desc: '任务材料注册表' },
  { name: 'review_queue', type: 'review_queue', id: 'main', wait: 6000, desc: '审阅队列(材料卡+侧栏筛选)' },
  { name: 'review_material', type: 'review_material', id: (ids) => ids.review_material, wait: 6000, desc: '单条审阅材料(富渲染+批注+裁决)' },
  { name: 'web_review', type: 'web_review', id: 'walker-game', wait: 9000, desc: '网页审阅(iframe 内嵌 walker-game+圈选批注)' },
  { name: 'session', type: 'session', id: (ids) => ids.session || '__none__', desc: '旧 IDE 会话(已退役实体,ide/sessions 空列表,回退态)' },
  { name: 'cc_session', type: 'cc_session', id: (ids) => ids.cc_session, wait: 7000, desc: 'PTY 会话(xterm 终端流)' },
  { name: 'trace', type: 'trace', id: (ids) => ids.trace, desc: 'trace 详情(事件流时间线)' },
  { name: 'authored', type: 'authored', id: 'main', desc: '草稿箱(authored notes 集中管理)' },
  { name: 'plan_audit', type: 'plan_audit', id: 'census-readonly-probe', desc: '计划审计报告(只读普查不起新 job,拍到的是过期任务错误态)' },
  { name: 'nav-audit', type: 'nav_audit', id: 'main', wait: 6000, desc: '可达性审计(实体导航孤岛扫描)' },
  { name: 'studio_reader', type: 'studio_reader', id: 'omnicompany', wait: 7000, desc: '阅读视图(材料本体+决策面板,id=项目名)' },
  { name: 'settings', type: 'settings', id: 'main', desc: '设置/系统信息' },
];

const DASH_FORMS = [['desktop', 1600, 1000], ['tablet-port', 834, 1194]];

async function runDashboard(browser) {
  const ids = await resolveDashboardIds();
  console.log('dashboard ids:', JSON.stringify(ids));
  for (const ent of DASH_ENTITIES) {
    const id = typeof ent.id === 'function' ? ent.id(ids) : ent.id;
    for (const [fname, w, h] of DASH_FORMS) {
      const dir = join(OUT, 'dashboard', ent.name, fname);
      if (!id) {
        manifest.unreachable.push({ app: 'dashboard', page: ent.name, formfactor: fname, reason: 'no-real-id: 列表 API 无数据,拿不到真实 id' });
        rec('dashboard', ent.name, fname, { status: 'unreachable', reason: 'no-real-id' });
        continue;
      }
      const url = `${DASH}/?open_type=${encodeURIComponent(ent.type)}&open_id=${encodeURIComponent(id)}&open_title=${encodeURIComponent(ent.name)}`;
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      const page = await ctx.newPage();
      const notes = [];
      try {
        // SPA 有长驻 WS,networkidle 常常不收敛;domcontentloaded 后固定等待即可
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 }).catch((e) => notes.push(`goto:${e.message.split('\n')[0]}`));
        await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(ent.wait || 4500);
        // 仍在"加载中"则多等一轮
        const loading = await page.evaluate(() => document.body && document.body.innerText.includes('加载中')).catch(() => false);
        if (loading) { notes.push('首轮仍显示加载中,多等 4s'); await page.waitForTimeout(4000); }
        const stillLoading = await page.evaluate(() => document.body && document.body.innerText.includes('加载中')).catch(() => false);
        if (stillLoading) notes.push('截图时仍显示加载中');
        const title = await page.title().catch(() => '');
        const method = await capture(page, dir);
        rec('dashboard', ent.name, fname, { status: 'ok', id, title, a11y: method, notes: notes.join('; ') || undefined, desc: ent.desc });
        console.log('ok', 'dashboard', ent.name, fname);
      } catch (e) {
        manifest.unreachable.push({ app: 'dashboard', page: ent.name, formfactor: fname, reason: `capture-fail: ${String(e.message || e).split('\n')[0]}` });
        rec('dashboard', ent.name, fname, { status: 'unreachable', reason: String(e.message || e).split('\n')[0], id });
        console.log('FAIL', 'dashboard', ent.name, fname, e.message);
      } finally {
        await ctx.close().catch(() => {});
      }
    }
  }
}

// ── LOFA ─────────────────────────────────────────────────────────────────────
const LOFA_FORMS = [['phone', 390, 844], ['tablet-port', 834, 1194], ['tablet-land', 1194, 834]];

async function lofaNewPage(browser, w, h) {
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

const currentView = (page) => page.evaluate(() => (window.LOFA && window.LOFA.router && window.LOFA.router.current && window.LOFA.router.current()) || '').catch(() => '');
const viewShown = (page, id) => page.evaluate((vid) => { const el = document.getElementById(vid); return !!(el && el.classList.contains('show')); }, id).catch(() => false);

async function lofaClickTab(page, tab) {
  await page.click(`#bottomNav .lg-tab[data-tab="${tab}"]`);
  await page.waitForTimeout(3000);
}

// sessions 列表里按图标精确找行:term 图标唯一带 <rect,chat 图标只有 path;
// 跳过 recoverable 行(.ss-recover,点了会 POST /resume,只读普查不触发)
async function lofaOpenSessionRow(page, wantView) {
  const isTerm = wantView === 'termView';
  await page.waitForSelector('#sessionsView .lg-row', { timeout: 10000 }).catch(() => {});
  const handle = await page.evaluateHandle((term) => {
    const rows = Array.from(document.querySelectorAll('#sessionsView .lg-row'));
    return rows.find((r) => {
      if (r.querySelector('.ss-recover')) return false;
      const svg = r.querySelector('.lg-row-icon svg');
      if (!svg) return false;
      const hasRect = svg.innerHTML.includes('<rect');
      return term ? hasRect : !hasRect;
    }) || null;
  }, isTerm);
  const el = handle.asElement();
  if (!el) return false;
  await el.click();
  await page.waitForTimeout(isTerm ? 3500 : 2500);
  return viewShown(page, wantView);
}

// name=普查目录名; viewId=期望 .show 的视图; nav=导航函数,返回 true=到达; desc=一句话现状
const LOFA_VIEWS = [
  { name: 'sessions', viewId: 'sessionsView', wait: 1500, desc: '统一会话空间(chat+终端合列,默认首页)', nav: async () => true },
  { name: 'review', viewId: 'reviewView', desc: '审阅列表(底 tab)', nav: async (page) => { await lofaClickTab(page, 'review'); return viewShown(page, 'reviewView'); } },
  { name: 'projects', viewId: 'projectsView', desc: '项目板(底 tab,项目宫格/分组列表)', nav: async (page) => { await lofaClickTab(page, 'projects'); return viewShown(page, 'projectsView'); } },
  { name: 'me', viewId: 'meView', desc: '我的(底 tab,设置/连接状态/系统项)', nav: async (page) => { await lofaClickTab(page, 'me'); return viewShown(page, 'meView'); } },
  {
    name: 'chat', viewId: 'chatView', wait: 3000, desc: '对话视图(点 sessions 首条 chat 行进入)',
    nav: async (page) => lofaOpenSessionRow(page, 'chatView'),
    unreach: 'sessions 列表无 chat 行(或全部 recoverable)',
  },
  {
    name: 'term', viewId: 'termView', wait: 4500, desc: '终端视图(xterm;普查时主机无存活 PTY,只读直开 recoverable 会话的断开态,未触发 POST /resume)',
    nav: async (page) => {
      // 1) 优先点存活终端行
      if (await lofaOpenSessionRow(page, 'termView')) return true;
      // 2) 无存活行: 从 API 取 recoverable PTY 元信息,router.open('term') 只读直开(不 POST /resume,视图渲染断开态)
      //    注意走 page.evaluate 里的 fetch: 页面上下文 ignoreHTTPSErrors 且源 http://localhost 在 LOFA CORS 白名单;
      //    Node 侧裸 fetch 会被自签证书拒掉。
      const meta = await page.evaluate(async (base) => {
        try {
          const d = await (await fetch(base.replace(/\/+$/, '') + '/api/cc/sessions')).json();
          return (d.recoverable || []).find((x) => !String(x.id).startsWith('chat-')) || null;
        } catch (e) { return null; }
      }, LOFA_API);
      if (!meta) return false;
      await page.evaluate((m) => window.LOFA.router.open('term', m), meta);
      await page.waitForTimeout(4000);
      return viewShown(page, 'termView');
    },
    unreach: 'sessions 无存活终端行,且 API 无 recoverable PTY 会话可只读直开',
  },
  {
    name: 'reviewDetail', viewId: 'reviewDetailView', wait: 3000, desc: '审阅详情(材料正文+裁决条;≥600 与列表双栏)',
    nav: async (page) => {
      await lofaClickTab(page, 'review');
      const has = await page.waitForSelector('#reviewView .lg-row', { timeout: 12000 }).then(() => true).catch(() => false);
      if (!has) return false;
      await page.locator('#reviewView .lg-row').first().click();
      await page.waitForTimeout(2500);
      return viewShown(page, 'reviewDetailView');
    },
    unreach: 'review 列表为空,无 .lg-row 可点',
  },
  {
    name: 'projectDetail', viewId: 'projectDetailView', wait: 3000, desc: '项目详情(项目行点击进入)',
    nav: async (page) => {
      await lofaClickTab(page, 'projects');
      for (const sel of ['#projectsView .proj-item-main', '#projectsView .lg-row', '#projectsView .proj-app']) {
        const el = page.locator(sel).first();
        if (await el.count()) { await el.click().catch(() => {}); await page.waitForTimeout(2500); if (await viewShown(page, 'projectDetailView')) return true; }
      }
      return false;
    },
    unreach: 'projects 列表为空,无项目行可点',
  },
  {
    name: 'connect', viewId: 'connectView', wait: 1500, desc: '连接编辑页(主机地址/连接,me→主机行 或深链 connect)',
    nav: async (page) => { await page.evaluate(() => window.LOFA.router.open('connect')); await page.waitForTimeout(1500); return viewShown(page, 'connectView'); },
  },
  {
    name: 'notes', viewId: 'notesView', wait: 4500, desc: '笔记(全屏 iframe: /lofa/overlay/app/notes-web.html)',
    nav: async (page) => { await page.evaluate(() => window.LOFA.router.open('notes')); await page.waitForTimeout(3000); return viewShown(page, 'notesView'); },
    unreach: '未连接主机(lofa.baseUrl 未生效)',
  },
  {
    name: 'code', viewId: 'codeView', wait: 4500, desc: '代码面板(全屏 iframe,需主机下发代码面板配置)',
    nav: async (page) => { await page.evaluate(() => window.LOFA.router.open('code')); await page.waitForTimeout(3000); return viewShown(page, 'codeView'); },
    unreach: '主机未下发代码面板配置(store.code 为空,openCode 直接 toast)',
  },
  {
    name: 'web', viewId: 'webView', wait: 4500, desc: '通用网页(项目应用/链接点击后全屏 iframe)',
    nav: async (page) => {
      await lofaClickTab(page, 'projects');
      // UI 入口 1: 「应用」段(真启动器)— 切 segmented 到 apps 再点首个应用宫格
      //   (appCell 与 projectCell 同为 .proj-app,用 .proj-app-icon.img/.emoji 区分:app 有 icon_url/emoji)
      const segApps = page.locator('#projectsView .lg-seg button[data-v="apps"]').first();
      if (await segApps.count()) {
        await segApps.click().catch(() => {});
        await page.waitForTimeout(3000);
        const clicked = await page.evaluate(() => {
          const cells = Array.from(document.querySelectorAll('#projectsView .proj-app'));
          const app = cells.find((c) => { const ic = c.querySelector('.proj-app-icon'); return ic && (ic.classList.contains('img') || ic.classList.contains('emoji')); });
          if (app) { app.click(); return true; }
          return false;
        }).catch(() => false);
        if (clicked) { await page.waitForTimeout(3000); if (await viewShown(page, 'webView')) return true; }
      }
      // UI 入口 2: 项目行的 proj-link 快速链接
      const link = page.locator('#projectsView .proj-link').first();
      if (await link.count()) { await link.click().catch(() => {}); await page.waitForTimeout(3000); if (await viewShown(page, 'webView')) return true; }
      // UI 入口 3: 项目详情内的链接
      const main = page.locator('#projectsView .proj-item-main').first();
      if (await main.count()) {
        await main.click().catch(() => {}); await page.waitForTimeout(2500);
        const dlink = page.locator('#projectDetailView .proj-link').first();
        if (await dlink.count()) { await dlink.click().catch(() => {}); await page.waitForTimeout(3000); if (await viewShown(page, 'webView')) return true; }
      }
      return false;
    },
    unreach: '项目数据无应用/链接入口(应用段 .proj-app 与 .proj-link 均不存在)',
  },
];

async function runLofa(browser) {
  const views = VIEWS.length ? LOFA_VIEWS.filter((v) => VIEWS.includes(v.name)) : LOFA_VIEWS;
  for (const view of views) {
    for (const [fname, w, h] of LOFA_FORMS) {
      const dir = join(OUT, 'lofa', view.name, fname);
      const { ctx, page } = await lofaNewPage(browser, w, h);
      const notes = [];
      try {
        const ok = await view.nav(page);
        if (!ok) {
          const reason = `unreachable:${view.unreach || '导航后目标视图未显示'}`;
          manifest.unreachable.push({ app: 'lofa', page: view.name, formfactor: fname, reason });
          rec('lofa', view.name, fname, { status: 'unreachable', reason, desc: view.desc });
          console.log('UNREACH', 'lofa', view.name, fname, reason);
          continue;
        }
        await page.waitForTimeout(view.wait || 2000);
        const method = await capture(page, dir);
        rec('lofa', view.name, fname, { status: 'ok', a11y: method, desc: view.desc });
        console.log('ok', 'lofa', view.name, fname);
      } catch (e) {
        manifest.unreachable.push({ app: 'lofa', page: view.name, formfactor: fname, reason: `capture-fail: ${String(e.message || e).split('\n')[0]}` });
        rec('lofa', view.name, fname, { status: 'unreachable', reason: String(e.message || e).split('\n')[0], desc: view.desc });
        console.log('FAIL', 'lofa', view.name, fname, e.message);
      } finally {
        await ctx.close().catch(() => {});
      }
    }
  }
}

// ── INDEX.md ─────────────────────────────────────────────────────────────────
function indexMd() {
  const dashCaps = manifest.captures.filter((c) => c.app === 'dashboard');
  const lofaCaps = manifest.captures.filter((c) => c.app === 'lofa');
  const okCount = (caps) => caps.filter((c) => c.status === 'ok').length;
  const lines = [];
  lines.push('# 前端全量页面普查 INDEX(阶段一)');
  lines.push('');
  lines.push(`- 生成: ${manifest.generatedAt}(census.mjs 自动产出)`);
  lines.push(`- dashboard: ${manifest.dash}(深链 open_type/open_id;desktop 1600×1000 + tablet-port 834×1194)`);
  lines.push(`- LOFA: playwright 路由拦截 http://localhost 供本地 \`lofa/app/www\` + \`lofa.baseUrl=${manifest.lofaApi}\`(phone 390×844 / tablet-port 834×1194 / tablet-land 1194×834, isMobile+hasTouch)`);
  lines.push(`- 快照三件套: \`shot.png\`(视口截图) / \`dom.html\`(page.content() 完整 DOM) / \`a11y.json\`(可访问性树)`);
  lines.push(`- a11y 实际口径: ${Array.from(a11yMethods).join(' | ') || '(无)'}`);
  lines.push(`- **覆盖率: dashboard ${okCount(dashCaps)}/${dashCaps.length}(目标 46) · LOFA ${okCount(lofaCaps)}/${lofaCaps.length}(目标 36) · 合计 ${okCount(manifest.captures)}/${manifest.captures.length}(目标 82)**`);
  lines.push('');
  const section = (title, caps, forms) => {
    lines.push(`## ${title}`);
    lines.push('');
    const byPage = new Map();
    for (const c of caps) { if (!byPage.has(c.page)) byPage.set(c.page, []); byPage.get(c.page).push(c); }
    for (const [page, rows] of byPage) {
      const desc = (rows.find((r) => r.desc) || {}).desc || '';
      lines.push(`### ${page}`);
      lines.push('');
      if (desc) lines.push(`- 现状: ${desc}`);
      const idNote = rows.find((r) => r.id);
      if (idNote) lines.push(`- 使用 id: \`${idNote.id}\``);
      for (const f of forms) {
        const r = rows.find((x) => x.formfactor === f);
        if (!r) { lines.push(`- ${f}: (未跑)`); continue; }
        if (r.status === 'ok') {
          const base = `${caps === dashCaps ? 'dashboard' : 'lofa'}/${page}/${f}`;
          const extra = r.notes ? ` — ${r.notes}` : '';
          lines.push(`- ${f}: [shot.png](${base}/shot.png) · [dom.html](${base}/dom.html) · [a11y.json](${base}/a11y.json) — ok${extra}`);
        } else {
          lines.push(`- ${f}: **${r.reason || 'unreachable'}**`);
        }
      }
      lines.push('');
    }
  };
  section('Dashboard(23 实体)', dashCaps, ['desktop', 'tablet-port']);
  section('LOFA(12 视图)', lofaCaps, ['phone', 'tablet-port', 'tablet-land']);
  lines.push('## Unreachable / 异常清单');
  lines.push('');
  if (!manifest.unreachable.length) lines.push('- (无)');
  for (const u of manifest.unreachable) lines.push(`- ${u.app}/${u.page}/${u.formfactor}: ${u.reason}`);
  lines.push('');
  return lines.join('\n');
}

// ── main ─────────────────────────────────────────────────────────────────────
await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ args: ['--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests'] });
try {
  if (ONLY !== 'lofa') await runDashboard(browser);
  if (ONLY !== 'dashboard') await runLofa(browser);
} finally {
  await browser.close();
}
// --only / --views 重跑子集时,与上一份 manifest 合并:丢弃本次实际重跑的 (app,page),保留其余
const ranKeys = new Set(manifest.captures.map((c) => `${c.app}/${c.page}`));
if ((ONLY || VIEWS.length) && ranKeys.size) {
  try {
    const prev = JSON.parse(await readFile(join(OUT, '_manifest.json'), 'utf8'));
    manifest.captures = (prev.captures || []).filter((c) => !ranKeys.has(`${c.app}/${c.page}`)).concat(manifest.captures);
    manifest.unreachable = (prev.unreachable || []).filter((u) => !ranKeys.has(`${u.app}/${u.page}`)).concat(manifest.unreachable);
  } catch { /* 无旧 manifest 就算了 */ }
}
await writeFile(join(OUT, '_manifest.json'), JSON.stringify(manifest, null, 2));
await writeFile(join(OUT, 'INDEX.md'), indexMd());
const okN = manifest.captures.filter((c) => c.status === 'ok').length;
console.log(`done → ${OUT} | ok ${okN}/${manifest.captures.length} | unreachable ${manifest.unreachable.length}`);
