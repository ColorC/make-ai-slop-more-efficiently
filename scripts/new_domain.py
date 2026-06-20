# [OMNI] origin=claude-code ts=2026-06-20 type=script
# OMNI-PERSISTENT-SCRIPT owner=platform purpose="领域脚手架: 复制 example_domain 模板成你自己的新领域"
"""new_domain —— 领域脚手架。

复制 `packages/domains/example_domain/` 成 `packages/domains/<name>/`, 并把模板里的
`example_domain` / `example` 占位名替换成你的领域名。之后照 example_domain/DESIGN.md 的
四步把它实现 + 注册即可。

用法:
    python scripts/new_domain.py my_domain
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAINS = REPO_ROOT / "src" / "omnicompany" / "packages" / "domains"
TEMPLATE = DOMAINS / "example_domain"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def main() -> int:
    ap = argparse.ArgumentParser(description="复制 example_domain 模板成一个新领域")
    ap.add_argument("name", help="新领域名(小写, 字母数字下划线, 如 my_domain)")
    args = ap.parse_args()

    name = args.name.strip()
    if not _NAME_RE.match(name):
        print(f"✗ 非法领域名 {name!r}: 必须小写字母开头, 只含 [a-z0-9_]", file=sys.stderr)
        return 2
    if not TEMPLATE.is_dir():
        print(f"✗ 模板缺失: {TEMPLATE}", file=sys.stderr)
        return 2
    dst = DOMAINS / name
    if dst.exists():
        print(f"✗ 已存在: {dst}", file=sys.stderr)
        return 2

    shutil.copytree(TEMPLATE, dst, ignore=shutil.ignore_patterns("__pycache__"))

    # 替换文本里的占位名: example_domain → name, example- → name- (节点 id 前缀)
    for p in dst.rglob("*"):
        if not p.is_file() or p.suffix not in {".py", ".md"}:
            continue
        text = p.read_text(encoding="utf-8")
        new = (text.replace("example_domain", name)
                   .replace("example-", f"{name}-")
                   .replace("example.", f"{name}."))
        if new != text:
            p.write_text(new, encoding="utf-8", newline="")

    print(f"✅ 新领域已建: {dst.relative_to(REPO_ROOT)}")
    print("下一步(见该域 DESIGN.md):")
    print("  1. 改 team.py 的节点/边为你的真实管线")
    print("  2. 在 routers/ 实现各节点逻辑(参考 domains/research/routers/)")
    print("  3. 在 src/omnicompany/core/pipelines.py 注册一个 _lazy 条目")
    print("  4. python scripts/validate_domains.py 校验, 然后 omni run "
          f"{name}.run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
