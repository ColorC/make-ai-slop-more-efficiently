// wiki-core 入口。渲染/索引核来自吸收的 @user/obsidian-md（正本在此，
// 改动用 tools/sync-obsidian-md.mjs 回写 AIWorkSpace 并提交 P4）。
// viewer / editor / comments / dev-bridge 按阶段陆续落地（见计划 W1–W3）。
export { createRenderer, buildIndex, obsidianSyntax, mermaidFence } from "./obsidian-md/index.js";
