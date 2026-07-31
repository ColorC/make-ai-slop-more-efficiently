# [OMNI] origin=claude-code domain=services/knowledge/schema.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.entry_schema.definition.py"
"""omnikb.schema — 知识条目的类型定义与 Markdown/YAML 解析。

════════════════════════════════════════════════════════════════════
⚠ DEPRECATION — K-type 概念已废弃（2026-04-18 对话结论）
════════════════════════════════════════════════════════════════════

**本文件中的 6 种 K-type（KFormatEntry / KRouterEntry / KArchitectureEntry /
KDecisionEntry / KExperimentEntry / KRepoArchitectEntry;KHypothesisEntry 已随
决策本体合并清单#1 拆除,猜想=统一决策库 belief）是伪分类**，把 Router/Format 的"本质"和"加载方式（代码/markdown/DB）"混为一谈。

实证（2026-04-18）：
- KFormat/KRouter/KDec/KRepo 各 0 实例，当初为之设计的"schema 专化"收益未兑现
- KArch/KExp 用得起来只因为它们其实是"文档"的不同自由变体
- 按 type 分支的真实代码仅 2-3 处（audit/graph/deep_read），其它皆为类型无关

**正确模型**（演化目标）：Router + Format 二元统一；代码/markdown/DB 是存储载体
而非类别；文档（未结构化语义综合体）是一等公民，可结构化为 Router 网络。

**本文件当前不再是新代码的参考**。对外：
- __init__.py 已将 K-type 类移出 __all__
- 外部**禁止**新增 `from omnicompany.packages.services._learning.knowledge import KXxxEntry`
- 内部（knowledge/ + hypothesis/ 包内）保留直至 V2 重构完成

详见 docs/plans/[2026-04-18]hypothesis-omnicompany-alignment/PLAN.md §V2。
════════════════════════════════════════════════════════════════════

OmniKB 共有 6 种条目类型, 共享同一份文件树存储和索引基础设施:

## 镜像型 (与六元原语一一对应)
  - KFormatEntry  (kformat) — Format 的知识镜像, 描述"一类数据是什么"
  - KRouterEntry  (krouter) — Router 的知识镜像, 描述"一类转换如何发生"

## 叙事型 (用户需求提出的 3 种新条目, 存 OmniCompany 自知 + 外部画像)
  - KArchitectureEntry  (karch) — 架构主题, 自由散文 + code_anchors
  - KDecisionEntry      (kdec)  — ADR (Architecture Decision Record)
  - KExperimentEntry    (kexp)  — 实验/尝试记录, 实验室笔记本风格
  - KRepoArchitectEntry (krepo) — 外部仓画像, 由 absorption/repo_introspect 写入

## 统一字段 (所有类型共享)
  - id / name / description / tags / source_path / maturity
  - maturity ∈ {draft, living, stable, deprecated}

## 类型特有字段
  见每个类的 docstring。

## 文件格式
  Markdown + YAML frontmatter:
  ```
  ---
  omnikb_type: karch
  id: "kb.arch.bus_unification"
  name: "..."
  ...其他字段
  ---

  # 正文

  Markdown 随意写, 可以有 mermaid / code block / 表格。
  ```
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    import yaml
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

OMNIKB_TYPES = ("kformat", "krouter", "karch", "kdec", "kexp", "krepo")
"""所有合法的 omnikb_type 值, 新增类型请加在此列表。"""

MATURITY_VALUES = ("draft", "living", "stable", "deprecated")
"""draft=刚建/未审核, living=管线写入中, stable=已定稿, deprecated=已废弃"""


# ═══════════════════════════════════════════════════════════
# 基类: 所有 entry 的共享字段
# ═══════════════════════════════════════════════════════════

class _BaseEntry(BaseModel):
    """所有 entry 类型的共享字段。

    子类必须设定 ``omnikb_type`` 为特定 Literal 值以区分。
    """

    omnikb_type: str
    """条目类型, 取值见 OMNIKB_TYPES"""

    id: str
    """全局唯一 id, 推荐格式: kb.<type>.<domain>.<name>"""

    name: str
    """人类可读名称"""

    description: str = ""
    """简短描述, 由 frontmatter.summary 或正文首段提取"""

    tags: list[str] = Field(default_factory=list)
    """语义标签, 显式声明 + 路径隐式标签合并"""

    maturity: Literal["draft", "living", "stable", "deprecated"] = "draft"

    source_path: str = ""
    """源 md 文件的绝对路径"""

    # 允许子类在未来添加额外字段而不破坏向前兼容
    model_config = ConfigDict(extra="allow")


# ═══════════════════════════════════════════════════════════
# 1. KFormatEntry — Format 的知识镜像
# ═══════════════════════════════════════════════════════════

class KFormatEntry(_BaseEntry):
    """描述一类数据实体"是什么", 比可执行 Format 多领域背景与历史上下文,
    但没有 JSON Schema 约束能力。

    典型用途: 描述一个业务概念 (如 "游戏关卡配置表"), 其约束、典型形态、常见误区,
    即使还没对应的可执行 Format 也可以先写出来作为设计草稿。
    """

    omnikb_type: Literal["kformat"] = "kformat"

    relates_to_formats: list[str] = Field(default_factory=list)
    """关联的可执行 Format id (在 FormatRegistry 中已注册的)"""

    relates_to_krouters: list[str] = Field(default_factory=list)
    """关联的 KRouter id (本 KB 内部引用)"""


# ═══════════════════════════════════════════════════════════
# 2. KRouterEntry — Router 的知识镜像
# ═══════════════════════════════════════════════════════════

class KRouterEntry(_BaseEntry):
    """描述一类转换过程"如何发生", 比可执行 Router 多决策依据与失败模式记录,
    但不包含实际代码实现。

    典型用途: 记录某个 Router 的设计决策 (为什么选 LLM 而非规则)、已知失败模式、
    参考实现。
    """

    omnikb_type: Literal["krouter"] = "krouter"

    kformat_in: str = ""
    """输入的 KFormat id (本 KB 内部引用)"""

    kformat_out: str = ""
    """输出的 KFormat id (本 KB 内部引用)"""

    format_in: str = ""
    """对应可执行 Router 的 FORMAT_IN (跨越到执行域)"""

    format_out: str = ""
    """对应可执行 Router 的 FORMAT_OUT (跨越到执行域)"""

    relates_to_routers: list[str] = Field(default_factory=list)
    """对应可执行 Router 的类名列表"""


# ═══════════════════════════════════════════════════════════
# 3. KArchitectureEntry — 架构主题
# ═══════════════════════════════════════════════════════════

class KArchitectureEntry(_BaseEntry):
    """一个架构主题的深度描述。

    可以描述 OmniCompany 自身的一部分架构 (scope="omnicompany"), 也可以描述
    外部 repo 的某块架构 (scope="external:<owner>/<name>")。

    典型用途:
      - kb.arch.bus_unification — Move 8 事件统一
      - kb.arch.agent_node_loop — AgentNodeLoop 的 4 层压缩
      - kb.arch.six_primitives — 六元原语定义
      - kb.arch.ext.codex.sandbox — codex 的跨平台 sandbox (外部)

    正文是自由 Markdown, 但 code_anchors 必须准确, 会被 KBAuditRouter 校验。
    """

    omnikb_type: Literal["karch"] = "karch"

    scope: str = "omnicompany"
    """omnicompany | external:<owner>/<name>"""

    code_anchors: list[str] = Field(default_factory=list)
    """指向相关代码的锚点。格式: "path/to/file.py" 或 "path/to/file.py:L10-L50"""

    related_decisions: list[str] = Field(default_factory=list)
    """关联的 KDecision id (kb.decision.*)"""

    related_experiments: list[str] = Field(default_factory=list)
    """关联的 KExperiment id (kb.experiment.*)"""

    related_karchs: list[str] = Field(default_factory=list)
    """交叉引用的其他 KArchitecture id (交叉网)"""


# ═══════════════════════════════════════════════════════════
# 4. KDecisionEntry — Architecture Decision Record
# ═══════════════════════════════════════════════════════════

class KDecisionEntry(_BaseEntry):
    """追踪一个关键决策的来龙去脉, 格式对齐业界 ADR 实践。

    典型用途:
      - kb.decision.move8_unified_events
      - kb.decision.guardian_session1_active_defense
      - kb.decision.agent_node_loop_as_router

    正文应包含: drivers (为什么) / options (考虑过什么) / decision (选了什么) /
    consequences (正负后果)。
    """

    omnikb_type: Literal["kdec"] = "kdec"

    date_decided: str = ""
    """ISO 日期 YYYY-MM-DD, 决策生效日"""

    status: Literal["proposed", "decided", "superseded", "rejected"] = "decided"

    supersedes: list[str] = Field(default_factory=list)
    """本决策取代了哪些旧决策 (kb.decision.* id)"""

    superseded_by: list[str] = Field(default_factory=list)
    """本决策被哪些新决策取代"""

    drivers: list[str] = Field(default_factory=list)
    """决策动机的一句话列表"""

    options_considered: list[str] = Field(default_factory=list)
    """考虑过的选项列表 (散文)"""

    decision: str = ""
    """最终决定的散文描述"""

    consequences_positive: list[str] = Field(default_factory=list)
    """积极后果"""

    consequences_negative: list[str] = Field(default_factory=list)
    """消极后果 / 已知局限"""

    related_karchs: list[str] = Field(default_factory=list)
    """关联的 KArchitecture id"""


# ═══════════════════════════════════════════════════════════
# 5. KExperimentEntry — 实验记录
# ═══════════════════════════════════════════════════════════

class KExperimentEntry(_BaseEntry):
    """追踪一次实验或尝试, 类似实验室笔记本。

    典型用途:
      - kb.experiment.20260408_absorption_stage_3d
      - kb.experiment.20260407_omnikb_retirement
      - kb.experiment.20260405_graveyard_reimplement_strategy

    和 KDecision 的区别: KDec 记录"决定做 X", KExp 记录"试了做 X 发现 Y"。
    一个实验可能触发多个决策 (related_decisions), 一个决策可能基于多次实验。
    """

    omnikb_type: Literal["kexp"] = "kexp"

    date_started: str = ""
    """ISO 日期"""

    date_concluded: str = ""
    """ISO 日期, 为空表示进行中"""

    hypothesis: str = ""
    """实验假设的散文陈述"""

    method_summary: str = ""
    """方法概述 (可指向 docs/plans/... 获取完整方法)"""

    samples_run: list[dict] = Field(default_factory=list)
    """跑过的样本列表, 每项自由 dict 结构 (含 repo/trace_id/outcome 等)"""

    findings_summary: list[str] = Field(default_factory=list)
    """关键发现的要点列表"""

    status: str = ""
    """自由字符串描述当前状态"""

    followups: list[str] = Field(default_factory=list)
    """后续任务列表 (可以是计划文档路径 / issue id / kb.experiment id)"""

    related_karchs: list[str] = Field(default_factory=list)
    related_decisions: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# 6. KRepoArchitectEntry — 外部仓画像
# ═══════════════════════════════════════════════════════════

class KRepoArchitectEntry(_BaseEntry):
    """对一个外部 GitHub 仓的架构画像。

    由 absorption / repo_introspect 子管线写入, 支持跨 absorption 累加
    (同一个 id 多次写入会走 merge, 不会丢历史)。

    典型用途:
      - kb.repo.openai__codex
      - kb.repo.google-gemini__gemini-cli
    """

    omnikb_type: Literal["krepo"] = "krepo"

    scope: str = ""
    """形如 "external:openai/codex" """

    last_surveyed: str = ""
    """ISO 日期, 最近一次 survey 的时间"""

    last_sha: str = ""
    """最近一次 survey 时的 git sha"""

    download_state: Literal["absent", "working", "archived", "deleted"] = "absent"
    """当前 workspace 状态"""

    capability_areas: list[dict] = Field(default_factory=list)
    """识别到的能力领域列表, 每项含 name/paths/evidence_files/omni_parallel"""

    prior_landmarks_tier_1: list[dict] = Field(default_factory=list)
    """历史 tier-1 landmark 记录 (用于去重)"""

    known_unread_areas: list[str] = Field(default_factory=list)
    """明确承认没读过的顶层目录列表"""

    related_experiments: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# 类型调度
# ═══════════════════════════════════════════════════════════

KnowledgeEntry = (
    KFormatEntry
    | KRouterEntry
    | KArchitectureEntry
    | KDecisionEntry
    | KExperimentEntry
    | KRepoArchitectEntry
)

_TYPE_TO_CLASS: dict[str, type[_BaseEntry]] = {
    "kformat": KFormatEntry,
    "krouter": KRouterEntry,
    "karch": KArchitectureEntry,
    "kdec": KDecisionEntry,
    "kexp": KExperimentEntry,
    "krepo": KRepoArchitectEntry,
}


def entry_class_for(omnikb_type: str) -> type[_BaseEntry] | None:
    """根据 omnikb_type 返回对应的 entry 类, 未知类型返回 None。"""
    return _TYPE_TO_CLASS.get(omnikb_type)


# ═══════════════════════════════════════════════════════════
# Frontmatter 解析
# ═══════════════════════════════════════════════════════════

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)

# 容忍 OmniMark 自动头 (`# [OMNI] origin=... ts=...`) 出现在 frontmatter 之前。
# OmniGuardian Session 1 起所有 guarded_write 都会贴这一行, 解析器必须能跳过。
_OMNIMARK_LINE_RE = re.compile(r"^#\s*\[OMNI\][^\n]*\n+")


def _extract_frontmatter(text: str) -> tuple[dict, str]:
    """提取 YAML frontmatter 和正文。

    会先剥掉可选的 OmniMark 自动头行, 再尝试匹配 ``---\n...\n---\n`` 块。

    Returns:
        (frontmatter_dict, body_text)
    """
    # 跳过 OmniMark 自动头
    stripped = _OMNIMARK_LINE_RE.sub("", text, count=1)

    m = _FRONTMATTER_RE.match(stripped)
    if not m:
        return {}, text  # 返回原始 text 让上层判定 None

    fm_raw = m.group(1)
    body = stripped[m.end():]

    if _HAS_YAML:
        try:
            fm = yaml.safe_load(fm_raw) or {}
        except Exception:
            fm = {}
    else:  # pragma: no cover
        # 极简 fallback, 只处理 key: value 单行, 用于不装 pyyaml 的环境
        fm = {}
        for line in fm_raw.splitlines():
            if ":" in line and not line.startswith(("-", " ")):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip('"\'')

    return fm, body


def _extract_summary(body: str, max_chars: int = 300) -> str:
    """从正文中提取首段作为摘要, 跳过标题行。"""
    lines = [ln for ln in body.strip().splitlines() if not ln.startswith("#")]
    paragraph = ""
    for line in lines:
        if line.strip():
            paragraph += line.strip() + " "
        elif paragraph:
            break
    return paragraph.strip()[:max_chars]


def _extract_implicit_tags(path: Path) -> list[str]:
    """从物理路径提取目录隐式标签。

    规则: 路径中 'knowledge' 节点后的子目录名都作为 tag, 加上 'knowledge' 的
    直接父级 (通常是 package 名) 作为 domain tag。
    """
    parts = path.parts
    try:
        idx = len(parts) - 1 - parts[::-1].index("knowledge")
    except ValueError:
        return []

    tags: list[str] = []

    if idx > 0:
        parent = parts[idx - 1]
        # 忽略通用路径组件
        if parent not in ("omnicompany", "packages", "src", "services", "domains", "vendors", "data"):
            tags.append(f"domain.{parent}")

    for sub_dir in parts[idx + 1:-1]:
        tags.append(sub_dir)

    return tags


def _ensure_list(val: Any) -> list:
    """把任意值规范为 list。None -> []; 标量 -> [val]; list -> 原样。"""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def parse_kb_document(path: Path) -> KnowledgeEntry | None:
    """解析一个 OmniKB Markdown 文档, 返回对应的知识条目。

    - 未含合法 omnikb_type 返回 None
    - 自动合并显式 tags 和路径隐式 tags
    - description 优先用 frontmatter.summary, 否则从正文首段提取
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fm, body = _extract_frontmatter(text)

    omnikb_type = fm.get("omnikb_type", "")
    cls = entry_class_for(omnikb_type)
    if cls is None:
        return None

    description = fm.get("summary") or _extract_summary(body)
    explicit_tags = _ensure_list(fm.get("tags", []))
    implicit_tags = _extract_implicit_tags(path.resolve())

    merged_tags: list[str] = []
    seen: set[str] = set()
    for t in explicit_tags + implicit_tags:
        t = str(t)
        if t not in seen:
            seen.add(t)
            merged_tags.append(t)

    # 构造基础字段
    base_data = {
        "id": fm.get("id", ""),
        "name": fm.get("name", path.stem),
        "description": description,
        "tags": merged_tags,
        "maturity": fm.get("maturity", "draft"),
        "source_path": str(path),
    }

    # 收集该类型特有字段
    type_specific = {k: v for k, v in fm.items() if k not in {
        "omnikb_type", "id", "name", "summary", "tags", "maturity",
    }}

    # 规范 list 字段
    for key in (
        "relates_to_formats", "relates_to_krouters", "relates_to_routers",
        "code_anchors", "related_decisions", "related_experiments", "related_karchs",
        "supersedes", "superseded_by", "drivers", "options_considered",
        "consequences_positive", "consequences_negative",
        "samples_run", "findings_summary", "followups",
        "capability_areas", "prior_landmarks_tier_1", "known_unread_areas",
    ):
        if key in type_specific:
            type_specific[key] = _ensure_list(type_specific[key])

    try:
        return cls(**base_data, **type_specific)
    except Exception:  # pragma: no cover
        # frontmatter 字段不匹配时返回 None, 由 validator 层处理
        return None
