---
name: semantic_auditor
description: omnicompany LLM 语义合规检查 service - 给 artifact 跑 LLM 审计, 拿 Finding (含 standard_id + confidence) 写 REGISTRY.md §语义合规待审.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/semantic_auditor ts=2026-05-04T12:50:00Z type=doc status=active agent=ai-ide belongs_to_service=semantic_auditor -->
<!-- [OMNI] summary="semantic_auditor 操作手册 — 跑 LLM 语义审计的操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,semantic_auditor,how-to,diagnosis -->
<!-- [OMNI] material_id="material:services._diagnosis.semantic_auditor.skill.operations_manual.md"-->

# semantic_auditor · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想给某些 artifact (`.py` / `.md` / 等) 跑 LLM 语义审计
- Guardian 已通过但仍想看"是否真符合规范意图" (语义层)
- 想给 REGISTRY.md §语义合规待审 加新 Finding
- 重构前先扫一遍语义级合规

**不用我**:
- 想跑路径 / 命名 / 结构 / 存在性检查 → 找 guardian (确定性规则更快更便宜)
- 想看 LAP 协议合规 → 找 lap_auditor (维度更窄, 但深度审计)
- 想看 Finding 列表 / 解决某条 Finding → 找 tech_debt (consumer + resolver)
- 想自动修代码 → 找 [services/repair/](../../) 或人工

## 前置条件

- omnifactory 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (LLMAuditWorker 调 qwen-3.6-plus 走 the_company 聚合 API)
- `docs/standards/standards-index.yaml` 存在 (审计路由表, 仓库根有)
- 待审 artifact 在仓库内 (相对项目根的路径)
- 在 git repo 内 (部分输入模式依赖 git diff)

## 操作步骤

### 场景 A · 给指定路径列表跑审计

```bash
omni run semantic_auditor -i paths='["src/omnifactory/packages/services/_authoring/docauthor/run.py"]'
```

**验证**: 输出 list[Finding] 含 `standard_id` / `target_path` / `description` / `confidence` / `line_hint`. confidence ≥ 0.7 写到 REGISTRY 主表, < 0.7 写 needs_human_review.

### 场景 B · 跑 git diff 范围审计

```bash
omni run semantic_auditor -i mode="git-diff" -i base_ref="HEAD~5"
```

**用途**: 给最近 N commit 改动的文件跑审计 (CI 集成场景).

### 场景 C · 全仓库扫描

```bash
omni run semantic_auditor -i mode="full-scan"
```

**注意**: 大仓库慢 (LLM 调用多 + token 贵). 实战常用 git-diff 跟具体路径.

### 场景 D · 库调用 (作为其他 service 的依赖)

```python
from omnifactory.packages.services._diagnosis.semantic_auditor.team import build_team
from omnifactory.runtime.exec import PipelineRunner

team = build_team()
runner = PipelineRunner(team)
result = runner.run({"paths": ["src/.../foo.py"]})
findings = result.outputs["semantic_auditor.finding-set"]["findings"]
```

### 场景 E · 看产出后的 Finding

跑完 semantic_auditor 后 Finding 落在 REGISTRY.md, 用 tech_debt SKILL:

```bash
omni debt list --section=语义合规待审
omni debt stats
omni debt resolve SA-NNN --reason="..." --by="..."
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run semantic_auditor` | 跑审计管线 | `-i paths='[...]'` 或 `-i mode="git-diff"` 或 `-i mode="full-scan"` |
| `build_team()` (Python) | 库调用 | 见 [team.py](team.py) |
| 单 Worker 调用 (测试用) | 测一段 | 见 [workers/](workers/) |
| `AuditAgent` (Python) | 单 (artifact, standard, excerpt) 三元组审计 | 见 [audit_agent.py](audit_agent.py) |
| `load_standards_index` / `match_standards` (Python) | 标准索引查询 | 见 [standards_loader.py](standards_loader.py) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| 报 THE_COMPANY_API_KEY 缺失 | 环境变量没设 | 配 `~/.env` `THE_COMPANY_API_KEY=...` |
| 报 standards-index.yaml 不存在 | 仓库根没该文件 | 该文件应在 `docs/standards/standards-index.yaml`, 缺则报修复或恢复 |
| Finding confidence 普遍很低 | LLM 对 standard 理解不足 / excerpt 不全 | 检查 standards-index.yaml 的 excerpt_strategy 是否漏了关键 section; 或 standard 文档本身写得不清 |
| Finding `standard_id` 不在 standards-index | LLM 自造规则 (D7 拒绝) | FindingWriter 自动拒, 看输出 `output.rejected` 字段; LLM 多次自造说明 prompt 没约束住 |
| Finding 重复 | (standard_id, target_path) 已存在 open / needs_human_review 条目 | D7 去重生效, 看 `output.rejected` reason; 想再次入库要先 resolve 老条目 |
| LLM 跑得慢 / 贵 | 全扫 + LLM 调用多 | 用 path 列表或 git-diff 模式缩范围; 调 standards-index.yaml 的 path_match 范围 |
| kind 推断错 (例 .py 既含 Router 又含 LLM 调用只标一个 kind) | D2 局限 (kind_inference 首条命中即定型) | 当前局限, 升级路径在已知局限段 |
| key_sections 命中失败 | standard 文档改名 `## X` → `## X (新)` 后 key_sections 漏 | 运行时 warn + fallback full 模式; 改 standards-index.yaml 同步标题 |
| Pipeline 级只看到 1 个 verdict (没看到每个 excerpt 的子 verdict) | LLMAuditWorker 是薄外壳, 单审在 AuditAgent | D6 设计, 真细节看 AgentNodeLoop bus 落盘的事件 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D7 决策 / 5 节点管线拓扑) → [DESIGN.md](DESIGN.md)
- 标准索引 → docs/standards/standards-index.yaml
- 跟 Guardian 互补 → ../../_core/guardian/SKILL.md
- consumer + resolver tech_debt → ../tech_debt/SKILL.md
- 信息充分性原则 → docs/standards/llm_first.md
