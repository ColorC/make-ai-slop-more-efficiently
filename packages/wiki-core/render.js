// 浏览器安全的渲染门面：obsidian-md 渲染核 + frontmatter 剥离，viewer 与
// 无打包器宿主（经 dist/render.browser.mjs bundle，见 tools/build-browser-renderer.mjs）
// 共用这一份。注意：包入口 index.js re-export 了 Node-only 的 index-builder
// （node:fs / fast-glob），浏览器侧只能引本模块或 obsidian-md/renderer.js。
export { createRenderer } from "./obsidian-md/renderer.js";

export function stripFrontmatter(content) {
  const text = String(content ?? "");
  const m = text.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return m ? text.slice(m[0].length) : text;
}
