# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-06-22 type=cli status=active
# [OMNI] summary="omni atlas —— project_atlas 资源中心的审/导闭环:list 待审 / approve 入 canonical / export 到两个 AI 的 skills 目录 / reject。"
# [OMNI] why="收集 worker 产出落在 staging(待人审);这条 CLI 把'人审→批准→export 到 ~/.claude+~/.codex'闭环补上,让 object-SKILL 真正被两个 AI 用上(防重复造轮)。"
# [OMNI] tags=cli,atlas,project_atlas,review,export
"""omni atlas —— project_atlas object-SKILL 审/导闭环。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click

from omnicompany.packages.domains.project_atlas._paths import (
    PLAN_DIR,
    SKILLS_ROOT,
    STAGING_ROOT,
    ensure_dirs,
)
from omnicompany.packages.domains.project_atlas.spaces import SPACES

_CLAUDE_SKILLS = Path.home() / ".claude" / "skills"
_CODEX_SKILLS = Path.home() / ".codex" / "skills"


def _last_health_summary() -> tuple[str, int] | None:
    """上次健康巡检: 时刻 + findings 条数。"""
    from omnicompany.packages.domains.project_atlas.health import latest_health_report
    rep = latest_health_report()
    if not rep:
        return None
    ts = rep.get("generated_at")
    n = len(rep.get("findings") or [])
    if not ts:
        return None
    return ts, n


def _iter_objects(root: Path):
    """yield (space, name, skill_path),遍历 staging 或 canonical 下的 <space>/<obj>/SKILL.md。"""
    if not root.is_dir():
        return
    for space_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for obj_dir in sorted(p for p in space_dir.iterdir() if p.is_dir()):
            sk = obj_dir / "SKILL.md"
            if sk.is_file():
                yield space_dir.name, obj_dir.name, sk


def _has_warn(sk: Path) -> bool:
    try:
        return "⚠" in sk.read_text(encoding="utf-8")
    except OSError:
        return False


@click.group("atlas")
def cmd_atlas() -> None:
    """project_atlas 资源中心:列/审批/导出 object-SKILL(staging→canonical→两 AI skills 目录)。"""


@cmd_atlas.command("list")
@click.option("--space", default=None, help="只看某空间")
@click.option("--status", type=click.Choice(["staging", "approved", "all"]), default="staging")
@click.option("--warn-only", is_flag=True, help="只列带 ⚠ 提醒的")
@click.option("--json", "as_json", is_flag=True)
def atlas_list(space: str | None, status: str, warn_only: bool, as_json: bool) -> None:
    """列 object-SKILL(staging 待审 / approved 已批 / all)。"""
    roots = []
    if status in ("staging", "all"):
        roots.append(("staging", STAGING_ROOT))
    if status in ("approved", "all"):
        roots.append(("approved", SKILLS_ROOT))
    rows = []
    for st, root in roots:
        for sp, name, sk in _iter_objects(root):
            if space and sp != space:
                continue
            w = _has_warn(sk)
            if warn_only and not w:
                continue
            rows.append({"status": st, "space": sp, "name": name, "warn": w})
    if as_json:
        click.echo(json.dumps({"items": rows, "total": len(rows)}, ensure_ascii=False, indent=2))
        return
    health = _last_health_summary()
    if health:
        h_ts, h_n = health
        click.echo(f"上次健康巡检: {h_ts}, findings {h_n} 条")
    by_space: dict[str, list] = {}
    for r in rows:
        by_space.setdefault(r["space"], []).append(r)
    for sp in sorted(by_space):
        items = by_space[sp]
        nwarn = sum(1 for r in items if r["warn"])
        click.echo(f"\n[{sp}] {len(items)}" + (f"  ({nwarn} ⚠)" if nwarn else ""))
        for r in items:
            flag = " ⚠" if r["warn"] else ""
            click.echo(f"  {r['status']:<9} {r['name']}{flag}")
    click.echo(f"\n共 {len(rows)} 个 object-SKILL")


@cmd_atlas.command("approve")
@click.argument("refs", nargs=-1)
@click.option("--all", "approve_all", is_flag=True, help="批准全部 staging(可配 --space 限定)")
@click.option("--space", default=None)
def atlas_approve(refs: tuple[str, ...], approve_all: bool, space: str | None) -> None:
    """批准:staging/<space>/<name> → canonical skills/。ref 形如 space/name。"""
    ensure_dirs()
    targets: list[tuple[str, str]] = []
    if approve_all:
        for sp, name, _ in _iter_objects(STAGING_ROOT):
            if space and sp != space:
                continue
            targets.append((sp, name))
    for ref in refs:
        if "/" in ref:
            sp, name = ref.split("/", 1)
            targets.append((sp, name))
        else:
            click.echo(f"跳过(需 space/name 形式): {ref}", err=True)
    if not targets:
        click.echo("没有可批准的(用 ref `space/name` 或 `--all [--space X]`)")
        return
    n = 0
    for sp, name in targets:
        src = STAGING_ROOT / sp / name
        if not (src / "SKILL.md").is_file():
            click.echo(f"  ✗ 不存在: {sp}/{name}", err=True)
            continue
        dst = SKILLS_ROOT / sp / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        n += 1
    click.echo(f"批准 {n} 个 → canonical {SKILLS_ROOT}")


@cmd_atlas.command("reject")
@click.argument("ref")
def atlas_reject(ref: str) -> None:
    """驳回并从 staging 删除一个 object(ref=space/name)。"""
    if "/" not in ref:
        raise click.UsageError("ref 需 space/name 形式")
    sp, name = ref.split("/", 1)
    d = STAGING_ROOT / sp / name
    if not d.is_dir():
        click.echo(f"不存在: {ref}")
        return
    shutil.rmtree(d)
    click.echo(f"已驳回删除 staging/{sp}/{name}")


@cmd_atlas.command("export")
@click.option("--space", default=None, help="只导某空间")
@click.option("--targets", default="claude,codex", help="导出目标(逗号分隔): claude,codex")
@click.option("--dry-run", is_flag=True)
def atlas_export(space: str | None, targets: str, dry_run: bool) -> None:
    """导出已批准的 canonical object-SKILL → ~/.claude/skills + ~/.codex/skills(各为 <name>/SKILL.md)。"""
    tg = []
    if "claude" in targets:
        tg.append(_CLAUDE_SKILLS)
    if "codex" in targets:
        tg.append(_CODEX_SKILLS)
    if not tg:
        raise click.UsageError("--targets 至少含 claude 或 codex")
    items = [(sp, name, sk) for sp, name, sk in _iter_objects(SKILLS_ROOT) if not space or sp == space]
    if not items:
        click.echo("canonical(skills/)为空——先 `omni atlas approve --all` 批准。")
        return
    seen: dict[str, str] = {}
    n = 0
    for sp, name, sk in items:
        if name in seen and seen[name] != sp:
            click.echo(f"  ⚠ 重名跨空间冲突: {name}({seen[name]} vs {sp}),后者覆盖前者", err=True)
        seen[name] = sp
        for tdir in tg:
            dst = tdir / name / "SKILL.md"
            if dry_run:
                click.echo(f"  [dry] {sp}/{name} → {dst}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(sk, dst)
        n += 1
    click.echo(f"{'(dry-run) ' if dry_run else ''}导出 {n} 个 object-SKILL → {', '.join(str(t) for t in tg)}")


def _omni() -> str:
    cand = Path(sys.executable).with_name("omni.exe")
    return str(cand if cand.exists() else "omni")


@cmd_atlas.command("refresh")
@click.option("--space", default=None, help="只刷某空间(默认全部可采空间)")
@click.option("--force", is_flag=True,
              help="清空该空间 staging + 对象清单后重采(重新 grounded 抓当前代码; 否则只补缺)")
@click.option("--no-recollect", is_flag=True, help="跳过重采, 只 approve+export(轻量, 月度默认)")
@click.option("--dry-run", is_flag=True)
def atlas_refresh(space: str | None, force: bool, no_recollect: bool, dry_run: bool) -> None:
    """月度更新闭环: 重采(重新 grounded 抓当前代码)→ approve → export 两个 AI。手动随时可跑。

    SKILL 会随设施演进过期(实测 poof 数日即旧)。--force 清后重抓全部(重, 手动);
    默认只补缺 + 重导(轻, 适合月度 cron);--no-recollect 只重导。
    """
    omni = _omni()
    spaces = [space] if space else list(SPACES)
    if not no_recollect:
        for sp in spaces:
            if sp not in SPACES:
                click.echo(f"  跳过(非可采空间): {sp}", err=True)
                continue
            if force and not dry_run:
                d = STAGING_ROOT / sp
                if d.exists():
                    shutil.rmtree(d)
                op = PLAN_DIR / f"{sp}.objects.json"
                if op.exists():
                    op.unlink()
            if dry_run:
                click.echo(f"  [dry] 重采 {sp}" + (" (force: 清后重抓)" if force else " (补缺)"))
                continue
            click.echo(f"  重采 {sp} ...")
            r = subprocess.run([omni, "run", "project_atlas.run", "-i", f"space={sp}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            click.echo(f"    rc={r.returncode}  {(r.stdout or r.stderr or '')[-180:]}")
    if dry_run:
        click.echo("  [dry] 然后 approve --all + export")
        return
    click.echo("  approve --all ...")
    subprocess.run([omni, "atlas", "approve", "--all"], encoding="utf-8", errors="replace")
    click.echo("  export ...")
    subprocess.run([omni, "atlas", "export"], encoding="utf-8", errors="replace")
    click.echo("✓ refresh 完成")


@cmd_atlas.command("health")
@click.option("--apply", "do_apply", is_flag=True, help="自动修复导出漂移(按 canonical 覆盖两个生效目录)")
@click.option("--json", "as_json", is_flag=True)
def atlas_health(do_apply: bool, as_json: bool) -> None:
    """技能库健康巡检: 解析级体检(BOM/frontmatter/name+description) + 导出漂移(可 --apply 自动修) + 正文死绝对路径(只报)。"""
    from omnicompany.packages.domains.project_atlas.health import run_health
    rep = run_health(apply_fix=do_apply)
    if as_json:
        click.echo(json.dumps(rep, ensure_ascii=False, indent=2))
        return
    c = rep["counts"]
    click.echo(f"canonical {rep['canonical_count']} 个 / 共扫描 {rep['scanned_skill_files']} 份 SKILL.md")
    click.echo(f"findings: parse {c['parse']} · drift {c['drift']} · dead_ref {c['dead_ref']}")
    if do_apply and rep["counts"]["drift"]:
        click.echo(f"  --apply: 已修复 {rep['repair']['repaired']} 处导出漂移")
    for f in rep["findings"][:60]:
        loc = f"{f['space']}/{f['name']}" if f["space"] or f["name"] else f["path"]
        click.echo(f"  [{f['category']}] {loc}: {f['detail']}")
    if len(rep["findings"]) > 60:
        click.echo(f"  … 还有 {len(rep['findings']) - 60} 条, 见 {rep.get('_written')}")
    click.echo(f"报告: {rep.get('_written')}")


__all__ = ["cmd_atlas"]
