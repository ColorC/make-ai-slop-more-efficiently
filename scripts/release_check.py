# [OMNI] origin=claude-code ts=2026-06-20 type=script
# OMNI-PERSISTENT-SCRIPT owner=productization purpose="发布就绪遍历: 按 RELEASE-PLAYBOOK 逐维度检查项目发布状态"
"""release_check —— 发布就绪遍历器。

任何项目发布 / 更新发布版本前跑一遍, 按 docs/plans/productization/RELEASE-PLAYBOOK.md
的维度逐个检查, 报 ✓/⚠/✗ + 该做什么。确定性、不调模型、项目无关。

用法:
    python scripts/release_check.py                 # 查本仓
    python scripts/release_check.py --repo <path>   # 查别的项目
    python scripts/release_check.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

OK, WARN, FAIL, NA = "OK", "WARN", "FAIL", "NA"
_MARK = {OK: "✓", WARN: "⚠", FAIL: "✗", NA: "·"}


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _pyproject_version(repo: Path) -> str | None:
    m = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', _read(repo / "pyproject.toml"))
    if m:
        return m.group(1)
    m = re.search(r'"version"\s*:\s*"([^"]+)"', _read(repo / "package.json"))
    return m.group(1) if m else None


def _git_tags(repo: Path) -> set[str]:
    r = subprocess.run(["git", "-C", str(repo), "tag"], capture_output=True, text=True)
    return set(r.stdout.split()) if r.returncode == 0 else set()


def _glob_any(repo: Path, *patterns: str) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(repo.glob(pat))
    return out


# ── 维度检查 (返回 status, detail, action) ──────────────────────────
def c_version(repo: Path):
    ver = _pyproject_version(repo)
    if not ver:
        return NA, "无 pyproject/package.json 版本号", ""
    cl = _read(repo / "CHANGELOG.md")
    if not cl:
        return WARN, f"版本 {ver}；无 CHANGELOG.md", "加 CHANGELOG.md 并记本版"
    if ver in cl:
        return OK, f"版本 {ver}；CHANGELOG 已记", ""
    return WARN, f"版本 {ver}；CHANGELOG 未提本版", f"CHANGELOG 加 {ver} 条目"


def c_release(repo: Path):
    ver = _pyproject_version(repo)
    if not ver:
        return NA, "", ""
    tags = _git_tags(repo)
    if f"v{ver}" in tags or ver in tags:
        return OK, f"v{ver} tag 存在", ""
    has_rel = bool(_glob_any(repo, ".github/workflows/release*.yml", ".github/workflows/*release*.yml"))
    act = f"打 tag v{ver} 触发发布" + ("" if has_rel else "（先补 release 工作流）")
    return FAIL, f"版本 {ver} 无对应 tag —— 能力就位、扳机未扣", act


def c_ci(repo: Path):
    wf = _glob_any(repo, ".github/workflows/*.yml", ".github/workflows/*.yaml")
    if not wf:
        return FAIL, "无 CI 工作流", "加 .github/workflows/ci.yml（测试+lint+构建）"
    txt = " ".join(_read(p) for p in wf)
    missing = []
    if not re.search(r"pytest|test", txt):
        missing.append("测试 job")
    if "codeql" not in txt.lower():
        missing.append("CodeQL")
    if not (repo / ".github" / "dependabot.yml").is_file():
        missing.append("dependabot")
    # actions 是否钉 SHA(否则用浮动 tag)
    if re.search(r"uses:\s*\S+@v\d", txt):
        missing.append("actions 钉 SHA")
    if not missing:
        return OK, f"{len(wf)} 个工作流，齐", ""
    return WARN, f"{len(wf)} 个工作流；缺 {', '.join(missing)}", "补上缺的"


def c_tests(repo: Path):
    tdir = repo / "tests"
    if not tdir.is_dir():
        return FAIL, "无 tests/", "加测试（离线 fake-client，不打 live LLM）"
    n = len(list(tdir.rglob("test_*.py"))) + len(list(tdir.rglob("*_test.py")))
    if n == 0:
        return FAIL, "tests/ 无测试文件", "加测试"
    if n < 5:
        return WARN, f"{n} 个测试文件（覆盖偏薄）", "补 CLI 出口/核心循环/契约测试"
    return OK, f"{n} 个测试文件", ""


def c_readme(repo: Path):
    t = _read(repo / "README.md")
    if not t:
        return FAIL, "无 README.md", "加 README"
    lines = t.count("\n")
    has_qs = bool(re.search(r"快速开始|quick ?start|installation|安装|pip install|npm install", t, re.I))
    if lines < 20 or not has_qs:
        return WARN, f"README {lines} 行" + ("" if has_qs else "；无快速开始"), "补快速开始/徽章"
    return OK, f"README {lines} 行，有快速开始", ""


def c_docs(repo: Path):
    if not (repo / "docs").is_dir():
        return WARN, "无 docs/", "加 docs/（架构/规范）"
    site = _glob_any(repo, "mkdocs.yml", "docs/conf.py", "docusaurus.config.*", "**/vitepress*")
    if site:
        return OK, "有 docs/ + 文档站配置", ""
    return WARN, "有 docs/ 但无文档站(mkdocs/sphinx 等)", "加 mkdocs-material→GitHub Pages"


def c_community(repo: Path):
    need = {"LICENSE": repo / "LICENSE", "SECURITY.md": repo / "SECURITY.md",
            "CONTRIBUTING.md": repo / "CONTRIBUTING.md"}
    miss = [n for n, p in need.items() if not p.is_file() and not _glob_any(repo, n + "*")]
    has_tmpl = bool(_glob_any(repo, ".github/ISSUE_TEMPLATE/*", ".github/PULL_REQUEST_TEMPLATE*"))
    if not has_tmpl:
        miss.append("ISSUE/PR 模板")
    if not miss:
        return OK, "健康文件齐", ""
    sev = FAIL if "LICENSE" in miss else WARN
    return sev, "缺 " + ", ".join(miss), "补齐"


def c_homepage(repo: Path):
    t = _read(repo / "pyproject.toml")
    if re.search(r'(?i)homepage\s*=', t):
        return OK, "pyproject 设了 Homepage", ""
    return WARN, "无 Homepage URL", "设主页 + 公开仓索引列本项目；可极简 GitHub Pages landing"


CHECKS = [
    ("1 版本&CHANGELOG", "P0", c_version),
    ("2 发布版/扣扳机", "P0", c_release),
    ("3 CI", "P0", c_ci),
    ("4 测试", "P0", c_tests),
    ("5 README", "P1", c_readme),
    ("6 文档", "P1", c_docs),
    ("7 社区文件", "P1", c_community),
    ("8 主页/landing", "P2", c_homepage),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    repo = args.repo.resolve()
    results = []
    for name, prio, fn in CHECKS:
        try:
            status, detail, action = fn(repo)
        except Exception as e:  # noqa: BLE001
            status, detail, action = NA, f"检查异常: {e}", ""
        results.append({"dim": name, "priority": prio, "status": status,
                        "detail": detail, "action": action})

    if args.json:
        print(json.dumps({"repo": str(repo), "results": results}, ensure_ascii=False, indent=2))
        return 0

    print(f"发布就绪遍历: {repo.name}  (RELEASE-PLAYBOOK)\n" + "=" * 64)
    for r in results:
        line = f"  {_MARK[r['status']]} [{r['priority']}] {r['dim']:<16} {r['detail']}"
        print(line)
        if r["action"]:
            print(f"         → {r['action']}")
    p0_fail = [r for r in results if r["priority"] == "P0" and r["status"] == FAIL]
    print("=" * 64)
    if p0_fail:
        print(f"  ✗ {len(p0_fail)} 个 P0 维度未过 —— 发布前必须扣全。")
        return 1
    print("  P0 维度全过（或仅 WARN）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
