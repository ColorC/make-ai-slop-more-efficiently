# [OMNI] origin=human domain=software_engineering/equiv_test ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.equiv_test.module_aggregate.exports.py"
"""equiv_test — 跨语言语义等价性测试管线 [EXPERIMENTAL]

Golden File 模式：Python 实际执行录制输出 → TS/Rust 跑一遍 → 逐 key 比对。
比 lang_rewrite L4（LLM 裁判）更严格，但需要被测模块可独立执行。

状态：Experimental — 设计有效，但未接入 lang_rewrite 主流程。
可手动触发：`omni run equiv-test --py-path <file> --ts-path <file>`
或直接跑：`python scripts/run_equiv_test.py <py_path> <ts_path>`
"""
