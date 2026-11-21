// v2-blueprint-wave2-demo-shots.mjs — demo ?theme=g 对照截图(sessions/review,与 LOFA 实装目检 1:1)。
// 前置:demo 目录起 http.server 8123(python -m http.server 8123 --bind 127.0.0.1)。
import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';
import { join } from 'path';
const OUT = join(process.cwd(), 'tests/e2e/visual/_shots/v2-blueprint-wave2');
await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
{
  const ctx = await browser.newContext({ viewport: { width: 430, height: 1000 } });
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:8123/index.html?theme=g#/sessions', { waitUntil: 'load', timeout: 15000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(OUT, 'demo-sessions-g.png') });
  console.log('ok demo-sessions-g');
  await ctx.close();
}
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:8123/index.html?theme=g#/review', { waitUntil: 'load', timeout: 15000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(OUT, 'demo-review-g.png') });
  console.log('ok demo-review-g');
  await ctx.close();
}
await browser.close();
