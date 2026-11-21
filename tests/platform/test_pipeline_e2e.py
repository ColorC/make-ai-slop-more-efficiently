# [OMNI] origin=claude-code domain=tests ts=2026-06-21T00:00:00Z type=test status=active
"""fake-client e2e —— 用真实执行机器(PipelineRunner + Bus + 路由)跑完一条管线, 但把
LLM 路由换成 mock 路由(返回预设 Verdict, 即"假客户端")。全离线, 不打任何 LLM API。

验证此前只验"管线能搭起来"没验"管线真能跑完一遍"的接缝: 节点派发、Verdict 流转、
数据沿 intake→process 端到端传递。用发布的 example_domain 管线当被测拓扑。
"""

from __future__ import annotations

import os

os.environ.setdefault("OMNICOMPANY_SKIP_GUARDIAN_PRECHECK", "1")

import tempfile
from pathlib import Path

import pytest

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.domains.example_domain.team import build_example_pipeline
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.exec.runner import PipelineRunner
from omnicompany.runtime.routing.router import Router


class FakeIntake(Router):
    """假客户端: 替代 intake 节点(本应可能调 LLM), 返回预设结果。"""

    FORMAT_IN = "example.request"
    FORMAT_OUT = "example.intake"

    def run(self, data):
        data["normalized"] = True
        return Verdict(kind=VerdictKind.PASS, output=data)


class FakeProcess(Router):
    """假客户端: 替代 process 节点, 把输入加工成产物。"""

    FORMAT_IN = "example.intake"
    FORMAT_OUT = "example.result"

    def run(self, data):
        data["result"] = "done"
        return Verdict(kind=VerdictKind.PASS, output=data)


@pytest.mark.asyncio
async def test_pipeline_runs_end_to_end_with_fake_routers():
    spec = build_example_pipeline()
    bindings = {"intake": FakeIntake(), "process": FakeProcess()}
    with tempfile.TemporaryDirectory() as tmp:
        async with SQLiteBus(Path(tmp) / "e2e.db") as bus:
            runner = PipelineRunner(spec, bindings, bus, source="e2e-test", max_steps=10)
            out = await runner.run({"topic": "hello e2e"})
    # 数据沿 intake→process 端到端流通
    assert out.get("normalized") is True, f"intake 没跑到: {out}"
    assert out.get("result") == "done", f"process 没跑到: {out}"
