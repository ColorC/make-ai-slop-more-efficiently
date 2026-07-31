# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-14T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.stage3.proposal_parser.router.py"
"""spec_parser — Stage 3 R1 SpecParserRouter（RULE + LLM）

从 absorption.report.v3 的改进提案解析为结构化任务列表（absorption.proposal.list）。

逻辑：
1. 先尝试从 input_data["structured"]["proposals"] 直接读取（ReportWriter 已结构化）
2. 若结构化数据不足，回退到 LLM 解析 report_md 中的改进提案段落
3. 为每个 proposal 分配 proposal_id，推断 risk_level
4. 写 pending_proposals.md 供人工快速查阅

FORMAT_IN:  absorption.report.v3
FORMAT_OUT: absorption.proposal.list

设计文档：docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from omnicompany.core.config import resolve_domain_data_dir
from omnicompany.core.guarded_write import write_file as _guarded_write
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

_MODEL = "qwen3.6-plus"

# 改动 core/runtime 路径的正则 → 自动升 risk_level 到 high
_HIGH_RISK_PATH_PATTERNS = [
    re.compile(r"runtime/exec/runner"),
    re.compile(r"runtime/bus/"),
    re.compile(r"core/dispatch"),
    re.compile(r"core/pipelines"),
    re.compile(r"runtime/agent/agent_node_loop"),
]

_SPEC_SYSTEM = """你是 OmniCompany 改进提案分析师。
你会收到一份外部 repo 的吸纳报告和 OmniCompany 的实际代码能力摘要。
你的任务：生成**功能层级**的改进提案。

## 层级纪律（严格遵守）

提案只描述"需要什么功能"，不指定"改哪个文件"：
  好: "需要可插拔的记忆提供者抽象，支持多种后端"
  坏: "在 core/memory/provider.py 创建 MemoryProvider ABC"

## 已有能力检查

仔细对照 OmniCompany 已有能力清单。已有的标注 "已有可改进"，不要重复提案。

## layer 字段 —— 这很关键，直接影响审批流程

每个提案必须带 `layer` 字段，四选一：

- `infrastructure` — 改动会落在 OmniCompany 的**核心层模块**上（能力清单里 runtime/* / core/* / protocol/* / bus/* / primitives/* 等任一模块，不论是新建、扩展、替换还是微调）
- `service` — 新增一个独立的业务 package（`packages/services/X` 或 `packages/domains/X`），**不修改**任何核心层模块
- `tool` — 仅是一个单文件小工具，既不改核心层也不开新 package
- `unknown` — 你判不准

### 判断方法

**仔细读本消息后面的"OmniCompany 当前能力清单"全文**。每行列出了 OmniCompany 已有模块的路径 + 功能关键词。判 layer 时：

1. 从提案的 title/summary 提取功能关键词（例：提案"集中式错误分类"→ 关键词"错误"、"重试"、"恢复"）
2. 在能力清单里搜这些关键词，看是否命中某个 runtime/* 或 core/* 模块的描述
3. 命中核心层模块 → `infrastructure`；没命中但会新开 packages/services/X → `service`；都不是 → `tool`；**完全没把握就标 `unknown`**，不要猜

判 unknown 不会被惩罚——下游会强制人工审批，模糊判断不误导流程。但错判（tool 误标为 infrastructure 相反）会破坏审批流。

## 输出格式

每个提案：
{
  "proposal_id": "PRO-001",
  "title": "≤20字简洁标题",
  "summary": "2-3句：做什么 + 为什么值得",
  "omnifactory_status": "缺失 | 部分存在 | 已有可改进",
  "source": {
    "repo": "repo名称",
    "finding": "对应发现标题",
    "gap_id": "G1-G7",
    "priority": "P0|P1|P2",
    "reference_file": "来源文件路径"
  },
  "type": "new_capability | enhance_existing | architectural_pattern",
  "layer": "infrastructure | service | tool | unknown",
  "risk_level": "low|medium|high",
  "acceptance_criteria": ["可测试的验收条件"]
}

输出纯 JSON 数组，无其他文字：
[{...}, {...}]
"""


class SpecParserRouter(Router):
    """Stage 3 R1：解析 absorption 报告的改进提案为结构化任务列表。

    先尝试从 structured.proposals 直接读取（RULE），
    若不足则 LLM 解析 report_md 中的提案段落。
    写 pending_proposals.md 供人工快速查阅。
    """

    DESCRIPTION = (
        "Stage 3 提案解析（3 路 fan-in via composite Format）：消费 absorption.proposal.context "
        "复合上下文（report.v3 + capability_inventory + gap_registry），产出结构化任务列表 "
        "absorption.proposal.list，写 pending_proposals.md 供人工审阅。"
        "遵守 P-13/F-15：只读声明 FORMAT_IN 的 schema 字段，不从 input_data 透传读未声明字段。"
    )
    FORMAT_IN = "absorption.proposal.context"
    FORMAT_OUT = "absorption.proposal.list"

    _MODEL = _MODEL

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        # composite FORMAT_IN：三路输入以 component format_id 为 key（由 runner._merge_inputs 保证）
        report = input_data.get("absorption.report.v3") or {}
        inventory = input_data.get("omni.self.capability_inventory") or {}
        gaps = input_data.get("omni.self.gap_registry") or {}

        # supplement 模式（2026-04-18 加）：feedback 回路 JUMP 回来时带
        # supplement_guidance + previous_proposals，LLM 看旧提案 + 补充要求 重新综合
        supplement_guidance: str = input_data.get("supplement_guidance") or ""
        previous_proposals: list[dict] = list(input_data.get("previous_proposals") or [])
        iteration: int = int(input_data.get("iteration") or report.get("iteration") or 1)

        repo_name = report.get("repo_name", input_data.get("repo_name", "unknown"))
        structured: dict = report.get("structured") or {}
        report_md: str = report.get("report_md", "")

        # ── Step 1: 从 structured.proposals 直接读取 ───────────────────────
        raw_proposals: list[dict] = []
        structured_highlights = structured.get("highlights") or []
        structured_proposals = structured.get("proposals") or []

        # supplement 模式优先走 LLM：旧结构化提案已过时（人类要求补充综合）
        if supplement_guidance:
            # 空 report_md 也不回退 —— feedback 路径确保至少有 structured 数据
            raw_proposals = self._parse_with_llm(
                repo_name, report_md or "", inventory, gaps,
                supplement_guidance=supplement_guidance,
                previous_proposals=previous_proposals,
            )
        elif structured_proposals:
            # ReportWriter 已产出结构化提案
            raw_proposals = structured_proposals
        elif report_md:
            # 回退到 LLM 解析 Markdown（带 wiki 上下文：inventory + gaps 结构化版）
            raw_proposals = self._parse_with_llm(repo_name, report_md, inventory, gaps)

        if not raw_proposals:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"repo_name": repo_name, "proposals": [], "total_count": 0},
                diagnosis="SpecParser: 无法从报告中提取任何改进提案",
            )

        # ── Step 2: 补全字段、分配 ID、推断 risk_level ───────────────────
        proposals = []
        for i, p in enumerate(raw_proposals, 1):
            proposal = _normalize_proposal(p, i, repo_name, structured_highlights)
            proposals.append(proposal)

        # ── Step 3: 写 pending_proposals.md ───────────────────────────────
        repo_dir = resolve_domain_data_dir("absorption") / repo_name
        repo_dir.mkdir(parents=True, exist_ok=True)
        pending_path = repo_dir / "pending_proposals.md"
        _write_pending_proposals(pending_path, proposals, repo_name)
        print(f"[SpecParser] {len(proposals)} 个提案 → {pending_path}")

        p0_count = sum(1 for p in proposals if p.get("source", {}).get("priority") == "P0")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "repo_name": repo_name,
                "proposals": proposals,
                "total_count": len(proposals),
                "p0_count": p0_count,
                "pending_review_path": str(pending_path),
            },
            confidence=0.85,
            diagnosis=f"SpecParser: {len(proposals)} 个提案（{p0_count} P0），pending={pending_path}",
            granted_tags=["domain.absorption", "stage.v3.stage3"],
        )

    def _parse_with_llm(
        self,
        repo_name: str,
        report_md: str,
        inventory: dict,
        gaps: dict,
        *,
        supplement_guidance: str = "",
        previous_proposals: list[dict] | None = None,
    ) -> list[dict]:
        """LLM 解析吸纳报告为改进提案。

        2026-04-18 升级（P-13/F-15 示范）：
        - inventory / gaps 是结构化 Format（来自 omni.self.capability_inventory / gap_registry）
        - 用 wiki_loader 的 render_*_for_prompt 渲染为 LLM 友好 Markdown
        - 不再走硬编码字符串、不再从 input_data 读 self_portrait 透传字段

        2026-04-18 feedback 回路支持：
        - supplement_guidance 非空时追加"本轮补充要求"节
        - previous_proposals 非空时追加"已产出提案（避免重复）"节
        """
        # 2026-04-18 零容忍截断：完整 report 传入（含 DETAIL 区）。
        # 见 docs/standards/llm_first.md 原则 3（零容忍版）。
        # 以前在这里 split("---DETAIL---")[0] 丢了后半，导致 learning_loop/delegate/HRR
        # 主题永远提不出（详见 docs/plans/[2026-04-15]PROPOSAL-QUALITY/specparser_v2_experiment.md）。
        section = report_md

        # 从 Format 对象渲染 LLM prompt 段（F-15：明示从声明的 Format 字段派生）
        from omnicompany.packages.services._learning.absorption.wiki_loader import (
            render_capability_inventory_for_prompt,
            render_gap_registry_for_prompt,
        )
        capability_section = render_capability_inventory_for_prompt(inventory)
        gaps_section = render_gap_registry_for_prompt(gaps)

        # supplement 节（feedback 回路 JUMP 回时填充）
        supplement_section = ""
        if supplement_guidance:
            # 关键修复（2026-04-18）：合并模式，而非替换模式。
            # 上一轮 JUMP 回来时，LLM 必须输出"完整合并后的提案清单"：
            # = 原有提案（保留 proposal_id / title，可能按 guidance 微调字段如 layer）
            #   + guidance 要求新增的提案（用新 proposal_id，如 PRO-009+）
            prev_full_json = ""
            if previous_proposals:
                import json as _json
                prev_full_json = _json.dumps(previous_proposals, ensure_ascii=False, indent=2)
            supplement_section = f"""

---

## ⚠️ 本轮补充综合要求（supplement mode，iteration > 1）

**本轮任务是合并，不是重写**。你需要输出"完整合并后的提案清单"，包括：
  (a) **保留上一轮所有提案**（除非 guidance 明确要求删除/合并/拆分）
  (b) **按 guidance 新增提案**（用新 proposal_id，例如上一轮 PRO-001~008，新增 PRO-009 起）
  (c) **按 guidance 对所有提案应用格式调整**（例如为每条加新字段、改 gap_id、微调 title）

### 本轮 guidance：

{supplement_guidance}

### 上一轮完整提案（JSON 原文，保留全部字段，按 guidance 修改/扩展后输出）：

```json
{prev_full_json}
```

**输出时必须包含上一轮所有 proposal_id，不得只输出新增条目**。如果你只输出了增量条目，管线会认为你漏了上一轮的工作。
"""

        user_msg = f"""# 生成改进提案

Repo: {repo_name}

## 吸纳报告（完整，含 §一-§六速览 + ---DETAIL--- 区每 finding 详解）

{section}

## OmniCompany 当前能力清单（来自 omni.self.capability_inventory）

{capability_section}

## OmniCompany 已识别的缺口（来自 omni.self.gap_registry）

{gaps_section}
{supplement_section}
请基于以上信息生成改进提案 JSON 数组。

## 综合指令（不只是机械 1:1 映射）

1. **先扫一遍完整 findings 列表**（含 DETAIL 区的每条详解），**识别跨 finding 的系统性主题** —— 几个看似独立的 finding 合起来是否构成一个更大的系统（例如"轨迹记录 + 会话分析 + 技能沉淀"合起来是"自学习闭环"）。
2. **若外部 repo 报告里有明确宣称的主卖点（project_thesis / §一 概览段 / 标题副标题）**，必须至少有 1 条提案直接对应该主卖点 —— 不只是相关功能域，而是直接回应宣称。
3. **对照能力清单**，已有的标 'omnifactory_status: 已有可改进'，不要重复提案。
4. **对应已知 gap** 的填 'source.gap_id'（G1~G7）。
5. **宁多勿少**：原料里有 20+ findings 时，预期至少 8-12 条提案，否则说明综合过度。
"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SPEC_SYSTEM,
                info_audit=False,  # strict-JSON 节点 opt-out piggyback; 由 post_hoc 兜底
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            # 容错：piggyback 可能在后面追加 info_audit JSON 块，
            # 只取第一个顶层 JSON 数组（主答案）
            return _extract_first_json_array(raw)
        except Exception as e:
            print(f"[SpecParser] LLM 解析失败: {e}")
            return []


def _build_omnifactory_summary() -> str:
    """构建 OmniCompany 自知识摘要（2026-04-18：改为从 wiki 动态加载）。

    取代原硬编码字符串，读：
    - src/omnifactory/README.md 的能力五分类表
    - src/omnifactory/**/DESIGN.md 里 status=active|design 的核心目的节
    - docs/gaps/INDEX.md + G1~G7.md 的缺口档案

    失败时走 wiki_loader 内部兜底字符串。
    缓存在 wiki_loader.load_wiki_self_knowledge 的 lru_cache 里，单进程只读一次。
    """
    from omnicompany.packages.services._learning.absorption.wiki_loader import load_wiki_self_knowledge
    return load_wiki_self_knowledge()


def _extract_first_json_array(text: str) -> list[dict]:
    """从文本里抽第一个完整 JSON 数组。容忍前后杂质（piggyback 附加的 JSON 块）。"""
    # 先尝试整体解析（没有污染时的理想情况）
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    # 逐字符扫描，找第一个完整的 [...] 数组
    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return []
    return []


def _extract_proposal_section(report_md: str) -> str:
    """从 Markdown 中提取改进提案章节。"""
    patterns = [
        r"(?:##[^#]|###[^#]).*?(?:改进提案|提案|proposal|改进).*?\n(.*?)(?=\n##[^#]|\Z)",
        r"## 五[、.。].*?\n(.*?)(?=\n##[^#]|\Z)",
    ]
    for pat in patterns:
        m = re.search(pat, report_md, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(0)  # 零容忍截断（llm_first.md 原则 3）
    return ""


def _infer_risk_level(target_changes: list[dict]) -> str:
    """根据 target_changes 的文件路径推断 risk_level。"""
    for change in target_changes:
        path = change.get("path", "")
        for pat in _HIGH_RISK_PATH_PATTERNS:
            if pat.search(path):
                return "high"
        if "modify" in change.get("action", "") and any(
            kw in path for kw in ("runner", "dispatch", "bus", "exec")
        ):
            return "high"
        if change.get("action") == "modify":
            return "medium"
    return "low"


_VALID_LAYERS = {"infrastructure", "service", "tool"}


def _normalize_proposal(raw: dict, index: int, repo_name: str, highlights: list[dict]) -> dict:
    """补全 proposal 字段，分配 ID，推断 risk_level；layer 由 LLM 决定（不硬编码规则）。"""
    proposal_id = raw.get("proposal_id") or f"PRO-{index:03d}"
    raw_title = raw.get("title") or raw.get("what", f"提案 {index}")
    # 按词边界截断（LLM 输出上界，非资料截断；llm_first.md 原则 3 合法例外）
    title = raw_title if len(raw_title) <= 30 else raw_title[:30].rsplit("，", 1)[0].rsplit(" ", 1)[0][:30]
    summary = raw.get("summary") or raw.get("why", "")

    # 尝试从 highlights 中匹配 source 信息
    source = raw.get("source") or {}
    if not source.get("gap_id") and highlights:
        for h in highlights:
            if h.get("title", "") in title or title in h.get("title", ""):
                source.setdefault("gap_id", h.get("gap_id", ""))
                source.setdefault("finding", h.get("title", ""))
                source.setdefault("priority", "P0" if h.get("portability") == "directly_reusable" else "P1")
                break
    source.setdefault("repo", repo_name)

    target_changes = raw.get("target_changes") or []
    if not target_changes:
        # 从 location 字段推断
        location = raw.get("location", "")
        if location:
            target_changes = [{"path": f"src/omnifactory/{location}", "action": "create", "description": summary}]

    risk_level = raw.get("risk_level") or _infer_risk_level(target_changes)

    # Layer 字段完全由 LLM 决定（llm_first.md 原则 1：0 硬编码规则）。
    # LLM 若给了合法枚举就用；否则标 "unknown"（fail loud，下游审批视作 infrastructure 处理）。
    raw_layer = raw.get("layer")
    layer = raw_layer if raw_layer in _VALID_LAYERS else "unknown"

    # infrastructure + unknown 层强制人工审批（用户铁律：无法确定是否动基础设施 → 必须人工过目）
    human_required = raw.get("human_approval_required")
    forces_approval = layer in ("infrastructure", "unknown")
    if human_required is None:
        human_required = forces_approval or (risk_level == "high")
    else:
        human_required = bool(human_required) or forces_approval

    return {
        "proposal_id": proposal_id,
        "title": title[:30],
        "summary": summary,
        "source": source,
        "type": raw.get("type", "new_package"),
        "layer": layer,
        "target_changes": target_changes,
        "acceptance_criteria": raw.get("acceptance_criteria") or [],
        "risk_level": risk_level,
        "human_approval_required": human_required,
        "estimated_files": raw.get("estimated_files", len(target_changes) or 1),
        "estimated_lines": raw.get("estimated_lines", 100),
    }


def _write_pending_proposals(path: Path, proposals: list[dict], repo_name: str) -> None:
    """写人类可读的 pending_proposals.md。"""
    lines = [
        f"# 待审批改进提案 — {repo_name}",
        "",
        "审批方式：创建 `approved_proposals.txt`，每行写一个要执行的 proposal_id（如 `PRO-001`）。",
        "不写入 = 跳过；空文件 = 全部跳过。",
        "",
        f"共 {len(proposals)} 个提案：",
        "",
        "| ID | 标题 | layer | 类型 | 风险 | 需审批 |",
        "|---|---|---|---|---|---|",
    ]
    for p in proposals:
        pid = p["proposal_id"]
        title = p["title"]
        layer = p.get("layer", "tool")
        ptype = p["type"]
        risk = p["risk_level"]
        human = "✓" if p["human_approval_required"] else ""
        lines.append(f"| {pid} | {title} | {layer} | {ptype} | {risk} | {human} |")

    lines += ["", "---", ""]
    for p in proposals:
        lines += [
            f"## {p['proposal_id']}: {p['title']}",
            "",
            f"**摘要**: {p['summary']}",
            f"**类型**: {p['type']} | **风险**: {p['risk_level']} | **预估**: {p['estimated_files']} 文件 / ~{p['estimated_lines']} 行",
            "",
            "**改动目标**:",
        ]
        for tc in p.get("target_changes") or []:
            lines.append(f"  - `{tc['action']}` `{tc['path']}`：{tc['description']}")
        ac = p.get("acceptance_criteria") or []
        if ac:
            lines.append("")
            lines.append("**验收条件**:")
            for c in ac:
                lines.append(f"  - {c}")
        lines.append("")

    _guarded_write(path, "\n".join(lines), writer="internal-engine",
                   domain="absorption", purpose="absorption pending proposals for human review")
