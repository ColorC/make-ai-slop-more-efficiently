# [OMNI] origin=claude-code domain=omnicompany/omnicompany ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:omnicompany.worker_base.semantic_aliases.py"
"""omnicompany 层的 Worker 基类 + Material / Team 语义别名.

**关键认知 (2026-04-20 用户洞察)**:
  规范文档说 "Router 读作 Worker" 不够 — 其他 agent 看到的是 import / 字段 / docstring.
  所以在 omnicompany 层提供真正的 Worker 基类 + Material / Team 别名,
  让新 Team 代码第一眼就是 omnicompany 词汇.

**用法 (推荐新代码统一用法)**:

    from omnicompany.packages.services._core.omnicompany import Worker, Material, Team
    from omnicompany.protocol.anchor import Verdict, VerdictKind

    class MyWorker(Worker):
        DESCRIPTION = "业务描述 (≥ 20 字符)"
        FORMAT_IN = "myteam.input"                # 订阅的 Material id
        # FORMAT_IN_MODE = "or"                   # list[str] 时必填 (R-24)
        FORMAT_OUT = "myteam.output"              # 产出的 Material id (单 Format)

        def run(self, input_data: dict) -> Verdict:
            # input_data[FORMAT_IN_id] = 上游 Material payload 本体 (平铺字段)
            req = input_data.get("myteam.input", {})
            result = do_work(req)
            return Verdict(
                kind=VerdictKind.PASS,
                output={"field1": ..., "field2": ...},   # 平铺, 非嵌套 (R-23)
                # 可选 output["_emit_as_new_job"] = True → 子 job (R-25)
            )

**对应关系 (terminology §6)**:

    protocol 层 (代码契约, 核心层正名)  omnicompany 层 (业务叙述)
    ──────────────────────────────────  ──────────────────────────────
    Router                              Worker          ← 本基类
    Format                              Material        ← 本模块 alias
    TeamSpec  (原 TeamSpec)         Team            ← 本模块 alias
    Material authority / run ledger     Stock           ← EventBus 仅审计投影
    FactoryEvent                        Material (实例) ← payload 层
"""
from __future__ import annotations

from omnicompany.protocol.format import Format as _Format
from omnicompany.protocol.team import TeamSpec as _TeamSpec
from omnicompany.runtime.routing.router import Router as _Router


class Worker(_Router):
    """omnicompany Worker 基类 — protocol 层等同于 Router.

    继承此类 (而非直接继承 Router) 以表明:
    - 这是 omnicompany 层的 Worker (面向 Material 订阅激活的 bus 驱动模式)
    - 支持 Phase 1 新约定: FORMAT_IN_MODE (R-24) + _emit_as_new_job (R-25) +
      平铺 verdict.output (R-23)

    类属性约定 (所有都是 Worker 子类的类级声明):

    DESCRIPTION : str
        人类可读描述, ≥ 20 字符 (R-01)

    FORMAT_IN : str | list[str]
        订阅的 Material id.
        - str: 单 Material 订阅 (常见)
        - list[str]: 多 Material 订阅, 必填 FORMAT_IN_MODE (R-24)

    FORMAT_IN_MODE : str  (仅 FORMAT_IN = list[str] 时有效)
        - "and" (默认 · composite fan-in): 所有 Material 到齐才激活
        - "or" (alternative): 任一 Material 到达即激活 (Agent Team ContextScript 用法)

    FORMAT_OUT : str
        产出的 Material id. 单 Format (Protocol 层 FORMAT_OUT: str 硬约定).
        多 Format 产出 → 应拆多个 Worker.

    run() 约定:
        input_data[FORMAT_IN_id] = 上游产出的 Material payload 本体 (平铺字段)
        返回 Verdict(output=平铺字段 dict) — **非嵌套** (R-23)
        output["_emit_as_new_job"] = True → 触发子 job 用新 trace_id (R-25)

    向后兼容:
        旧代码 class FooRouter(Router) 无需改继承 — Worker 只是为新代码提供
        清晰的 omnicompany 层入口. 两者语义等价.
    """

    #: list[str] 订阅的 AND/OR 语义声明. R-24 硬规则: list[str] 时必填.
    #: 单 FORMAT_IN (str) 时此属性不起作用.
    FORMAT_IN_MODE: str = "and"


# ══════════════════════════════════════════════════════════════════════
# omnicompany 层 Material / Team 语义别名
# ══════════════════════════════════════════════════════════════════════
#
# protocol 层类名保留 (Format / TeamSpec), 新代码用别名引用以对齐命名.
# 不是单独的类, 就是 alias — 等价性 100%.

Material = _Format
"""omnicompany 层 Material = protocol 层 Format (alias, 完全等价).

F-19: Material 必须通过 tags 声明 kind.source / kind.internal / kind.sink 之一.

用法:
    from omnicompany.packages.services._core.omnicompany import Material

    MY_MATERIAL = Material(
        id="myteam.data.request",
        name="...",
        description="...",
        tags=["myteam", "kind.source"],   # ← kind 必填 (F-19)
        ...
    )
"""


Team = _TeamSpec
"""omnicompany 层 Team = protocol 层 TeamSpec (alias).

Team 是一组 Worker 通过 Material 订阅协作完成 Job 的组织单位.

用法:
    from omnicompany.packages.services._core.omnicompany import Team

    def build_team() -> Team:
        return Team(
            id="myteam",
            name="...",
            nodes=[...],       # list[TeamNode], Worker 集合
            edges=[...],       # 可选 (黑板模式下由订阅图自动推导)
            entry="...",
        )
"""


__all__ = ["Worker", "Material", "Team"]
