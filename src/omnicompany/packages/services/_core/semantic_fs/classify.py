# [OMNI] origin=claude-code domain=services/_core/semantic_fs ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="产出即 material(语义文件系统里程碑二)。分类器走统一 call_json(喂受控 type+tag 词表), 输出对齐受控集合的 semantic_tags+summary;materialize 落盘钩子=分类→omni register(复用 register_dispatcher 形状 subprocess)→set_semantic 写回, 把打标从人工负担变产出副产物。置信不足进 human-inbox。"
# [OMNI] why="Notion/Roam 死于人手打标。解法=产出即打标、AI 填值、受控词表兜边界、不达标进人审, 绝不自由发挥出孤儿标签。"
# [OMNI] tags=semantic-os,material,auto-classify,auto-register
# [OMNI] material_id="material:core.semantic_fs.classify.py"
"""产出分类 + 自动入册(里程碑二)。

materialize(path): 落盘钩子 —— 分类(LLM 对齐受控词表)→ omni register(subprocess 复用现成)
→ set_semantic 写回 attrs。置信不足/标签越界 → human-inbox, 不静默乱写。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from . import schema as SC

# 产出粗粒度 registry kind(register CLI 接受的); 细粒度走 semantic_tags 的 type.*
_REGISTER_KINDS = ("data", "plan", "template", "material")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _vocab_prompt(root: Path) -> str:
    doms = sorted(SC.known_domains(root))
    return (
        "受控词表(semantic_tags 的值只能从这里取, 形如 '<ns>.<value>'):\n"
        f"- domain.<x>  x ∈ {{{', '.join(doms)}}}\n"
        f"- kind.<x>    x ∈ {{source, internal, sink}}(source=外部一手输入 / internal=中间产物 / sink=终态交付)\n"
        "- stage.<x>   x 自由(如 learn/produce/review/design)\n"
        "- type.<x>    x 自由(如 report/dataset/doc/spec/config)\n"
        "- topic.<x>   x 自由(主题词)\n"
        f"register_kind 只能是: {', '.join(_REGISTER_KINDS)}(产出大多是 data;计划是 plan;模板是 template)。"
    )


_SYS = """你是 omnicompany 的产出分类器。给你一个产出文件的路径与开头内容, 把它归类成 material:
- register_kind: 见受控词表(data/plan/template/material)。
- semantic_tags: 3-6 个, 全部来自受控词表, 必含一个 domain.* 和一个 kind.*。
- summary: 一句话(中文, ≤40字)它是什么。
- confidence: high / low(拿不准归 low)。
只输出 JSON, 标签绝不自由发挥(只能用受控词表里的值)。"""

_SCHEMA = {
    "type": "object", "required": ["register_kind", "semantic_tags", "summary", "confidence"],
    "properties": {
        "register_kind": {"type": "string", "enum": list(_REGISTER_KINDS)},
        "semantic_tags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "low"]},
    },
}


def classify_material(path: str | Path, *, model: str | None = None,
                      root: Path | None = None) -> dict[str, Any]:
    """LLM 分类一个产出文件 → {register_kind, semantic_tags(已校验), summary, confidence, invalid_tags}。"""
    from omnicompany.runtime.llm.structured import call_json
    base = root or omni_workspace_root()
    p = Path(path)
    try:
        head = p.read_text(encoding="utf-8", errors="replace")[:1500] if p.is_file() else ""
    except OSError:
        head = ""
    try:
        rel = str(p.relative_to(base)).replace("\\", "/")
    except ValueError:
        rel = str(p)
    user = f"{_vocab_prompt(base)}\n\n文件路径: {rel}\n开头内容:\n{head}"
    res = call_json(system=_SYS, user=user, schema=_SCHEMA, model=model or "qwen3.6-plus",
                    caller="semantic_fs.classify", max_tokens=800, max_corrections=2)
    tags = (res or {}).get("semantic_tags", []) or []
    ok, bad = SC.validate_tags(tags, base)
    return {"register_kind": (res or {}).get("register_kind", "data"),
            "semantic_tags": ok, "invalid_tags": bad,
            "summary": (res or {}).get("summary", ""),
            "confidence": (res or {}).get("confidence", "low"), "path": rel}


# register_kind(omnicompany 概念名)→ registry 内部 type(跟 cli/registration._KIND_ALIAS 一致)
_KIND_TO_TYPE = {"material": "format", "data": "data", "plan": "plan", "template": "template"}


def _register_inprocess(p: Path, kind: str, base: Path) -> str:
    """进程内注册一个文件到 InstanceRegistry, 返回 entity_id。

    不走 subprocess(本机 EDR 间歇封脚本宿主派生子进程, 见 windows_machine_security_constraints);
    直接复用 registry 写入, 字段对齐 cli/registration._do_register_material。
    """
    from omnicompany.packages.services._core.registry import get_registry, InstanceEntry
    type_name = _KIND_TO_TYPE.get(kind, "data")
    try:
        rel = p.relative_to(base)
        package = ".".join(rel.parts[:-1])
    except ValueError:
        package = ""
    name = p.stem
    entity_id = f"{type_name}:{package}.{name}".rstrip(".")
    reg = get_registry()
    existing = reg.read(entity_id)
    attrs = dict(existing.attrs) if existing else {}
    attrs.update({"kind_omnicompany": kind, "registered_via": "semantic_fs.materialize",
                  "is_directory": False})
    source_file = str(p.relative_to(base)).replace("\\", "/") if base in p.parents else str(p)
    reg.write(InstanceEntry(entity_id=entity_id, type=type_name, name=name, package=package,
                            source_file=source_file, attrs=attrs, deps=(existing.deps if existing else [])))
    return entity_id


def materialize(path: str | Path, *, model: str | None = None, root: Path | None = None,
                push_inbox: bool = True, force: bool = False) -> dict[str, Any]:
    """落盘钩子: 分类 → 进程内注册 → set_semantic 写回。

    置信不足 / 有越界标签 → 仍登记但进 human-inbox 待人核;不静默写脏标签。
    """
    base = root or omni_workspace_root()
    p = Path(path).resolve()
    if not p.is_file():
        return {"ok": False, "error": f"不是文件: {p}"}
    c = classify_material(p, model=model, root=base)
    try:
        entity_id = _register_inprocess(p, c["register_kind"], base)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"注册失败: {e}", "classify": c}
    if not entity_id:
        return {"ok": False, "error": "注册未拿到 entity_id", "classify": c}
    # set_semantic 写回(双时间: 内容时点取文件 mtime 日, 入册时点今天)
    import time as _t
    mday = _t.strftime("%Y-%m-%d", _t.localtime(p.stat().st_mtime))
    sem = SC.set_semantic(entity_id, semantic_tags=c["semantic_tags"],
                          content_time=mday, ingested_time=_today(), root=base)
    needs_review = c["confidence"] == "low" or bool(c["invalid_tags"])
    if push_inbox and needs_review:
        from omnicompany.runtime.buses import HumanBus, HumanKind
        HumanBus().ask(
            question=(f"产出已自动入册但置信不足/有越界标签, 请核分类: {c['path']}\n"
                      f"  kind={c['register_kind']} tags={c['semantic_tags']} "
                      f"越界={c['invalid_tags']} 置信={c['confidence']}\n  摘要: {c['summary']}"),
            kind=HumanKind.HUMAN_BLOCKING,
            context={"facility": "semantic_fs.materialize", "entity_id": entity_id, "classify": c},
            source="semantic_fs.materialize")
    return {"ok": True, "entity_id": entity_id, "register_kind": c["register_kind"],
            "semantic_tags": c["semantic_tags"], "summary": c["summary"],
            "confidence": c["confidence"], "invalid_tags": c["invalid_tags"],
            "needs_review": needs_review, "semantic_written": sem.get("ok", False)}


_SWEEP_SKIP = {"_archive", "_graveyard", "__pycache__", ".git", "venv", ".venv",
               "node_modules", "dist", "build",
               # 运行态/缓存不是"产出内容", 不入 material
               "cache", "scratch", "runtime", "_runtime", "snapshots", "figma_snapshots",
               "logs", "tmp", "temp", "credentials", "_workspaces"}
_PRODUCT_EXTS = (".md", ".csv")  # 自动 sweep 只收清晰的"产出内容"(报告/数据集); 其余按需手动 materialize
# 默认 sweep 的产出目录(管线产物落这里就会被每日纳入 material)。可在 data/semantic_fs/sweep_dirs.json 扩。
_DEFAULT_SWEEP_DIRS = ["docs/reports", "data/reports", "data/domains"]


def _sweep_dirs(root: Path) -> list[str]:
    cfg = root / "data" / "semantic_fs" / "sweep_dirs.json"
    if cfg.is_file():
        try:
            import json as _j
            return _j.loads(cfg.read_text(encoding="utf-8")).get("dirs", _DEFAULT_SWEEP_DIRS)
        except Exception:  # noqa: BLE001
            pass
    cfg.parent.mkdir(parents=True, exist_ok=True)
    import json as _j
    cfg.write_text(_j.dumps({"dirs": _DEFAULT_SWEEP_DIRS,
                             "_note": "管线产出落这些目录会被 gov-materialize-sweep 每日自动纳入 material;加目录即扩覆盖"},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return _DEFAULT_SWEEP_DIRS


def _registered_source_files(root: Path) -> set[str]:
    from omnicompany.packages.services._core.registry import get_registry
    out = set()
    for e in get_registry().list_all():
        if e.source_file:
            out.add(Path(e.source_file).as_posix())
    return out


def sweep(*, dirs: list[str] | None = None, model: str | None = None, limit: int = 40,
          push_inbox: bool = False, root: Path | None = None, echo: Any = None) -> dict[str, Any]:
    """每日自动纳管: 扫产出目录, 把**还没入册**的产出文件(.md/.csv)materialize 成 material。

    跳运行态/缓存目录;只处理新文件(已注册的跳过, 稳态便宜);超 limit 下轮再扫(透明记数)。
    这就是"管线产出 / 我写的文件自动变 material"的自动机制(每日 cron 触发)。
    """
    base = root or omni_workspace_root()
    dirs = dirs or _sweep_dirs(base)
    registered = _registered_source_files(base)
    new_files: list[Path] = []
    for d in dirs:
        root_d = base / d
        if not root_d.is_dir():
            continue
        for p in root_d.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in _PRODUCT_EXTS:
                continue
            if any(part in _SWEEP_SKIP for part in p.parts):
                continue
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                continue
            if rel in registered:
                continue  # 已是 material, 跳过(稳态只处理新增)
            new_files.append(p)
    total_new = len(new_files)
    capped = total_new > limit
    todo = new_files[:limit]
    if echo:
        echo(f"[materialize-sweep] 产出目录 {dirs} | 新文件 {total_new}"
             + (f" → 本轮 {limit}(其余下轮)" if capped else "") )
    done = 0
    for p in todo:
        r = materialize(p, model=model, root=base, push_inbox=push_inbox)
        if r.get("ok"):
            done += 1
        if echo:
            echo(f"  {'OK' if r.get('ok') else 'ERR'} {p.relative_to(base).as_posix()}: "
                 f"{r.get('semantic_tags', r.get('error'))}")
    return {"ok": True, "dirs": dirs, "new_files": total_new, "materialized": done, "capped": capped}


def materialize_dir(directory: str | Path, *, model: str | None = None, limit: int | None = None,
                    root: Path | None = None, push_inbox: bool = False,
                    echo: Any = None) -> dict[str, Any]:
    """扫一个产出目录, 把还没入册的文件逐个 materialize(产出即 material 的批量/补扫形态)。"""
    base = root or omni_workspace_root()
    d = Path(directory)
    if not d.is_dir():
        return {"ok": False, "error": f"不是目录: {d}"}
    files = [p for p in d.rglob("*")
             if p.is_file() and p.suffix.lower() in (".md", ".json", ".yaml", ".yml", ".csv", ".txt")
             and not any(part in _SWEEP_SKIP for part in p.parts)]
    if limit:
        files = files[:limit]
    results = []
    for p in files:
        res = materialize(p, model=model, root=base, push_inbox=push_inbox)
        results.append(res)
        if echo:
            echo(f"  {'OK' if res.get('ok') else 'ERR'} {p.name}: "
                 f"{res.get('semantic_tags', res.get('error'))}")
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "scanned": len(files), "materialized": ok,
            "needs_review": sum(1 for r in results if r.get("needs_review")), "results": results}
