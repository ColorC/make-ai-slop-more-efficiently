<!-- [OMNI] origin=ai-ide domain=services/cleanup_bot ts=2026-05-04T13:00:00Z type=doc status=active agent=ai-ide belongs_to_service=cleanup_bot -->
<!-- [OMNI] summary="cleanup_bot service 自我叙事 README — 系统环境异常清理. 针对 AI agent 路径拼接错误产生的错位嵌套垃圾目录, 3 节点链路扫盘+LLM 判断+生成 PowerShell 脚本辅助用户清理" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 抽核心目的到 README, DESIGN 留架构性内容" -->
<!-- [OMNI] tags=readme,cleanup_bot,diagnosis,self-narrative -->
<!-- [OMNI] material_id="material:services._diagnosis.cleanup_bot.readme.self_narrative.md"-->

# cleanup_bot · 系统环境异常清理

> 给 AI agent 路径拼接错误产生的"错位嵌套垃圾目录" (例 `E:\e\workspace`) 做扫描 + LLM 判断 + PowerShell 清理脚本生成. 三节点链路 (扫描 → LLM 判断 → 打印计划), **只打印不自动删除**.

---

## 这是什么

cleanup_bot 是 omnicompany 的**系统环境异常清理 service**. 它扫描宿主机磁盘, 通过 LLM 判断哪些路径是 AI agent 误触产生的"垃圾目录", 生成 PowerShell 清理脚本, **打印给用户手动执行**, 不自动删.

形态: **三节点线性 Team** (扫描器 ANCHOR → 异常检测器 LLM → 计划生成器 ANCHOR), 类似 lap_auditor 的 ANCHOR-LLM-ANCHOR 模式.

跟其他诊断 service 的边界:
- **doctor / guardian / lap_auditor** 看代码 / 文档合规 (项目内)
- **cleanup_bot** 看**宿主机文件系统异常** (项目外, 跟 AI agent 误触相关)
- 设计意图: 防 AI agent 路径拼接错 (例 `E:\` + `e\` → `E:\e\`) 产生的嵌套错位

## 解决什么 / 不解决什么

**解决**:
- 识别"错位嵌套垃圾目录" (单字母根目录 / 路径重复嵌套 / AI 误触特征)
- 生成 PowerShell 清理脚本
- 防止用户手动误删正常目录

**不解决**:
- 自动执行删除 (`RollbackPlannerWorker` 只打印计划, 用户手动跑)
- 非路径相关的环境异常 (例 注册表脏 / 进程残留)
- 项目内合规检查 (那是 doctor / guardian / lap_auditor 的事)

## 设计目的与最终目标

**设计目的**: AI agent 频繁路径错误是个真痛点 (Windows 路径拼接尤其容易). 不能依赖 agent 自检 (它就是产生错误的源头), 要独立 service 扫. 但**不能让 AI 自动删** (LLM 误判风险), 必须**最后由人按手按执行**.

**理论锚点**: 体现"AI agent 工作要有人为最后兜底" 的设计哲学 — 即使 LLM 判定"这是垃圾", 也只是产清理计划, 不动文件.

**最终目标** (当下能认知的):
- 按需扩展扫描策略 (例 正则关键词过滤 / 多关键词组合)
- 多 keyword 输入 (当前一次一个)
- 跟 CORE-SELF-STABILITY 第二阶段 自我画像漂移检测有交集 — 但跟项目内代码漂移不同, cleanup_bot 看宿主机文件系统层

## 规划

- **当前 V1** (active, 2026-04-21 Stage 3 Clean Migration): 3 Worker + 4 Material + 1 Team
- **下一步**: 按需扩展扫描策略 (正则关键词过滤 / 多关键词组合)
- **远景**: 跟 OS 级监督机制接通 (但跟项目内合规无关)

## 构成

- 入口与 Team → [team.py](team.py) (`build_team()`) + [run.py](run.py)
- Materials (4 条) → [formats.py](formats.py)
  - `cleanup.input` (kind.source) — `{root_dir, keyword}`
  - `cleanup.evidence` (kind.internal) — 收集到的可疑路径列表
  - `cleanup.plan` (kind.internal) — LLM 产 Markdown 含 PowerShell 脚本
  - `cleanup.done` (kind.sink) — 计划打印完成
- Workers → [workers/](workers/)
  - `EvidenceGathererWorker` (ANCHOR) — `os.walk` 递归扫描 (max_depth=5), 收集含 keyword 的路径
  - `AnomalyDetectorWorker` (LLM) — 分析路径合法性, 生成 PowerShell 清理脚本
  - `RollbackPlannerWorker` (ANCHOR) — 格式化打印, 不自动删除
- 旧名 compat shim → [routers.py](routers.py)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 类似设计模式 → ../lap_auditor/README.md (同三节点 ANCHOR-LLM-ANCHOR 链路)
- 项目根叙事 → ../../../../../README.md
