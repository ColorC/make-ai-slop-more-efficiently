# Agent Node Loop 工具接口规范

> **状态**：奠基规范（2026-04-18 立档，由 `prefab_semantic_loop` 从 bash+AST 防护迭代到结构化工具套件驱动）
> **范围**：所有 OmniCompany 里的 `AgentNodeLoop` 子类工具设计
> **关系**：[llm_first.md](../_global/llm_first.md) 原则 2（全量可达 + Agent Loop + 安全网）的**工具层落地规范**

---

## 一句话总结

**给 Agent 的是结构化工具，不是通用 Bash。范围缩小由 schema 保证，不靠 prompt 告诫。**

---

## 原则 1 · 不给 Agent 通用 Bash

### 规则

**不要给 Agent 一个 `run_bash(cmd)` 式的通用 shell。**

通用 Bash 的问题：
- LLM 会写 `grep -rn X 巨型目录/` → timeout → 进程树难 kill → 卡死
- 写 prompt 告诫"别做 X"是**头疼医头**，下次 LLM 还会犯
- AST 防护永远追不上 LLM 的创造性（pipe / 命令替换 / 嵌套 shell）
- 路径白名单对**单命令**有效，对**管道末端**/**命令参数中的 glob**都有漏洞

### 反模式（真实案例）

`prefab_semantic_loop.py` 2026-04-17 v1 给了 `run_bash` + bashlex AST 白名单。Agent 在 T18 写 `grep -rn "pbui_activity_artcontest_main" d:/P4/main/Client/ | head -20` —— 扫几 GB 内容，subprocess timeout 在 Windows shell=True 下未真正 kill 子进程树，Agent Loop 卡死 20 分钟无输出。修复手段走向越来越复杂：加子串黑名单 → bashlex AST → Windows taskkill /T /F → prompt 里加告诫。全是头疼医头。

### 正向替代

**提供受限的结构化工具集**（对标 Claude Code 的 [GrepTool/GlobTool/FileReadTool](../../../../参考项目/claude-code-analysis/src/tools/)）：
- Glob / Grep / Read / Ls / （其他领域特定 readonly 工具）
- 每工具 `path` **必填**（schema required），Agent **无法缺省到全盘搜索**
- 底层用经过验证的高性能二进制（ripgrep）
- Agent 能写任意命令 → Agent 只能在结构化字段里填合法值

## 原则 2 · Schema 约束即范围缩小

### 规则

**工具的 JSON Schema 必须强制最小范围字段。**

必须是 `required`：
- `path`（搜索/读取起点，不能缺省到全盘）
- `pattern`（搜索/glob 的模式，不能搜"所有内容"）

可选但应有默认值：
- `head_limit`（结果上限，默认 100-250）
- `output_mode`（对 grep 类工具，默认 `content` 或 `files_with_matches`）
- `glob` / `type`（文件类型过滤）
- `context` / `case_insensitive`

**反模式**：
- ❌ `grep(pattern, [path])` —— path 可选 → LLM 缺省就是全盘
- ❌ `find(query)` —— 没有路径概念 → 系统扫全盘

**正向**：
- ✅ `grep(pattern: str, path: str, glob?: str, head_limit: int = 250)` —— path 必填
- ✅ `glob(pattern: str, path: str, head_limit: int = 100)` —— path 必填

### 证据

Claude Code 的 [GrepTool.ts](../../../../参考项目/claude-code-analysis/src/tools/GrepTool/GrepTool.ts) schema：`pattern` required，`path` optional（默认 cwd —— 但 CC 有明确 cwd 上下文）。在**无明确 cwd** 的 AgentNodeLoop 场景下（我们的场景），`path` 应升级为 required。

## 原则 3 · 底层用经过验证的高性能二进制

### 规则

- 文本搜索：**ripgrep**（不是系统 grep）
- 文件查找：**ripgrep --files**（不是系统 find）
- 行级读：Python stdlib 或 `cat`

### 理由

- ripgrep 比 Git Bash grep（msys）快 10-100 倍
- ripgrep 原生支持 glob / type / VCS 自动排除 / UTF-8 / 多线程
- 输出格式稳定（行号 + 冒号分隔），parse 容易
- Claude Code 全线用 ripgrep —— 这是工业级选择

### 注意（Windows）

Git Bash 的 `rg` 可能是 Claude Code 注入的 shell function（不是独立二进制）。Python 子进程必须**显式定位独立 `rg.exe`**：
1. `OMNI_RG_PATH` 环境变量
2. `shutil.which("rg")` / `shutil.which("rg.exe")`
3. 候选路径：`@vscode/ripgrep`（Antigravity / Cursor / VS Code 都带）

参考实现：prefab_semantic_loop.py `_find_rg_binary()`

## 原则 4 · 默认硬上限，不靠 prompt 请求自制

### 规则

每个工具都必须有：
- `head_limit` 默认值（不是无限）
- 单次输出 chars 硬上限（truncate + "TRUNCATED N chars, use head_limit/offset" 提示）
- subprocess timeout（20-30s，超时强 kill 进程树）

### 默认值建议

| 工具 | head_limit | output_char_cap | subprocess_timeout |
|---|---|---|---|
| Grep (content mode) | 250 | 60KB | 20s |
| Glob | 100 | 60KB | 20s |
| Read | 2000 lines | 60KB | — |
| Ls | 200 entries | 60KB | — |

head_limit=0 作为"unlimited"逃生口（LLM 明确传才允许），不作默认。

### 反模式

- ❌ 无 head_limit → 一个 grep 可能吐 50k 行塞爆 context
- ❌ 只在 system prompt 说"请使用 head_limit=N" → LLM 不可靠
- ❌ Windows `subprocess.run(..., timeout=30, shell=True)` → 不真 kill 子进程树 → 进程僵尸

## 原则 5 · 默认排除噪声

### 规则

文本/文件搜索工具**默认**排除：
- VCS 目录：`.git / .svn / .hg / .bzr / .jj / .sl`
- 二进制/大文件：依 ripgrep 默认（它已处理）

LLM 不需要传参才排除 —— 工具自动做。

## 原则 6 · 写入边界收紧

### 规则

在 readonly-focused AgentNodeLoop（采样 / 分析 / 翻译类任务）里：

- **所有搜索/读取工具 readonly**（`is_readonly=True`）
- **唯一写入工具** 负责最终产物 `submit_xxx(payload)`
- 产物落盘路径由 Router 决定（Agent **不能控制写到哪**），只能通过参数控制**写什么**
- 产物路径有白名单前缀（如 `pilot_identification_auto/`）
- 写入前 lint 验证 schema（必需字段 / 必需 sections）

### 反模式

- ❌ 给 Agent `write_file(path, content)` → Agent 可能写到系统任意位置
- ❌ `submit_findings(path, content)` —— Agent 能指定 path → 失控
- ✅ `submit_findings(findings_md, metadata)` —— Agent 只决定内容，Router 决定路径

## 标准 tool 体系位置（canonical）

**ToolDefinition**（供 AgentNodeLoop 子类 `TOOLS` 引用）：
[`omnicompany.runtime.agent.agent_loop_tools`](../../../src/omnicompany/runtime/agent/agent_loop_tools.py)
- `GlobTool` — 按文件名 glob 找文件
- `GrepTool` — ripgrep 内容搜索
- `ReadFileTool` — 读文件（cat -n 格式）
- `ListDirTool` — 列目录

**底层 impl**：
[`omnicompany.runtime.exec.tool_executor.ToolExecutor`](../../../src/omnicompany/runtime/exec/tool_executor.py)
- `execute_glob` / `execute_grep` — ripgrep + VCS 排除 + head_limit + output_mode
- `_find_rg_binary()` — 跨平台 rg.exe 候选查找（Windows 下解决 CC shell function 问题）
- `_read_file_call` / `_list_dir_call` — 定义在 agent_loop_tools.py

**领域特定写工具** 由各 Router 自己定义，形如：
```
submit_<domain>(payload_md: str, metadata: dict, ...)
→ 唯一写入工具，Router 决定写入路径
```

参考：prefab_semantic_loop.py 的 `SubmitFindingsTool`。

## 标准 tool 签名速查

### Glob
```
glob(
    pattern: str (required),           # '**/*.lua', 'pbui_activity_*.prefab'
    path: str (strongly recommended),  # 缺省 cwd（agent 上下文通常不对）
    head_limit: int = 100,
)
→ ripgrep --files --glob；VCS 目录自动排除；fallback Python rglob
```

### Grep
```
grep(
    pattern: str (required),
    path: str (strongly recommended),
    glob: str = "",                    # 文件过滤（include 是 legacy alias）
    output_mode: "content" | "files_with_matches" | "count" = "content",
    context: int = 0,                  # -C
    case_insensitive: bool = False,
    multiline: bool = False,
    head_limit: int = 250,
)
→ ripgrep + --max-columns 500 + VCS 排除；fallback Python re
```

### ReadFile
```
read_file(
    path: str (required),
    offset: int = 0,
    limit: int = 2000,
)
→ cat -n 格式带行号
```

### ListDir
```
list_dir(
    path: str (required),
)
→ 文件名 + 大小 + 子目录（前 200 条）
```

---

## 迁移指南（现有 AgentNodeLoop 子类）

凡使用以下模式的 Loop 都应迁移到结构化工具：

- 暴露给 Agent 的工具含 `run_bash` / `execute_command` / `shell` / `cmd` 字段
- 工具 schema 里 `path` 是 optional 或无 path 概念
- 依赖 prompt 文本里写"不要搜全盘"之类告诫

迁移步骤：

1. 识别 Agent 常用的 shell 命令（grep / find / cat / ls / head / sed）
2. 换成对应的结构化工具（Grep / Glob / Read / Ls）
3. 从 system prompt 删除告诫文本
4. 保留领域特定工具（如 `feishu_search` / `wiki_get_node` / `download_whitebox_images`）

---

## 检查表

设计 AgentNodeLoop 工具时过一遍：

- [ ] 工具集里**没有** `run_bash` / 通用 shell 工具
- [ ] 搜索类工具的 `path` 参数**必填**
- [ ] 每个工具有合理 `head_limit` 默认值
- [ ] 文本搜索底层用 ripgrep，不用系统 grep
- [ ] 所有搜索/读工具 `is_readonly=True`
- [ ] 唯一写工具的路径由 Router 决定，非 Agent 传入
- [ ] 工具结果有 chars 硬上限（60KB 左右），truncate 时提示如何分页
- [ ] subprocess timeout 在 Windows 下能真正 kill 进程树
- [ ] SYSTEM_PROMPT 里**没有**"不要做 X 慢命令"之类告诫（那是 schema 的事）

---

## 一句话规则

> **工具是结构，不是自由。schema 每多一个 required 字段，就少一次 Agent 犯错的机会。**
