# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.rules_registry.aggregator.py"
"""Guardian 规则聚合器。

显式 import 所有规则模块并按 OMNI 编号顺序组装 RULES 列表。
新增规则模块：
  1. 在此处 import 对应模块的 RULES
  2. 加入下方 RULES 拼接
不需要修改 patrol.py。
"""
from __future__ import annotations

from ._base import FileContext, GuardianRule, Violation, parse_omnimark  # noqa: F401
from ._base import _is_python, _has_content, _not_graveyard, _is_external, _is_scratch  # noqa: F401

from .omnimark import RULES as _R001
from .boundaries import RULES as _R002_013
from .data_storage import RULES as _R005_011
from .migration import RULES as _R009_012
from .archmap import RULES as _R007_021
from .observability import RULES as _R017_020
from .location import RULES as _R023_024
from .naming import RULES as _R030_033
from .design_md import RULES as _R034
from .distributed_docs import RULES as _R035
from .terminology import RULES as _R036
from .material_kind import RULES as _R037
from .format_in_mode import RULES as _R038
from .stage3_completeness import RULES as _R040
from .directory_hygiene import RULES as _R041_042
from .runtime_hygiene import RULES as _R047_050  # 目录级扫描 · HygieneScanWorker 消费
from .compliance_prevention import RULES as _R070_073  # 合规预防 · 全 needs_judgment · 2026-04-24
from .manual_evidence_parse import RULES as _R080  # LLM 输出手解反模式 · 2026-04-26
from .prompt_quality import RULES as _R090  # AI 指令(prompt)反模式 · 2026-04-28 用户立
from .authority_convergence import RULES as _R093  # 唯一权威收束防漂移 · 2026-06-13
from .custom_llm_call import RULES as _R094  # 自定义 LLM 调用反模式(裸 claude/codex subprocess)· 2026-06-21 用户立
from .self_built_agent import RULES as _R095  # 自造 agent 反模式(手搓 ReAct 循环不用统一 AgentNodeLoop)· 2026-06-23 用户立
from .semantic_tags import RULES as _R096  # 语义标签受控词表(防孤儿标签, 缺失不报)· 2026-06-27 语义文件系统
from .progress_snapshot import RULES as _R097  # plan.md 正文进度快照(进度归 whatnow)· 2026-06-27 语义空间健康治理结晶
from .prose_health import RULES as _R098  # 语言治理确定性半(禁用代称/术语变体/已知缩写)· 2026-06-27 commit-time
from .plan_bindings_guardian import RULES as _R099  # 绑定注册表巡检(缺锚/登记不完整/悬空)· 2026-07-03 overnight-run 第六节
from .testmap_sync import RULES as _R100  # OMNI-100 (testmap 同步提醒 · 功能点-测试台账完成标准 · 2026-07-03)

# 按 OMNI 编号顺序组装（便于阅读 patrol 日志）
RULES: list[GuardianRule] = [
    *_R001,        # OMNI-001
    *_R002_013,    # OMNI-002/003/004/006/013
    *_R005_011,    # OMNI-005/011
    *_R009_012,    # OMNI-009/010/012
    *_R007_021,    # OMNI-007/008/014/015/016/021
    *_R017_020,    # OMNI-017/018/019/020
    *_R023_024,    # OMNI-023/024
    *_R030_033,    # OMNI-030/031/032/033
    *_R034,        # OMNI-034a-g (DESIGN.md 结构合规)
    *_R035,        # OMNI-035a-e (分布式文档 v2 合规)
    *_R036,        # OMNI-036 (命名迁移反倒退)
    *_R037,        # OMNI-037 (Material kind 必填 · F-19 · New World Diagnostics Phase C)
    *_R038,        # OMNI-038 (FORMAT_IN_MODE 必填 · R-24 · New World Diagnostics Phase C)
    *_R040,        # OMNI-040 (Clean Migration Stage 3 完整性 · 2026-04-21 B4/C1)
    *_R041_042,    # OMNI-041/042 (目录白名单 + 归档命名一致性 · 2026-04-21 C2/C3)
    *_R047_050,    # OMNI-047/048/049/050 (运行空间卫生 · 2026-04-23 I-09~I-12 分波推进)
    *_R070_073,    # OMNI-070/071/072/073 (合规预防 · 防绕体系 · 2026-04-24 全 needs_judgment)
    *_R080,        # OMNI-080 (LLM 输出 text 手解反模式 · 2026-04-26 用户立 · structured output 强制)
    *_R090,        # OMNI-090/091/092 (AI 指令 prompt 反模式 · 2026-04-28 用户立 · 概念注册 · LLM 复核分类)
    *_R093,        # OMNI-093a-d (唯一权威收束防漂移 · 2026-06-13 LLM-CALL-UNIFICATION)
    *_R094,        # OMNI-094 (自定义 LLM 调用反模式 · 裸 claude/codex subprocess · 2026-06-21 用户立)
    *_R095,        # OMNI-095 (自造 agent 反模式 · 手搓 ReAct 循环不用统一 AgentNodeLoop · 2026-06-23 用户立)
    *_R096,        # OMNI-096 (语义标签受控词表 · 防孤儿标签 · 缺失不报 · 2026-06-27 语义文件系统)
    *_R097,        # OMNI-097 (plan.md 正文进度快照 · 进度归 whatnow · 2026-06-27 健康治理结晶)
    *_R098,        # OMNI-098 (语言治理确定性半 · 禁用代称/术语变体/已知缩写 · commit-time · 2026-06-27)
    *_R099,        # OMNI-099 (绑定注册表巡检 · 缺锚/登记不完整/悬空 · 2026-07-03 overnight-run 第六节)
    *_R100,        # OMNI-100 (testmap 同步提醒 · 功能点-测试台账完成标准 · 2026-07-03)
]

__all__ = [
    "RULES",
    "FileContext",
    "GuardianRule",
    "Violation",
    "parse_omnimark",
]
