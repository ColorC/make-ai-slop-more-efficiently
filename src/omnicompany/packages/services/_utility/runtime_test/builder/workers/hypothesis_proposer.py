# [OMNI] origin=claude-code domain=services/runtime_test_builder/workers ts=2026-04-27T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.builder.hypothesis_proposer.agent.py"
# OMNI-024 ALLOW: 域内私有 worker, 一类一文件经 workers/__init__ 聚合出口, 类实现与本文件模块级 prompt/helper/装配紧耦合, 迁入共享 routers.py 会割裂内聚
"""HypothesisProposerWorker — Worker #2 (AGENT, 真 meta 层 v2 核心创新).

接 target_profile (上游 TargetExplorer 产), 综合 hypothesis_library (4 通用 + 5 模式)
→ 当场针对生成 N 条 target 特化假设. 不是固定模板套.

每条假设 = 必要不充分条件, 含可证伪方式.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
)
# hypothesis_library 是隐私排除的可选服务(含 P4 模式, 不进公开白名单): 动态导入,
# 缺失时降级为空假设集(worker 仍可跑, 只是没有先验假设库注入)
import importlib as _importlib
try:
    _hyplib = _importlib.import_module(
        "omnicompany.packages.services._learning.hypothesis_library")
    UNIVERSAL_HYPOTHESES = _hyplib.UNIVERSAL_HYPOTHESES
    PATTERNS = _hyplib.PATTERNS
    render_for_prompt = _hyplib.render_for_prompt
except ModuleNotFoundError:
    UNIVERSAL_HYPOTHESES, PATTERNS = [], []
    def render_for_prompt(*_a, **_k):
        return 
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import PRESET_LIGHTWEIGHT


_SYSTEM_PROMPT = """你是 runtime_test_builder · HypothesisProposer (真 meta 层 v2 核心节点).

## 你的任务

接 target_profile, 综合 hypothesis_library (4 通用候选 + 5 现成模式), 针对此 target **当场产**:

- 3-10 条 target 特化假设清单
- 每条带 source / 主张 / 为什么对此 target 关键 / 验证方式 / 可证伪方式 / importance

不是套固定模板, 是**针对生成**.

## 重要原则

1. **通用假设 (UNIVERSAL_HYPOTHESES) 是候选起点不是必用**:
   - 看 target_profile 字段 (has_fixtures / has_random_or_creative / has_external_anchors), 判哪些通用假设适用
   - 不适用的进 skipped_universal_ids 解释为啥跳

2. **现成模式 (PATTERNS) 是参考清单不是模板**:
   - 看哪个 pattern 跟 target 工作类型最近 (e.g. byte_diff_acceptance ↔ has_byte_diffable_output)
   - 用 pattern 的 verification_template 当起点, 但 verification_recipe 要细化到此 target
   - **★ 三档基础质量检查应默认考虑** (适用任何 OmniCompany 包 target, 不论工作类型):
     - `five_element_check` — 扫 target 的 formats.py 五要素健康 (id / parent / json_schema / description / tags). **任何带 Material 定义的包都该过这条**
     - `red_line_check` — 扫 target 源码硬性铁律违反 (截断 / 单模型 / EventBus / 打分等)
     - `directory_hygiene` — 扫目录卫生 (文件命名/位置/不该在的散文 .md 等)
   - 这三档对应 OmniCompany 项目级铁律, 默认应该提. 即使 target 工作类型不是这三档原本设想的"代码改进", 它们仍适用 (因为 target 包本身就是代码 + Material 定义)

3. **novel 假设是 target 特殊的**:
   - 如果 target 有 library 没覆盖的角度 (e.g. "提案的修改风险评估应跟 git diff 量级匹配"), 加 novel 条
   - novel hypothesis_id 加 '_novel' 后缀 (例: 'risk_calibrated_novel')
   - **★ 自检**: 如果你想标 novel, 先回头看 PATTERNS 5 条, 看你产的这条概念上是否跟某条 pattern 等价 — 如果是, 应该 match 到那条 pattern 而不是标 novel
     - 例: 你产 "material_schema_compliance" 验 formats.py 字段完整性 → 这本质是 `five_element_check` 的具体形式, match=five_element_check 而不是 novel
     - 例: 你产 "no_truncation_compliance" 验源码无截断 → 这是 `red_line_check` 的具体形式, match=red_line_check 而不是 novel

## ★ library_match_id 字段 (每条假设必填) ★

每条假设都必须填 `library_match_id`. 这是给下游调度员用的桥梁:

- 如果你的假设是基于 library 里某条 (UNIVERSAL_HYPOTHESES 或 PATTERNS) 的变种 →
  填**那条登记 id** (即上面 library 里每条标题里那个 id, 一字不差)
- 如果你的假设是完全 target 特化的新假设 (library 没覆盖) →
  填 `null`

**举例**:
- 你的假设 hypothesis_id 叫 `structural_stability` 但本质是稳定性变种 → library_match_id = "stable"
- 你的假设 hypothesis_id 叫 `reference_honesty` 但本质是引用真实性变种 → library_match_id = "reference_existence"
- 你的假设 hypothesis_id 叫 `escaping_correctness_novel` 是 csv-to-md 特殊的 GFM 转义校验, library 没这条 → library_match_id = null

source 字段跟 library_match_id 配对:
- source = "universal" → library_match_id 应是 4 条通用之一 (stable/honest/robust/observable)
- source = "pattern"   → library_match_id 应是 5 条模式之一 (byte_diff_acceptance/reference_existence/five_element_check/directory_hygiene/red_line_check)
- source = "novel"     → library_match_id = null

## 反模式

- 禁打分 (importance 用 high/medium/low 粗粒度分类, 不是 0-100 分)
- 禁通用大话 ("应该好" "应该稳定") 不带 target 特化细节
- 禁套全部通用假设 (target 不需要的就跳, 写明原因)
- 禁 verification_recipe 模糊 (必须具体到工具/数据/流程: e.g. "用 ast.walk 扫 formats.py 找 Subscript+Slice 模式" 而非 "扫代码看截断")

## 工具

- submit_hypothesis_set: 一次终结提交全部假设清单"""


class _PromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        # 接上游 target_profile (作为 _from_TargetExplorerWorker 镜像或直接平展)
        profile_mirror = biz_input.get("_from_TargetExplorerWorker") or {}
        if not profile_mirror:
            # 平展 fallback
            profile_mirror = {
                k: biz_input[k]
                for k in (
                    "target_team_id", "package_path", "output_format_summary",
                    "design_purpose", "product_kind_signals", "has_fixtures",
                    "has_repo_input", "has_byte_diffable_output", "has_external_anchors",
                    "has_random_or_creative", "consumed_input_shape",
                )
                if k in biz_input
            }

        target_team_id = profile_mirror.get("target_team_id", "?")

        # 渲染 hypothesis_library
        univ_md = render_for_prompt(list(UNIVERSAL_HYPOTHESES))
        patt_md = render_for_prompt(list(PATTERNS))

        # 渲染 profile
        import json as _json
        profile_brief = _json.dumps(profile_mirror, ensure_ascii=False, indent=2)

        task = f"""## 任务: 为 target `{target_team_id}` 当场针对生成假设清单

### target_profile (上游 TargetExplorer 产)

```json
{profile_brief}
```

### hypothesis_library · 通用候选 (4 条)

{univ_md}

### hypothesis_library · 现成模式 (5 条)

{patt_md}

### 操作

1. 读 target_profile 的 has_* 标志 + product_kind_signals + design_purpose
2. 对每条通用假设 (UNIVERSAL_HYPOTHESES 4 条) 判:
   - 适用此 target → 加进 hypotheses 清单, 写细化 verification_recipe
   - 不适用 → 进 skipped_universal_ids + 1 句话 reason
3. 对每条现成模式 (PATTERNS 5 条) 判:
   - 跟 target 工作类型相近 → 加进 hypotheses, source='pattern', recipe 细化
4. 是否还有 target 特殊角度 library 没覆盖? 加 novel 条 (≤3, source='novel')
5. 总条数 3-10
6. 调 submit_hypothesis_set 一次提交

### 提交字段

详见 system prompt + tool schema.

要求每个 hypothesis 的 verification_recipe 必须**具体到此 target 的工具/数据/流程**, 不能是模糊大话.

### ★ 关键: 每条假设都要填 library_match_id

- 基于上面 library 里某条变种 → 填那条登记 id (stable / honest / robust / observable / byte_diff_acceptance / reference_existence / five_element_check / directory_hygiene / red_line_check)
- 完全 target 特化的新假设 → 填 null

下游调度员根据这个字段选 verifier. 写错或漏写 → 假设落 pending."""

        return [{"role": "user", "content": task}]


class SubmitHypothesisSetRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "submit_hypothesis_set"
    DESCRIPTION: ClassVar[str] = "Submit the proposed hypothesis set for this target."
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string"},
            "hypotheses": {
                "type": "array",
                "minItems": 3,
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {"type": "string", "minLength": 1},
                        "library_match_id": {
                            "type": ["string", "null"],
                            "description": (
                                "登记 library id (stable/honest/robust/observable/"
                                "byte_diff_acceptance/reference_existence/five_element_check/"
                                "directory_hygiene/red_line_check) 或 null (novel)."
                            ),
                        },
                        "source": {"type": "string", "enum": ["universal", "pattern", "novel"]},
                        "description": {"type": "string", "minLength": 20},
                        "rationale_for_this_target": {"type": "string", "minLength": 30},
                        "verification_recipe": {"type": "string", "minLength": 30},
                        "falsifiability": {"type": "string"},
                        "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": [
                        "hypothesis_id",
                        "library_match_id",
                        "source",
                        "description",
                        "rationale_for_this_target",
                        "verification_recipe",
                        "importance",
                    ],
                },
            },
            "novelty_signals": {"type": "array", "items": {"type": "string"}},
            "skipped_universal_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["target_team_id", "hypotheses"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args, ctx) -> str:
        return f"submitted {len(args.get('hypotheses', []))} hypotheses for {args.get('target_team_id')}"


def _land_beliefs_in_library(inp: dict) -> int:
    """特化假设产出挂统一决策库(决策本体合并清单#2:不再自有形态)。

    每条假设 → kind=belief, track={plan, team:<target_team_id>};
    幂等键=alias rtb:<team>:<hypothesis_id>(重跑合并不重复)。失败不阻塞管线。
    """
    from omnicompany.packages.domains.decisions import library

    team_id = (inp.get("target_team_id") or "").strip() or "unknown-team"
    by_alias: dict[str, str] = {}
    for rec in library.active_records():
        for a in rec.get("aliases") or []:
            by_alias.setdefault(a, rec["id"])
    _IMPORTANCE_TO_RISK = {"high": "high", "medium": "medium", "low": "low"}
    n = 0
    for h in inp.get("hypotheses") or []:
        if not isinstance(h, dict):
            continue
        alias = f"rtb:{team_id}:{h.get('hypothesis_id', '')}"[:80]
        payload: dict = {
            "kind": "belief",
            "statement": (h.get("description") or "")[:300],
            "nature": "factual",
            "risk_if_wrong": _IMPORTANCE_TO_RISK.get(h.get("importance") or "", "medium"),
            "evidence_query": (h.get("verification_recipe") or "")[:400],
            "rationale": (h.get("rationale_for_this_target") or "")[:400],
            "tags": ["runtime-test-builder", f"team:{team_id}"],
            "track": {"kind": "plan", "id": f"team:{team_id}"},
            "origin": {"channel": "claude", "author": "runtime_test_builder.hypothesis_proposer"},
            "created_by": "runtime_test_builder",
        }
        existing = by_alias.get(alias)
        if existing:
            payload["id"] = existing
        payload["aliases"] = [alias]
        library.upsert(payload)
        n += 1
    return n


class _ExtractResult(ExtractResultRouter):
    def extract(self, *, final_text, messages, turn_count, stop_reason) -> Verdict:
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_hypothesis_set"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            inp.setdefault("novelty_signals", [])
                            inp.setdefault("skipped_universal_ids", [])
                            count = len(inp.get("hypotheses", []))
                            try:
                                landed = _land_beliefs_in_library(inp)
                            except Exception as exc:  # 挂库失败不阻塞验证主流程
                                logger.warning("特化假设挂库失败(非致命): %s", exc)
                                landed = 0
                            return Verdict(
                                kind=VerdictKind.PASS,
                                output=dict(inp),
                                diagnosis=(f"提出 {count} 条假设 for {inp.get('target_team_id')}"
                                           f" (挂库 belief {landed} 条)"),
                                confidence=0.9,
                            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={},
            diagnosis=f"未调 submit_hypothesis_set (turns={turn_count})",
        )


class HypothesisProposerWorker(AgentNodeLoop):
    DESCRIPTION: ClassVar[str] = "针对 target 当场产假设清单 (核心创新 · 非固定模板)."
    FORMAT_IN: ClassVar[str] = "runtime_test_builder.target_profile"
    FORMAT_OUT: ClassVar[str] = "runtime_test_builder.hypothesis_set"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [SubmitHypothesisSetRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT
    LOOP_CONFIG = PRESET_LIGHTWEIGHT
    TERMINATING_TOOLS = ("submit_hypothesis_set",)

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any):
        return _PromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any):
        return _ExtractResult(bus=bus)
