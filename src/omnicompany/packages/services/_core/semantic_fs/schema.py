# [OMNI] origin=claude-code domain=services/_core/semantic_fs ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="语义元数据 schema(语义文件系统里程碑一)。给 material 补三类可空语义字段(semantic_tags 受控词表/content_time+ingested_time 双时间/embedding_ref 嵌入指针), 挂 InstanceEntry.attrs 不加新顶层字段;受控词表值=domain./kind./stage./topic./type. 闭集(domain 从真域派生)。"
# [OMNI] why="Notion/Roam 死于'元数据维护成本压垮收益'+'AI自由打标出孤儿标签'。解法=受控词表+全 optional 渐进采用+缺失不报。借 Zep bi-temporal 双时间。"
# [OMNI] tags=semantic-os,material,metadata-schema,controlled-vocabulary
# [OMNI] material_id="material:core.semantic_fs.schema.py"
"""语义元数据 schema + 受控词表校验(里程碑一)。

三类语义字段(全部可空、渐进采用, 挂 InstanceEntry.attrs['semantic'] 子字典, 不加新顶层字段):
  - semantic_tags: list[str], 受控分类标签。值形如 '<ns>.<value>', ns ∈ TAG_NAMESPACES。
      kind.* 值闭集 {source,internal,sink};domain.* 值来自真实注册域;其余非空即可。
  - content_time / ingested_time: 双时间(内容时点 vs 入册时点, 借 Zep bi-temporal)。
  - embedding_ref: 嵌入指针(里程碑三填, 现留空)。

铁律: 全 optional;缺失**不报违规**(反 WinFS 鸡生蛋);非法值才报。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

SEMANTIC_FIELDS = ("semantic_tags", "content_time", "ingested_time", "embedding_ref")
TAG_NAMESPACES = ("domain", "kind", "stage", "topic", "type")
KIND_VALUES = ("source", "internal", "sink")


@lru_cache(maxsize=4)
def _known_domains(root_str: str) -> frozenset[str]:
    """从真实注册域派生闭集: packages/domains/* + data/domains/* 子目录名。"""
    base = Path(root_str)
    doms: set[str] = set()
    for rel in ("src/omnicompany/packages/domains", "data/domains"):
        d = base / rel
        if d.is_dir():
            for sub in d.iterdir():
                if sub.is_dir() and not sub.name.startswith((".", "_")):
                    doms.add(sub.name)
    # 框架自身常用域(taxonomy 里的元域)
    doms.update({"omnicompany", "governance", "guardian", "doctor", "registry",
                 "decisions", "format-material", "diagnosis"})
    return frozenset(doms)


def known_domains(root: Path | None = None) -> frozenset[str]:
    return _known_domains(str(root or omni_workspace_root()))


def validate_tags(tags: list[str], root: Path | None = None) -> tuple[list[str], list[str]]:
    """返回 (合法标签, 非法标签)。非法 = ns 不在 TAG_NAMESPACES, 或 kind/domain 值越界。"""
    doms = known_domains(root)
    ok: list[str] = []
    bad: list[str] = []
    for t in tags or []:
        t = str(t).strip()
        if "." not in t:
            bad.append(t)
            continue
        ns, val = t.split(".", 1)
        if ns not in TAG_NAMESPACES or not val:
            bad.append(t)
        elif ns == "kind" and val not in KIND_VALUES:
            bad.append(t)
        elif ns == "domain" and val not in doms:
            bad.append(t)
        else:
            ok.append(t)
    return ok, bad


# ── 读/写 material 的语义字段(挂 InstanceEntry.attrs['semantic']) ─────

def get_semantic(entity_id: str) -> dict[str, Any]:
    """读一个已注册 material 的语义字段(没有则空 dict)。"""
    from omnicompany.packages.services._core.registry import get_registry
    e = get_registry().read(entity_id)
    if e is None:
        return {}
    return dict((e.attrs or {}).get("semantic", {}))


def set_semantic(entity_id: str, *, semantic_tags: list[str] | None = None,
                 content_time: str | None = None, ingested_time: str | None = None,
                 embedding_ref: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """给已注册 material 写语义字段(挂 attrs['semantic'], 真源文件不动)。非法标签拒写。"""
    from omnicompany.packages.services._core.registry import get_registry
    reg = get_registry()
    e = reg.read(entity_id)
    if e is None:
        return {"ok": False, "error": f"未注册: {entity_id}"}
    sem = dict((e.attrs or {}).get("semantic", {}))
    if semantic_tags is not None:
        ok, bad = validate_tags(semantic_tags, root)
        if bad:
            return {"ok": False, "error": f"非法标签(不在受控词表): {bad}", "valid": ok}
        sem["semantic_tags"] = ok
    if content_time is not None:
        sem["content_time"] = content_time
    if ingested_time is not None:
        sem["ingested_time"] = ingested_time
    if embedding_ref is not None:
        sem["embedding_ref"] = embedding_ref
    e.attrs = dict(e.attrs or {})
    e.attrs["semantic"] = sem
    reg.write(e)
    return {"ok": True, "entity_id": entity_id, "semantic": sem}
