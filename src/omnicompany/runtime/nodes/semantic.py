# [OMNI] origin=human domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.nodes.compatibility_reexports.aggregator.py"
"""语义节点 — 兼容性重导出模块

所有节点已按职责拆分到子模块：
  - safety.py:    DeathZoneCheckRouter, IntentParseRouter
  - pain.py:      PainClassifyRouter, PainPropagateRouter, RewardComputeRouter, EscalationCheckRouter
  - guardian.py:  GuardianCheckRouter, ConvergenceAuditRouter
  - context.py:   TruthInjectRouter, MirrorRouter, TaskIntentRouter, TraceAccumulateRouter
  - routing.py:   RouteRetrieveRouter, BoltzmannSelectRouter, SemanticTypeClassifierRouter,
                  SpecializedDispatchRouter, _build_classification_context,
                  _ensure_routing_nodes_registered

注：evolution_judge.py（MutationJudgeRouter, CrystallizeCheckRouter）已归档至 _graveyard/evolution_v1/
"""

# safety
from omnicompany.runtime.nodes.safety import (  # noqa: F401
    DeathZoneCheckRouter,
    IntentParseRouter,
    _classify_tool_action,
)

# pain
from omnicompany.runtime.nodes.pain import (  # noqa: F401
    PainClassifyRouter,
    PainPropagateRouter,
    RewardComputeRouter,
    EscalationCheckRouter,
)

# guardian & convergence
from omnicompany.runtime.nodes.guardian import (  # noqa: F401
    GuardianCheckRouter,
    ConvergenceAuditRouter,
)

# context & tracing
from omnicompany.runtime.nodes.context import (  # noqa: F401
    TruthInjectRouter,
    MirrorRouter,
    TaskIntentRouter,
    TraceAccumulateRouter,
)

# routing & dispatch
from omnicompany.runtime.nodes.routing import (  # noqa: F401
    RouteRetrieveRouter,
    BoltzmannSelectRouter,
    SemanticTypeClassifierRouter,
    SpecializedDispatchRouter,
    _build_classification_context,
    _ensure_routing_nodes_registered,
)
