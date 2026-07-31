# [OMNI] origin=claude-code purpose=experiment ts=2026-04-15
"""实验 D: REQUIRED_CONTEXT 事前拦截验证.

构造一个 Router 声明 REQUIRED_CONTEXT=['foo','bar.baz'], 分别以
  (a) 齐全 input    → 应 PASS
  (b) 缺 foo       → 应 FAIL(M4 拦截)
  (c) bar.baz 为空  → 应 FAIL
  (d) 缺 bar       → 应 FAIL
跑一条最简管线, 检验 runner 的事前拦截逻辑.
"""
import asyncio
import sys

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")

from omnicompany.protocol.anchor import (
    AnchorSpec, Verdict, VerdictKind, ValidatorSpec, ValidatorKind, Route, RouteAction,
)
from omnicompany.protocol.pipeline import NodeKind, NodeMaturity, PipelineEdge, PipelineNode, PipelineSpec
from omnicompany.runtime.exec.runner import PipelineRunner
from omnicompany.runtime.routing.router import Router


class CaptureRouter(Router):
    """声明 REQUIRED_CONTEXT; 若 run 被调到说明 M4 未拦截."""
    DESCRIPTION = "test: required context capture"
    FORMAT_IN = "test.input"
    FORMAT_OUT = "test.output"
    REQUIRED_CONTEXT = ["foo", "bar.baz"]

    def run(self, input_data):
        return Verdict(
            kind=VerdictKind.PASS,
            output={"reached_run": True, "data": input_data},
            confidence=1.0,
            diagnosis="CaptureRouter PASS",
        )


def _build_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id="test-required-ctx",
        name="test-required-ctx",
        description="M4 REQUIRED_CONTEXT 测试",
        entry="capture",
        nodes=[
            PipelineNode(
                id="capture",
                kind=NodeKind.ANCHOR,
                anchor=AnchorSpec(
                    id="capture",
                    name="capture",
                    format_in="test.input",
                    format_out="test.output",
                    validator=ValidatorSpec(id="v", kind=ValidatorKind.HARD, description="..."),
                    routes={
                        VerdictKind.PASS: Route(action=RouteAction.EMIT),
                        VerdictKind.FAIL: Route(action=RouteAction.HALT),
                    },
                ),
                maturity=NodeMaturity.GROWING,
            ),
        ],
        edges=[],
    )


async def _run_case(name: str, input_data: dict) -> dict:
    pipeline = _build_pipeline()
    from omnicompany.bus.sqlite import SQLiteBus
    bus = SQLiteBus()  # 默认路径
    await bus.connect()
    runner = PipelineRunner(
        pipeline=pipeline,
        bindings={"capture": CaptureRouter()},
        bus=bus,
    )
    try:
        result = await runner.run(input_data)
    except Exception as e:
        return {"case": name, "raised": True, "err": f"{type(e).__name__}: {e}"}
    # runner.run 返回 final output or halts with RuntimeError on FAIL
    return {"case": name, "raised": False, "result_preview": str(result)[:200]}


async def main():
    cases = [
        ("(a) 齐全", {"foo": 1, "bar": {"baz": "y"}}),
        ("(b) 缺 foo", {"bar": {"baz": "y"}}),
        ("(c) bar.baz 为空", {"foo": 1, "bar": {"baz": ""}}),
        ("(d) 缺 bar", {"foo": 1}),
    ]
    print("=== 实验 D: REQUIRED_CONTEXT 事前拦截 ===\n")
    for name, inp in cases:
        r = await _run_case(name, inp)
        if r["raised"]:
            print(f"  {name:20s} → RAISED: {r['err'][:160]}")
        else:
            print(f"  {name:20s} → OK: {r['result_preview']}")


asyncio.run(main())
