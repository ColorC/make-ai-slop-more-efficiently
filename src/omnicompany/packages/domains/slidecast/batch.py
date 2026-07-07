# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-21T00:00:00Z type=cli_entry status=active
# [OMNI] summary="网页 demo 批量出口:按 curated 已发布 posts(+选定 works)逐篇跑 slidecast.run(deck-only)再统一 publish。可续跑。"
# [OMNI] why="可重复批量 —— 一条命令把所有文章批量做成终端绿 deck+讲解并发布;已有 run 的跳过(续跑省钱),视频步暂不接。"
# [OMNI] tags=slidecast,batch,demos,colorc
"""slidecast 网页 demo 批量生成+发布(可重复、可续跑)。

用法:
  python -m omnicompany.packages.domains.slidecast.batch              # 全部已发布文章(跳过已生成的)
  python -m omnicompany.packages.domains.slidecast.batch 06 aigc      # 只跑 slug 含这些词的
  python -m omnicompany.packages.domains.slidecast.batch --force 06   # 强制重新生成(忽略已有 run)

每篇:omni run slidecast.run -i article=<原文> -i build=1(deck-only)→ 最后增量 publish()
(只重建新生成的 deck,已发布的复用)。顺序执行(共用 _studio,避免并发竞争)。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import publish as P

_ROOT = Path(__file__).resolve().parents[5]  # omnicompany 根
_OMNI = _ROOT / "venv" / "Scripts" / "omni.exe"


def batch(filters: list[str] | None = None, force: bool = False,
          slugs: list[str] | None = None, articles: list[dict] | None = None) -> dict:
    """批量生成+发布。filters=slug 子串过滤;slugs=精确 slug 集;articles=显式清单(覆盖 _articles,
    供 personal_site demo 分支传入新文章/work);force=忽略已有 run 重生成。返回 {generated, skipped, failed}。"""
    arts = articles if articles is not None else P._articles()
    if slugs:
        want = set(slugs)
        items = [d for d in arts if d["slug"] in want]
    elif filters:
        items = [d for d in arts if any(f in d["slug"] for f in filters)]
    else:
        items = arts
    print(f"批量 demo:{len(items)} 篇候选(deck-only,不出视频)")

    generated: set[str] = set()
    skipped, failed = [], []
    for i, d in enumerate(items, 1):
        slug = d["slug"]
        if not force and P._latest_run(slug):
            skipped.append(slug)
            print(f"[{i}/{len(items)}] 跳过 {slug}(已有 run)")
            continue
        src = P.HOME / d["src"]
        print(f"[{i}/{len(items)}] 生成 {slug}  ({src.name})")
        r = subprocess.run(
            [str(_OMNI), "run", "slidecast.run", "-i", f"article={src}", "-i", "build=1"],
            cwd=str(_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and P._latest_run(slug):
            generated.add(slug)
        else:
            failed.append(slug)
            print("  [err]", ((r.stderr or "") + (r.stdout or ""))[-300:])

    print(f"\n== 发布到 colorc.cc 真源(新建 {len(generated)} 篇,增量重建)==")
    P.publish(rebuild=generated)
    print(f"\n批量完成:新生成 {len(generated)} · 跳过 {len(skipped)} · 失败 {len(failed)}"
          + (f"  失败={failed}" if failed else ""))
    return {"generated": sorted(generated), "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    argv = sys.argv[1:]
    force = "--force" in argv
    filt = [a for a in argv if not a.startswith("--")]
    batch(filt or None, force=force)
