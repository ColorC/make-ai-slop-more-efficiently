# [OMNI] origin=claude-code purpose=phase3-skeleton-gen ts=2026-04-17
"""批量生成 Tier B 模块的 DESIGN.md 骨架。

目标模块：src/omnicompany/ 下应有 DESIGN.md 但缺的包。
不覆盖已有 DESIGN.md。骨架含：
  - OmniMark 头（status=skeleton）
  - ## 状态 填充
  - ## 核心目的 用一句话（从 __init__.py 或 packagename 推断）
  - 其他 5 节 TBD
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")

# Tier B 目标：services（除 doctor/absorption/hypothesis）+ runtime 未覆盖的 +
# protocol / core 子模块不需要（已 active）+ domains（先建顶层 INDEX）+ bus / primitives

TARGETS = [
    # services（17 个除 hypothesis 外）
    ("packages/services/absorption", "services/absorption", "外部仓库吸纳管线（RepoMapper + ModuleExplorer + LearningExtractor + ReportWriter 四段）"),
    ("packages/services/doctor", "services/doctor", "管线级健康诊断（Format / Router / Pipeline 三条诊断管线）"),
    ("packages/services/guardian", "services/guardian", "代码/文档规范自动巡逻（OMNI-001 ~ OMNI-034 规则家族）"),
    ("packages/services/knowledge", "services/knowledge", "OmniKB 知识库管理（尚在设计阶段）"),
    ("packages/services/evolution", "services/evolution", "管线/配置演化子系统"),
    ("packages/services/registry", "services/registry", "运行时 Router / Format 注册与查询"),
    ("packages/services/repair", "services/repair", "诊断结果→修复 patch 候选（crystallize 上游）"),
    ("packages/services/workflow_factory", "services/workflow_factory", "从需求自动生成管线草稿（G3 缺口相关）"),
    ("packages/services/skill_importer", "services/skill_importer", "外部 skill 库导入"),
    ("packages/services/cleanup_bot", "services/cleanup_bot", "清理过期数据 / 临时文件 / 日志"),
    ("packages/services/lap_auditor", "services/lap_auditor", "LAP 协议合规审计"),
    ("packages/services/pipeline_ci", "services/pipeline_ci", "管线持续集成检查（PR 前自动跑）"),
    ("packages/services/pattern_discovery", "services/pattern_discovery", "跨域模式发现"),
    ("packages/services/repo_architect", "services/repo_architect", "repo 架构分析（节点 + 工具 + format 推导）"),
    ("packages/services/repo_learner", "services/repo_learner", "repo 学习辅助"),
    ("packages/services/selftest", "services/selftest", "系统自测套件"),
    ("packages/services/trace_induction", "services/trace_induction", "执行轨迹 → 模式归纳"),

    # runtime 剩余子模块
    ("runtime/nodes", "runtime/nodes", "节点类型定义基类（ANCHOR / TRANSFORMER / SCATTER / JOIN 等）"),
    ("runtime/routing", "runtime/routing", "Router 基类 + 内置 Router（Context / LLM / Tool）"),
    ("runtime/signals", "runtime/signals", "六元信号原语（Signal / Hook）"),
    ("runtime/storage", "runtime/storage", "通用存储抽象"),

    # 其他核心
    ("bus", "bus", "事件总线（SQLite 实现，管线观测入口）"),
    ("primitives", "primitives", "六元原语定义（Signal / Hook 基础类）"),
    ("tools", "tools", "内置工具库"),
    ("tracing", "tracing", "链路追踪"),
    ("cli", "cli", "命令行入口"),
    ("dashboard", "dashboard", "可视化看板（管线运行 / 健康档案 / 审计）"),

    # domains（各自有子模块，这里只建顶层索引）
    ("packages/domains/demogame", "domains/demogame", "游戏配表学习 + Unity QA + 业务生成的综合域"),
    ("packages/domains/voxelcraft", "domains/voxelcraft", "代码块演化 + 视觉 QA 域"),
    ("packages/domains/narrative", "domains/narrative", "叙事生成 + CSL 域"),
    ("packages/domains/software_engineering", "domains/software_engineering", "软件工程七阶段（plan/design/tdd/implement/review/verify/equiv_test）"),

    # ─── 2026-04-18 EC-8 扩展：demogame 子包 8 个 + software_engineering 阶段 11 个 ───
    # demogame 子包（ux 已存在，跳过）
    ("packages/domains/demogame/table_learning", "demogame/table_learning", "配表语义学习管线（SourceDiscovery + FieldDiscoveryLoop + benchmark 校验）"),
    ("packages/domains/demogame/produce", "demogame/produce", "配表生产管线（从 MI 信息源到 xlsm/CSV 产出 + LIVE 回路）"),
    ("packages/domains/demogame/unity_qa", "demogame/unity_qa", "Unity 游戏 QA 管线（截图 + 场景解析 + GM 命令触发）"),
    ("packages/domains/demogame/benchmark", "demogame/benchmark", "配表基准测试（rel 版本 CSV diff 作为 ground truth）"),
    ("packages/domains/demogame/unity_explore", "demogame/unity_explore", "Unity 运行时探索（list_buttons / list_scenes / openModule）"),
    ("packages/domains/demogame/business_audit", "demogame/business_audit", "业务审计（跨 session / 跨表 / 跨域的一致性检查）"),
    ("packages/domains/demogame/knowledge", "demogame/knowledge", "demogame 知识沉淀（业务规则 / 配表约定 / 命名习惯）"),
    ("packages/domains/demogame/reports", "demogame/reports", "demogame 业务报告产出（Table Report + Business Report 双轨）"),

    # software_engineering 阶段
    ("packages/domains/software_engineering/plan", "se/plan", "需求 → 实施计划的规划阶段（含 PLAN agent loop）"),
    ("packages/domains/software_engineering/design", "se/design", "计划 → 架构 / 接口设计阶段"),
    ("packages/domains/software_engineering/tdd", "se/tdd", "测试驱动开发阶段（先写 failing test）"),
    ("packages/domains/software_engineering/implement", "se/implement", "实现阶段（代码编写 + 本地验证）"),
    ("packages/domains/software_engineering/review", "se/review", "代码审查阶段（多 agent 交叉审）"),
    ("packages/domains/software_engineering/verify", "se/verify", "集成验证阶段（跑测试 + 跑管线）"),
    ("packages/domains/software_engineering/equiv_test", "se/equiv_test", "等价性测试阶段（重构前后行为等价）"),
    ("packages/domains/software_engineering/debugger", "se/debugger", "调试器能力（单步 / 断点 / 中间态查看）"),
    ("packages/domains/software_engineering/lang_rewrite", "se/lang_rewrite", "跨语言翻译（Python → TS / Rust 等，当前最成熟子包）"),
    ("packages/domains/software_engineering/lang_rewrite_verifier", "se/lang_rewrite_verifier", "跨语言翻译验证器（行为等价性检查）"),
    ("packages/domains/software_engineering/_shared", "se/_shared", "软件工程域共享工具 / 基类 / 通用提示词"),
]

SKELETON_TEMPLATE = """<!-- [OMNI] origin=claude-code domain={domain} ts={ts} type=doc status=skeleton -->

# {title} · 设计文档

## 状态
- **版本**: V0 (skeleton)
- **成熟度**: skeleton
- **下一步**: 由熟悉本模块的维护者填充核心目的 / 接口 / 决策

## 核心目的
{purpose_hint}

<!-- TBD: 此节尚未填充 — 需要补：解决什么问题 / 不解决什么问题 / 1-3 段 -->

## 核心接口
<!-- TBD: 此节尚未填充 — 需要补：对外暴露的关键类/函数/协议（含源码链接） -->

## 架构决策
<!-- TBD: 此节尚未填充 — 需要补：至少 5 条 ### D1-DN 决策（状态 skeleton 可暂空） -->

## 数据流 / 拓扑
<!-- TBD: 此节尚未填充 — 需要补：输入→处理→输出，或关键组件协作图 -->

## 已知局限
<!-- TBD: 此节尚未填充 — 需要补：至少 2 条局限 + 升级路径 -->

## 参考资料
<!-- TBD: 此节尚未填充 — 需要补：关联源码 / 相关 plans / 外部参考 -->
"""


def gen_skeleton(target_rel: str, domain: str, purpose_hint: str) -> tuple[Path, bool]:
    """生成骨架。返回 (path, created)."""
    target_dir = ROOT / target_rel
    if not target_dir.exists():
        return None, False
    design_path = target_dir / "DESIGN.md"
    if design_path.exists():
        return design_path, False  # 不覆盖

    # 标题：取目录名最后一段
    title_slug = target_rel.split("/")[-1]
    content = SKELETON_TEMPLATE.format(
        domain=domain,
        ts=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        title=title_slug,
        purpose_hint=purpose_hint,
    )
    design_path.write_text(content, encoding="utf-8")
    return design_path, True


def main() -> int:
    created = 0
    skipped = 0
    missing = 0
    for target_rel, domain, purpose in TARGETS:
        path, was_created = gen_skeleton(target_rel, domain, purpose)
        if path is None:
            print(f"  SKIP (not exist)  {target_rel}")
            missing += 1
            continue
        if was_created:
            print(f"  CREATED           {path.relative_to(ROOT.parent)}")
            created += 1
        else:
            print(f"  EXISTS (skip)     {path.relative_to(ROOT.parent)}")
            skipped += 1
    print(f"\nSummary: created={created}, skipped={skipped}, missing_dir={missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
