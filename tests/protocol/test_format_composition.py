# [OMNI] origin=claude-code ts=2026-04-11T00:00:00Z
"""
tests/test_format_composition.py — Format composition（has-a）系统单元测试

覆盖 15 个场景，确保有红有绿：
  1-7:  FormatRegistry.components 注册 + 循环检测 + 辅助方法
  8-9:  runner._merge_inputs() composite 命名空间合并 vs 向后兼容
  10-12: CompositeFormatCheckRouter PASS/WARN/跳过
  13:   Doctor format 管线 E2E（composite 通过全链）
  14-15: OMNI-026 / OMNI-027 Guardian 规则触发
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from typing import Any

from omnicompany.protocol.format import Format, FormatRegistry
from omnicompany.protocol.anchor import VerdictKind


# ── helpers ──────────────────────────────────────────────────────────────────

def make_reg(*formats: Format) -> FormatRegistry:
    """按顺序注册 formats，返回已填充的 registry。"""
    reg = FormatRegistry()
    for fmt in formats:
        reg.register(fmt)
    return reg


def _leaf(fid: str, desc: str = "A leaf format") -> Format:
    return Format(id=fid, name=fid.capitalize(), description=desc)


# ═══════════════════════════════════════════════════════════════
# 1-7: FormatRegistry.components 注册与辅助方法
# ═══════════════════════════════════════════════════════════════

class TestCompositeRegistration:

    def test_valid_composite_registration(self):
        """场景1: 注册有效 composite Format（所有 components 先注册）→ 成功"""
        reg = make_reg(
            _leaf("a"),
            _leaf("b"),
            _leaf("c"),
        )
        reg.register(Format(
            id="ab.composite",
            name="ABComposite",
            description="由 a 和 b 组合而成的复合类型",
            components=["a", "b"],
        ))
        assert reg.is_registered("ab.composite")

    def test_component_not_found_raises(self):
        """场景2: component 未先注册 → ValueError"""
        reg = make_reg(_leaf("a"))
        with pytest.raises(ValueError, match="component 'missing' not found"):
            reg.register(Format(
                id="bad",
                name="Bad",
                description="bad composite",
                components=["a", "missing"],
            ))

    def test_cycle_detection_direct(self):
        """场景3: A.components=[B], 但 B 已注册含 A → 通过 force 制造循环 → ValueError"""
        reg = make_reg(_leaf("a"))
        # 注册 b with components=['a'] — 正常
        reg.register(Format(id="b", name="B", description="B", components=["a"]))
        # 尝试 force-register 'a' with components=['b'] → cycle: a→b→a
        with pytest.raises(ValueError, match="circular"):
            reg.register(
                Format(id="a", name="A", description="A", components=["b"]),
                force=True,
            )

    def test_self_reference_raises(self):
        """场景4（兼场景3变种）: Format 引用自身作为 component → ValueError"""
        reg = FormatRegistry()
        # 先 force 注册一个空壳，再尝试 force 注册自引用
        reg.register(Format(id="self_ref", name="SR", description="SR"))
        with pytest.raises(ValueError, match="circular"):
            reg.register(
                Format(id="self_ref", name="SR", description="SR", components=["self_ref"]),
                force=True,
            )

    def test_is_composite_true(self):
        """场景5: is_composite() 对有 components 的 Format → True"""
        reg = make_reg(_leaf("a"), _leaf("b"))
        reg.register(Format(
            id="comp",
            name="Comp",
            description="由 a 和 b 组合",
            components=["a", "b"],
        ))
        assert reg.is_composite("comp") is True

    def test_is_composite_false(self):
        """场景6: is_composite() 对无 components 的 Format → False"""
        reg = make_reg(_leaf("leaf"))
        assert reg.is_composite("leaf") is False
        assert reg.is_composite("nonexistent") is False

    def test_get_all_components_nested(self):
        """场景7: get_all_components() 递归展开嵌套 composite"""
        reg = make_reg(_leaf("x"), _leaf("y"), _leaf("z"))
        reg.register(Format(
            id="xy",
            name="XY",
            description="由 x 和 y 组合",
            components=["x", "y"],
        ))
        reg.register(Format(
            id="xyz",
            name="XYZ",
            description="由 xy 和 z 组合",
            components=["xy", "z"],
        ))
        leaves = reg.get_all_components("xyz")
        assert set(leaves) == {"x", "y", "z"}

    def test_get_all_components_flat(self):
        """场景7b: get_all_components() 直接叶子"""
        reg = make_reg(_leaf("a"), _leaf("b"))
        reg.register(Format(
            id="ab",
            name="AB",
            description="由 a 和 b 组合",
            components=["a", "b"],
        ))
        assert set(reg.get_all_components("ab")) == {"a", "b"}


# ═══════════════════════════════════════════════════════════════
# 8-9: runner._merge_inputs() composite 命名空间合并
# ═══════════════════════════════════════════════════════════════

class TestMergeInputsComposite:
    """通过直接调用 runner._merge_inputs() 验证 key 命名逻辑。"""

    def _make_runner(self, composite_format_in: str | None = None):
        """构造最小化的 PipelineRunner mock，暴露 _merge_inputs / _get_format_out_by_id / _get_raw_format_in。"""
        from omnicompany.runtime.exec.runner import PipelineRunner
        from omnicompany.protocol.pipeline import PipelineSpec, PipelineNode, NodeKind, NodeMaturity
        from omnicompany.protocol.anchor import TransformerSpec, TransformMethod

        # 构造两个上游节点 (src_a, src_b) 和一个目标节点 (target_node)
        src_a = PipelineNode(
            id="src_a",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="src-a", name="SrcA",
                from_format="root",
                to_format="feishu.api-spec",
                method=TransformMethod.RULE, description="A",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        )
        src_b = PipelineNode(
            id="src_b",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="src-b", name="SrcB",
                from_format="root",
                to_format="oa.workflow-info",
                method=TransformMethod.RULE, description="B",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        )
        target_node = PipelineNode(
            id="target_node",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="tgt", name="Target",
                from_format=composite_format_in or "root",
                to_format="result",
                method=TransformMethod.RULE, description="T",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        )

        pipeline = PipelineSpec(
            id="test-pipe",
            name="Test",
            description="",
            nodes=[src_a, src_b, target_node],
            edges=[],
            entry="src_a",
        )

        bus = MagicMock()
        bus.publish = MagicMock()

        runner = PipelineRunner.__new__(PipelineRunner)
        runner.pipeline = pipeline
        runner.bindings = {}
        runner.bus = bus
        runner._nodes = {n.id: n for n in pipeline.nodes}
        runner._edges = {}
        runner._in_degree = {}
        runner.format_registry = None
        return runner

    def _make_verdict(self, output: Any):
        from omnicompany.protocol import Verdict, VerdictKind
        return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output, diagnosis="test")

    def test_composite_uses_format_out_as_key(self):
        """场景8: composite Format fan-in → key = format_out of upstream node"""
        from omnicompany.protocol.format import Format, FormatRegistry

        reg = FormatRegistry()
        reg.register(Format(id="feishu.api-spec", name="FA", description="collab platform API 信息"))
        reg.register(Format(id="oa.workflow-info", name="OA", description="OA 工作流信息"))
        reg.register(Format(
            id="oa.automation-context",
            name="OAContext",
            description="由 feishu.api-spec 和 oa.workflow-info 组合",
            components=["feishu.api-spec", "oa.workflow-info"],
        ))

        runner = self._make_runner(composite_format_in="oa.automation-context")
        runner.format_registry = reg

        received = {
            "src_a": self._make_verdict({"data_a": 1}),
            "src_b": self._make_verdict({"data_b": 2}),
        }
        merged = runner._merge_inputs("target_node", received)

        # composite 模式：key 应为 format_out（即 component Format ID）
        assert "feishu.api-spec" in merged, f"Expected 'feishu.api-spec' key, got: {list(merged.keys())}"
        assert "oa.workflow-info" in merged, f"Expected 'oa.workflow-info' key, got: {list(merged.keys())}"
        assert "_from_src_a" not in merged
        assert "_from_src_b" not in merged

    def test_non_composite_uses_from_node_id_key(self):
        """场景9: 非 composite Format fan-in → key = _from_{src_id}（向后兼容）"""
        runner = self._make_runner(composite_format_in="root")  # 'root' not registered as composite
        runner.format_registry = None  # no registry = non-composite path

        received = {
            "src_a": self._make_verdict({"data_a": 1}),
            "src_b": self._make_verdict({"data_b": 2}),
        }
        merged = runner._merge_inputs("target_node", received)

        assert "_from_src_a" in merged
        assert "_from_src_b" in merged
        assert "feishu.api-spec" not in merged

    def test_single_upstream_direct_pass(self):
        """单上游时直传，不做 key 命名"""
        runner = self._make_runner()
        runner.format_registry = None

        received = {"src_a": self._make_verdict({"value": 42})}
        merged = runner._merge_inputs("target_node", received)
        assert merged == {"value": 42}


# ═══════════════════════════════════════════════════════════════
# 10-12: CompositeFormatCheckRouter
# ═══════════════════════════════════════════════════════════════

class TestCompositeFormatCheckRouter:

    def _run(self, format_obj: dict) -> dict:
        from omnicompany.packages.services.doctor.routers import CompositeFormatCheckRouter
        router = CompositeFormatCheckRouter()
        input_data = {
            "format_id": format_obj.get("id", "test.format"),
            "extracted": {"format_obj": format_obj},
            "checks": [],
        }
        verdict = router.run(input_data)
        return verdict.output

    def test_composite_with_intent_passes(self):
        """场景10: composite Format + description 含组合意图 → check passed=True"""
        out = self._run({
            "id": "oa.automation-context",
            "description": "由 oa.workflow-info 和 feishu.api-spec 组合而成的上下文",
            "components": ["oa.workflow-info", "feishu.api-spec"],
        })
        comp_check = out["check_composite_format"]
        assert comp_check["passed"] is True

    def test_composite_without_intent_warns(self):
        """场景11: composite Format + description 无组合意图 → check passed=False"""
        out = self._run({
            "id": "oa.automation-context",
            "description": "上下文信息",
            "components": ["oa.workflow-info", "feishu.api-spec"],
        })
        comp_check = out["check_composite_format"]
        assert comp_check["passed"] is False
        assert comp_check["check"] == "composite_format"

    def test_non_composite_skipped(self):
        """场景12: 非 composite Format → check passed=True + observation 含'跳过'"""
        out = self._run({
            "id": "plain.format",
            "description": "A plain format",
            "components": [],
        })
        comp_check = out["check_composite_format"]
        assert comp_check["passed"] is True
        assert "跳过" in comp_check["observation"]


# ═══════════════════════════════════════════════════════════════
# 13 (removed): Doctor format 管线 E2E
# 原 TestDoctorFormatPipelineE2E 测的是 v1 doctor 链契约 (format_id 入参 +
# health_grade 输出)。2026-04-22 doctor 重构为 material 子域 (FormatExtractorRouter
# 已是 MaterialExtractorWorker 别名, 入参改 material_id), 2026-04-25 又删除了
# health_grade 字段 (改 v2 health_record schema)。该全链断言的对象已不存在,
# 新 material/v2 契约由 tests/doctor/test_health_writers_contract.py 覆盖。
# composite Format 本身的单元覆盖见上方 TestCompositeFormatCheckRouter (场景 10-12)。
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 14-15: Guardian 规则 OMNI-026
# ═══════════════════════════════════════════════════════════════

class TestGuardianOMNI026:
    """OMNI-026: FORMAT_IN/OUT 为列表规则验证。"""

    def _ctx(self, content: str, path: str = "src/omnicompany/packages/test/routers.py"):
        from omnicompany.packages.services.guardian.rules._base import FileContext
        return FileContext(path=path, abs_path="", change_type="M", content=content)

    def _get_rule(self, rule_id: str):
        from omnicompany.packages.services.guardian.rules import RULES
        return next(r for r in RULES if r.id == rule_id)

    def test_omni026_fires_on_list_format_in(self):
        """场景14: FORMAT_IN = [...] 列表 → OMNI-026 触发"""
        omni026 = self._get_rule("OMNI-026")
        ctx = self._ctx(
            "class BenchmarkValidatorRouter(Router):\n"
            '    FORMAT_IN = ["demogame.generated_script", "demogame.validation_context"]\n'
            '    FORMAT_OUT = "demogame.benchmark_report"\n'
        )
        assert omni026.check(ctx) is True

    def test_omni026_does_not_fire_on_single_string(self):
        """场景15: FORMAT_IN = 'demogame.foo' 单字符串 → OMNI-026 不触发"""
        omni026 = self._get_rule("OMNI-026")
        ctx = self._ctx(
            "class MyRouter(Router):\n"
            '    FORMAT_IN = "demogame.foo"\n'
            '    FORMAT_OUT = "demogame.bar"\n'
        )
        assert omni026.check(ctx) is False

    def test_omni026_message_mentions_composite_format(self):
        """OMNI-026 的 message 应包含 composite Format 的说明"""
        omni026 = self._get_rule("OMNI-026")
        msg = omni026.message_template
        assert "composite Format" in msg or "AnchorSpec" in msg, (
            f"OMNI-026 message should mention correct alternatives, got: {msg!r}"
        )

    def test_omni025_fires_on_fstring_format_in(self):
        """OMNI-025: FORMAT_IN = f"..." → 触发"""
        omni025 = self._get_rule("OMNI-025")
        ctx = self._ctx(
            'DOMAIN = "unity-qa"\n'
            "class ParseIssueRouter(Router):\n"
            '    FORMAT_IN = f"{DOMAIN}.fix-input"\n'
            '    FORMAT_OUT = f"{DOMAIN}.fix-context"\n'
        )
        assert omni025.check(ctx) is True

    def test_omni025_does_not_fire_on_literal(self):
        """OMNI-025: FORMAT_IN 是字面量 → 不触发"""
        omni025 = self._get_rule("OMNI-025")
        ctx = self._ctx(
            "class ParseIssueRouter(Router):\n"
            '    FORMAT_IN = "unity-qa.fix-input"\n'
            '    FORMAT_OUT = "unity-qa.fix-context"\n'
        )
        assert omni025.check(ctx) is False
