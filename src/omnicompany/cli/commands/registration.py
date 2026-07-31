# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T00:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni register / omni lookup 命令组, 显式注册 8 种 kind 实例到中心 + 查询入口"
# [OMNI] why="services/_core/registry 已实施 6 种 + scanner AST 自动发现型. 这层加显式注册入口让用户/AI IDE 主动绑 trace_id 到内容, 跟 G1 身份模块联动. data + plan 两种走同一 InstanceRegistry"
# [OMNI] tags=cli,register,lookup,registry,identity
# [OMNI] material_id="material:cli.commands.explicit_registration_and_lookup.implementation.py"
"""omni register / omni lookup 命令.

`omni register --kind=<> --content=<>` — 显式注册一份内容到 InstanceRegistry, 自动绑当前 trace_id
`omni lookup [--kind=<>] [--id=<>] [--package=<>]` — 查询注册中心
`omni register-types` — 列已注册的 kind 类型 (8 种)

跟 `omni registry list/health` (现有 AST 扫描型查询) 的区别:
- omni registry list  — 看 AST 扫描产生的实体 (代码内 class FooRouter / Format(...) 等)
- omni register       — 显式注册一份内容 (对 data / plan / 沙盒草稿等 AST 扫不到的)
- omni lookup         — 统一查询入口, 两种来源都查
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import click

from omnicompany.packages.services._core.identity import current_session_meta
from omnicompany.packages.services._core.registry import (
    get_registry, query, InstanceEntry, meta_registry,
)


# kind 别名: 用户用的 omnicompany 名 → registry 内部 type 名
_KIND_ALIAS = {
    "material": "format",
    "worker": "router",
    "team": "pipeline",
    "agent": "agent_loop",
}


def _resolve_type_name(kind: str) -> str:
    """omnicompany kind → registry type."""
    return _KIND_ALIAS.get(kind, kind)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[4]


# ── PHASE3 第二段 · 模板 pattern 校验 + 写入凭据 ─────────────────────

def _load_kind_template(kind: str, proj: Path) -> dict | None:
    """读 templates/<kind>/注册件.yaml 拿 instance_location / instance_naming 等.

    kind 没对应模板返回 None (例 external_pointer 没模板).
    """
    yaml_path = proj / "templates" / kind / "注册件.yaml"
    if not yaml_path.is_file():
        return None
    try:
        import yaml as _yaml
        return _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _validate_path_against_pattern(content_path: Path, pattern: str, proj: Path) -> tuple[bool, str]:
    """校验 content_path 是否符合 instance_location.pattern.

    简化算法: pattern 取前缀至第一个 `<` 占位符前的字面段, 跟 content_path
    相对项目根的前缀对比. 例 pattern="docs/plans/<package>/[<date>]<topic>/plan.md"
    → 前缀 "docs/plans/", content_path 必以此开头.

    pattern 含 <workspace_root> 这种顶层占位时跳过校验 (太通用判不准).
    """
    if not pattern:
        return True, ""
    if "<workspace_root>" in pattern:
        return True, "(pattern 含 <workspace_root>, 跳过路径前缀校验)"
    prefix = pattern.split("<", 1)[0].rstrip("/")
    if not prefix:
        return True, ""
    try:
        rel = content_path.relative_to(proj).as_posix()
    except ValueError:
        return False, f"content 路径 {content_path} 不在项目根 {proj} 内"
    if not rel.startswith(prefix):
        return False, f"路径 '{rel}' 不以 '{prefix}' 开头 (pattern: {pattern})"
    return True, ""


def _validate_naming_against_pattern(content_path: Path, pattern: str) -> tuple[bool, str]:
    """校验 filename 是否符合 instance_naming.pattern.

    pattern 全字面 (例 "plan.md"): 对比 filename 严格相等.
    pattern 含占位 (例 "<filename>.<ext>" 或 "<name>_v<n>.py"): 跳过 (含义太通用).
    """
    if not pattern:
        return True, ""
    if "<" in pattern:
        return True, "(pattern 含占位, 跳过 filename 严格校验)"
    if content_path.is_dir():
        return True, "(content 是目录, 命名校验对文件)"
    if content_path.name != pattern:
        return False, f"文件名 '{content_path.name}' != pattern '{pattern}'"
    return True, ""


def _issue_write_credential(
    *, entity_id: str, source_file: str, trace_id: str, proj: Path,
) -> Path:
    """注册成功后发 write credential, 落到 data/services/registry/credentials/<id>.json.

    凭据是 lock 组 (PHASE3 第四段) 的写入门禁前提. 本段先把凭据机制建起来,
    锁组打开时按 credential 校验"该 trace_id 是否曾合法注册过这条 path".
    """
    import time as _time
    import hashlib as _hashlib
    cred_dir = proj / "data" / "services" / "registry" / "credentials"
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_id = _hashlib.sha256(f"{entity_id}|{trace_id}".encode("utf-8")).hexdigest()[:16]
    cred_path = cred_dir / f"{cred_id}.json"
    payload = {
        "credential_id": cred_id,
        "entity_id": entity_id,
        "source_file": source_file,
        "trace_id": trace_id,
        "issued_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    cred_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cred_path


def _parse_omnimark_header(file_path: Path) -> dict[str, str]:
    """从文件抓 OmniMark 头字段 (origin/ts/type/agent/summary/why/tags)."""
    if not file_path.is_file():
        return {}
    try:
        text = file_path.read_text(encoding="utf-8")[:4096]
    except (OSError, UnicodeDecodeError):
        return {}
    fields: dict[str, str] = {}
    # 匹配 [OMNI] key=value 跟 key="..." 两种形态
    for m in re.finditer(r"\[OMNI\][^\n]*", text):
        line = m.group(0)
        for kv in re.finditer(r"(\w+)=([^\s\"]+|\"[^\"]*\")", line):
            k, v = kv.group(1), kv.group(2).strip('"')
            if k != "OMNI":
                fields.setdefault(k, v)
    return fields


def _parse_plan_binding(plan_md_path: Path) -> dict | None:
    """从 plan.md 抓 binding YAML 块 (在 frontmatter `---` 包围的 binding 字段).

    返回 dict 含 workspace / packages / targets / applicable_standards / expected_completion / ttl_days,
    或 None 如果没找到 binding.
    """
    if not plan_md_path.is_file():
        return None
    try:
        text = plan_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    # 找 frontmatter (--- 包围) 或者就近 binding 块
    m = re.search(r"```ya?ml\s*\n(binding:[\s\S]+?)```", text)
    if not m:
        m = re.search(r"^---\s*\n([\s\S]+?)^---\s*$", text, re.MULTILINE)
    if not m:
        return None
    yaml_block = m.group(1)
    try:
        import yaml
        data = yaml.safe_load(yaml_block)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # 提取 binding 字段 + 顶层 applicable_standards / expected_completion / ttl_days
    binding = data.get("binding") if "binding" in data else data
    if not isinstance(binding, dict):
        return None
    out = dict(binding)
    for k in ("applicable_standards", "expected_completion", "ttl_days"):
        if k in data and k not in out:
            out[k] = data[k]

    # JSON 不支持 date / datetime, 转成 ISO 字符串
    import datetime as _dt
    def _normalize(v):
        if isinstance(v, (_dt.date, _dt.datetime)):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: _normalize(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_normalize(x) for x in v]
        return v
    return _normalize(out)


# ── sidecar 设施 (不可写文件指针注册) ────────────────────────────────


_EXTERNAL_POINTER_DIR = "data/services/registry/external_pointers"


def _encode_target_path_for_filename(target: str) -> str:
    """编码 target path 作 sidecar 文件名 (跨 OS 安全).

    'D:\\P4\\main\\PlannerTools\\IGameAgentClient\\figma_cli\\client.py'
      → 'D--P4-main-PlannerTools-IGameAgentClient-figma_cli-client.py'
    """
    s = target.replace("\\", "/").replace(":", "--")
    s = re.sub(r"[/\\]+", "-", s)
    # 防御 super-long: 限 240 字符 (Windows 文件名极限 255)
    if len(s) > 240:
        import hashlib
        suffix_hash = hashlib.sha1(target.encode("utf-8")).hexdigest()[:12]
        s = s[:200] + f"--{suffix_hash}"
    return s


def _create_external_pointer_sidecar(
    *, proj: Path, target_path: str,
    summary: str, why: str, tags: str, kind_inner: str,
    explicit_id: str | None, name_override: str | None,
    force: bool,
) -> Path:
    """立 sidecar JSON 给不可写文件 (二进制 / 外部项目). 不动 target 一字节.

    sidecar 物理位置: <proj>/data/services/registry/external_pointers/<encoded>.json
    """
    import time as _time
    sidecar_dir = proj / _EXTERNAL_POINTER_DIR
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    # 检查 target 是否真存在 (如果给的是绝对路径或相对项目根的路径)
    target_p = Path(target_path)
    if not target_p.is_absolute():
        target_abs = (proj / target_path).resolve()
    else:
        target_abs = target_p.resolve()
    target_existence = "file_exists" if target_abs.is_file() else (
        "dir_exists" if target_abs.is_dir() else "not_found"
    )
    is_external_project = False
    try:
        target_abs.relative_to(proj)
    except ValueError:
        is_external_project = True

    # is_binary 启发: 后缀判
    binary_exts = {".png", ".jpg", ".jpeg", ".pyc", ".so", ".dll",
                   ".xlsx", ".xlsm", ".pdf", ".zip", ".exe", ".pkl"}
    is_binary = target_abs.suffix.lower() in binary_exts

    # 编码 + 写 sidecar
    encoded = _encode_target_path_for_filename(target_path)
    sidecar_path = sidecar_dir / f"{encoded}.json"
    if sidecar_path.exists() and not force:
        click.echo(f"sidecar 已存在: {sidecar_path.relative_to(proj)} (用 --force 覆盖)", err=True)
        raise SystemExit(1)

    meta = current_session_meta()
    sidecar_data = {
        "material_id": explicit_id or name_override or encoded,
        "target_path": target_path,
        "target_path_resolved": str(target_abs).replace("\\", "/"),
        "target_existence_check": target_existence,
        "kind_inner": kind_inner,
        "is_binary": is_binary,
        "is_external_project": is_external_project,
        "omnimark": {
            "origin": "ai-ide",
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "type": "external_pointer",
            "summary": summary or f"指针 → {target_path}",
            "why": why or "目标不可写, 走 sidecar 注册",
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
        },
        "registered_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "trace_id": meta["trace_id"],
        "registered_via": "external_pointer_sidecar",
    }
    sidecar_path.write_text(
        json.dumps(sidecar_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    click.echo(f"OK 立 sidecar: {sidecar_path.relative_to(proj)}")
    if target_existence == "not_found":
        click.echo(f"  WARN target 不存在: {target_path} (注册仍生效, 但需校核)", err=True)
    return sidecar_path


# ── omni register ────────────────────────────────────────────────────


def _do_register_material(
    *, kind: str, content: str | None,
    external_target: str | None, target_summary: str | None, target_why: str | None,
    target_tags: str | None, target_kind_inner: str | None,
    name: str | None, explicit_id: str | None, package: str | None,
    force: bool, as_json: bool,
    strict: bool = False,
) -> None:
    """register material 核心实施. 被 cmd_register_material / 老式 cmd_register
    无子命令兼容路径 / cmd_register_batch 共用.

    strict=True: 模板 pattern 校验失败阻拦 (路径 / 命名 不规整 → exit 2).
    strict=False (默认): 校验失败 warn 不阻拦, 注册仍写入 (向后兼容老调用方).
    """
    type_name = _resolve_type_name(kind)
    if not meta_registry.has_type(type_name):
        click.echo(f"未知 kind: {kind} (registry type: {type_name})", err=True)
        click.echo(f"已注册类型: {meta_registry.list_types()}", err=True)
        raise SystemExit(1)

    proj = _project_root()

    # ── --external-target 分支: sidecar 注册 (不可写文件指针) ──
    if external_target:
        if content:
            click.echo("错误: --content 跟 --external-target 互斥", err=True)
            raise SystemExit(1)
        if kind != "external_pointer":
            click.echo("错误: --external-target 必须跟 --kind=external_pointer 配套", err=True)
            raise SystemExit(1)
        sidecar_path = _create_external_pointer_sidecar(
            proj=proj,
            target_path=external_target,
            summary=target_summary or "",
            why=target_why or "",
            tags=target_tags or "",
            kind_inner=target_kind_inner or "unknown",
            explicit_id=explicit_id,
            name_override=name,
            force=force,
        )
        content_path = sidecar_path  # 后续走 sidecar 文件作 content 注册
    else:
        if not content:
            click.echo("错误: --content 必须填 (除非用 --external-target)", err=True)
            raise SystemExit(1)
        content_path = Path(content).resolve()

    # PHASE3 第二段 · 模板 pattern 校验 (路径 + 命名)
    template = _load_kind_template(kind, proj)
    validation_warnings: list[str] = []
    if template:
        loc_pattern = (template.get("instance_location") or {}).get("pattern", "")
        name_pattern = (template.get("instance_naming") or {}).get("pattern", "")
        path_ok, path_msg = _validate_path_against_pattern(content_path, loc_pattern, proj)
        if not path_ok:
            validation_warnings.append(f"[路径] {path_msg}")
        elif path_msg:
            validation_warnings.append(f"[路径 OK] {path_msg}")
        name_ok, name_msg = _validate_naming_against_pattern(content_path, name_pattern)
        if not name_ok:
            validation_warnings.append(f"[命名] {name_msg}")
        elif name_msg:
            validation_warnings.append(f"[命名 OK] {name_msg}")
        # strict 模式遇硬违规阻拦
        hard_fail = any(w.startswith("[路径] ") or w.startswith("[命名] ") for w in validation_warnings)
        if strict and hard_fail:
            click.echo("严格模式拒绝注册 (--strict 启用):", err=True)
            for w in validation_warnings:
                click.echo(f"  {w}", err=True)
            raise SystemExit(2)
    elif strict:
        click.echo(f"严格模式拒绝: kind={kind} 无模板 templates/{kind}/注册件.yaml, 无法校验", err=True)
        raise SystemExit(2)

    # 派生 name
    if name is None:
        if content_path.is_dir():
            name = content_path.name
        else:
            name = content_path.stem

    # 派生 package (从文件路径)
    if package is None:
        try:
            rel = content_path.relative_to(proj)
            parts = rel.parts[:-1] if rel.suffix else rel.parts
            package = ".".join(parts)
        except ValueError:
            package = ""

    # entity_id: 走显式 (--id) 或自动派生
    if explicit_id:
        # 用户给定长语义化 id (不卡格式), 但仍冠 type 前缀让查询/路径稳
        entity_id = explicit_id if ":" in explicit_id else f"{type_name}:{explicit_id}"
    else:
        entity_id = f"{type_name}:{package}.{name}".rstrip(".")
    reg = get_registry()
    if reg.exists(entity_id) and not force:
        click.echo(f"已注册: {entity_id} (用 --force 覆盖)", err=True)
        raise SystemExit(1)

    # OmniMark 头 + 当前 session 身份
    header = _parse_omnimark_header(content_path) if content_path.is_file() else {}
    plan_binding = None
    if content_path.is_dir():
        # 目录类: 找 plan.md / DESIGN.md / __init__.py 头
        for cand in ("plan.md", "DESIGN.md", "__init__.py"):
            f = content_path / cand
            if f.is_file():
                header = _parse_omnimark_header(f)
                # plan 类型: 抓 binding 块写入 attrs (跟 G2 + plan 规范 v1 联动)
                if kind == "plan" and cand == "plan.md":
                    plan_binding = _parse_plan_binding(f)
                break
    elif kind == "plan" and content_path.suffix == ".md":
        plan_binding = _parse_plan_binding(content_path)

    meta = current_session_meta()

    # 相对 source_file
    try:
        source_file = str(content_path.relative_to(proj)).replace("\\", "/")
    except ValueError:
        source_file = str(content_path)

    attrs = {
        "kind_omnicompany": kind,                  # 八种概念名 (跟 registry type 区分)
        "trace_id": meta["trace_id"],              # 注册时的 session
        "registered_via": "cli_explicit",          # 区别 AST scan 自动注册
        "omnimark_header": header,                 # 头字段全保留
        "is_directory": content_path.is_dir(),
    }
    if plan_binding:
        # plan 规范 v1 binding 块 (workspace / packages / targets / applicable_standards / expected_completion / ttl_days)
        attrs["plan_binding"] = plan_binding

    entry = InstanceEntry(
        entity_id=entity_id,
        type=type_name,
        name=name,
        package=package,
        source_file=source_file,
        attrs=attrs,
        deps=[],
    )

    reg.write(entry)

    # PHASE3 第二段 · 写入凭据 (lock 组前提)
    cred_path = _issue_write_credential(
        entity_id=entity_id, source_file=source_file,
        trace_id=meta["trace_id"], proj=proj,
    )

    if as_json:
        out = entry.to_dict()
        out["registry_path"] = str(reg._entity_path(entity_id))
        out["credential_path"] = str(cred_path.relative_to(proj)).replace("\\", "/")
        if validation_warnings:
            out["validation_warnings"] = validation_warnings
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        click.echo(f"OK 已注册 {entity_id}")
        click.echo(f"  source_file : {source_file}")
        click.echo(f"  package     : {package}")
        click.echo(f"  kind        : {kind} (registry type={type_name})")
        click.echo(f"  trace_id    : {meta['trace_id']}")
        click.echo(f"  registry    : {reg._entity_path(entity_id)}")
        click.echo(f"  credential  : {cred_path.relative_to(proj)}")
        if validation_warnings:
            for w in validation_warnings:
                if w.startswith("[路径] ") or w.startswith("[命名] "):
                    click.echo(click.style(f"  WARN {w} (用 --strict 阻拦)", fg="yellow"))
                else:
                    click.echo(click.style(f"  {w}", fg="bright_black"))


# ── omni register 命令组 (CLI-PHASE3 第一段重拆) ─────────────────────
#
# 顶层 group: register (兼容老式 register --kind --content 调用 = register material)
# 子命令:    register identity / register material / register batch
#
# 兼容策略 invoke_without_command=True: 老调用方 (register_dispatcher.py 等)
# 调 `omni register --kind=X --content=Y` 不指定子命令 → 自动当 register material 跑.

@click.group("register", invoke_without_command=True)
@click.option("--kind", default=None,
              help="(老式调用 / register material 用) 实体 kind: material / worker / team / agent / hook / tool / data / plan / template / external_pointer")
@click.option("--content", default=None,
              type=click.Path(exists=True, dir_okay=True, file_okay=True),
              help="(老式调用 / register material 用) 内容路径")
@click.option("--external-target", default=None, help="(老式) sidecar 指针注册物理路径")
@click.option("--target-summary", default=None)
@click.option("--target-why", default=None)
@click.option("--target-tags", default=None)
@click.option("--target-kind-inner", default=None)
@click.option("--name", default=None)
@click.option("--id", "explicit_id", default=None)
@click.option("--package", default=None)
@click.option("--force", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def cmd_register(
    ctx, kind, content, external_target, target_summary, target_why,
    target_tags, target_kind_inner, name, explicit_id, package,
    force, as_json,
):
    """注册命令组: identity (身份) / material (材料) / batch (批量).

    老式调用 `omni register --kind=X --content=Y` 等价 `omni register material --kind=X --content=Y`,
    给已有调用方 (例 services/_authoring/mass_materialization/workers/register_dispatcher.py)
    平滑迁移用.
    """
    if ctx.invoked_subcommand is not None:
        return
    # 没指定子命令 = 老式调用兼容
    if not kind:
        click.echo(ctx.get_help())
        return
    _do_register_material(
        kind=kind, content=content,
        external_target=external_target, target_summary=target_summary,
        target_why=target_why, target_tags=target_tags,
        target_kind_inner=target_kind_inner, name=name,
        explicit_id=explicit_id, package=package, force=force, as_json=as_json,
    )


@cmd_register.command("material")
@click.option("--kind", required=True,
              help="实体 kind: material / worker / team / agent / hook / tool / data / plan / template / external_pointer")
@click.option("--content", required=False,
              type=click.Path(exists=True, dir_okay=True, file_okay=True),
              help="要注册的内容路径 (文件或目录, 例: 沙盒草稿). "
                   "跟 --external-target 互斥: 后者立 sidecar 走指针注册不要 --content.")
@click.option("--external-target", default=None,
              help="不可写文件的物理路径 (二进制 / 外部项目). 立 sidecar JSON 注册指针, "
                   "不动 target 一字节. 必须跟 --kind=external_pointer 配套用.")
@click.option("--target-summary", default=None,
              help="(仅 --external-target) sidecar 的 summary 字段")
@click.option("--target-why", default=None,
              help="(仅 --external-target) sidecar 的 why 字段")
@click.option("--target-tags", default=None,
              help="(仅 --external-target) sidecar 的 tags 字段, 逗号分隔")
@click.option("--target-kind-inner", default=None,
              help="(仅 --external-target) target 在 omnicompany kind 里属于哪种")
@click.option("--name", default=None,
              help="实体名 (省略时从 OmniMark summary / 文件名 / target_path 推断)")
@click.option("--id", "explicit_id", default=None,
              help="显式 entity_id (跳过自动派生, 例 'core.protection.policy.config.json')")
@click.option("--package", default=None,
              help="package 点分路径 (省略时从文件路径推断)")
@click.option("--force", is_flag=True, help="覆盖已存在的 entity_id")
@click.option("--strict", is_flag=True,
              help="模板 pattern 校验失败 → 阻拦注册. 默认 warn 不阻拦兼容老调用.")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_register_material(
    kind, content, external_target, target_summary, target_why,
    target_tags, target_kind_inner, name, explicit_id, package,
    force, strict, as_json,
):
    """显式注册一份材料到中心 (10 种 kind 通用).

    流程:
      1. 验证 kind 在已注册 10 种类型里
      2. 加载 templates/<kind>/注册件.yaml 拿 instance_location/naming pattern
      3. 校验 content 路径 + 命名 (strict 阻拦 / 默认 warn)
      4. 抓 OmniMark 头 (origin/ts/type/summary/why/tags)
      5. 派生 entity_id = `<type>:<package>.<name>`
      6. 写 InstanceEntry + 发 credential 到 data/services/registry/credentials/
    """
    _do_register_material(
        kind=kind, content=content,
        external_target=external_target, target_summary=target_summary,
        target_why=target_why, target_tags=target_tags,
        target_kind_inner=target_kind_inner, name=name,
        explicit_id=explicit_id, package=package, force=force,
        strict=strict, as_json=as_json,
    )


@cmd_register.command("identity")
@click.option("--role", type=click.Choice(["ai-ide", "human", "test"]),
              default="ai-ide", help="身份角色 (默认 ai-ide)")
@click.option("--display-name", default=None,
              help="可读身份名 (例 '本地开发 IDE'), 留空走 trace_id")
@click.option("--token", default=None,
              help="显式 token (留空 = 用当前 trace_id 作 token)")
@click.option("--active-plan", default=None,
              help="当前进行的 plan (例 docs/plans/...)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_register_identity(role, display_name, token, active_plan, as_json):
    """注册当前 session 身份, 拿 identity_token (= trace_id 持久化).

    复用 services/_core/identity 的 record_active_session, 把当前 session
    元数据写入 data/cc_session_active.json. 后续 `omni who` / `omni whoami`
    能查回这份身份, 写文件时 OmniMark 头自动绑定.
    """
    from omnicompany.packages.services._core.identity import (
        record_active_session, current_session_meta,
    )

    meta = current_session_meta()
    chosen_token = token or meta["trace_id"]
    record_active_session(
        trace_id=chosen_token,
        source="cli_register_identity",
        active_plan=active_plan,
        extra={
            "role": role,
            "display_name": display_name or chosen_token,
        },
    )

    out = {
        "trace_id": chosen_token,
        "role": role,
        "display_name": display_name or chosen_token,
        "active_plan": active_plan,
    }
    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        click.echo(f"OK 注册身份 token={chosen_token}")
        click.echo(f"  role         : {role}")
        click.echo(f"  display_name : {display_name or chosen_token}")
        if active_plan:
            click.echo(f"  active_plan  : {active_plan}")
        click.echo("  → omni whoami / omni who 可查")


@cmd_register.command("batch")
@click.option("--manifest", required=True,
              type=click.Path(exists=True, dir_okay=False, file_okay=True),
              help="manifest.yaml 路径, 含 entries: [{kind, content, name?}, ...]")
@click.option("--continue-on-error", is_flag=True,
              help="单条 fail 不阻断, 继续后续 entry (默认 fail-fast)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出聚合报告")
def cmd_register_batch(manifest, continue_on_error, as_json):
    """读 manifest.yaml, 逐条调 register material 批量注册.

    manifest 格式:
        entries:
          - kind: plan
            content: docs/plans/...
          - kind: data
            content: data/...
            name: optional_explicit_name

    输出聚合: 成功数 / 失败数 / 失败列表.
    """
    import yaml as _yaml
    try:
        data = _yaml.safe_load(Path(manifest).read_text(encoding="utf-8"))
    except Exception as e:
        click.echo(f"错误: 读 manifest 失败 {e}", err=True)
        raise SystemExit(1)
    entries = (data or {}).get("entries", []) if isinstance(data, dict) else []
    if not isinstance(entries, list) or not entries:
        click.echo("错误: manifest entries 为空 (期望 list of {kind, content, ...})", err=True)
        raise SystemExit(1)

    ok = 0
    fail = 0
    fail_list: list[dict] = []
    for i, ent in enumerate(entries):
        if not isinstance(ent, dict):
            fail += 1
            fail_list.append({"index": i, "error": "entry 不是 dict"})
            if not continue_on_error:
                break
            continue
        try:
            _do_register_material(
                kind=ent.get("kind", ""),
                content=ent.get("content"),
                external_target=ent.get("external_target"),
                target_summary=ent.get("target_summary"),
                target_why=ent.get("target_why"),
                target_tags=ent.get("target_tags"),
                target_kind_inner=ent.get("target_kind_inner"),
                name=ent.get("name"),
                explicit_id=ent.get("id"),
                package=ent.get("package"),
                force=bool(ent.get("force", False)),
                as_json=False,
            )
            ok += 1
        except SystemExit as e:
            fail += 1
            fail_list.append({
                "index": i,
                "kind": ent.get("kind"),
                "content": ent.get("content"),
                "error": f"SystemExit({e.code})",
            })
            if not continue_on_error:
                break
        except Exception as e:
            fail += 1
            fail_list.append({
                "index": i,
                "kind": ent.get("kind"),
                "content": ent.get("content"),
                "error": f"{type(e).__name__}: {e}",
            })
            if not continue_on_error:
                break

    summary = {
        "total": len(entries),
        "ok": ok,
        "fail": fail,
        "fail_entries": fail_list,
    }
    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        click.echo("\n=== 批量注册收尾 ===")
        click.echo(f"  total: {summary['total']}")
        click.echo(f"  ok   : {ok}")
        click.echo(f"  fail : {fail}")
        for f in fail_list[:5]:
            click.echo(f"    - [{f['index']}] {f.get('kind')}/{f.get('content')}: {f['error']}")
        if len(fail_list) > 5:
            click.echo(f"    ... 共 {len(fail_list)} 条 fail (用 --json 看全)")
    if fail and not continue_on_error:
        raise SystemExit(2)


# ── omni lookup ──────────────────────────────────────────────────────


@click.command("lookup")
@click.option("--kind", default=None,
              help="按 kind 过滤 (material / worker / team / agent / hook / tool / data / plan)")
@click.option("--id", "id_filter", default=None,
              help="按 entity_id 精确查 (例: format:demogame.season_book)")
@click.option("--package", "pkg_filter", default=None, help="按 package 过滤")
@click.option("--trace-id", default=None,
              help="按 trace_id 过滤 (查某 session 注册的内容)")
@click.option("--source", type=click.Choice(["all", "explicit", "ast_scan"]),
              default="all", help="过滤来源 (explicit=显式注册 / ast_scan=自动扫描)")
@click.option("--limit", type=int, default=50, help="结果上限")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_lookup(
    kind: str | None, id_filter: str | None, pkg_filter: str | None,
    trace_id: str | None, source: str, limit: int, as_json: bool,
) -> None:
    """查询注册中心.

    跟 omni registry list 的区别:
    - omni registry list — 看 AST 扫描结果 (代码实体)
    - omni lookup        — 统一查询, 含显式注册 + AST 扫描两种来源, 支持 trace_id 过滤
    """
    reg = get_registry()
    type_name = _resolve_type_name(kind) if kind else None

    # 直接 entity_id 精确查
    if id_filter:
        entry = reg.read(id_filter)
        if entry is None:
            click.echo(f"未找到: {id_filter}", err=True)
            raise SystemExit(1)
        if as_json:
            click.echo(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
        else:
            click.echo(f"entity_id   : {entry.entity_id}")
            click.echo(f"type        : {entry.type}")
            click.echo(f"name        : {entry.name}")
            click.echo(f"package     : {entry.package}")
            click.echo(f"source_file : {entry.source_file}")
            for k, v in (entry.attrs or {}).items():
                click.echo(f"attrs.{k:20s}: {v}")
        return

    # 链式查询
    q = query(reg)
    if type_name:
        q = q.type(type_name)
    if pkg_filter:
        q = q.package(pkg_filter)
    result = q.execute()

    rows: list[InstanceEntry] = []
    for entry in result:
        if trace_id and (entry.attrs.get("trace_id") != trace_id):
            continue
        registered_via = entry.attrs.get("registered_via")
        if source == "explicit" and registered_via != "cli_explicit":
            continue
        if source == "ast_scan" and registered_via == "cli_explicit":
            continue
        rows.append(entry)
        if len(rows) >= limit:
            break

    if as_json:
        click.echo(json.dumps([e.to_dict() for e in rows], ensure_ascii=False, indent=2))
        return

    click.echo(f"找到 {len(rows)} 条 (limit={limit}, source={source}):")
    for e in rows:
        via = e.attrs.get("registered_via", "ast_scan")
        marker = "+" if via == "cli_explicit" else "*"
        click.echo(f"  {marker} [{e.type:12s}] {e.entity_id}")
        click.echo(f"      {e.source_file}")


# ── omni register-types ──────────────────────────────────────────────


@click.command("register-types")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_register_types(as_json: bool) -> None:
    """列已注册的 kind 类型 (验证 8 种全齐)."""
    types = meta_registry.all_types()
    if as_json:
        out = [
            {"name": t.name, "display": t.display_name, "data_dir": t.data_dir}
            for t in types
        ]
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    click.echo(f"已注册 {len(types)} 种 kind 类型:")
    for t in types:
        click.echo(f"  {t.name:14s} ({t.display_name})  → data/services/registry/{t.data_dir}/")
