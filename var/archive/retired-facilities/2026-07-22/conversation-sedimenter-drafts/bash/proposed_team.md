<!-- [OMNI] origin=claude-code domain=services/_learning agent=ai-ide-e32f0243 ts=2026-06-23T06:28:21Z -->
# 可沉淀 team 骨架草稿: bash

> 候选操作: **Bash 探查目录与定位文档** | 触发: 进入陌生项目/技能树或需要确认设计案、脚本、规范文件位置时
> 由 conversation-operation-sedimenter 从一段对话自动提议, **草稿**, 待人/team-builder 接力硬化。

## Materials

- `bash.request` (kind=source) — Bash 探查目录与定位文档 的输入请求
- `bash.s1` (kind=internal) — 第1步产物
- `bash.s2` (kind=internal) — 第2步产物
- `bash.result` (kind=sink) — Bash 探查目录与定位文档 的最终产物

## Workers

- `bash_step1_worker`: 用 Bash 切换到候选根目录并 ls/find/wc/grep 查看结构与文件清单  
  FORMAT_IN=`bash.request` → FORMAT_OUT=`bash.s1`
- `bash_step2_worker`: 用 Read 打开 README、设计案、技能 SKILL、规则文档等关键文件  
  FORMAT_IN=`bash.s1` → FORMAT_OUT=`bash.s2`
- `bash_step3_worker`: 必要时跨多个工作区路径重复探查以确认真实生效目录  
  FORMAT_IN=`bash.s2` → FORMAT_OUT=`bash.result`

## 拓扑

entry = `bash.request`

- bash.request → bash_step1_worker → bash.s1
- bash.s1 → bash_step2_worker → bash.s2
- bash.s2 → bash_step3_worker → bash.result

## 本对话其余常见操作(供选别的候选)

- 读取规范后建立/更新待办 (freq≈8): Read 读取技能说明、alignment、设计案或全局规则 → 根据读到的约束拆分任务 → 用 TodoWrite 记录或刷新进度状态
- 创建归档目录并写说明文档 (freq≈6): Bash 查看活动设计案目录清单和现有并行开发规范/引用资源 → Bash mkdir -p 创建决策记录、图片、历史阶段等目录 → Write 写入说明、对齐与红线等归档文档 → TodoWrite 标记归档整理完成或进入下一步
- 同步修改多技能/多树命名规范 (freq≈5): Read 读取两套技能树中的配置/门禁脚本，确认旧名与路径逻辑 → Edit 对 .agents 与 .claude 下对应脚本做成对修改 → Agent 并行派发给多个技能子任务处理各自文档和脚本改名 → Bash grep/python 校验残留旧名、模板校验、导入与路径解析
- 写入代理/团队使用说明 (freq≈4): Write 生成或覆盖 CLAUDE.md/AGENTS.md 等说明文件 → Read 回读刚写入的说明确认内容 → 再次 Write 修订另一份说明或同步同类入口文档
- 搜索权威规则并抽取红线写入设计记录 (freq≈3): Read 查看全局 CLAUDE.md 与 .ai/README.md 了解项目约定 → Bash grep 在策划通用目录搜索红线/项目宪法/项目定位等权威档 → Read 打开命中的设计文档规则文件 → Write 将对齐结论与红线写入活动决策记录
