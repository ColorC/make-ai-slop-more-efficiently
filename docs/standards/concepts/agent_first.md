# Agent-First · 方法论

> 2026-04-23 立档 · 由用户 (L1) 明示 · 属设计哲学规范
> 权威: 与 `pipeline.md` / `material.md` 并列
> 核心设施统一: authority-confirmation.md 定方向, autonomous-execution-rules.md 定长程门禁。agent 探针自由不等于入口自由; LLM/agent/EventBus 权威入口按该确认表执行。

---

## 一 · 核心陈述

**LLM 不擅长以第三人称评判管线的信息完整度和内容** (用户 2026-04-23 原话).

按"设想的工作流"去提前安排**通常会出谬误** — 设想者看不见 LLM 实际运行时看到什么, 也难凭空判断充分性.

正确范式:

```
  先搭完整 workspace (信息库, 宁滥毋缺, 确保无任何遗漏)
  → 让 agent 作探针, 建立运行档案
  → 运行一段时间后观测
  → 查看是否有提炼必要 (将确认有明确流程的工作固化或提前注入以节约 token)
```

---

## 二 · 四步法

### 2.1 Step 1 · 搭 workspace (完整信息库)

- 把所有**可能用到**的信息都放进去 — 宁滥毋缺
- workspace 范围由 package Workspace 声明, 不由 agent 自己扩 (见 `workspace.md`)
- 若 agent 反复找不到东西 → 补信息库, 不补 agent prompt
- 标准: 一个外部审阅者看 workspace 能 ≈100% 猜到 agent 要做什么

### 2.2 Step 2 · Agent 作探针

- agent 不是"执行管线的工人", 是"用来测试 workspace 信息完整性的**探针**"
- 允许它试错, 每一步落 EventBus 审计 · ServiceBus workspace 给安全网
- 不先限它的 prompt 细节, **让它自主决定调用路径**
- 运行结果 (trace + 产物) 是**档案**, 不是"成功/失败" 二元判断

### 2.3 Step 3 · 观测建档

- trace_induction · hypothesis · agent_crystallize 等扩展包消费探针档案
- 看"agent 实际用到什么 / 错过了什么 / 反复走弯路的地方"
- **不提前定观测维度** — 维度也从探针数据里长出来

### 2.4 Step 4 · 按需提炼

- 观测到**明确、稳定、可复用**的流程 → 固化为 HARD worker 或 SOFT prompt
- 固化以"节约 token + 提速" 为目的, 不以"锁死流程" 为目的
- 固化后仍保留 agent 回退路径 (探针可再次启动)
- **不固化"曾经出现过一次"的流程** — 只固化观测到稳定的

---

## 三 · 什么时候 **不** 走 agent-first

agent-first 不是万能. 以下场景**应先确定性设计**:

| 场景 | 为什么 |
|---|---|
| **确定性 I/O 转换** (CSV → JSON 等) | 规则明确, agent 反而慢 |
| **固定协议对接** (collab platform API / P4 submit) | 协议是外部合同, 不允许探 |
| **数据契约校验** | 确定性好测 |
| **性能关键路径** (热循环内) | token 成本压 SLO |

agent-first 最适用: **规则未知 / 探索性任务 / 跨领域综合判断 / 第三人称信息充分性评判**.

---

## 四 · Agent-First 的治理边界

agent-first 不等于"放任 agent 自由写代码". 治理:

1. **workspace 约束**: agent 写入紧限于 package 声明范围 (见 `workspace.md`)
2. **ServiceBus 审计**: 所有 bus 动作回流 EventBus 可追踪 (见 ServiceBus 定位)
3. **HumanBus 回路**: agent 认为"超出可确定性处理"时提 human_blocking, 不硬闯
4. **max_turns 铁律 B**: 预算宽松但有, 触发即 bug 不是正常路径
5. **Guardian 后置巡查**: agent 留下的痕迹入合规扫描; 涉及核心设施统一时必须过 `OMNI-093a~d`, 防止分散文档或新 agent prompt 再造第二套唯一权威

---

## 五 · team_builder 是第一个范例

team_builder (A3 2026-04-23) 是本规范的**首个实施者**:

- Step 1: workspace 声明 (`<tb>/.omni/workspace.py`) + 7 类 material 设计文件作信息库
- Step 2: agent worker 分阶段 (待后续 A3.x 建设) 作探针运行
- Step 3: 观测几轮后, trace_induction 消费档案
- Step 4: 观测到的稳定流程固化为 HARD worker

team_builder 产出的**每个新 team** 也按同样范式 (递归应用 agent-first).

---

## 六 · 不做清单

- **不先写"理想管线"** 让 agent 照着走 — 那是老工作流思维
- **不事先设计 agent prompt** 的所有细节 — agent 试了再说
- **不为少量观测就提炼** — 等稳定
- **不把 workspace 做成可变 state** — workspace 是 package 边界声明, 不是"当前思考状态"

---

## 七 · 与其他规范的关系

- **Workspace** (`workspace.md`): agent-first 的安全网 (硬约束)
- **ServiceBus 定位** (`runtime/buses/base.py`): 统一出口设施 (审计基础设施)
- **Pipeline 规范** (`pipeline.md`): HARD/SOFT 区分保持, agent 是第三类 worker type
- **Material 规范** (`material.md`): agent 产物按 material 规范落盘 (workspace 内)

## 八 · 工具范式 — 高自由度 + 三位一体 (L1 2026-04-30 立)

> **铁律**: 工具自由度非常非常重要. 但引导 + 规范 + 安全网都要足.

### 8.1 反模式 — 领域分类工具

❌ 给 agent 7 个按外部信号源分的 ToolWorker (`MeegleQueryToolWorker` / `LarkWikiListToolWorker` / `P4DirListToolWorker` / `FigmaProjectsToolWorker` / ...) 各封一类 SDK.

后果:
- 工具粒度过细, agent 想新组合就要改代码加新工具 → 阻塞自由探索
- 我替 agent 想了"它会需要什么", 模型智能没用上
- 工具内部硬编模式 (id-mode vs keyword-mode), agent 不能扩展

### 8.2 正模式 — bash 主 + 必要包装

✅ 工具集 ≤ 10 个, 三类:

```
基础工具 (主, 1-3 个):
  bash(command)     # 自由调外部 (lark-cli / meegle / git / 任意 shell)
  read(path)        # 读任意文件 (P4 / xlsm / figma JSON)
  write(path, content)  # 写产物到 workspace 内
  glob(pattern)     # 找文件
  grep(pattern, path)  # 搜内容

包装层 (必要, 2-5 个) — 仅当 SDK 不能 bash 直调:
  figma_api(method, params)  # figma_cli Python 客户端 (HTTP)
  xlsm_read_sheet(path, sheet)  # openpyxl 读 (bash 不能)

工作笔记 (规范化沉淀, 1-3 个):
  note_append(category, content)  # agent 沉淀发现
  note_list()                     # agent 自查目前已记
```

### 8.3 三位一体

#### 一 · 引导 (主动给上下文, 不预设流程)

prompt 含:
- "已有可用脚本清单" — 已有工具路径 + 简要用法 (例 `_scratch/demogame_kb_planning/fetch_meegle.py`, `lark-cli` 在 `D:/.../bin/`)
- 锚业务 few-shot (成功样例)
- 工具白名单 + 推荐组合用法 (例: "拉collab platform wiki 用 `bash lark-cli docs +fetch`")

避免: "你必须先 X 再 Y" (规则化预设).

#### 二 · 规范 (agent 主动按规范, 减少撞墙)

- 新阅读工具 → `<workspace>/read_tools/`
- 拉取产物 → `<workspace>/external_pulls/<source>/`
- 工作笔记 → 固定文件格式 (frontmatter + 字段中文 + 标依据)
- bash cwd → 项目根 (不污染)
- 检查思路 → 多源印证 + 标引用源

#### 三 · 安全网 (被动防御)

- 非标准路径写入 → DiskBus `write_prefixes` 拒
- 系统非安全指令 → bash 命令白名单/黑名单 (`rm -rf /` / `sudo` / 等)
- 删除空间外内容 → BashBus `cwd_prefixes` 限定
- (workspace.md ServiceBus 已有底座, 本规范上层补)

### 8.4 三位一体的边界

| 层 | 是 | 不是 |
|---|---|---|
| 引导 | "已有 X 可调" | "你必须先 X 再 Y" |
| 规范 | "新工具放 read_tools/" | "只能用我列的工具不能新建" |
| 安全网 | 双层 (agent 主动 + bus 被动) | 单层 (只靠 bus 兜底, 不教 agent) |

### 8.5 实施 checklist

新 agent worker 落地前自检:

- [ ] 工具集 ≤ 10 个, 不超过
- [ ] 工具中 bash 占主 (≥1 个)
- [ ] 没写按外部信号源分类的 ToolWorker
- [ ] prompt 含已有脚本清单 + 锚业务 few-shot + 工具使用约定
- [ ] workspace 声明 write_prefixes + cwd_prefixes (安全网)
- [ ] agent 产物落规范子目录 (read_tools / external_pulls / notes)

### 8.6 跟 ConfigurableAgent 实施层联动 (2026-05-02 加)

§8.1-8.5 的工具范式 (bash 主 + ≤10 + 三位一体) 在实施层由 [`ConfigurableAgent + AgentSpec`](../../../src/omnicompany/packages/services/_core/agent/configurable.py) 落地, 跟规范一一对应:

| 规范条款 | ConfigurableAgent / AgentSpec 实施 |
|---|---|
| 工具集 ≤ 10 (§8.5 第 1 条) | `AgentSpec.tools` 字段是 tuple[str, ...], `len() ≤ 10` 由 [agent 注册件.yaml](../../../templates/agent/注册件.yaml) check `agent-006` 强制 |
| bash 占主 (§8.5 第 2 条) | `tools` 含 `"bash"` 字符串 (走 TOOL_REGISTRY 注册的 BashRouter / DevBashRouter) |
| 没按外部信号源分类的 ToolWorker (§8.1 反模式) | 业务工具走 `TOOL_REGISTRY` 注册 + 字符串名引用, 不在 SPEC.tools 里 import 类 |
| prompt 走外部 .md (§2.1 workspace 一部分) | `AgentSpec.prompt_path` 指向 .md 文件, 启动时加载 (跟单源 + 薄包装铁律对齐) |
| workspace 声明 write_prefixes (§8.5 第 5 条) | `AgentSpec.workspace = {"write_prefixes": (...), "cwd_prefixes": (...)}` |
| agent 产物落规范子目录 (§8.5 第 6 条) | `AgentSpec.workspace.write_prefixes` 含 `read_tools/` `external_pulls/` `notes/` 等 |

业务侧立新 agent **优先走 ConfigurableAgent** (一行 SPEC 全配置驱动, 不写 Python 业务代码), 例外才直接继承 `AgentNodeLoop` (复杂自定义 PromptBuilder / ExtractResult 等场景, `SPEC.allow_custom_code=True` 显式标).

详见本规范（agent 概念）+ [ConfigurableAgent 范本](../../../templates/agent/范本.py) + [单源 + 薄包装](../_global/single_source_thin_wrap.md).
