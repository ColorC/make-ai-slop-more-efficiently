# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T00:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni new / omni sandbox 命令组, 8 种 kind 实例创建 + 沙盒指引"
# [OMNI] why="模板四件套已就绪, 这层是薄包装让用户一行命令立新实例, 默认落沙盒不进正式区, 跟身份模块联动注入 OmniMark 头"
# [OMNI] tags=cli,creation,sandbox,new,kinds
# [OMNI] material_id="material:cli.commands.sandbox_scaffold.instance_creator.py"
"""omni new / omni sandbox 命令组.

`omni new --kind=<kind> --name=<name>` — 复制 templates/<kind>/骨架 到沙盒, 注入
OmniMark 头身份字段, 显示向导路径

`omni sandbox open` — 返回沙盒路径 (建目录如不存在)
`omni sandbox guide --kind=<kind>` — 显示对应 kind 的向导.md
`omni sandbox list` — 列沙盒 drafts 里的草稿

支持 8 个 kind: worker / material / team / agent / hook / tool / data / plan
不支持 template (元模板由项目维护者立, 不是普通用户).
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

import click

from omnicompany.packages.services._core.identity import current_session_meta


KIND_LIST = ["worker", "material", "team", "agent", "hook", "tool", "data", "plan"]

# 每个 kind 的骨架形态: 单文件 (.py / .md) 或 文件夹
_SKELETON_SHAPES = {
    "worker":   ("file", "骨架.py"),
    "material": ("file", "骨架.py"),
    "agent":    ("file", "骨架.py"),    # 同时复制 范本_prompt.md
    "hook":     ("file", "骨架.py"),
    "tool":     ("file", "骨架.py"),
    "data":     ("file", "骨架.md"),
    "plan":     ("file", "骨架.md"),
    "team":     ("folder", "骨架"),
}


def _project_root() -> Path:
    """omnicompany 项目根 (含 src/omnicompany + docs)."""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[4]


def _sandbox_root() -> Path:
    return _project_root() / ".omni" / "sandbox"


def _ensure_sandbox() -> Path:
    root = _sandbox_root()
    (root / "drafts").mkdir(parents=True, exist_ok=True)
    (root / "archive").mkdir(parents=True, exist_ok=True)
    return root


# ── omni sandbox 命令组 ──────────────────────────────────────────────


@click.group("sandbox")
def cmd_sandbox() -> None:
    """omnicompany 沙盒目录命令组.

    沙盒在 `<omnicompany>/.omni/sandbox/`, 是写入正式区前的合法草稿区.
    详细规范见 docs/standards/cli/sandbox.md.
    """


@cmd_sandbox.command("open")
def cmd_sandbox_open() -> None:
    """返回沙盒路径 (创建目录如不存在)."""
    root = _ensure_sandbox()
    click.echo(str(root))


@cmd_sandbox.command("guide")
@click.option("--kind", required=True,
              type=click.Choice(KIND_LIST + ["header"]),
              help="对应概念的填写指引 (kind=header 显示 OmniMark 头规范)")
def cmd_sandbox_guide(kind: str) -> None:
    """显示某个 kind 的填写指引 (向导.md)."""
    proj = _project_root()
    if kind == "header":
        guide = proj / "docs" / "standards" / "cli" / "omni-header.md"
    else:
        guide = proj / "templates" / kind / "向导.md"
    if not guide.is_file():
        click.echo(f"指引文件不存在: {guide}", err=True)
        raise SystemExit(1)
    click.echo(guide.read_text(encoding="utf-8"))


@cmd_sandbox.command("list")
def cmd_sandbox_list() -> None:
    """列沙盒 drafts/ 里的草稿."""
    drafts = _sandbox_root() / "drafts"
    if not drafts.is_dir() or not any(drafts.iterdir()):
        click.echo("沙盒为空 (drafts/ 不存在或无内容). 用 `omni new` 立新草稿.")
        return
    for entry in sorted(drafts.iterdir()):
        if entry.is_dir():
            sub = sorted(entry.iterdir())
            click.echo(f"[{entry.name}/]  ({len(sub)} 项)")
            for s in sub:
                click.echo(f"  {s.name}")
        else:
            click.echo(entry.name)


# ── omni sandbox check ───────────────────────────────────────────────


def _check_omnimark_header(file_path: Path) -> tuple[bool, list[str]]:
    """检查 OmniMark 头 5 字段齐 (origin/ts/type/summary/why+tags).

    占位符识别只看 OmniMark 头注释行 (`# [OMNI]` / `<!-- [OMNI]` 起头), 不扫
    markdown 正文 / docstring (那里 `<...>` 常作语义说明文字, 不是待填占位符).
    """
    try:
        text = file_path.read_text(encoding="utf-8")[:8192]
    except (OSError, UnicodeDecodeError):
        return False, ["读不出来文件 (编码 / 权限问题)"]
    if "[OMNI]" not in text:
        return False, ["缺 OmniMark 头 (没找到 [OMNI] 标记)"]
    issues = []
    required = ["origin", "ts", "type", "summary", "why", "tags"]
    for f in required:
        if f"{f}=" not in text and f"{f}=" not in text.replace('"', ''):
            issues.append(f"OmniMark 头缺字段 {f}")
    # 占位符识别只在 [OMNI] 头行扫 (跳 markdown 正文/docstring)
    import re
    omni_lines = [ln for ln in text.splitlines()[:30] if "[OMNI]" in ln]
    placeholders: list[str] = []
    for ln in omni_lines:
        placeholders.extend(re.findall(r"<[a-zA-Z][^>\s]*?>", ln))
    # 同时扫文件前 2000 字符的 Python 类/属性占位符 (例 `<TOOL_NAME_UPPER>`,
    # `<DriverCamelCase>`) — 这些是 Python 语法位置必须填的, 不在 [OMNI] 行
    code_head = text[:2000]
    code_placeholders = re.findall(r"<[A-Z][A-Za-z0-9_]*>", code_head)
    placeholders.extend(code_placeholders)
    if placeholders:
        unique = sorted(set(placeholders))[:5]
        issues.append(f"OmniMark 头 / 代码骨架还有 {len(set(placeholders))} 个占位符未填: {unique}")
    return (not issues), issues


def _check_python_compile(file_path: Path) -> tuple[bool, list[str]]:
    import py_compile
    try:
        py_compile.compile(str(file_path), doraise=True)
        return True, []
    except py_compile.PyCompileError as e:
        return False, [f"py_compile 失败: {str(e)[:200]}"]


def _check_yaml_parse(file_path: Path) -> tuple[bool, list[str]]:
    try:
        import yaml
    except ImportError:
        return True, []
    try:
        with open(file_path, encoding="utf-8") as f:
            yaml.safe_load(f)
        return True, []
    except yaml.YAMLError as e:
        return False, [f"yaml 解析失败: {str(e)[:200]}"]


def _check_in_sandbox(content_path: Path) -> tuple[bool, list[str]]:
    sandbox = _sandbox_root().resolve()
    try:
        content_path.resolve().relative_to(sandbox)
        return True, []
    except ValueError:
        return False, [f"内容不在沙盒目录内 ({sandbox})"]


@cmd_sandbox.command("check")
@click.option("--content", required=True,
              type=click.Path(exists=True, dir_okay=True, file_okay=True),
              help="要检查的内容路径 (文件或目录)")
@click.option("--strict", is_flag=True, help="严格模式: 不在沙盒内也报错")
def cmd_sandbox_check(content: str, strict: bool) -> None:
    """检查草稿是否合规 (OmniMark 头 / 解析 / 占位符全填).

    通过 ≠ 自动转正, 只是拿到合规标记. 通过后调 `omni sandbox promote` 才转正式区.
    """
    content_path = Path(content).resolve()

    all_issues: list[tuple[str, str]] = []  # (file, issue)

    # 1. 沙盒位置检查
    if strict:
        ok, issues = _check_in_sandbox(content_path)
        if not ok:
            for i in issues:
                all_issues.append((str(content_path), i))

    # 2. 文件 / 目录递归检查
    if content_path.is_file():
        files_to_check = [content_path]
    else:
        files_to_check = [
            f for f in content_path.rglob("*")
            if f.is_file() and not any(p.startswith(".") for p in f.parts)
        ]

    for f in files_to_check:
        # OmniMark 头 (.py / .md / .yaml / .yml 都查)
        if f.suffix in (".py", ".md", ".yaml", ".yml"):
            ok, issues = _check_omnimark_header(f)
            for i in issues:
                all_issues.append((str(f), i))
        # py_compile
        if f.suffix == ".py":
            ok, issues = _check_python_compile(f)
            for i in issues:
                all_issues.append((str(f), i))
        # yaml parse
        if f.suffix in (".yaml", ".yml"):
            ok, issues = _check_yaml_parse(f)
            for i in issues:
                all_issues.append((str(f), i))

    proj = _project_root()
    click.echo(f"检查 {len(files_to_check)} 个文件:")
    if not all_issues:
        click.echo("  PASS · 全部通过")
        return

    click.echo(f"  FAIL · {len(all_issues)} 处问题")
    for fp, issue in all_issues:
        try:
            rel = Path(fp).relative_to(proj)
        except ValueError:
            rel = fp
        click.echo(f"    {rel}: {issue}")
    raise SystemExit(1)


# ── omni sandbox promote ─────────────────────────────────────────────


# 服务 bucket 固定清单 (跟 directory_structure.md §4.2.1 对齐)
_VALID_SERVICE_BUCKETS = frozenset({
    "_authoring", "_core", "_diagnosis", "_learning", "_utility",
})

# kind → 期望子目录名 (在 service 内部)
_KIND_SUBDIR = {
    "agent": "agents",
    "worker": "workers",
    "tool": "tools",
    "team": "teams",
    "hook": "hooks",
    "material": "materials",
}


def _validate_promote_target(
    target_path: Path, kind: str, proj: Path,
) -> tuple[bool, list[str], list[Path]]:
    """验证 target 路径合规, 返回 (合规, 问题列表, 待创建目录列表).

    检查项 (跟 directory_structure.md §4.2.1 + 七、不变量第 7-8 条对齐):
      1. target 在 src/omnicompany/packages/services/<bucket>/<service>/... 下
      2. <bucket> 在 _VALID_SERVICE_BUCKETS 清单内 (5 个固定 bucket)
      3. <service> 已存在 (新 service 必走"用户批准+加规范"流程)
      4. kind 子目录命名按 _KIND_SUBDIR 约定 (agent → agents/ 等)
      5. 列出 target.parent 链上待创建的目录 (供调用方决定要不要 abort)

    domain (`packages/domains/<domain>/...`) 跟其他 plan/data 类目标走宽松路径
    (不强制 bucket 校验, 但仍列待创建目录).
    """
    issues: list[str] = []
    to_create: list[Path] = []

    # 列待创建目录 (从 target.parent 向上找第一个已存在的 ancestor)
    cur = target_path.parent
    while not cur.exists():
        to_create.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    to_create.reverse()  # 从外到内

    # 解析路径段 (相对项目根)
    try:
        rel = target_path.relative_to(proj).as_posix().split("/")
    except ValueError:
        issues.append(f"target 不在项目根内: {target_path}")
        return (False, issues, to_create)

    # 只对 src/omnicompany/packages/services/ 做 bucket 验证, 其他路径 (docs/plans, data) 放行
    if (
        len(rel) >= 5
        and rel[0] == "src"
        and rel[1] == "omnicompany"
        and rel[2] == "packages"
        and rel[3] == "services"
    ):
        bucket = rel[4]
        if bucket not in _VALID_SERVICE_BUCKETS:
            issues.append(
                f"bucket {bucket!r} 不在固定清单 {sorted(_VALID_SERVICE_BUCKETS)}. "
                f"立新 bucket 必先改 docs/standards/_global/directory_structure.md §4.2.1"
            )

        # service 子目录: rel[5] = <service>
        if len(rel) >= 6:
            service_dir = proj / "src/omnicompany/packages/services" / bucket / rel[5]
            service_is_new = not service_dir.exists()
            if service_is_new:
                issues.append(
                    f"service {bucket}/{rel[5]!r} 是新 service, 不在已注册清单. "
                    f"立新 service 走: 用户批准 → omni new --kind=<> 立草稿 → "
                    f"promote --allow-new-service 通过, 或先手动 mkdir + 加 manifest.yaml"
                )

        # kind 子目录命名: rel[-2] 应该是 kind 对应子目录 (agents/ workers/ tools/ ...)
        expected_subdir = _KIND_SUBDIR.get(kind)
        if expected_subdir and len(rel) >= 3 and rel[-2] != expected_subdir:
            issues.append(
                f"kind={kind!r} 期望落在 {expected_subdir}/ 子目录, "
                f"实际 target 父目录是 {rel[-2]!r}"
            )

    return (not issues, issues, to_create)


@cmd_sandbox.command("promote")
@click.option("--content", required=True,
              type=click.Path(exists=True, dir_okay=True, file_okay=True),
              help="沙盒里的草稿路径")
@click.option("--target", required=True,
              help="目标位置 (相对项目根, 例: src/omnicompany/packages/services/_learning/foo/foo.py)")
@click.option("--kind", required=True,
              help="kind (注册时用): worker / material / team / agent / hook / tool / data / plan")
@click.option("--skip-check", is_flag=True, help="跳过预检 (调试用, 不推荐)")
@click.option("--skip-register", is_flag=True, help="跳过注册到中心 (只移动)")
@click.option("--allow-new-service", is_flag=True,
              help="允许创建新 service 子目录 (默认 false, 跟用户铁律对齐). "
                   "新 bucket 永不允许 (走改规范流程)")
@click.option("--no-siblings", is_flag=True,
              help="不搬同目录关联文件 (默认搬 agent _prompt.md / team yaml manifest 等)")
def cmd_sandbox_promote(
    content: str, target: str, kind: str,
    skip_check: bool, skip_register: bool,
    allow_new_service: bool, no_siblings: bool,
) -> None:
    """沙盒草稿转正式区 + 注册到中心.

    流程 (4 步, 2026-05-02 跟用户铁律加 target 验证):
      1. 检查 (omni sandbox check) — 必通过
      2. **target 路径合规验证** — 看 directory_structure.md §4.2.1 + 不变量 7-8 条
      3. 移动 sandbox/drafts/<草稿> + 同目录 sibling 文件 → <target> 跟同级
      4. 调 omni register 注册到中心 (skip-register=True 时跳过)

    用户铁律: 不擅自新建目录. 新 bucket 永远拦, 新 service 默认拦, 加 --allow-new-service 才放行.
    """
    import subprocess
    import sys
    proj = _project_root()
    content_path = Path(content).resolve()
    target_path = (proj / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()

    # ── 步骤 1: 预检 ──
    if not skip_check:
        click.echo("== 步骤 1 / 4 · 预检 ==")
        check_cmd = [sys.executable, "-m", "omnicompany.cli.main", "sandbox", "check",
                     "--content", str(content_path)]
        r = subprocess.run(check_cmd, capture_output=True, text=True)
        click.echo(r.stdout)
        if r.returncode != 0:
            click.echo("FAIL · 预检不通过, 拒绝转正. 修完后重跑.", err=True)
            raise SystemExit(1)

    # ── 步骤 2: target 合规验证 (新规, 不合规立即拦不等 mkdir) ──
    click.echo("== 步骤 2 / 4 · target 合规验证 ==")
    ok, issues, to_create = _validate_promote_target(target_path, kind, proj)
    if to_create:
        click.echo(f"  待创建目录 ({len(to_create)} 条):")
        for d in to_create:
            try:
                rel = d.relative_to(proj)
            except ValueError:
                rel = d
            click.echo(f"    {rel}")
    if not ok:
        # 区分: bucket 错绝拦, service 新看 --allow-new-service
        bucket_issue = any("bucket" in i and "固定清单" in i for i in issues)
        new_service_issue = any("是新 service" in i for i in issues)

        if bucket_issue:
            click.echo("FAIL · target bucket 不合规. 不变量第 8 条不允许擅自立新 bucket.", err=True)
            for i in issues:
                click.echo(f"  - {i}", err=True)
            raise SystemExit(1)

        if new_service_issue and not allow_new_service:
            click.echo("FAIL · target 含新 service 子目录, 用户铁律不允许擅自新建. "
                       "确认后加 --allow-new-service 重跑.", err=True)
            for i in issues:
                click.echo(f"  - {i}", err=True)
            raise SystemExit(1)

        # 其他 issue (kind 子目录命名等) 也拦
        if issues and not new_service_issue:
            click.echo("FAIL · target 验证不通过:", err=True)
            for i in issues:
                click.echo(f"  - {i}", err=True)
            raise SystemExit(1)

        # 走到这就是 new_service_issue + --allow-new-service, 显式放行
        click.echo("  WARN · 新 service 已批准 (--allow-new-service)")
    else:
        click.echo("  OK · target 合规")

    # ── 步骤 3: 移动 (含 sibling) ──
    click.echo("== 步骤 3 / 4 · 移动 ==")
    if target_path.exists():
        click.echo(f"目标已存在: {target_path}", err=True)
        raise SystemExit(1)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    shutil.move(str(content_path), str(target_path))
    moved.append(target_path)
    click.echo(f"  {content_path.relative_to(proj)} → {target_path.relative_to(proj)}")

    # sibling 文件 (按 kind 决定搬什么)
    if not no_siblings:
        siblings = _siblings_to_promote(content_path, kind)
        for src_sib, target_sib_name in siblings:
            if not src_sib.exists():
                continue
            tgt_sib = target_path.parent / target_sib_name
            if tgt_sib.exists():
                click.echo(f"  跳过 sibling (目标已存在): {target_sib_name}")
                continue
            tgt_sib.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_sib), str(tgt_sib))
            moved.append(tgt_sib)
            click.echo(f"  + sibling: {src_sib.relative_to(proj)} → {tgt_sib.relative_to(proj)}")

    # ── 步骤 4: 注册 ──
    if not skip_register:
        click.echo("== 步骤 4 / 4 · 注册到中心 ==")
        reg_cmd = [sys.executable, "-m", "omnicompany.cli.main", "register",
                   "--kind", kind, "--content", str(target_path)]
        r = subprocess.run(reg_cmd, capture_output=True, text=True)
        click.echo(r.stdout)
        if r.returncode != 0:
            click.echo(f"WARN · 注册失败但已转正, 手动跑: omni register --kind={kind} --content={target_path}",
                      err=True)
            click.echo(r.stderr, err=True)

    # 草稿父目录清理: 移完所有内容后, 父目录还残留 __pycache__ / 空子目录就清掉
    src_parent = content_path.parent
    sandbox_root = _sandbox_root().resolve()
    try:
        # 必须仍在沙盒下 (防御: 不在沙盒就不清)
        src_parent.resolve().relative_to(sandbox_root)
        # 清剩余 __pycache__ + 空目录 (递归)
        for pc in list(src_parent.rglob("__pycache__")):
            if pc.is_dir():
                shutil.rmtree(pc, ignore_errors=True)
        # 父目录里若所有剩余文件都已被 sibling 搬走 → 清空目录链
        if src_parent.is_dir() and not any(src_parent.iterdir()):
            src_parent.rmdir()
            click.echo(f"  清空草稿目录: {src_parent.relative_to(proj)}")
            # 上溯一级 (例 .omni/sandbox/drafts/agent/<name>/), 若 kind 目录也空就清
            kind_dir = src_parent.parent
            try:
                kind_dir.resolve().relative_to(sandbox_root)
                if kind_dir.is_dir() and not any(kind_dir.iterdir()):
                    kind_dir.rmdir()
            except (ValueError, OSError):
                pass
    except (ValueError, OSError):
        pass

    click.echo()
    click.echo(f"OK 转正完成: {target_path.relative_to(proj)} (移动 {len(moved)} 个文件)")


def _siblings_to_promote(content_path: Path, kind: str) -> list[tuple[Path, str]]:
    """按 kind 决定搬哪些 sibling.

    返回 [(源 sibling 路径, 目标 sibling 文件名), ...].
    目标文件名: 一般跟源同名, 让调用方拼到 target.parent 下.
    """
    parent = content_path.parent
    base = content_path.stem  # e.g. "mass_classifier"
    siblings: list[tuple[Path, str]] = []

    if kind == "agent":
        # agent: 同目录的 _prompt.md 跟 .yaml 配置一起搬
        for ext in ("_prompt.md", "_config.yaml"):
            cand = parent / f"{base}{ext}"
            if cand.exists():
                siblings.append((cand, cand.name))

    if kind == "team":
        # team: pipeline.yaml 跟 .omni/manifest.yaml 跟 DESIGN.md 都搬 (yaml 形态用 pipeline.yaml)
        for fname in ("pipeline.yaml", "DESIGN.md", "formats.py", "testmap.yaml"):
            cand = parent / fname
            if cand.exists() and cand != content_path:
                siblings.append((cand, fname))
        # .omni/manifest.yaml (深一层)
        manifest = parent / ".omni" / "manifest.yaml"
        if manifest.exists():
            siblings.append((manifest, ".omni/manifest.yaml"))

    return siblings


# ── omni sandbox archive ─────────────────────────────────────────────


@cmd_sandbox.command("archive")
@click.option("--keep-drafts", is_flag=True, help="归档后不清空 drafts (调试用)")
def cmd_sandbox_archive(keep_drafts: bool) -> None:
    """归档沙盒 drafts/ → archive/<YYYY-MM-DD-HHMM>/.

    drafts/ 里所有内容打包到 archive/, 然后清空 drafts/. archive 保留期由用户配
    (推荐 90 天, 由清理工人定期扫除).
    """
    import time as _t
    sandbox = _sandbox_root()
    drafts = sandbox / "drafts"
    archive_root = sandbox / "archive"

    if not drafts.is_dir() or not any(drafts.iterdir()):
        click.echo("drafts/ 为空, 没什么可归档")
        return

    ts = _t.strftime("%Y-%m-%d-%H%M")
    archive_dir = archive_root / ts
    if archive_dir.exists():
        archive_dir = archive_root / f"{ts}-{int(_t.time()) % 1000}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    for entry in drafts.iterdir():
        target = archive_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
        moved_count += 1

    click.echo(f"归档 {moved_count} 项到 {archive_dir.relative_to(_project_root())}")

    if not keep_drafts:
        for entry in drafts.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        click.echo("drafts/ 已清空")
    else:
        click.echo("--keep-drafts 保留草稿原状")


# ── omni new 命令 ────────────────────────────────────────────────────


@click.command("new")
@click.option("--kind", required=True, type=click.Choice(KIND_LIST),
              help="新实例的概念类型 (8 选 1)")
@click.option("--name", required=True,
              help="新实例名 (蛇形小写, 例如 csv_to_md_writer)")
@click.option("--target", default=None,
              help="目标位置 (默认沙盒 drafts/<kind>/<name>/, 设此参数则直接落正式区)")
@click.option("--domain", default=None,
              help="业务域 (例如 demogame / voxelcraft), 替换骨架里的 <domain> 占位符")
@click.option("--no-substitute", is_flag=True,
              help="跳过 OmniMark 头身份注入 (调试用)")
@click.option("--form", default=None,
              type=click.Choice(["python", "yaml"]),
              help="(仅 --kind=team 有效) team 形态: python (生成 pipeline.py + formats.py + DESIGN.md, "
                   "Python 形态; 复杂 fan-in/fan-out 用) 或 yaml (生成 pipeline.yaml 单文件, "
                   "推荐, 跟用户原始需求 6.3.1 对齐). 默认 yaml")
def cmd_new(
    kind: str,
    name: str,
    target: str | None,
    domain: str | None,
    no_substitute: bool,
    form: str | None,
) -> None:
    """新建一份 <kind> 实例到沙盒 (默认) 或指定位置.

    流程:
      1. 复制 templates/<kind>/骨架 到目标位置 (用 <name> 命名)
      2. 注入 OmniMark 头身份字段 (origin / agent / ts 用当前 session 身份)
      3. 替换基本占位符 (<domain> 等)
      4. 显示向导路径供下一步填写
    """
    proj = _project_root()
    skel_dir = proj / "templates" / kind
    if not skel_dir.is_dir():
        click.echo(f"模板目录不存在: {skel_dir}", err=True)
        raise SystemExit(1)

    # F3: --form 仅对 team 有效, 默认 yaml (跟用户原始需求 6.3.1 对齐)
    if form and kind != "team":
        click.echo(f"WARN · --form 仅 --kind=team 有效, 当前 kind={kind!r} 忽略", err=True)
        form = None
    if kind == "team" and form is None:
        form = "yaml"  # 默认 yaml 形态

    shape, skel_name = _SKELETON_SHAPES[kind]
    if kind == "team" and form == "yaml":
        # 切到单文件 yaml 形态 (复制纯配置范本.yaml 重命名)
        shape = "file"
        skel_name = "纯配置范本.yaml"
    skel_path = skel_dir / skel_name
    if not skel_path.exists():
        click.echo(f"骨架不存在: {skel_path}", err=True)
        raise SystemExit(1)

    # 决定目标
    if target is None:
        target_parent = _sandbox_root() / "drafts" / kind / name
        target_parent.mkdir(parents=True, exist_ok=True)
        location_note = f"沙盒 {target_parent}"
    else:
        target_parent = Path(target).resolve()
        target_parent.mkdir(parents=True, exist_ok=True)
        location_note = f"指定位置 {target_parent}"

    copied: list[Path] = []

    if shape == "folder":
        # team 整目录复制
        if any(target_parent.iterdir()):
            click.echo(f"目标目录非空, 拒绝覆盖: {target_parent}", err=True)
            raise SystemExit(1)
        shutil.copytree(skel_path, target_parent, dirs_exist_ok=True)
        copied = [p for p in target_parent.rglob("*") if p.is_file()]
    else:
        # 单文件复制
        ext = Path(skel_name).suffix
        target_file = target_parent / f"{name}{ext}"
        if target_file.exists():
            click.echo(f"目标已存在, 拒绝覆盖: {target_file}", err=True)
            raise SystemExit(1)
        shutil.copy2(skel_path, target_file)
        copied.append(target_file)
        # agent kind 配套 prompt 文件
        if kind == "agent":
            prompt_src = skel_dir / "范本_prompt.md"
            if prompt_src.is_file():
                prompt_target = target_parent / f"{name}_prompt.md"
                shutil.copy2(prompt_src, prompt_target)
                copied.append(prompt_target)
        # team --form=yaml 配套 testmap.yaml 骨架 (--form=python 形态整目录 copytree 天然带上)
        if kind == "team" and form == "yaml":
            testmap_src = proj / "templates" / "team" / "骨架" / "testmap.yaml"
            if testmap_src.is_file():
                testmap_target = target_parent / "testmap.yaml"
                shutil.copy2(testmap_src, testmap_target)
                copied.append(testmap_target)

    # 注入 OmniMark 头 + 替换占位符
    if not no_substitute:
        meta = current_session_meta()
        for f in copied:
            if f.suffix in (".py", ".md", ".yaml", ".yml"):
                _inject_header(f, meta=meta, name=name, domain=domain or "")

    # 显示成功 + 下一步
    guide_path = skel_dir / "向导.md"
    click.echo(f"OK 新建 {kind} '{name}' 到 {location_note}")
    click.echo(f"  复制 {len(copied)} 个文件:")
    for f in copied:
        try:
            rel = f.relative_to(proj)
            click.echo(f"    {rel}")
        except ValueError:
            click.echo(f"    {f}")
    click.echo()
    click.echo("下一步:")
    click.echo(f"  1. 读填写指引:  omni sandbox guide --kind={kind}")
    click.echo(f"     (或直接看: {guide_path.relative_to(proj)})")
    click.echo("  2. 按指引填字段, 替换里面的 <placeholder>")
    click.echo("  3. 检查跟转正:  omni sandbox check --content=<file>  (CLI G6 实装中)")


def _inject_header(
    file_path: Path,
    *,
    meta: dict,
    name: str,
    domain: str,
) -> None:
    """替换 OmniMark 头身份字段 + 简单占位符.

    OmniMark 头格式 (Python: `# [OMNI] ...`, Markdown: `<!-- [OMNI] ... -->`,
    YAML: `# [OMNI] ...`). 替换以下字段:
      - origin=<...> → origin=ai-ide
      - ts=<...> → ts=<当前 ISO 时间>
      - agent=<...> → agent=<当前 trace_id>

    简单占位符 (跨字段替换):
      - <domain> → 用户给的 --domain 值
      - testmap.yaml 的 `app: <name>` 行 → 用户给的 --name 值 (窄范围, 只认精确的
        `app: <name>` 整行, 不做全文 <name> 泛替换 —— plan/骨架.md 等文件里
        `service:<name>` 这类是示例占位符, 泛替换会误伤它们)
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    trace_id = meta.get("trace_id") or "ai-ide-unknown"

    # OmniMark 头身份字段
    text = re.sub(r"origin=<[^>]*>", "origin=ai-ide", text)
    text = re.sub(r"ts=<[^>]*>", f"ts={ts}", text)
    text = re.sub(r"agent=<[^>]*>", f"agent={trace_id}", text)

    # domain 占位符
    if domain:
        text = re.sub(r"<本服务包/领域名>", domain, text)
        text = re.sub(r"<服务包名>", domain, text)
        text = re.sub(r"<业务域>", domain, text)
        text = re.sub(r'DOMAIN = "<[^"]*>"', f'DOMAIN = "{domain}"', text)

    # name 占位符 (窄范围: 只认 testmap.yaml 的 `app: <name>` 整行)
    if name:
        text = re.sub(r"^app:\s*<name>\s*$", f"app: {name}", text, flags=re.MULTILINE)

    file_path.write_text(text, encoding="utf-8")
