# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-05T17:04:51Z
# [OMNI] material_id="material:core.guardian.team.topology_builder.py"
"""guardian.pipeline — 守护检查管线拓扑

  fs_scanner ──→ arch_auditor ──→ health_reporter
                                      ↑
                                      │ (fan-in)

三个确定性检查节点 → 一个汇总报告节点。
全部 HARD 节点，不涉及 LLM。
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, TeamEdge,
    NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec, TransformerSpec, TransformMethod,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "guardian"


def build_team() -> TeamSpec:
    nodes = [
        # ── 文件系统洁净度扫描 ──
        TeamNode(
            id="fs_scanner",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-fs-scan",
                name="FsScanner",
                from_format=f"{DOMAIN}.check-request",
                to_format=f"{DOMAIN}.fs-report",
                method=TransformMethod.RULE,
                description="扫描项目根目录及工作区，检测散落文件、非法位置写入、类型命名临时文件等污染",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 架构规范审计 ──
        TeamNode(
            id="arch_auditor",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-arch-audit",
                name="ArchAuditor",
                from_format=f"{DOMAIN}.fs-report",
                to_format=f"{DOMAIN}.arch-report",
                method=TransformMethod.RULE,
                description="检查 src/ 下的架构规范：DEPRECATED 模块、空 __init__、"
                            "Router 实现是否符合 LAP 约定（INPUT_KEYS / run 签名 / docstring）",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 健康报告汇总 ──
        TeamNode(
            id="health_reporter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-health-report",
                name="HealthReporter",
                format_in=f"{DOMAIN}.arch-report",
                format_out=f"{DOMAIN}.health-report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-health-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 评估健康度：基于扫描事实给出评分、判断和改进建议",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        TeamEdge(source="fs_scanner", target="arch_auditor"),
        TeamEdge(source="arch_auditor", target="health_reporter"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-pipeline",
        name="Guardian Health Check Pipeline",
        description="守护检查管线：扫描文件系统污染 → 审计架构规范 → 汇总健康报告",
        nodes=nodes,
        edges=edges,
        entry="fs_scanner",
        tags=["guardian", "health", "audit"],
    )


def build_patrol_team() -> TeamSpec:
    """Guardian LLM 巡查单节点 Team (2026-04-21 C4).

    单节点 patrol 管线, 可被 `omni run guardian-patrol` 调用, 走统一 CLI 入口
    自动加载 .env (THE_COMPANY_API_KEY 等凭据). 比直接 Python 调 PatrolWorker 省心.
    """
    nodes = [
        TeamNode(
            id="patrol",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-patrol",
                name="GuardianPatrol",
                format_in=f"{DOMAIN}.patrol-request",
                format_out=f"{DOMAIN}.patrol-report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-patrol-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 巡查每个 service 的 Stage 3 真伪 + DESIGN.md 对齐度 + 目录卫生, "
                        "产出 Markdown 报告到 data/services/guardian/patrol/."
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT),
                },
            ),
            maturity=NodeMaturity.ACTIVE,
        ),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-patrol",
        name="Guardian LLM Patrol",
        description="Guardian LLM 精准巡查: Stage 3 真伪 + DESIGN.md 对齐 + 目录卫生",
        nodes=nodes,
        edges=[],
        entry="patrol",
        tags=["guardian", "patrol", "llm"],
    )


def build_hygiene_team() -> TeamSpec:
    """Guardian 运行空间卫生单节点 Team (2026-04-23 I-09).

    扫空文件夹 (OMNI-047) + (后续) 临时文件/过期产物/体积告警 · 只产告警不清理.
    可被 `omni run guardian-hygiene` 调用, 走统一 CLI 入口自动加载 .env / 配置.
    """
    nodes = [
        TeamNode(
            id="hygiene_scan",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-hygiene-scan",
                name="GuardianHygieneScan",
                format_in=f"{DOMAIN}.hygiene-request",
                format_out=f"{DOMAIN}.hygiene-report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-hygiene-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "扫描运行空间卫生维度 (空目录 / 临时文件 / 过期产物 / 体积告警), "
                        "产出 Violation 清单落盘 data/services/guardian/hygiene/."
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT),
                },
            ),
            maturity=NodeMaturity.ACTIVE,
        ),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-hygiene",
        name="Guardian Runtime Hygiene",
        description=(
            "Guardian 运行空间卫生巡查: 空文件夹 + (后续) 临时文件/过期产物/体积告警. "
            "只告警不清理, 下游清理设施消费 (plan §九)."
        ),
        nodes=nodes,
        edges=[],
        entry="hygiene_scan",
        tags=["guardian", "hygiene", "runtime"],
    )
