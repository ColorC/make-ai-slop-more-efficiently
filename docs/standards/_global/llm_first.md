# LLM 优先与语境完整性

> **状态**: 奠基原则（2026-04-17 立档，由 B-Step4 FrameCorrespondenceLoop 案例驱动）
> **范围**: 任何涉及"智能判断"和"LLM 调用"的 OmniCompany 节点/管线设计
> **关系**: 本文是 `information_sufficiency.md` 的**上游思想**——information_sufficiency 讲怎么操作地补信息，本文讲**为什么规则不该先行** + **怎么定位 LLM 失误**

---

## 原则 1 · 智能 → LLM，规则必须经 LLM 验证后才能存在

### 规则

**如果一个操作需要"智能"（判断、语义理解、上下文关联），默认用 LLM。**

**只有当一条规则是 100% 铁律（举不出一个反例）时，才允许把它硬编码。**

**关键推论**：**在 LLM 判断之前，你不知道某条规则是否是铁律。**所以：**不能用"自以为是铁律但没经验证"的规则去设计内容。**

### 反模式（Anti-pattern）

设计者凭直觉写下一条过滤/分类/排序规则，没经过 LLM 实证就把它硬编码进 Router/Tool/节点 的 `_filter_*()` 函数里。运行后效果不对，设计者改规则边界（调阈值），却从不质疑"规则这件事本身是否该存在"。

**典型案例**: `frame_correspondence_loop.py`（B-Step4）在 2026-04-17 暴露了 3 个此类规则：

| 规则 | 位置 | 问题 | 反例 |
|---|---|---|---|
| `_SKIP_PAGE_KW` 包含 `"专利"` | L127 | "专利 Page 通常是草稿" | 家园中秋反例：**专利 Page = 最终版 canonical**（设计师用最终版申请专利）|
| `_is_ui_frame` 要求 `550 ≤ w ≤ 1200` | L136 | "UI 帧都是手机分辨率" | 反例：**UE 交互案大画板** 6661×2018 是主设计载体，被直接过滤 |
| `_pick_best_sections` 按 EP 号择新 | L180 | "EP 号越高越好" | 反例：切磋活动 **EP5 万圣节 + EP6 西瓜杯是两期并存的活动**，被互相挤掉 |

三条规则都听起来"合理"，但没有一条是铁律。运行时 recall 率 <10%（每 prefab 只给 1.3 张帧），直到用户人工确认 3 pilot 才暴露。

### 正向替代

- **先上 LLM，看 LLM 会怎么判**（即使成本高也值得——你正在发现规律）
- LLM 判出的模式如果**稳定重现**且**举不出反例**，再固化为规则（此时规则是"LLM 行为的缓存"，不是"设计者猜想"）
- 固化时**保留 LLM fallback 路径**（有反例时规则让位）
- 硬编码规则必须附测试：golden input + expected output + 反例验证记录

### 操作检查表

设计节点时，每看到一个 if/filter/sort/score，自问：
- [ ] 这条规则 100% 铁律？能举出反例吗？
- [ ] 我有没有让 LLM 先在 3-5 个真实样本上验证过这个判断？
- [ ] 如果规则失败，是报错还是静默放行？
- [ ] 下游能不能发现规则已漏判？（可观测性）

---

## 原则 1.5 · 设施反模式 (2026-05-03 立, MaterialIdAgent 跑批夯实)

LLM 调用层的设施缺位常被绕开成 band-aid. 下面 4 条都是反模式, 见到改正:

### 反模式 A · `max_tokens` 默认 4000 / 8000

现代 agent 输出 (尤其 JSON / 多文件 review / 批量分类) 默认 16000 起步. 写死 4000 等于自己挖坑撞 length truncation, 然后用"拆小批 / 升 max_tokens 应急" 绕开根因.

**正修**: `LLMClient` 默认 `max_tokens=16000`, `AgentSpec.llm_max_tokens=16000`. 模型实际上限是 **续写** 兜底, 不是单 turn 卡死.

### 反模式 B · 撞 max_tokens 不续写

`finish_reason="length"` (OpenAI) 或 `stop_reason="max_tokens"` (Anthropic) 时, 把已生成内容当 assistant 消息回灌 + inject "Resume directly — no apology, no recap." 用户消息再调一次. **这是底层设施**, 跟 Claude Code 的 `isWithheldMaxOutputTokens` 路径同模式 (build-src/src/query.ts L1188+).

不实续写就只能升 max_tokens 跟拆批, 都治标. 续写实施: [`runtime/llm/llm.py`](../../../src/omnicompany/runtime/llm/llm.py) `_continue_if_truncated_openai / _anthropic`, 重试 ≤ 3 次.

### 反模式 C · Windows 终端 GBK 编码不能输出 unicode → 过滤代替修

`UnicodeEncodeError: 'gbk' codec can't encode '⊆'` 时**不要** `_safe()` 把字符过滤成 `?`. 那是销毁数据.

**正修**: `sys.stdout.reconfigure(encoding="utf-8")` 或 `PYTHONIOENCODING=utf-8`. CLI 入口 + 跑 LLM 的脚本入口都该有.

### 反模式 D · 跑 LLM 走 ad-hoc 一次性脚本, 不走 agent 框架

我们有 ConfigurableAgent + AgentSpec + omni run + J 管线 yaml team — 业务跑 LLM 必走这套. 写 `.omni/sandbox/drafts/run_xxx_batch.py` 自己 SQLiteBus + agent.run() 循环 = 重新发明轮子, 也跳过了 trace_id / 元 IO 审计 / 续写等设施.

**正修**: 把 LLM 工作做进 J 管线的 team yaml (例 file_scanner → MaterialIdAgent → header_patcher → report_aggregator), 走 `omni run mass_materialization_pipeline`.

#### 标准入口与文件产物闭环（2026-07-14 加固）

业务 LLM 工作先使用 Atlas 的 `llm-workflow` Skill 选型，再进入下面的唯一设施：

| 任务 | 唯一入口 | 产物处理 |
|---|---|---|
| 单次、固定 schema 的判断 | 注册 Team/Worker 内的 `runtime/llm/structured.py::call_json` | 原生 tool/structured output；不得用 prompt 模拟 JSON |
| 批量、可续跑的判断 | 注册 Team/Worker 内的 `runtime/llm/batch.py` | 由 batch 设施负责并发、失败隔离和续跑 |
| 需要读资料、写文件、执行命令、反复修订 | 统一 `AgentNodeLoop` / `ConfigurableAgent`，或受审计的 `omni worker run` | Agent 直接写目标文件并在同一会话内完成 lint 修复闭环 |

禁止在 `docs/`、`scripts/`、`.omni/sandbox/` 或仓根新建临时 Python 包装器来调用 `LLMClient`、`call_json`、batch 或 agent 启动器。实验也必须进入沙盒 Team/Worker/Agent；如果工作会再次运行，就注册为可 `omni run` 的 Team。

文件型任务的完成协议固定为：

1. Agent 读取真源和允许写入边界。
2. Agent 直接创建或修改目标文件。
3. Agent 用 Bash 运行任务声明的 lint / test 命令。
4. lint 失败时，错误输出回到**当前 Agent 会话**；Agent 修改同一批文件后再次运行 lint。
5. 只有 lint 通过，Agent 才能调用 finish 或结束外部 worker。

外层编排不得把 lint 失败转换成一次全新的 LLM 调用，也不得重新生成整份产物。结构化工具只约束参数形状；字段值、正文完整性和文件语义仍由 lint/test 与同会话修订负责。

#### 生产级中文唯一作者（2026-07-17 加固）

任何会被外部用户看到或作为正式产品资产交付的中文文字，统一遵守 `config/publication_common/production-copy-authorship.md`。个人网站、小红书、游戏文本、产品界面、运营文案和对外报告的中文成文只交给本次显式选定且已登记的 writer model；ChatGPT/Codex、Claude Opus/Fable 及其他未被选为作者的 Agent 只允许建立中立静态材料目录、定位证据、做事实核验或实现非文字视觉与代码。

禁止让其他模型先写母稿、事实稿、内容 brief、提纲、标题、建议句或图卡文字，再交给成文作者润色。成文作者应从目的、规范和静态材料目录独立回源、组织与成文。语义返修继续原作者会话；检查者只提交问题位置与一手证据，不提供替换句。

#### Agent 执行时限与重复熔断

- 30/60 秒可作为前台观测周期，不是所有命令或 LLM 调用的硬上限。长任务转为后台作业并用事件流、日志或健康探针暴露进度；聊天轮次在确认首个心跳后即可返回。
- 流式 LLM 默认不设累计执行时长上限。reasoning、text、tool args 或协议事件仍在正常增长就允许继续，禁止为满足固定秒数而机械拆文件或重启会话。
- 只有出现连接断开、进程退出、状态文件失效、连续无返回或计数长时间完全不变等可观测异常，才进入超时处置。真实超时后杀掉进程树、写入 `agent.tool_timeout` 并结束当前路径；禁止原样重试。
- 同一 Agent 会话内，工具名与原生参数完全相同的调用若已经失败，下一次在 dispatch 前拒绝，并写入 `agent.tool_repeat_blocked`。
- Agent Bash 只能执行本节点声明的确定性命令。审阅 Agent、下游 Team 和其他 `omni run` 管线由外层注册工作流直接调用，禁止在 Bash 中套娃启动。
- 每次 Agent 结束时，Verdict 与 `agent.loop.finish` 必须包含 wall time、turn 数、工具调用数、错误数、超时数与重复拦截数。发现异常耗时后先修公共设施，再继续业务重跑。

#### Shell 方言、路径与编码硬门

- 每个 Shell 工具必须声明 `bash` 或 `powershell` 方言；不得把 Bash 的花括号展开、heredoc、`&&`、`mkdir -p`、`rm -rf` 发送到 Windows PowerShell，也不得把 PowerShell cmdlet 或 `$env:` 发送到 Bash。
- Agent Shell 禁止 `head`、`find`、自由 `rg` 和递归 `ls/Get-ChildItem`（含管道后的调用）；文件发现、内容检索和分段读取分别走受约束的 Glob、Grep、Read，并声明精确根与排除目录，避免 stdin 等待、Windows 遗留子进程和 `.tmp/data` 级噪声爆炸。
- Agent Shell 禁止创建目录和写文本：`mkdir`、`New-Item`、重定向、`tee`、`sed -i`、`Set-Content`、`Add-Content`、`Out-File` 都必须改走声明精确目标路径的 Write/Edit 设施。父目录不存在时停止，不得猜路径后顺手创建。
- Windows PowerShell 与其子进程必须显式设置 UTF-8，Python 子进程必须设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`。输出或写后读回出现 Unicode replacement character 时，本次证据无效并立即停止。
- 复杂 PowerShell 在真实执行前必须通过同方言解析或最小只读 dry-run；不得把 Bash/CMD 中可用的拼接格式当作 PowerShell 已验证格式。

#### Agent 可观测性缺口零容忍

- 每个 `AgentNodeLoop` 在开始读取资料或调用模型前，必须先落盘一份独立于 EventBus 的运行状态，至少包含 PID、父 PID、trace id、Agent 名、当前阶段、启动时间和最近心跳。
- 等待模型首包也必须持续刷新 `llm_waiting` 心跳；收到流式事件后记录 `llm_streaming`。不能把“模型尚未返回内容”当成不可观测的理由。
- 状态文件无法创建、更新失败、监督端发现心跳停止，均视为可观测性缺口。立即终止整个进程树，不继续盲等，也不并发启动第二个写入者。
- 重启前先修复观测通道；确认旧进程树消失后，从目标工作区最新的已落盘文件续跑。重启不等于从头生成，也不抹掉同一任务已有证据。
- stdout、stderr、EventBus、状态文件是互相独立的证据面。任一单独缺失不应制造黑箱；状态文件是进程监督的最低强制契约，EventBus 继续承担语义审计。
- Windows 上监督端读取状态文件可能与原子替换发生短暂共享冲突；写端只允许做有上限的毫秒级替换重试。持续失败仍按可观测性缺口终止，不能无限重试。

### 反模式 E · finish() + 文本 JSON + 手解析 (json.loads / regex / markdown fence)

**最容易撞**. agent 输出需要结构化消费 (例 `{entries: [...], by_kind: {...}}`) 时:

| 反模式 | 正向 |
|---|---|
| `tools=("read_file", ..., "finish")` <br> agent 调 `finish(result="<JSON 字符串>")` <br> ExtractResult 用 `json.loads / re.search markdown fence / 大括号正则` 三层 fallback 解析 | `tools=("read_file", ..., "submit_xxx_proposals")` <br> `submit_xxx_proposals` 是 `SingleToolRouter` 子类, 含 `INPUT_SCHEMA` <br> ExtractResult 走 messages 找 `tool_use` 块, 直接读 `block.input` (已是 dict) |

跨项目铁律 feedback_no_manual_parse_use_structured_output 立 (2026-04-25 用户立).

**手解必失败**: LLM 偶尔输出 narrative + JSON / 单引号 / unescaped 特殊字符, regex 接不住. function call 走 API schema 强校验, 不可能撞这些.

**正修参考**: [`design_validator.py`](../../../src/omnicompany/packages/services/_core/team_builder/workers/design_validator.py) `SubmitDesignReportRouter` + `_DesignValidatorExtractResult`. 跟 [`material_id_agent.py`](../../../src/omnicompany/packages/services/_authoring/mass_materialization/agents/material_id_agent.py) `SubmitMaterialIdProposalsRouter` 同模式.

**实施清单**:
1. 立 `SubmitXxxRouter(SingleToolRouter)`, 含 `TOOL_NAME / DESCRIPTION / INPUT_SCHEMA`
2. `register_tool("submit_xxx", SubmitXxxRouter)` 进 TOOL_REGISTRY
3. agent SPEC.tools 加 `"submit_xxx"`, prompt 写"调 submit_xxx 工具" 不写"调 finish"
4. ExtractResult 走 messages 找 `tool_use` block.input 直接消费, **不写 json.loads / regex**

---

## 原则 2 · LLM 效果不对，先怀疑语境，不怀疑智能

### 规则

**LLM 操作出现不符合预期的结果时，排查顺序必须是：**
1. 语境（context）是否完整？LLM 是否知道"你还可以提供这个"？
2. 上下文长度（token budget）是否合适？有没有被截断或被无关内容稀释？
3. Prompt 指令是否清晰？
4. （最后才）模型智能是否不够？

**LLM 从不会主动说"我信息不够"**——它的默认行为是"用手头的东西硬答"。所以你永远**不能依赖 LLM 自省**来定位语境缺失。

### 反模式

- LLM 效果不对 → 换更大的模型 / 改 Prompt 措辞 / 加"请仔细思考"之类的咒语
- 设计时预判 LLM "不会用到" 某些资料，提前剪枝
- "LLM 应该懂 X" —— 其实 LLM 只懂你放进 context 的东西

### 正向实践

**短期**：每个 LLM 节点都做 Dry Run（参考 `information_sufficiency.md`）——问 LLM "给你这些输入，你觉得还缺什么？"

**长期**：Agent Node Loop + 全量资料可达。让 LLM 通过工具（`Grep` / `Read` / `WebFetch` / `FigmaQuery` / `PlaybookLookup`...）**自己拉取**需要的资料。原则：**但凡相关，就让他可达**——Figma 官方文档、游戏整体介绍、UX 入职文档、业务策划案、外部规范都应挂到工具可达范围内。

**前提（安全网）**：Agent Node Loop 必须有高级全能工具 + 全套防护网：
- 工具是 readonly / append-only / dry-run-able，不会造成**意外改动**
- 边界明确，不会造成**架构混乱**（例如不让它改 `core/` / `protocol/`）
- 凭证/敏感数据不可达（**无安全问题**）
- 写入必经事件总线 + 回滚能力（**无数据损失**）

在你让 LLM 看过全部可达资料之前，**你不知道他到底需要什么信息**。所以：**前期不给**不等于**后期也不该给**。发现有帮助就加上。

### 操作检查表

排查 LLM 不符合预期的节点时，顺序：
- [ ] 把真实调用的完整 prompt + context 拉出来看（事件总线→ prompt 记录）
- [ ] 问自己：如果我是 LLM 只看到这些字符，我能做出预期结果吗？
- [ ] 有没有一段我"以为他知道"但实际没塞进去的背景？
- [ ] context 长度合理吗？有没有被大段无关内容挤掉关键信息？
- [ ] 加上可能有用的资料/工具后**再**调一次。如果仍然不对，才往 prompt 措辞/模型能力方向查。

---

## 与其他标准的关系

- **[counterexample_ledger.md](counterexample_ledger.md)** — 0 反例铁律的**持续验证机制**（每条死规则必带反例账本 + 反思 hook, 出 1 个反例就反思）。**硬性配套**, 立死规则必走。
- **information_sufficiency.md** — 信息充分性的**操作手册**（F-14/Dry Run/Agent Retry 等机制）。本文是其思想基础。
- **[material.md](../concepts/material.md)** — Format 设计时同样适用原则 1：不要在 Format description 里写"LLM 没验证过的规则"。
- **[team.md](../concepts/team.md)** — 节点拓扑决策也适用：`LLMRouter` vs `Router` 的选择应按原则 1 默认偏向前者，除非确定性是铁律。
- **`omnicompany-dev` SKILL.md** 核心原则 #5（上下文正面枚举）是本文原则 2 的**具体清单化版本**，但不能替代本文的 long-term stance（agent loop + 全量资料可达）。

---

## 一句话总结

> **"在你没让 LLM 看过全貌之前，任何规则都可能是幻觉；任何 LLM 失误都可能是语境失误。默认 LLM-first，规则有证据才能立。"**

---

## 原则 3 · 禁止预防性截断（2026-04-15 立档，2026-04-18 升级为零容忍）

### 规则（零容忍版）

**除非模型 API 明确报溢出错误（model_context_exceeded / OOM），否则禁止在喂 LLM 前主动截断任何资料。**

**截断一旦发生就没有"剩余内容"这回事** —— 丢失是不可逆的。凡是认为"截后半不重要"、"下游可以补"、"就截一点"的想法都是错的。六次被解释为"LLM 幻觉"的事件，复查都是上游某处静默截断。

违反本原则的所有形式均禁止：
- `text[:6000]` / `content[:max_chars]` 等硬切片
- `lines[:300]` 等行数截断
- `split("...SECTION...")[0]` 等用分隔符丢弃后半段
- "只取前 N 条"的默认列表截断（除非 N 是"agent 明确请求的参数"）
- 任何"为了 context 预算"做的预防性裁切
- 工具返回给 LLM 时的静默截断（即便有 `... more`/`_truncated` 提示）

### 统一长上下文处理办法（3 层规范）

处理长资料时按以下**顺序**选用，能走上层就不走下层：

**第 1 层 · 全量直传（默认）**

资料拼到 prompt 完整传入。模型 API 自己会判能不能装下 —— 能装下就处理；真装不下会抛明确错误（`context_length_exceeded`），这时才进入第 2 层。

**不要代码预判**。模型 context 窗口动态、模型会变、token 估算不准。唯一权威是 API 的回报错误。

**第 2 层 · agent-loop 主动检索（资料确实超大时）**

将完整资料存进 session state / 外部 KV，给 LLM 两个工具：
- `list_chunks()` 或等价索引：看资料结构
- `read_chunk(offset, limit)` 或 `search(keyword)`：按需拉

这类似 Claude Code 的 Read/Grep。**agent 自主决定读什么读多少**，不是代码替它决定。

**第 3 层 · 两阶段 LLM（第 1 层+第 2 层都不行时）**

第 1 次调用：LLM 读全量资料产出"提炼版"（由 LLM 判断什么可丢）
第 2 次调用：用提炼版做实际任务

注意：这跟"代码截断"的区别是 —— **由 LLM 自己判断丢什么**，不是代码预设。按"如果没把握，LLM 看"原则，让模型决定。

### 合法例外（不错杀）

以下场景允许截断，**但字段名必须明示**（`_preview` / `_truncated` / `_excerpt` 后缀）以防下游被误用：

| 用途 | 场景 | 命名要求 |
|---|---|---|
| 审计日志落盘 | `audit_store` 写 JSONL | `result_preview`, `content_preview` |
| 错误消息/诊断摘要 | `diagnosis=f"...{clean[:200]}"` logger | 直接放诊断字符串里，不存字段 |
| 事件 bus 的预览字段 | bootstrap / event preview | `concept_preview`, `result_preview` |
| Markdown 表格格子 | `[:140]` 单元格长度约束 | 给人读，Guardian 扫描时可豁免 |
| LLM 输出长度约束 | 标题 ≤30 字、ID ≤20 字 | 有明确业务上界（非 context 预算）|

**判断标准**：这段截断后的内容**会不会再次被喂给 LLM**？
- 会 → 违规
- 不会（只给人看 / 日志） → 允许

### 反模式及真实案例

| 代码位置 | 形式 | 后果 |
|---|---|---|
| `spec_parser.py:152`（已修） `report_md[:3000]/[:6000]` | 硬截断 report | 学习类 P0 findings #12~#17 全在截断之后，LLM 根本没看到 |
| `spec_parser.py:177`（2026-04-18 发现） `split("---DETAIL---")[0]` | 用分隔符丢弃后半段 | SpecParser 只看 summary 区，23 findings detail 区全丢，learning_loop/delegate/HRR 三大主题永远提不出 |
| `learning_extractor.py` 旧版 | `lines[:300]` + hardcap 10 | 55 模块压缩到 10 条，82% 信息丢失 |
| `module_reader.py` default `max_lines=600` | 读外部 repo 文件默认 600 行 | module_explorer 每次 local_read 悄悄丢 file 后半，LLM 被迫基于前 600 行瞎猜后续 |
| `routing/soft_node_executor.py` `input_summary[:4000]` | 核心路由路径截断 | 所有走 soft_node 的节点 input 被切 |
| `runtime/llm/compression_summary.py` `history_text[:100000]` | compaction 时截断 | agent 历史对话丢尾巴，context 压缩后错乱 |

### 判断启发（写代码时自检）

见到任何以下形式**立刻停**并改用 3 层办法：
- `[:NUMBER]` 后跟的变量会进 `messages=[...]` 或 prompt 字符串
- `head=N`、`max_chars=`、`truncate_to=`
- `.split("分隔符")[0]` 丢弃后半
- `lines[:max_lines]` 在 tool 返回里（应由 agent 通过 offset/limit 参数主动控制）
- 注释含"截断超长内容"、"防止 context 溢出"、"for token budget"

### 编译期防御（Guardian OMNI-036，待实施）

扫 `\[:(\d+)\]` 模式，周边 50 行内若出现 `LLMClient` / `client.call` / `messages=` / `system=` → HIGH 级告警，要求本条落实在上述 3 层办法之一。

---

## 原则 4 · 预算宽松到触发即 bug（2026-04-15 立档）

### 规则

**默认长任务 Agent 不使用固定轮数作为结束条件**。

参考基线：
- 需要写文件、lint 和同会话修订的 agent loop 默认 `max_turns=None`
- LLM 调用重试 默认 **充分**（不是 5 次）
- pipeline max_steps 默认 **1000 步**（不是 50）

### 反模式

把"怕出事跑太久"当成默认值。典型代码：
```python
max_turns: int = 30  # 担心 agent 跑太久
max_steps: int = 50  # 担心管线循环
```

**后果**：真实任务需要 60 轮的，在 30 轮被砍断 → 产出不完整。而且调试者以为是能力问题，去改 prompt，越改越偏。

### 正确默认

```python
max_turns: int | None = None  # 由完成协议和可观测异常结束
max_steps: int = 1000
_RETRY_MAX_ATTEMPTS = 10  # 除非明确是 pathological case
```

**哲学**：预算不是控制 LLM 的工具，是**防御死循环/网络故障的安全网**。安全网不能比正常任务还低。

### 真实案例

- `agent_node_loop.py` `max_turns=80` / `max_turns=30` — hermes-agent 学习任务在第 77 轮强制终止，下游 LearningExtractor 收到不完整 trace
- `xiaohongshu.author` `max_turns=24` — 正文、内容块和视觉源已经修改，但未来得及重跑 lint 就被伪装成完成态
- `spec_parser.py` 暗含 LLM 产出 ≤10 条 hardcap — LLM 想产 20 条也不行

### 判断启发

`max_` 开头 + 整数默认 → **默认值必须是"触发即 bug"级别**，不是"合理上限"。

**合理上限**是业务规则，应该写在 Router DESCRIPTION 里，不是写在 `__init__` 默认参数里。

---

## 原则 5 · 严格格式禁 JSON 字符串 · 用 function call / structured output (2026-04-25 立档)

### 规则

**LLM 产出严格结构化数据时, 禁止让 LLM 在自由文本里嵌 JSON 字符串. 必须用 function call (tool_use) 或 structured output (response_format=json_schema) 让 endpoint 强制 schema.**

### Why (2026-04-25 报告生成 worker 7 次失败实证)

LLM 在自由文本里写 JSON 时, 中文/markdown 内容嵌 ASCII `"` 反复破 JSON 结构. 无论怎么治标 (加 prompt 反例 / yaml.safe_load fallback / `_sanitize_json_escapes` / `===BLOCK===` 自创分隔), 都是补丁:
- LLM 单 worker JSON 输出抖动概率 ~30%
- 6 worker 串联全过率 ~12%
- 加 retry × 3 后单 worker ~95%, 6 串联 ~74% — 仍不够稳

根因: **协议错了**. 让 LLM 自由文本 → 要求合法 JSON 是反人类要求 (LLM 中文 quote / markdown 嵌 JSON 双引号必踩). 应该让 endpoint 处理 schema, LLM 只填字段 value.

### 反模式

```python
# ❌ Worker prompt 教 LLM 输出 JSON 字符串
SYSTEM_PROMPT = '''输出严格 JSON:
{
  "title": "<文章标题>",
  "body_markdown": "<markdown body, 含一级标题>"
}
'''
# LLM 输出: {"title": "从"能跑"到"真对"...", ...}  ← 中文标题里 ASCII " 破 JSON

# ❌ ===BLOCK=== 自创分隔 (治标)
# ❌ yaml.safe_load fallback (治标)
# ❌ FinishWithResultRouter minLength (治标)
```

### 正向

```python
# ✓ function call schema
SUBMIT_DRAFT_TOOL = {
    "name": "submit_draft",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 5},
            "body_markdown": {
                "type": "string",
                "minLength": 800,
                "description": "Complete markdown. ANY characters allowed (auto-escaped by JSON encoder).",
            },
        },
        "required": ["title", "body_markdown"],
    },
}
# LLM 调 submit_draft(title="...", body_markdown="..."), endpoint 强制 schema, JSON encode 自动转义
```

### 适用范围

- LLM 输出**结构化产物**: 必须 function call schema
- LLM 输出**自由 markdown / 文本**: 直接 message 文本, 不嵌 JSON
- 100% 必做字段 → schema `required` + `minLength` (按 auto-memory `feedback_100pct_required_goes_to_skeleton`)

### AgentNodeLoop 集成

按 `packages/services/publishing_commons/submit_artifact_tool.py` 范式:
- TOOL_NAME = "finish" (覆盖默认 FinishRouter, AgentNodeLoop 主循环识别 finish 退出)
- INPUT_SCHEMA 顶层 wrap `{reason, result: object {产物字段}}`
- AgentNodeLoop `tool_args.get("result", text)` 拿到产物 dict
- ExtractResult 收到 dict 直接用, 不需要 json.loads

### 真实案例 (2026-04-25 立档触发)

report_author Phase B v2:
- v2.0 让 LLM 输出 ===BLOCK=== 分隔字符串 → 7 次 e2e 抖动失败
- v2.1 改 SubmitXxx function call schema → 9 worker e2e 第 10 次全链 PASS
- L1 原话: "如果是严格格式, 不要用 JSON, 用 structured output 或 function call 这种底层内容"

---

## 原则 6 · 主观源不能直接映射客观产物 (2026-04-30 立档)

> **状态**: 由 demogame UX figma → Unity prefab 管线驱动 (2026-04-30 L1 立)
> **关系**: 原则 1 (0 反例铁律) 在跨域映射场景的具体化

### 规则

**当源和目标分属"主观/无规范层"和"客观/有规范层"时, 任何源 → 目标的字段级直接映射默认违反 0 反例铁律.**

主观/无规范层的特征:
- 制作时无强约束 / 几乎没有标准
- 表达同一意图有多种合法形式 (设计师自由发挥)
- 同一字段在不同案例下可能 ≠ 同一语义

客观/有规范层的特征:
- 工程实现层, 有结构强约束
- 大块分割明确 (如 prefab top/bottom/main/bg 分区)
- 字段语义稳定可枚举

### 反模式

设计者从源拿一个字段 (如 figma rotation / figma anchor constraints / figma 颜色 / figma 父子关系), 直接写入目标的对应字段 (Unity LocalRotation / anchor / color / GameObject parent), 假定语义对齐. 实测必有反例 — 设计师在源里"画给眼睛看"和工程师在目标里"实现可交互"是两套思路.

### 典型案例 — figma 树 ≠ Unity prefab 树 (demogame UX 域 2026-04-30)

**figma 一侧 (主观, 无规范)**:
- 设计稿层级是设计师的画图思路 (group / frame / 嵌套, 怎么方便画怎么来)
- 节点的 rotation / scale 常用作视觉表达, 工程上未必这么实现
- 装饰元素和功能元素混在一棵树里
- 图片节点跟实际 sprite 资源的对应需要规模化样本才看得出
- 文字内容 (characters) 是少数能直接参考的字段

**Unity prefab 一侧 (客观, 有规范)**:
- 大块分割明确: pbui_activity_xxx_main → anim → safe_area → top/body/bottom 标准分区
- pbui_common_* PI 引用是工程通用模板 (按钮 / 货币条 / 标题)
- LocalRotation 是真功能旋转 (装饰倾斜常用 Scale=-1, 不用 Rotation)
- 命名 prefix (btn_/text_/img_/bg_/layout_) 有相对稳定语义

### 哪些 figma 字段 ≠ 直接 Unity 字段 (实测有反例)

| figma 字段 | 不能直接映射的 Unity 字段 | 反例 |
|---|---|---|
| figma rotation (弧度) | LocalRotation / EulerAngles | 冰雪 figma 上 3 个倾斜节点, GT Unity 全 0 旋转 (设计视觉, 工程未实现) |
| figma absoluteBoundingBox | anchor + size | 同一 figma bbox 在 Unity 可有 N 种 anchor 表达; 工程师按 prefab 分区习惯选锚点 |
| figma 父子嵌套 | GameObject 树 | figma 1 个 group 在 Unity 可拆为 N 节点 (按工程分区); figma N 节点在 Unity 可合 1 (如 grid 重复元素 → 1 layout + N mount) |
| figma 节点存在性 | GameObject 存在性 | figma 上画了的节点在 Unity 可能没实现 (设计稿草案 / PI 引用黑盒 / 未上线功能); figma 没画的 Unity 可能有 (PI 内部子节点, 工程默认挂载) |

**唯一相对可参考的**: figma TEXT 节点的 characters (文字内容) 跟 Unity 文本节点的字内容有相对稳定对应. 但仍有反例 (动态文字 / Localization key).

### 正向替代

按"信号强度 + 反例可控"分档:

1. **强信号 (可直接用)**: figma TEXT 节点的 characters 字段 → Unity TMP_Text.m_text
2. **中信号 (需 LLM 起手 + 规模化反推)**: figma 图片节点 → Unity Sprite GUID, 走 LLM 看 figma 视觉 + P4 sprite 库匹配, 多样本验证后再固化
3. **弱信号 (只作 LLM 提示, 不当真值)**: figma 节点的命名/类型/位置 → 提示给 LLM 判 prefab 分区, 不直接映射 Unity 字段
4. **零信号 (不参考)**: figma rotation/scale/父子嵌套/节点存在性 → 不直接写 Unity 对应字段

prefab 结构应当**从 prefab 一侧的规范反推** (跨多 GT 看大块分区惯例 + LLM 在样本上逐步固化), 而不是 figma 树过来什么我们就照搬什么.

### 操作检查表

每写一段 figma → unity 映射代码, 自问:
- [ ] 我有没有跨 ≥ 3 GT 真实样本验证过这个映射 0 反例?
- [ ] 这个映射是 LLM 看了样本判出来的, 还是我"觉得应该"的?
- [ ] 有反例时, 我有没有 fallback 给 LLM 重判?
- [ ] 反过来想 — 我有没有用 figma 字段去硬拗 Unity 字段, 而不是从 Unity 一侧规模化反推?

### 一句话

> **figma 是设计师的画稿, Unity prefab 是工程师的实现. 两者不是同一棵树的两种表达, 是两个不同语义系统在不同抽象层的产物. 跨过去要靠 LLM + 规模化样本, 不能靠字段直接映射.**
