# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.llm.package_exports.init.py"
"""runtime.llm — see docs/ARCHITECTURE.md"""

from .batch import (
    BatchFailure,
    BatchResult,
    JsonCheckpoint,
    JsonCheckpointLoad,
    default_batch_status_path,
    load_json_checkpoint,
    read_batch_status,
    run_parallel_items,
    write_batch_status,
    write_json_checkpoint,
)
from .structured import (
    DEFAULT_MODEL,
    DEFAULT_STRUCTURED_MODEL,
    DEFAULT_STRUCTURED_MODEL_ENV,
    StructuredJSONError,
    call_json,
    default_structured_model,
    parse_json_block,
    validate_json_schema,
)

__all__ = [
    "BatchFailure",
    "BatchResult",
    "DEFAULT_MODEL",
    "DEFAULT_STRUCTURED_MODEL",
    "DEFAULT_STRUCTURED_MODEL_ENV",
    "JsonCheckpoint",
    "JsonCheckpointLoad",
    "StructuredJSONError",
    "call_json",
    "default_structured_model",
    "default_batch_status_path",
    "load_json_checkpoint",
    "parse_json_block",
    "read_batch_status",
    "run_parallel_items",
    "validate_json_schema",
    "write_batch_status",
    "write_json_checkpoint",
]
