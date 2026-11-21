# [OMNI] origin=claude-code domain=runtime/info_audit/post_hoc ts=2026-04-14T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.post_execution_auditor.implementation.py"
"""post_hoc — 真实任务 + 专门输出 的信息审计.

填补"piggyback 失败/长输出忘记 JSON 块"等空白：
节点执行完毕后, 用节点**实际**的执行上下文（真正的 system/user/response）
独立调一次 probe LLM, 专门做信息充分性审计.

相比两种已有机制:
  probe(STRICT):   非真实任务, 专门输出 —— 评估的是 FORMAT 描述, 非实际运行
  piggyback:       真实任务, 顺便输出   —— 长输出时 LLM 易忘记 / 格式冲突
  post_hoc(本模块): 真实任务, 专门输出   —— 节点执行后读实际 LLM 调用记录, 独立 probe

成本: 1 次额外 LLM 调用 / 节点. 比 piggyback 贵, 比在主流程内塞入更多要求精准.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from omnicompany.protocol.info_audit import InfoAuditReport

logger = logging.getLogger(__name__)


def find_last_llm_call(
    *,
    trace_id: str,
    node_id: str,
    max_scan_lines: int = 500,
) -> dict[str, Any] | None:
    """从 audit_store 里查 trace_id + node_id 最后一次 LLM 调用记录.

    按 trace_id.jsonl 文件查找 (audit_store 按 trace 分文件), 从后往前扫.
    max_scan_lines 限制扫描深度, 避免大文件拖慢后处理.
    """
    from omnicompany.runtime.info_audit.audit_store import _audit_root

    root = _audit_root()
    if not root.exists():
        return None

    # trace_id 文件名格式: <trace_id>.jsonl, 按日期目录分布
    days = sorted(
        [p for p in root.iterdir() if p.is_dir()],
        reverse=True,
    )[:3]  # 最近 3 天

    target_file_name = f"{trace_id}.jsonl"
    for day_dir in days:
        jf = day_dir / target_file_name
        if not jf.exists():
            continue
        try:
            lines = jf.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        # 倒序扫, 匹配 node_id
        for line in reversed(lines[-max_scan_lines:]):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("node_id") == node_id:
                return rec
    return None


def run_post_hoc_audit(
    *,
    trace_id: str,
    node_id: str,
    format_in: str,
    format_out: str,
    description: str,
    max_preview: int = 3000,
) -> InfoAuditReport | None:
    """节点执行完毕后, 用实际 LLM 调用上下文独立 probe 做审计.

    返回 None 表示找不到该节点的 LLM 调用记录 (说明节点没有调 LLM,
    不需要审计; 或者 audit_store 写盘延迟).
    """
    record = find_last_llm_call(trace_id=trace_id, node_id=node_id)
    if record is None:
        return None

    # 从记录抽取实际执行的 context
    system_prompt = record.get("system_prompt", "") or ""
    messages = record.get("messages", []) or []
    response_text = record.get("response_text", "") or ""

    # 取最后一条 user message 作为 user preview (保留最近上下文)
    user_preview = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                user_preview = content
            elif isinstance(content, list):
                # 多段 content (文本 + 工具结果等), 拼接文本部分
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                user_preview = "\n".join(parts)
            break

    # 调 probe (延迟 import 避免循环)
    try:
        from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict
        from omnicompany.runtime.llm.llm import use_audit_context
        # 把 node_id / trace_id 带入 probe 的 LLM 调用, audit_store 落盘时可定位
        with use_audit_context({
            "trace_id": trace_id,
            "node_id": node_id,
            "pipeline_id": record.get("pipeline_id", ""),
        }):
            report = run_info_audit_probe_strict(
                format_in=format_in,
                format_out=format_out,
                description=description,
                original_system=system_prompt[:max_preview],
                original_user_preview=user_preview[:max_preview],
                original_response_preview=response_text[:max_preview],
            )
        return report
    except Exception as e:
        logger.warning("[post_hoc_audit] probe failed: %s", e)
        return None
