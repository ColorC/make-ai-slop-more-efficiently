# [OMNI] origin=codex domain=dashboard/boss_sight ts=2026-06-13T07:10:00+08:00 type=infra status=active
# [OMNI] material_id="material:dashboard.boss_sight.reviewstage.material_type_registry.py"
"""Reviewstage material kind/tier resolution backed by Format tags."""
from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

from omnicompany.protocol.format import FormatRegistry

_DEFAULT_REGISTRY: FormatRegistry | None = None
_DEFAULT_REGISTRY_LOCK = threading.Lock()

# 域格式源登记点(决策树=具象管线的接入口): 新域接入 = 在域包 formats 模块里提供
# "register 函数"(往注册表登记 review.stage.* 层级 Format)与可选 STAGE_RULING_MAP
# (层级名 → 适用裁决 DEC id 列表), 然后在这里登记一行 "模块路径:函数名"。
# 模块缺失/导入失败一律吞掉不阻断审阅台启动(允许先登记后实现)。
DOMAIN_FORMAT_SOURCES: dict[str, str] = {
    "frontend_design": "omnicompany.packages.domains.frontend_design.formats:register_formats",
    "narrative": "omnicompany.packages.domains.narrative.formats:register_review_stage_formats",
    "bilibili_publish": "omnicompany.packages.domains.bilibili_publish.review_stage_formats:register_review_stage_formats",
    "voxelcraft": "omnicompany.packages.domains.voxelcraft.review_stage_formats:register_review_stage_formats",
}


def _domain_format_module(domain: str):
    src = DOMAIN_FORMAT_SOURCES.get(domain)
    if not src:
        return None
    import importlib
    try:
        return importlib.import_module(src.split(":", 1)[0])
    except Exception:  # noqa: BLE001
        return None


def domain_ruling_map(domain: str) -> dict[str, list[str]]:
    """该域的 层级名 → 适用已拍板裁决 DEC id 列表(域作者知识, 住在域包 formats 模块)。"""
    mod = _domain_format_module(domain)
    m = getattr(mod, "STAGE_RULING_MAP", None) if mod else None
    return m if isinstance(m, dict) else {}


def default_review_format_registry() -> FormatRegistry:
    """进程级共享 Format 注册表 — 审阅台生产路径用它解析 review.kind.* 扩展。

    扩展一种新审阅材料类型 = 往这个注册表 register 一个带 `review.kind.<name>` tag 的
    Format, 生产 MaterialStore(经 get_store) 即可识别, 无需改 enum。内置 5 个 kind 由
    DEFAULT_REVIEW_KINDS 兜底, 与本注册表无关。lazy 构建 + 缓存, 避免 import 期循环。
    """
    global _DEFAULT_REGISTRY
    with _DEFAULT_REGISTRY_LOCK:
        if _DEFAULT_REGISTRY is None:
            from omnicompany.protocol.format import create_builtin_registry
            reg = create_builtin_registry()
            try:
                from omnicompany.packages.services._core.omnicompany.formats import (
                    register_formats as _register_company_formats,
                )
                _register_company_formats(reg)
            except Exception:  # noqa: BLE001
                pass  # 公司材料 Format 注册失败不应阻断审阅台启动
            # 域级产物层级(决策树=具象管线): 各接入域的层级 Format 登记进来,
            # domain_stages/... 读它们的 review.stage.* 标签。单域失败不阻断其余域与审阅台启动。
            for _domain, _src in DOMAIN_FORMAT_SOURCES.items():
                try:
                    import importlib
                    _mod_name, _fn_name = _src.split(":", 1)
                    getattr(importlib.import_module(_mod_name), _fn_name)(reg)
                except Exception:  # noqa: BLE001
                    pass
            _DEFAULT_REGISTRY = reg
        return _DEFAULT_REGISTRY

DEFAULT_REVIEW_KINDS: tuple[str, ...] = (
    "image",
    "markdown",
    "html",
    "key_question",
    "custom_web_template",
    "video",
    # WORK-REPORT-AND-REVIEW-TYPES: 五个典型审阅类型(2026-06-25)
    "plan",                    # 计划
    "static-report",           # 静态工作报告网页
    "demo",                    # demo 网页
    "aigc-image",              # AIGC 图片附件(由父报告承载审阅语境)
    "agent-workflow-report",   # Agent 标准工作流程工作报告
)

# 这些载体可以为报告保存附件，但不能独占普通审阅队列。
ATTACHMENT_ONLY_REVIEW_KINDS = frozenset({"image", "aigc-image"})

DEFAULT_REVIEW_TIERS: tuple[str, ...] = (
    "mandatory",
    "important",
    "processual",
    "ignored",
)

REVIEW_KIND_TAG_PREFIX = "review.kind."
REVIEW_TIER_TAG_PREFIX = "review.tier."

# 域级产物层级(决策树=具象管线)标签族。层级词汇的家在域包 Format, 这里只做只读投影。
# review.stage.<域>.<序>.<层级名>       — 某域的一个有序产物层级
# review.stage-member.<域>.<project>    — 某项目属于某域
# review.stage-expected-kind.<kind>     — 该层的形态期望 kind(可多值)
# review.stage-gate.<enforcer>          — 进入下一层的门禁执法器标识(单值)
REVIEW_STAGE_TAG_PREFIX = "review.stage."
REVIEW_STAGE_MEMBER_TAG_PREFIX = "review.stage-member."
REVIEW_STAGE_EXPECTED_KIND_TAG_PREFIX = "review.stage-expected-kind."
REVIEW_STAGE_GATE_TAG_PREFIX = "review.stage-gate."
REVIEW_SUBJECT_TYPE_TAG_PREFIX = "review.subject-type."


def _value(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else value
    return str(raw or "").strip()


def _tag_values(registry: FormatRegistry | None, prefix: str) -> set[str]:
    if registry is None:
        return set()
    values: set[str] = set()
    for fmt in registry.all_formats():
        for tag in fmt.tags:
            if tag.startswith(prefix):
                value = tag[len(prefix):].strip()
                if value:
                    values.add(value)
    return values


def registered_review_kinds(registry: FormatRegistry | None = None) -> set[str]:
    """Known review material kinds.

    Defaults preserve the existing five kinds. Extensions are discovered from
    registered Format tags such as `review.kind.novel_chapter`.
    """
    return set(DEFAULT_REVIEW_KINDS) | _tag_values(registry, REVIEW_KIND_TAG_PREFIX)


def registered_review_tiers(registry: FormatRegistry | None = None) -> set[str]:
    return set(DEFAULT_REVIEW_TIERS) | _tag_values(registry, REVIEW_TIER_TAG_PREFIX)


def normalize_review_kind(value: Any, registry: FormatRegistry | None = None) -> str:
    kind = _value(value)
    if kind in registered_review_kinds(registry):
        return kind
    raise ValueError(
        f"review material kind {kind!r} is not registered. "
        f"Register a Format with tag {REVIEW_KIND_TAG_PREFIX}{kind} first."
    )


def normalize_review_tier(value: Any, registry: FormatRegistry | None = None) -> str:
    tier = _value(value)
    if tier in registered_review_tiers(registry):
        return tier
    raise ValueError(
        f"review material tier {tier!r} is not registered. "
        f"Register a Format with tag {REVIEW_TIER_TAG_PREFIX}{tier} first."
    )


UNFILED_PROJECT = "unfiled"


def known_review_projects() -> set[str]:
    """项目名录唯一真源=决策库: 活跃记录的 project 值集合 ∪ {"unfiled"}。

    禁止本地硬编码项目列表 — 新项目先在决策库立项(`omni decisions record -p <project>`),
    审阅台材料的 project 才能跟着解锁。"unfiled"=未分组, 永远合法。
    """
    from omnicompany.packages.domains.decisions import library

    projects = {(r.get("project") or "").strip() for r in library.active_records()}
    return {p for p in projects if p} | {UNFILED_PROJECT}


def normalize_review_project(value: Any) -> str:
    """project 白名单校验。空 → ValueError; 不在 known_review_projects() → ValueError。"""
    project = _value(value)
    if not project:
        raise ValueError("project is required and cannot be empty")
    known = known_review_projects()
    if project not in known:
        raise ValueError(
            f"project {project!r} 不在项目名录里。项目名录真源=决策库, "
            f"先 `omni decisions record -p {project}` 立项, 或改用已有项目名 "
            f"(如 {', '.join(sorted(known)[:8])})。"
        )
    return project


def review_kind_format_preconditions(value: Any, registry: FormatRegistry | None = None) -> list[str]:
    """该 review kind 对应 Format 声明的语义前置条件(= 该类材料的审阅格式要求)。

    设施化双保证的"设施"半边: 提交某 kind 材料时, CLI 读这里把要求作为友情提示回给 agent。
    无注册 Format 或无前置条件时返回空列表。
    """
    if registry is None:
        return []
    tag = f"{REVIEW_KIND_TAG_PREFIX}{_value(value)}"
    out: list[str] = []
    for fmt in registry.all_formats():
        if tag in fmt.tags:
            out.extend(fmt.semantic_preconditions)
    return out


def domain_stages(domain: str, registry: FormatRegistry | None = None) -> list[dict[str, Any]]:
    """某域的有序产物层级清单(决策树=具象管线的步骤定义)。

    从注册的 Format 标签 review.stage.<域>.<序>.<层级名> 解析: 每层带 name/order/desc
    (= Format.description)/expected_kinds(review.stage-expected-kind.*)/gate 执法器
    (review.stage-gate.*)。按 order 升序返回; 每层附 next=下一层名(末层为 None)。
    未注册该域或 registry 为空时返回空列表。
    """
    if registry is None:
        return []
    prefix = f"{REVIEW_STAGE_TAG_PREFIX}{domain}."
    stages: list[dict[str, Any]] = []
    for fmt in registry.all_formats():
        stage_tag = next((t for t in fmt.tags if t.startswith(prefix)), None)
        if stage_tag is None:
            continue
        rest = stage_tag[len(prefix):]  # "<序>.<层级名>"
        seq_str, _, name = rest.partition(".")
        try:
            order = int(seq_str)
        except ValueError:
            continue
        if not name:
            continue
        expected_kinds = sorted(
            t[len(REVIEW_STAGE_EXPECTED_KIND_TAG_PREFIX):]
            for t in fmt.tags
            if t.startswith(REVIEW_STAGE_EXPECTED_KIND_TAG_PREFIX)
        )
        gate = next(
            (t[len(REVIEW_STAGE_GATE_TAG_PREFIX):]
             for t in fmt.tags if t.startswith(REVIEW_STAGE_GATE_TAG_PREFIX)),
            "",
        )
        stages.append({
            "name": name,
            "order": order,
            "desc": fmt.description,
            "expected_kinds": expected_kinds,
            "gate": {"enforcer": gate},
        })
    stages.sort(key=lambda s: s["order"])
    for i, s in enumerate(stages):
        s["next"] = stages[i + 1]["name"] if i + 1 < len(stages) else None
    return stages


def registered_domains(registry: FormatRegistry | None = None) -> set[str]:
    """有产物层级登记的域名集合(从 review.stage.<域>.* 标签抽出的 <域> 段)。"""
    if registry is None:
        return set()
    out: set[str] = set()
    for fmt in registry.all_formats():
        for tag in fmt.tags:
            if tag.startswith(REVIEW_STAGE_TAG_PREFIX) and not tag.startswith(REVIEW_STAGE_MEMBER_TAG_PREFIX):
                rest = tag[len(REVIEW_STAGE_TAG_PREFIX):]
                domain = rest.split(".", 1)[0]
                if domain:
                    out.add(domain)
    return out


def project_domains(project: str, registry: FormatRegistry | None = None) -> set[str]:
    """项目所属的注册域集合(读 review.stage-member.<域>.<project> 标签)。

    归属维度将来迁进决策库项目名录(唯一真源); 现从域包 Format 标签只读投影。
    """
    if registry is None or not project:
        return set()
    out: set[str] = set()
    for fmt in registry.all_formats():
        for tag in fmt.tags:
            if not tag.startswith(REVIEW_STAGE_MEMBER_TAG_PREFIX):
                continue
            rest = tag[len(REVIEW_STAGE_MEMBER_TAG_PREFIX):]  # "<域>.<project>"
            domain, _, member = rest.partition(".")
            if domain and member == project:
                out.add(domain)
    return out


def project_registered_tracks(project: str, registry: FormatRegistry | None = None) -> set[str]:
    """项目在册轨道集 = 所属各域的层级名并集。

    形如 "<project>/..." 的项目前缀轨道视为在册(域剖面草案: 项目专属层级带项目前缀显式登记),
    但那类是运行期动态判断(见 store 轨道软校验), 本函数只返回域层级并集。
    """
    tracks: set[str] = set()
    for domain in project_domains(project, registry):
        for stage in domain_stages(domain, registry):
            tracks.add(str(stage["name"]))
    return tracks


def project_subject_types(project: str, registry: FormatRegistry | None = None) -> set[str]:
    """项目内容资产的主体类型约束，例如视频发布项目的 ``episode``。

    主体类型仍由域成员 Format 登记，不在审阅台硬编码项目名。没有声明的项目保持
    兼容：Material 可以不带 subject/revision；声明后的项目在在册 track 上由 store
    强制要求完整主体身份。
    """
    if registry is None or not project:
        return set()
    domains = project_domains(project, registry)
    out: set[str] = set()
    for fmt in registry.all_formats():
        if not any(f"{REVIEW_STAGE_MEMBER_TAG_PREFIX}{domain}.{project}" in fmt.tags for domain in domains):
            continue
        out.update(
            tag[len(REVIEW_SUBJECT_TYPE_TAG_PREFIX):].strip()
            for tag in fmt.tags
            if tag.startswith(REVIEW_SUBJECT_TYPE_TAG_PREFIX)
            and tag[len(REVIEW_SUBJECT_TYPE_TAG_PREFIX):].strip()
        )
    return out


def review_material_tags(kind: Any, tier: Any, extra: Iterable[str] | None = None) -> list[str]:
    tags = [
        f"{REVIEW_KIND_TAG_PREFIX}{_value(kind)}",
        f"{REVIEW_TIER_TAG_PREFIX}{_value(tier)}",
    ]
    for tag in extra or ():
        if tag not in tags:
            tags.append(tag)
    return tags


__all__ = [
    "ATTACHMENT_ONLY_REVIEW_KINDS",
    "DEFAULT_REVIEW_KINDS",
    "DEFAULT_REVIEW_TIERS",
    "REVIEW_KIND_TAG_PREFIX",
    "REVIEW_STAGE_TAG_PREFIX",
    "REVIEW_TIER_TAG_PREFIX",
    "UNFILED_PROJECT",
    "domain_stages",
    "known_review_projects",
    "normalize_review_kind",
    "normalize_review_project",
    "normalize_review_tier",
    "project_domains",
    "project_registered_tracks",
    "project_subject_types",
    "registered_domains",
    "registered_review_kinds",
    "registered_review_tiers",
    "review_kind_format_preconditions",
    "review_material_tags",
]
