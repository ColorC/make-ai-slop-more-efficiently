<!-- [OMNI] origin=ai-ide domain=omnicompany/standards ts=2026-05-02T06:00:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="LLM 基础设施现状盘点 + 入口约定 - omni llm CLI / LLMClient / LLMCallRouter / dashboard API" -->
<!-- [OMNI] why="用户原始需求 3.5: LLM 基础设施夹实. 本规范盘点已有设施 + 入口约定 + 演进路线" -->
<!-- [OMNI] tags=cli,llm,infrastructure,standard -->
<!-- [OMNI] material_id="material:standards.llm_infrastructure.spec.md" -->

# LLM 基础设施

> **状态**: 现状盘点 + 规范 v2 (2026-06-13)
> **关联实装**: `runtime/llm/` + `services/_core/agent/routers/llm_call.py` + `services/_core/omnicompany/llm_client.py` + `cli/commands/llm_audit.py` + `dashboard/llm_api.py`
> **统一权威**: authority-confirmation.md 定方向, autonomous-execution-rules.md 定长程门禁; 本文件只记录 LLM 设施分散入口, 不另立第二套权威。

## 一、 这层是干嘛的

omnicompany 内部所有 LLM 调用走统一接口, 自动落审计记录. 业务侧不直接 import openai / anthropic SDK, 走本地包装层.

设计目标:
- 单一入口: 底层 `LLMClient`, 单次结构化 `runtime/llm/structured.py::call_json`, 批量 `runtime/llm/batch.py`, 多轮 agent `LLMCallRouter/AgentNodeLoop`
- 强制审计: 每次调用落 `data/_runtime/llm_audit/<YYYY-MM-DD>/<trace_id>.jsonl`
- 跨厂支持: qwen / deepseek / claude 等多厂商 LLM 走同一接口
- 跟 G1 联动: 调用必带 trace_id, 跟 session 身份链关联
- 跟 dashboard 联动: 调用记录 + 成本统计 web 端可看

## 二、 现有设施

### Python 接口层 (3 个)

| 接口 | 位置 | 用途 |
|---|---|---|
| `LLMClient` | `runtime/llm/llm.py` | 底层 client + ModelRegistry, 只在基础设施层直接用 |
| `call_json()` | `runtime/llm/structured.py` | 唯一单次结构化 JSON 调用面, schema 校验 + 纠错重试 |
| `BatchLLMRunner` | `runtime/llm/batch.py` | 唯一批量 LLM 执行面, 并发 / 失败隔离 / 中间产物 / 续跑 |
| `LLMCallRouter` | `services/_core/agent/routers/llm_call.py:107` | Router 化的 LLM 调用, 走 EventBus 落 agent.llm-request / agent.llm-response 事件 |

业务侧推荐: 单次固定 schema 走 `runtime/llm/structured.py::call_json`; 批量跑同类单次任务走 `runtime/llm/batch.py`; 多轮工具循环走 `LLMCallRouter/AgentNodeLoop`. 直接用 `LLMClient` 仅限底层设施. 业务**不直接 import openai/anthropic**, 也不再新建部门私有 `call_json` / 线程池 / 续跑轮子.

### 配套子模块

| 子模块 | 位置 | 用途 |
|---|---|---|
| `compression_summary.py` | `runtime/llm/` | 上下文压缩 (跟 ContextCompactRouter 联动) |
| `embedding_client.py` | `runtime/llm/` | 向量化 |
| `vision.py` | `runtime/llm/` | 视觉 LLM (qwen3-vl-flash 等) |

### 审计层

每次 LLM 调用 (走任一上述接口) 落审计记录到:

```
data/_runtime/llm_audit/<YYYY-MM-DD>/<trace_id>.jsonl
```

字段含: `trace_id` / `pipeline` / `node` / `model` / `prompt` / `response` / `ts` / `duration_ms` 等.

跟 G1 身份链联动: `trace_id` 跟 `cc_session_active.json` 一致, 反查从 session → LLM 调用历史.

### CLI

| 命令 | 用途 |
|---|---|
| `omni llm audit` | 调用记录列表查询 (按 trace_id / pipeline / node / grep / since 过滤) |
| `omni llm audit --trace-id=<>` | 单 session 全部 LLM 调用 |
| `omni pipeline-llm` | (扩展) pipeline 级 LLM 统计 |

### dashboard API (本规范同期加, 2026-05-02)

| 端点 | 内容 |
|---|---|
| `GET /api/v2/llm/audit` | 调用记录列表 (跟 omni llm audit 同源) |
| `GET /api/v2/llm/audit/{trace_id}` | 单 trace_id 全部 LLM 调用 |
| `GET /api/v2/llm/stats` | 调用统计 (count / by-model / by-pipeline / 字符总数) |

## 三、 调用约定 (业务侧)

### 推荐路径 (Router 化)

业务 worker 写 LLM 调用走 `LLMCallRouter` 子类:

```python
from omnicompany.packages.services._core.agent import LLMCallRouter

class MyLLMWorker(...):
    def build_llm_call(self, *, bus):
        return LLMCallRouter(
            model="qwen-3.6-plus",   # 铁律 1: 默认模型
            tools_spec=...,
            retry=...,
            bus=bus,
            caller_prefix=type(self).__name__,
        )
```

简单一次性调用:

```python
from omnicompany.packages.services._core.omnicompany import call_llm_json

result = call_llm_json(
    prompt="...",
    schema={...},
    model="qwen-3.6-plus",
    trace_id=trace_id,    # 跟 G1 联动
)
```

### 模型铁律 (cross-project)

- **默认模型 `qwen-3.6-plus`** (CLAUDE.md 铁律 1)
- 别的模型要单独申请 + 跟用户报告
- visual 任务用 `qwen3-vl-flash`
- LLM 调用预算上限 `max_turns=1000` (铁律 B 预算宽松)
- LLM 调用必带 trace_id (没 trace_id → fallback 到 `cc_unknown_<ts>`, 但会 warn)

## 四、 跟主要设施联动

### 跟 G1 身份模块

LLM 调用 trace_id 来自当前 session (`current_session_meta()`). 一份 session → 多次 LLM 调用 → 一份 jsonl. dashboard `/api/v2/llm/audit/{trace_id}` 反查同 session 全部调用.

### 跟 G2 注册中心

LLM 调用本身不在注册中心 (太频繁). 但用 LLM 的 worker / agent 实体在注册中心, 通过 entity_id 反查"这个 worker 调过 LLM 多少次".

### 跟 G4 锁

LLM 调用不被锁拦 (锁拦的是文件写入, LLM 是函数调用). 但 LLM 产生的内容如果走 Edit/Write 落到 watched 路径, 仍然会被 G4 PreToolUse hook 拦截.

### 跟 cc_wrapper

cc_wrapper hooks/trace.py 已经把 Claude Code 的工具调用 (Edit/Write) 落 SQLite event bus. LLM 审计走另一份 jsonl (因为 LLM 调用频率高 + payload 大, 不进 SQLite). 两份审计源共用 trace_id 关联.

## 五、 反模式

**业务直接 import openai / anthropic** — 绕开本地包装层, 没 trace_id, 没审计, 跨厂切换困难. 走 `LLMCallRouter` / `call_llm_json`.

**LLM 调用不带 trace_id** — fallback `cc_unknown_<ts>`, 但反查时关联不到 session. 业务必显式传.

**用 `qwen-3.6-plus` 之外的模型不报告** — 铁律 1 违规. 用前显式跟用户拍板.

**多 LLM 客户端各跑一份审计** — 应该所有调用走同一个审计入口. 本规范的三个 Python 接口 (`LLMClient` / `call_llm_json` / `LLMCallRouter`) 都走同一份 audit_root.

**审计文件直接编辑** — 审计是只追加的事实记录. 不改不删 (除非归档过期文件).

## 六、 演进 (留下一阶段)

- **成本统计**: 现 stats 只算字符数, 应当跟厂商定价表联动算 USD/CNY 成本
- **prompt 模板管理**: prompt 散在各 worker 代码里 (跟 agent v2 把 prompt 提到 .md 文件类似), 应当所有业务 worker 的 prompt 统一移到 `<service>/prompts/<worker>.md`
- **retry / fallback 策略**: 现 `LLMCallRouter` 有 retry 配置, 但跨厂 fallback 没标准化 (例 qwen 挂自动切 deepseek)
- **rate limiting**: 当前没全局速率控制. agent loop 高并发时可能触发厂商 quota
- **embedding 索引**: `embedding_client.py` 在但没业务广泛用. 后续做 RAG / 知识库时启用
- **dashboard 显示**: 加 LLM 调用 timeline + 模型分布饼图 (现 stats API 已就绪, 等 frontend)

## 七、 实施引用

- `omnicompany/src/omnicompany/runtime/llm/llm.py` - LLMClient 底层
- `omnicompany/src/omnicompany/runtime/llm/compression_summary.py` - 上下文压缩
- `omnicompany/src/omnicompany/runtime/llm/embedding_client.py` - 向量化
- `omnicompany/src/omnicompany/runtime/llm/vision.py` - 视觉 LLM
- `omnicompany/src/omnicompany/packages/services/_core/omnicompany/llm_client.py` - call_llm_json 高层
- `omnicompany/src/omnicompany/packages/services/_core/agent/routers/llm_call.py` - LLMCallRouter
- `omnicompany/src/omnicompany/cli/commands/llm_audit.py` - omni llm audit CLI
- `omnicompany/src/omnicompany/dashboard/llm_api.py` - dashboard 只读 API (本规范同期加)
