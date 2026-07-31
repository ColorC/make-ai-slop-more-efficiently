#!/usr/bin/env node
// obsidian-md 同步：正本 = webworks/packages/wiki-core/obsidian-md/，
// 团队副本 = C:/workspace/AIWorkSpace/app\packages\obsidian-md\src\（路径/API 不变，团队工具无感）。
//   node tools/sync-obsidian-md.mjs           # 比对，列差异（dry-run）
//   node tools/sync-obsidian-md.mjs --write   # webworks → P4 单向回写（之后需 p4 提交）
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const LOCAL = resolve(here, "..", "obsidian-md");
const P4 = "C:/workspace/AIWorkSpace/app/packages/obsidian-md/src";
const FILES = [
  "index.js",
  "renderer.js",
  "index-builder.js",
  "plugins/obsidian-syntax.js",
  "plugins/mermaid-fence.js",
];

const write = process.argv.includes("--write");
const hash = (p) => (existsSync(p) ? createHash("md5").update(readFileSync(p)).digest("hex") : "(missing)");

let diff = 0;
for (const f of FILES) {
  const a = resolve(LOCAL, f);
  const b = resolve(P4, f);
  const ha = hash(a);
  const hb = hash(b);
  if (ha === hb) {
    console.log(`  = ${f}`);
    continue;
  }
  diff++;
  if (write) {
    writeFileSync(b, readFileSync(a));
    console.log(`  → ${f}  (回写 P4)`);
  } else {
    console.log(`  ≠ ${f}  local=${ha.slice(0, 8)} p4=${hb.slice(0, 8)}`);
  }
}

if (diff === 0) {
  console.log("\n同步状态: 两边一致。");
} else if (write) {
  console.log(`\n已回写 ${diff} 个文件到 ${P4}`);
  console.log("下一步（人工确认后执行）: p4 edit/add 对应文件并 submit；提交前过一眼 AIWorkSpace 消费方（wiki-viewer / kb-quiz-game / demogame-config）。");
  process.exitCode = 0;
} else {
  console.log(`\n${diff} 个文件有差异。回写: node tools/sync-obsidian-md.mjs --write`);
  process.exitCode = 1;
}
