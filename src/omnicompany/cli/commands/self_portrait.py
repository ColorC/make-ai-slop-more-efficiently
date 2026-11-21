# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-05T20:30:00Z type=router status=active agent=ai-ide
# [OMNI] summary="omni self-portrait — 路径锚定一致性 self-check. 路径是 service 归属真相, 字段 belongs_to_service 只用作 (1) 显式 override 验证 (2) 不在 services 下文件的违规检测"
# [OMNI] why="2026-05-05 修: 之前把'头空'标 Missing 是二重真相设计错误 — path 已表达 service 归属, 字段是冗余复制. 改成路径推为真相, 字段空时不算债. 跟 guardian 二重真相防护铁律对齐"
# [OMNI] tags=cli,self-portrait,self-stability,path-truth
# [OMNI] material_id="material:cli.commands.self_portrait.belongs_to_service_check.py"
"""omni self-portrait — 路径锚定一致性 self-check.

设计 (2026-05-05 修):
  路径是 service 归属真相. `belongs_to_service` 字段是辅助校验, 不是必填.

子命令:
  omni self-portrait check    # 验证全部, 报真冲突
  omni self-portrait stats    # 只汇总数字

判定规则:
  - 路径含 `packages/services/(_<group>/)?<service>/` → 推 service 名
  - 头空 + 路径能推 → ok (按路径推, 字段空不算债)
  - 头写了 + 跟路径推一致 → ok
  - 头写了 + 跟路径推不一致 → mismatch (报错)
  - 头写了 + 路径推不出 (不在 services 下) → mismatched_outside_services (报错)
  - 头空 + 路径推不出 → irrelevant (跟 service 体系无关)

只有 mismatch / mismatched_outside_services 算真冲突 (退出码 1).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import click


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[4]


# 匹配 packages/services/(_group/)?<service>/ 形态, 抓 service 名
# 例: packages/services/_authoring/docauthor/foo.py → docauthor
# 例: packages/services/guardian/foo.py            → guardian
_SERVICE_PATH_RE = re.compile(
    r"packages[/\\]services[/\\](?:_[^/\\]+[/\\])?(?P<service>[^/\\_][^/\\]*)[/\\]"
)


def _extract_service_from_path(rel_path: str) -> str | None:
    """从路径抠 service 名. 路径不含 services 段则返 None."""
    norm = rel_path.replace("\\", "/")
    m = _SERVICE_PATH_RE.search(norm)
    if not m:
        return None
    return m.group("service")


_SUPPORTED_EXTS = {".py", ".md", ".yaml", ".yml"}


def _iter_managed_files(root: Path) -> Iterator[Path]:
    """扫 src/omnicompany + docs 下有 OmniMark 头嫌疑的文件."""
    for base in (root / "src" / "omnicompany", root / "docs"):
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _SUPPORTED_EXTS:
                continue
            # 跳过常见缓存 / 历史 / vendor
            parts = f.parts
            if any(p in {"__pycache__", "_graveyard", "node_modules", "vendors"} for p in parts):
                continue
            yield f


def _classify(file_path: Path, root: Path) -> dict:
    """对一个文件分类 — 返回 dict 含 status / 详情."""
    from omnicompany.core.omnimark import parse_omnimark

    rel = str(file_path.relative_to(root))
    fields = parse_omnimark(file_path)
    if fields is None:
        return {"path": rel, "status": "no_header"}

    declared = fields.belongs_to_service.strip()
    inferred = _extract_service_from_path(rel)

    if declared == "" and inferred is None:
        return {"path": rel, "status": "irrelevant"}  # 不在 services 下也没填, OK

    if declared == "" and inferred is not None:
        # 路径已是真相: 头空时按 path 推 (2026-05-05 修).
        # 之前把"头空"标 missing 是二重真相设计错误 — path 跟字段都表达同一信息,
        # 字段冗余. 现按"path 推就行", 头空不再算债.
        return {"path": rel, "status": "ok", "service": inferred, "from": "path"}

    if declared != "" and inferred is None:
        return {
            "path": rel,
            "status": "mismatched_outside_services",
            "declared": declared,
        }

    if declared != inferred:
        return {
            "path": rel,
            "status": "mismatch",
            "declared": declared,
            "expected": inferred,
        }

    return {"path": rel, "status": "ok", "service": inferred}


@click.group("self-portrait")
def cmd_self_portrait():
    """自我画像 — 铆钉关联 self-check (CORE-SELF-STABILITY 第一阶段)."""


@cmd_self_portrait.command("check")
@click.option("--show-ok", is_flag=True, help="也列出 OK 的文件 (默认只列异常)")
@click.option("--show-irrelevant", is_flag=True, help="也列出不相关的文件")
@click.option("--limit", type=int, default=50, help="每类异常最多列多少 (默认 50)")
def cmd_check(show_ok: bool, show_irrelevant: bool, limit: int) -> None:
    """验证 belongs_to_service 跟路径锚定一致."""
    root = _project_root()

    buckets: dict[str, list[dict]] = {
        "ok": [],
        "missing": [],
        "mismatch": [],
        "mismatched_outside_services": [],
        "irrelevant": [],
        "no_header": [],
    }
    for f in _iter_managed_files(root):
        result = _classify(f, root)
        buckets[result["status"]].append(result)

    total = sum(len(v) for v in buckets.values())
    n_ok = len(buckets["ok"])
    n_missing = len(buckets["missing"])
    n_mismatch = len(buckets["mismatch"])
    n_outside = len(buckets["mismatched_outside_services"])
    n_irrelevant = len(buckets["irrelevant"])
    n_no_header = len(buckets["no_header"])

    click.echo(click.style(f"扫描总数: {total}", bold=True))
    click.echo(f"  OK (路径推或头声明一致)       : {n_ok}")
    click.echo(click.style(f"  Mismatch (头声明跟路径不一致) : {n_mismatch}",
                           fg="red" if n_mismatch else None, bold=bool(n_mismatch)))
    click.echo(click.style(f"  Outside (不在 service 却填了) : {n_outside}",
                           fg="red" if n_outside else None, bold=bool(n_outside)))
    click.echo(f"  Irrelevant (不在 service 也没填): {n_irrelevant}")
    click.echo(f"  NoHeader (没 OmniMark 头)     : {n_no_header}")
    if n_missing:
        # 不应再出现 (2026-05-05 修后头空按路径推算 ok). 出现说明逻辑回退了.
        click.echo(click.style(f"  ⚠ Missing 不应再出现, 但有 {n_missing} 条", fg="red", bold=True))

    def _list_bucket(name: str, color: str | None = None):
        items = buckets[name]
        if not items:
            return
        click.echo("")
        click.echo(click.style(f"=== {name} ({len(items)}) ===", fg=color, bold=True))
        for r in items[:limit]:
            if name == "missing":
                click.echo(f"  {r['path']}  →  应填: {r['expected']}")
            elif name == "mismatch":
                click.echo(f"  {r['path']}  声明:{r['declared']}  应是:{r['expected']}")
            elif name == "mismatched_outside_services":
                click.echo(f"  {r['path']}  声明了 belongs_to_service={r['declared']} 但不在 services 下")
            else:
                click.echo(f"  {r['path']}")
        if len(items) > limit:
            click.echo(click.style(f"  ... 还有 {len(items) - limit} 项 (用 --limit 调高)", fg="white"))

    _list_bucket("mismatch", "red")
    _list_bucket("mismatched_outside_services", "red")
    _list_bucket("missing", "yellow")
    if show_ok:
        _list_bucket("ok", "green")
    if show_irrelevant:
        _list_bucket("irrelevant")

    # retcode: 有 mismatch / outside = 失败 (1), 只 missing 不算失败 (信息), 全 OK = 0
    if n_mismatch or n_outside:
        raise SystemExit(1)


@cmd_self_portrait.command("stats")
def cmd_stats() -> None:
    """汇总 belongs_to_service 填写情况 (不列详情)."""
    root = _project_root()

    counts = {"ok": 0, "missing": 0, "mismatch": 0,
              "mismatched_outside_services": 0,
              "irrelevant": 0, "no_header": 0}
    by_service: dict[str, int] = {}
    for f in _iter_managed_files(root):
        r = _classify(f, root)
        counts[r["status"]] += 1
        if r["status"] == "ok":
            by_service[r["service"]] = by_service.get(r["service"], 0) + 1

    click.echo(click.style("belongs_to_service 填写汇总", bold=True))
    for k in ("ok", "missing", "mismatch", "mismatched_outside_services",
              "irrelevant", "no_header"):
        click.echo(f"  {k:35s}: {counts[k]}")
    click.echo("")
    if by_service:
        click.echo(click.style("已声明的 service 分布", bold=True))
        for svc, n in sorted(by_service.items(), key=lambda x: -x[1]):
            click.echo(f"  {svc:30s}: {n}")
