# [OMNI] origin=claude-code domain=cli/commands ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="omni semantic 命令组 — 语义文件系统(所有产出皆 material)。classify/materialize/materialize-dir(产出即 material) + tags(读写语义字段) + index/search(语义检索投影层)。"
# [OMNI] why="给语义文件系统设施一个统一 CLI 入口, 让产出自动成 material、可按语义检索。"
# [OMNI] tags=cli,semantic-os,material,semantic-filesystem
# [OMNI] material_id="material:cli.commands.semantic_fs_group.py"
"""omni semantic — 语义文件系统 CLI。"""
from __future__ import annotations

import json

import click

from .._access import any_caller, external_or_controller


@click.group("semantic")
def cmd_semantic() -> None:
    """语义文件系统: 产出即 material + 受控分类 + 语义检索(做 Spotlight 不做 WinFS)。"""


@cmd_semantic.command("classify")
@any_caller
@click.argument("path")
@click.option("--model", default=None)
def cmd_classify(path: str, model: str | None) -> None:
    """只分类一个产出文件(不入册): 输出受控 semantic_tags + summary + 置信。"""
    from omnicompany.packages.services._core.semantic_fs.classify import classify_material
    click.echo(json.dumps(classify_material(path, model=model), ensure_ascii=False, indent=2))


@cmd_semantic.command("materialize")
@external_or_controller
@click.argument("path")
@click.option("--model", default=None)
@click.option("--force", is_flag=True, help="已注册也重跑(覆盖)")
@click.option("--no-review", is_flag=True, help="置信不足也不提交当前会话可读回的审阅材料")
def cmd_materialize(path: str, model: str | None, force: bool, no_review: bool) -> None:
    """落盘钩子: 把一个产出文件分类→入册→写回语义字段(产出即 material)。"""
    from omnicompany.packages.services._core.semantic_fs.classify import materialize
    click.echo(json.dumps(materialize(path, model=model, force=force, submit_review=not no_review),
                          ensure_ascii=False, indent=2))


@cmd_semantic.command("materialize-dir")
@external_or_controller
@click.argument("directory")
@click.option("--model", default=None)
@click.option("--limit", type=int, default=None)
@click.option("--review", "submit_review", is_flag=True, help="置信不足时提交合并审阅材料")
def cmd_materialize_dir(directory: str, model: str | None, limit: int | None, submit_review: bool) -> None:
    """扫一个产出目录, 把还没入册的文件批量 materialize。"""
    from omnicompany.packages.services._core.semantic_fs.classify import materialize_dir
    payload = materialize_dir(directory, model=model, limit=limit, submit_review=submit_review, echo=click.echo)
    click.echo(json.dumps({k: payload[k] for k in ("scanned", "materialized", "needs_review")
                           if k in payload}, ensure_ascii=False, indent=2))


@cmd_semantic.command("index")
@external_or_controller
@click.option("--rebuild", is_flag=True, help="清空重建")
@click.option("--limit", type=int, default=None, help="只索引前 N 个已注册 material")
@click.option("--model", default=None, help="嵌入模型(默认 gemini-embedding-001)")
def cmd_index(rebuild: bool, limit: int | None, model: str | None) -> None:
    """语义检索投影层: 对已注册 material 建 embedding + chunk 两级索引(只读真源, 可重建)。"""
    from omnicompany.packages.services._core.semantic_fs.index import build_index, _DEFAULT_EMBED_MODEL
    payload = build_index(model=model or _DEFAULT_EMBED_MODEL, limit=limit, rebuild=rebuild, echo=click.echo)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@cmd_semantic.command("search")
@any_caller
@click.argument("query")
@click.option("--top", "top_k", type=int, default=5)
@click.option("--tag", "tags", multiple=True, help="meta 硬过滤 semantic_tag(可多次, 取交集)")
@click.option("--model", default=None)
def cmd_search(query: str, top_k: int, tags: tuple[str, ...], model: str | None) -> None:
    """语义检索: 向量召回 + semantic_tags 硬过滤 + 重排, 返回最相关 material。"""
    from omnicompany.packages.services._core.semantic_fs.index import search, _DEFAULT_EMBED_MODEL
    rows = search(query, top_k=top_k, tags=list(tags) or None, model=model or _DEFAULT_EMBED_MODEL)
    click.echo(json.dumps(rows, ensure_ascii=False, indent=2))


@cmd_semantic.command("gap-map")
@external_or_controller
@click.option("--from", "from_json", default=None,
              help="一手源公式 JSON 文件: [{field, formula}]。不给则用内置样例(plan 里的代表性公式)。")
@click.option("--model", default="qwen3.6-plus")
def cmd_gap_map(from_json: str | None, model: str) -> None:
    """里程碑四: 从一手源公式产出 cross-table-lookup/formula-semantic material 并入册可检索(MATERIAL-GAP-MAP 意图)。"""
    import json as _j
    from omnicompany.packages.services._core.semantic_fs.gap_map import produce
    if from_json:
        formulas = _j.loads(open(from_json, encoding="utf-8").read())
    else:  # 内置代表性样例(MATERIAL-GAP-MAP plan 的公式形态)
        formulas = [
            {"field": "TavernUpHeroRarity", "formula": "=VLOOKUP(U34,Unit!A:X,5,FALSE)"},
            {"field": "GuaranteeId", "formula": "=VLOOKUP(B2,TavernGuarantee!A:D,3,FALSE)"},
            {"field": "TenTavernID", "formula": "=U33+1"},
            {"field": "UpRate", "formula": "=IF(PoolType=\"Up\",0.5,0.3)"},
        ]
    payload = produce(formulas, model=model, echo=click.echo)
    click.echo(_j.dumps({k: payload[k] for k in ("cross_table_deps", "formula_semantics", "produced", "rule")},
                        ensure_ascii=False, indent=2))


@cmd_semantic.command("sweep")
@external_or_controller
@click.option("--limit", type=int, default=40, help="单轮最多 materialize 多少新文件")
@click.option("--model", default=None)
@click.option("--review", "submit_review", is_flag=True, help="置信不足时提交合并审阅材料")
def cmd_sweep(limit: int, model: str | None, submit_review: bool) -> None:
    """每日自动纳管: 扫产出目录(docs/reports·data/reports·data/domains)把还没入册的产出文件自动变 material。"""
    from omnicompany.packages.services._core.semantic_fs.classify import sweep
    payload = sweep(limit=limit, model=model, submit_review=submit_review, echo=click.echo)
    click.echo(json.dumps({k: payload[k] for k in ("dirs", "new_files", "materialized", "capped")},
                          ensure_ascii=False, indent=2))


@cmd_semantic.command("tags")
@any_caller
@click.argument("entity_id")
@click.option("--set", "set_tags", default=None, help="设 semantic_tags(逗号分隔), 受控词表校验")
def cmd_tags(entity_id: str, set_tags: str | None) -> None:
    """读/写一个已注册 material 的语义字段。"""
    from omnicompany.packages.services._core.semantic_fs.schema import get_semantic, set_semantic
    if set_tags is not None:
        tags = [t.strip() for t in set_tags.replace("，", ",").split(",") if t.strip()]
        click.echo(json.dumps(set_semantic(entity_id, semantic_tags=tags), ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps(get_semantic(entity_id), ensure_ascii=False, indent=2))
