# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T02:00:00Z type=router status=skeleton agent=ai-ide
# [OMNI] summary="据 facility_scanner 缺失项, 产 pytest skeleton — 给一个无测试的 team 自动生成 test_*.py 骨架"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 8 + 用户 plan §一第 7 条 '根据健康性假设再去创建用于诊断的 agent 或者 worker'. 这是诊断器构建器最小实现"
# [OMNI] tags=builder,pytest-skeleton,objective,no-llm
# [OMNI] material_id="material:diagnosis.doctor.builders.pytest_skeleton_builder.skeleton.py"
"""Pytest skeleton 构建器.

不用 LLM. 据 facility_scanner 跑出的覆盖矩阵, 给一个 team 自动产 pytest test 骨架.

V0 行为:
- 输入: team 路径 + 缺失 Material id 列表
- 工作: 扫 team 的 worker 类, 给每个 Material id 产一份 test_*.py 文件 (包含红绿样本断言 skeleton)
- 输出: skeleton 字符串 + 落档建议路径 (默认 <team>/tests/test_<material_safe>.py)
- 不直接落盘 (写盘走调用方决定, 避免污染 src)

每份 skeleton 含:
- TODO 注释告知开发者填什么
- 一个 test_<material>_green 函数 skeleton (合规 input → 期望产物)
- 一个 test_<material>_red 函数 skeleton (违规 input → 期望失败 / FAIL)
- 一个 test_<material>_discrimination 函数 skeleton (红绿对比 — 真有判别力)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PytestSkeleton:
    target_path: str         # 建议落档路径 (相对项目根)
    content: str             # 文件内容
    material_id: str         # 跟哪条 Material 对应
    rationale: str           # 为什么产这份 skeleton


@dataclass
class PytestSkeletonBuildResult:
    team_path: str
    skeletons: list[PytestSkeleton] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _safe_name(material_id: str) -> str:
    """material_id (含 .) → 安全 python 标识符."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", material_id)


class PytestSkeletonBuilder:
    """据 team 的 Material 跟覆盖矩阵, 产 pytest skeleton 文件."""

    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root)

    def build(self, team_path: str, uncovered_material_ids: list[str]) -> PytestSkeletonBuildResult:
        result = PytestSkeletonBuildResult(team_path=team_path)
        if not uncovered_material_ids:
            result.notes.append("无 uncovered material, 不需产 skeleton")
            return result

        team_dir = self.project_root / team_path
        if not team_dir.is_dir():
            result.notes.append(f"team_path 不存在: {team_dir}")
            return result

        for mid in uncovered_material_ids:
            safe = _safe_name(mid)
            target = f"{team_path.rstrip('/')}/tests/test_{safe}.py"
            content = self._gen_skeleton(team_path=team_path, material_id=mid)
            result.skeletons.append(PytestSkeleton(
                target_path=target,
                content=content,
                material_id=mid,
                rationale=f"facility_scanner 找出 {mid} 0 测试覆盖, 产 pytest 红绿对比 skeleton",
            ))

        return result

    def _gen_skeleton(self, team_path: str, material_id: str) -> str:
        safe = _safe_name(material_id)
        return f'''# [OMNI] origin=doctor.builder domain={team_path}/tests ts=2026-05-07 type=test status=skeleton agent=doctor.pytest_builder
# [OMNI] summary="自动产 pytest skeleton for {material_id} — 来自 doctor builder, 待填实"
# [OMNI] why="facility_scanner 跑出 {material_id} 0 测试覆盖, doctor 构建器自动产红绿对比 skeleton 让开发者填实"
# [OMNI] tags=test,pytest,auto-generated,skeleton,red-green-baseline
# [OMNI] material_id="material:tests.{safe}.auto_skeleton"

"""自动产 pytest skeleton for Material `{material_id}`.

⚠️ 这是 doctor builder 自动产的 skeleton, 需要开发者填实.

按用户铁律 feedback_connected_is_not_discriminating + feedback_validation_calibration_red_green_gradient,
跑过红绿对比 PASS 才算真接通.

待填:
- test_{safe}_green: 合规 input → 期望产物存在 + 字段齐
- test_{safe}_red: 违规 input → 期望 FAIL / 报错
- test_{safe}_discrimination: 红绿数 / 字段差异真有区分

填完后删本注释段 + 改 status=active.
"""
import pytest


@pytest.fixture
def green_input_for_{safe}():
    """TODO: 给 {material_id} 一份合规 input."""
    raise NotImplementedError("填合规 input fixture")


@pytest.fixture
def red_input_for_{safe}():
    """TODO: 给 {material_id} 一份违规 input (故意触发某条规范违反)."""
    raise NotImplementedError("填违规 input fixture")


def test_{safe}_green(green_input_for_{safe}):
    """绿样本: 合规 input 应产正常 {material_id} 实例."""
    # TODO: 调实际产 Material 的 worker / agent, 拿 green_input_for_{safe} 跑
    # 断言产物字段齐 + 内容符合预期
    pytest.skip("skeleton 待填: 调真 worker 跑 green_input + 断言产物")


def test_{safe}_red(red_input_for_{safe}):
    """红样本: 违规 input 应产 FAIL 或对应报错."""
    # TODO: 调实际产 Material 的 worker / agent, 拿 red_input_for_{safe} 跑
    # 断言产 FAIL verdict 或抛指定异常
    pytest.skip("skeleton 待填: 调真 worker 跑 red_input + 断言 FAIL")


def test_{safe}_discrimination(green_input_for_{safe}, red_input_for_{safe}):
    """红绿对比: 真有判别力 — 同 worker/agent 喂红 input vs 绿 input 应产明显不同结果."""
    # TODO: 同一调用喂 green vs red, 比对 verdict.kind / output / finding 数 等差异
    pytest.skip("skeleton 待填: 红绿对比断言")
'''


def build_pytest_skeleton_for_team(team_path: str, uncovered_material_ids: list[str], project_root: Path | str | None = None) -> dict:
    """便捷 API."""
    from dataclasses import asdict
    if project_root is None:
        here = Path(__file__).resolve()
        for p in (here, *here.parents):
            if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
                project_root = p
                break
        if project_root is None:
            project_root = here.parents[6]
    builder = PytestSkeletonBuilder(project_root=project_root)
    result = builder.build(team_path, uncovered_material_ids)
    return asdict(result)
