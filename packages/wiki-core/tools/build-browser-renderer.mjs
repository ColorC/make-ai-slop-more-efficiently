// 把渲染门面（render.js：obsidian-md 渲染核 + stripFrontmatter）打成单文件浏览器
// ESM bundle，供无打包器静态宿主消费（tabletop-simulator 这类 python http.server
// 直出的页面，裸依赖 markdown-it 系无法用 import map 解析：task-lists / katex
// 插件只有 CJS 产物）。
//
// 唯一正本仍是 render.js / obsidian-md/ —— dist 只是编译产物，禁止手改。
// `npm test` 会先跑本脚本再跑烟测，保证 dist 跟源码同步；改了渲染核记得跑。
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

const result = await build({
  entryPoints: [resolve(root, "render.js")],
  outfile: resolve(root, "dist/render.browser.mjs"),
  bundle: true,
  format: "esm",
  platform: "browser",
  minify: true,
  metafile: true,
  banner: {
    js: "// 构建产物，源头是 wiki-core/render.js（npm run build:browser 重新生成），勿手改。",
  },
});

const out = Object.entries(result.metafile.outputs)[0];
console.log(`built ${out[0]} (${(out[1].bytes / 1024).toFixed(0)} KB)`);
