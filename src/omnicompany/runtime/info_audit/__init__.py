# [OMNI] origin=claude-code domain=runtime/info_audit ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.package_aggregator.exports.py"
"""Information Audit Runtime.

Phase 2 + Phase 2.5 基础设施:

  - parser.py       从 LLM 响应文本里解析 info_audit JSON 块
  - probe.py        独立 isolated LLM 对一次产出做 strict 审计
  - audit_store.py  每次 LLM 调用的真实 prompt/响应/audit 落盘, 支持 dry-run 回放

对外最常用的入口:

  from omnicompany.runtime.info_audit import (
      parse_info_audit_from_text,
      run_info_audit_probe_strict,
      record_llm_call,
      load_historical_llm_calls,
  )
"""

from omnicompany.runtime.info_audit.audit_store import (
    LLMAuditRecord,
    load_historical_llm_calls,
    record_llm_call,
)
from omnicompany.runtime.info_audit.fallback import (
    FallbackConfig,
    FallbackResult,
    UniversalFallbackLoop,
)
from omnicompany.runtime.info_audit.guarded_write import (
    GuardedWriteResult,
    guarded_write,
    validate_readonly_bash,
)
from omnicompany.runtime.info_audit.parser import (
    extract_info_audit_block,
    parse_info_audit_from_text,
)
from omnicompany.runtime.info_audit.probe import (
    run_info_audit_probe_strict,
)

__all__ = [
    "LLMAuditRecord",
    "record_llm_call",
    "load_historical_llm_calls",
    "extract_info_audit_block",
    "parse_info_audit_from_text",
    "run_info_audit_probe_strict",
    "UniversalFallbackLoop",
    "FallbackConfig",
    "FallbackResult",
    "guarded_write",
    "GuardedWriteResult",
    "validate_readonly_bash",
]
