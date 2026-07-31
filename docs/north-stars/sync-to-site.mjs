// 北极星 / 愿望单同步:omnicompany 权威源 → 个人站本地 data/wishlist.md(公开口径)。
// 用法: node sync-to-site.mjs   (站点根可用 OMNI_HOMEPAGE_ROOT 覆盖)
// 之后由 personal-homepage 的 deploy.sh 推到 colorc.cc。
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// 权威源 2026-07-26 迁出 docs/ (OMNI-035h): 数据产物落 data/domains/personal_site/
const SRC =
  process.env.OMNI_NORTH_STARS_JSON ||
  path.resolve(HERE, "../../data/domains/personal_site/north-stars.json");
const HOMEPAGE =
  process.env.OMNI_HOMEPAGE_ROOT ||
  "e:/WindowsWorkspace/webworks/apps/personal-homepage";
const OUT = path.join(HOMEPAGE, "data", "wishlist.md");

const data = JSON.parse(fs.readFileSync(SRC, "utf-8"));
const stars = data.stars.filter((s) => s.public);
const clusters = data.clusters;

const lines = [];
lines.push("---");
lines.push("title: 愿望单 · 还在追的北极星");
lines.push(`updated: ${data._meta.updated}`);
lines.push("---");
lines.push("");
lines.push("# 愿望单 · 还在追的北极星");
lines.push("");
lines.push(
  "> 这页记的不是做完的东西,是我还在追的方向——大多是北极星(很远),少数是下一个阶段的里程碑。它们绝大多数没做完,有的还没开始。放这儿当个长期游标,也方便回头看自己往哪走。"
);
lines.push("");

for (const c of clusters) {
  const items = stars.filter((s) => s.cluster === c.id);
  if (!items.length) continue;
  lines.push(`## ${c.title}`);
  lines.push("");
  if (c.note) {
    lines.push(`${c.note}`);
    lines.push("");
  }
  for (const s of items) {
    lines.push(`### ${s.title}`);
    lines.push("");
    lines.push(`\`${s.kind}\` · 现状:${s.status}`);
    lines.push("");
    lines.push(`**${s.northstar}**`);
    lines.push("");
    if (s.detail) {
      lines.push(s.detail);
      lines.push("");
    }
  }
}

lines.push("---");
lines.push("");
lines.push(
  "*本页由 Claude Code 在作者指示下整理,内容同步自 omnicompany 内部的北极星源(只取对外方向口径)。*"
);

fs.writeFileSync(OUT, lines.join("\n"), { encoding: "utf-8" });
console.log(`同步完成:${stars.length} 条北极星 → ${OUT}`);
