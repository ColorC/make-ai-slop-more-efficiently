---
name: agent
description: omnicompany AgentNodeLoop 现代版 - Router 化 6 子节点 (PromptBuilder/ContextCompact/LLMCall/ToolDispatch/SingleTool/ExtractResult), 必接 bus 全程 trace_id 贯穿审计.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/agent ts=2026-05-04T15:10:00Z type=doc status=active agent=ai-ide belongs_to_service=agent -->
<!-- [OMNI] summary="agent 操作手册 — 怎么继承 AgentNodeLoop 建业务 agent + 12 Worker 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. agent 的 SKILL 主要是'怎么用 AgentNodeLoop 建业务 agent' 工程指引" -->
<!-- [OMNI] tags=skill,agent,how-to,core,agent-loop -->
<!-- [OMNI] material_id="material:services._core.agent.skill.operations_manual.md"-->

# agent · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想建新业务 agent (Worker 调 LLM + 工具循环)
- 想从旧 `runtime.agent.AgentNodeLoop` 子类迁过来 (阶段 C)
- 想给已有业务 agent 加新 SingleToolRouter 子类 (新工具)
- 想看现成 agent 调用样例

**不用我**:
- 跨 Team 协作 → 走 [omnicompany MaterialDispatcher](../omnicompany/SKILL.md), 不在单 agent loop 内
- 业务逻辑实现 → 各业务 Team 自己 (例 hypothesis / [docauthor](../../_authoring/docauthor/) / demogame_kb_storywiki)
- 旧 `runtime/agent` 直接用 → 已 deprecated, 用本包的新接口

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (LLMCallRouter 调 qwen-3.6-plus)
- 必接 `bus` (SQLiteBus 或 MemoryBus), 不接抛 `RuntimeError` (D2)
- 业务子类需在 `PromptBuilderRouter.build_initial_messages()` 注入身份锚 (D4)

## 操作步骤

### 场景 A · 建新业务 agent (继承 AgentNodeLoop)

```python
from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.bus import SQLiteBus

class MyBusinessAgent(AgentNodeLoop):
    """业务 agent 例子."""

    DESCRIPTION = "..."

    # override prompt 装配 (注入业务身份锚)
    def build_prompt_router(self, bus, **kwargs):
        return MyPromptBuilderRouter(bus=bus, ...)


class MyPromptBuilderRouter(PromptBuilderRouter):
    def build_initial_messages(self, input_data, context):
        # 注入业务身份锚 (D4)
        return [
            {"role": "system", "content": "你是 my_business agent, 做 ..."},
            {"role": "user", "content": input_data["..."]},
        ]


# 跑
bus = SQLiteBus("data/agent_runs.db")
agent = MyBusinessAgent(bus=bus, max_turns=200)
result = agent.run({"input": "..."})
```

### 场景 B · 加新 SingleToolRouter 子类 (业务工具)

```python
from omnicompany.packages.services._core.agent.routers.single_tool import SingleToolRouter

class MyBusinessToolRouter(SingleToolRouter):
    """业务工具例子, 例如调外部 API."""

    TOOL_NAME = "my_business_tool"
    DESCRIPTION = "..."
    PARAMETERS = {"foo": "string", "bar": "int"}

    def execute(self, params, context):
        # context["permission_mode"] 已通过 SingleTool 基类的权限门
        return {"result": ..., "raw": ...}
```

注册 (在业务 agent 的 `TOOL_ROUTERS` 里加):

```python
class MyBusinessAgent(AgentNodeLoop):
    TOOL_ROUTERS = [
        # 内置 5 个 SingleTool 子类 (Glob/Grep/ReadFile/ListDir/Finish) 已经默认带
        # 加业务工具
        MyBusinessToolRouter,
    ]
```

### 场景 C · 用 BashRouter 加 bash 工具白名单

```python
from omnicompany.packages.services._core.agent.routers.bash import BashRouter

class MyBashRouter(BashRouter):
    """业务 bash 工具白名单子类."""

    def _validate_command(self, cmd: str) -> bool:
        # 业务白名单: 只允许 git / python
        allowed_prefixes = ("git ", "python ")
        return cmd.startswith(allowed_prefixes)
```

### 场景 D · 看 SQLite bus 里 agent run 的 trace

```bash
omni traces                         # 列最近 traces
omni trace-view <trace_id>          # 看具体 trace
```

每个 Router 调用都发 input/output 事件, trace_id 贯穿. 出问题查 `data/agent_runs.db` 的 trace 看每步 Router input/output.

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `from ... import AgentNodeLoop` | 继承建业务 agent | 见 [loop.py](loop.py) |
| `from ... import PromptBuilderRouter` (基类) | 业务 prompt override | 见 [routers/prompt_builder.py](routers/prompt_builder.py) |
| `from ... import SingleToolRouter` (基类) | 业务工具子类 | 见 [routers/single_tool.py](routers/single_tool.py) |
| `from ... import BashRouter` (基类) | 业务 bash 工具 | 见 [routers/bash.py](routers/bash.py) |
| `omni traces` / `omni trace-view <id>` | 看 trace | (顶层命令) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| 构造 Router 报 `RuntimeError(bus 必传)` | D2 硬校验, bus=None 不接 | 必传 SQLiteBus 或 MemoryBus, 不绕 |
| 跑完 trace 找不到 | `omni traces` 没找到 | 检查 bus 是否真传了 SQLiteBus, MemoryBus 不持久 |
| LLM 调用 max_turns 跑满 | 业务 prompt 没引导 LLM 终止 | 加"完成时调 finish 工具"提示, 或调高 max_turns |
| 业务工具报权限 deny | `context["permission_mode"]=auto` 但工具不在白名单 | 改 permission_mode 或加业务工具到白名单 |
| 上下文一直增长内存爆 | L4 LLM 摘要压缩还没实装 (局限 2) | L1-L3 截断够用, 调 max_context_chars 缩小窗口 |
| BashRouter 报命令拒绝 | 业务子类 `_validate_command` 默认严 | override 加业务白名单 |
| 旧 `runtime.agent.AgentNodeLoop` 子类报 deprecated warning | 阶段 C 迁移未完成 (局限 1) | 等阶段 C 迁过来, 或暂忍 warning |
| 想细粒度工具权限 (按工具控制) | 当前 default/auto 粒度粗 (局限 3) | 当前局限, 在业务 agent 内 override 工具调用前自检 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D6 决策 / 6 Router 数据流) → [DESIGN.md](DESIGN.md)
- AGENT-NODE-LOOP-ROUTERIZATION 计划 → [docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md](../../../../../docs/plans/%5B2026-04-18%5DAGENT-NODE-LOOP-ROUTERIZATION/plan.md)
- 旧 runtime/agent (待迁) → [../../../runtime/agent/](../../../../runtime/agent/)
- 调用样例 hypothesis → ../../_diagnosis/hypothesis/routers.py
- Worker 设计单 R-19/R-20 Agent Worker 三件套 → [docs/standards/worker.md](../../../../../../docs/standards/concepts/worker.md)
