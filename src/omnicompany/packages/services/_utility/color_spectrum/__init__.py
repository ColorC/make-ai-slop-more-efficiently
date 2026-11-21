# [OMNI] origin=claude-code domain=services/color_spectrum ts=2026-04-25T05:00:00Z type=service status=active
# [OMNI] material_id="material:utility.color_spectrum.spectrum_runner.aggregator.py"
"""color_spectrum — 跨域共用 · 测试判别力色谱核心组件 (2026-04-25 立).

立题: voxelcraft 路径 mechanism / item / entity 都需要"色谱测试" — 验证 probe 在
不同错误程度的 stub 上能给出**单调对应**的判决 (浅错给浅判 / 深错给深判 / 完全对给 PASS).

之前 mechanism 路径手工写了 5 档色谱脚本 (`scripts/voxelcraft_tier3_color_spectrum_e2e.py`),
但每次新增 probe 都要重写一份, 这是用户 2026-04-25 命定的"高频操作应入核心层共用".

本模块提供:
- `ColorStage` / `StageResult` / `SpectrumReport`: 数据结构
- `run_color_spectrum(stages, probe_runner)`: 跑色谱
- `run_meta_spectrum(probe_factory)`: 元色谱 — 验证 ColorSpectrumChecker 自己
  (喂"故意无判别力的 probe", 看能不能抓色谱崩塌)

设计哲学:
- 通用: 不假设 MC / Java / 任何具体 domain. caller 给 probe_runner, 我们对照判决
- 元层: 测试质量本身可被测试 (用户原话 "色谱的色谱")
- 实战: 简单先用, 后续按需加 (并发/重跑/精度等)

参考:
- 立档 plan: docs/plans/[2026-04-25]CORE-FOUNDATIONS-FROM-N1A-FEEDBACK/plan.md §四
- 范例使用: scripts/voxelcraft_tier3_color_spectrum_e2e.py 改造为消费此模块 (B 阶段后续)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ColorStage:
    """色谱中一档 stub.

    name: 唯一标识 (如 "stage_2_not_registered")
    description: 人话描述
    expected_verdict: 期望 probe 判出的 verdict (字符串, 与 actual 比对)
    setup: (可选) 跑前钩子 — 一般用于部署 stub 到环境
    teardown: (可选) 跑后钩子 — 清理
    """
    name: str
    description: str
    expected_verdict: str
    setup: Callable[["ColorStage"], None] | None = None
    teardown: Callable[["ColorStage"], None] | None = None


@dataclass
class StageResult:
    stage: str
    expected: str
    actual: str
    passed: bool  # actual == expected
    raw: Any = None  # full probe output for debug


@dataclass
class SpectrumReport:
    stages: list[StageResult]
    monotone_count: int
    total: int

    @property
    def misordered(self) -> list[StageResult]:
        return [r for r in self.stages if not r.passed]

    @property
    def fully_monotone(self) -> bool:
        # 空色谱不算 fully (没跑等于没证明)
        return self.total > 0 and self.monotone_count == self.total

    @property
    def monotone_score(self) -> float:
        return self.monotone_count / self.total if self.total > 0 else 0.0


def run_color_spectrum(
    stages: list[ColorStage],
    probe_runner: Callable[[ColorStage], str],
    *,
    stop_on_fail: bool = False,
) -> SpectrumReport:
    """跑每档 stage, 让 probe_runner 跑 + 拿 verdict, 对比期望.

    Args:
        stages: 色谱档位列表
        probe_runner: callable(stage) -> 实际 verdict 字符串
        stop_on_fail: True 则首档失序就停 (调试用); False 跑全, 报全部 misordered

    Returns:
        SpectrumReport
    """
    if not stages:
        return SpectrumReport(stages=[], monotone_count=0, total=0)

    results: list[StageResult] = []
    for stage in stages:
        if stage.setup:
            try:
                stage.setup(stage)
            except Exception as e:
                results.append(StageResult(
                    stage=stage.name,
                    expected=stage.expected_verdict,
                    actual=f"<setup_error: {type(e).__name__}: {e}>",
                    passed=False,
                ))
                if stop_on_fail:
                    break
                continue

        try:
            actual = probe_runner(stage)
        except Exception as e:
            actual = f"<probe_exception: {type(e).__name__}: {e}>"

        passed = (actual == stage.expected_verdict)
        result = StageResult(
            stage=stage.name,
            expected=stage.expected_verdict,
            actual=actual,
            passed=passed,
        )
        results.append(result)

        if stage.teardown:
            try:
                stage.teardown(stage)
            except Exception as e:
                # teardown error 不影响 stage result, 只记
                print(f"[color_spectrum] teardown {stage.name} err: {e}")

        if stop_on_fail and not passed:
            break

    return SpectrumReport(
        stages=results,
        monotone_count=sum(1 for r in results if r.passed),
        total=len(results),
    )


# ══════════════════════════════════════════════════════════════════
# 元色谱 · "色谱的色谱"
# ══════════════════════════════════════════════════════════════════


# 一组标准简化色谱, 用于元层测试. 通用结构: 1 档 fail-expected + 1 档 pass-expected
_META_STAGES = [
    ColorStage("red", "故意红 (期望 fail)", expected_verdict="fail"),
    ColorStage("green", "故意绿 (期望 pass)", expected_verdict="pass"),
]


def run_meta_spectrum(
    probe_factories: dict[str, Callable[[ColorStage], str]] | None = None,
) -> dict:
    """元色谱: 喂"故意有/无判别力的 probe", 验证 ColorSpectrumChecker 能区分.

    默认提供 3 个 probe quality:
    - "good": 能正确区分 red→fail green→pass
    - "always_pass": 永远返 "pass" (色谱崩塌, 不该判别)
    - "always_fail": 永远返 "fail" (色谱崩塌, 反向)

    Args:
        probe_factories: dict {name: probe_runner}, 缺省用内置 3 种

    Returns:
        dict {meta_passed: bool, details: {<name>: {fully_monotone, misordered_count}}}

    期望:
    - good: fully_monotone = True
    - always_pass: misordered_count == 1 (red 失序)
    - always_fail: misordered_count == 1 (green 失序)
    """
    if probe_factories is None:
        probe_factories = {
            "good": _good_probe,
            "always_pass": _always_pass_probe,
            "always_fail": _always_fail_probe,
        }

    details = {}
    for name, probe in probe_factories.items():
        report = run_color_spectrum(_META_STAGES, probe)
        details[name] = {
            "fully_monotone": report.fully_monotone,
            "misordered_count": len(report.misordered),
            "monotone_score": report.monotone_score,
        }

    # 默认期望: good 全过 / always_pass 1 misordered / always_fail 1 misordered
    meta_passed = True
    if "good" in details:
        meta_passed = meta_passed and details["good"]["fully_monotone"]
    if "always_pass" in details:
        meta_passed = meta_passed and details["always_pass"]["misordered_count"] == 1
    if "always_fail" in details:
        meta_passed = meta_passed and details["always_fail"]["misordered_count"] == 1

    return {"meta_passed": meta_passed, "details": details}


def _good_probe(stage: ColorStage) -> str:
    """正确判别 probe: red 返 fail, green 返 pass, 其它返 'unknown'."""
    if "red" in stage.name.lower() or stage.expected_verdict == "fail":
        return "fail"
    return "pass"


def _always_pass_probe(stage: ColorStage) -> str:
    return "pass"


def _always_fail_probe(stage: ColorStage) -> str:
    return "fail"


__all__ = [
    "ColorStage",
    "StageResult",
    "SpectrumReport",
    "run_color_spectrum",
    "run_meta_spectrum",
]
