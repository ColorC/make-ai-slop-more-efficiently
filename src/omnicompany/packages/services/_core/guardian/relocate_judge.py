# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-28T14:00:00Z type=util
# [OMNI] material_id="material:core.guardian.relocate_judge.llm_disposition.py"
"""relocate_judge — Guardian 拖车 relocate 动作的目标位置 LLM 复核.

职责:
  收到一条违规 → 调 LLMClient (qwen-3.6-plus) 一次 → 返回 {target_path, confidence, reason}
  让拖车决定是 mv 还是降级到 quarantine.

设计原则:
  - 单次 LLM 调用 (非多轮 agent loop), 因为输入信息齐全, 一次足够.
  - 失败 (网络/解析) 返 None, 让调用方降级处理, 不静默放过.
  - 不做 max_chars 截断 (LLM_first 铁律 A): 大文件让 LLM 主动截 / agent 调用方决定怎么给.
  - 模型固定 qwen-3.6-plus (omnicompany 主轴, CLAUDE.md 五条铁律).

为什么不走 Worker / Agent loop:
  - 单次判定, 无中间状态, 无工具调用需求.
  - tow_truck 不在 Team 拓扑内, 不需 Worker 抽象.
  - 现有先例: evolve_signal.py:285 直调 LLMClient.

输出 schema (LLM 必须返 JSON):
  {
    "target_path": "data/_workspaces/<plan>/scripts/foo.py",  # 相对项目根
    "confidence": 0.85,                                        # [0.0, 1.0]
    "reason": "脚本 import torch 是训练代码, ..."               # 一句话
  }
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "qwen-3.6-plus"


@dataclass
class RelocateDecision:
    """LLM 对 relocate 目标的判定结果."""
    target_path: str          # 相对项目根, 如 "data/_workspaces/x/scripts/foo.py"
    confidence: float         # [0.0, 1.0]
    reason: str               # 一句话说明
    model: str = _DEFAULT_MODEL


_SYSTEM_PROMPT = """\
你是 OmniGuardian 的拖车 (relocate) 目标位置判定员.

任务: 一个文件违反了 distributed-docs 规范, 现在需要决定它应该挪到哪里 (或保留在 docs/ 内).

【规范摘要 (distributed-docs.md §四)】
- docs/ 闭集: 只允 .md 文档 + archmap.yaml/taxonomy.yaml/ARCH-CHANGES.jsonl 等机器可读索引
- 数据产物 (.json/.jsonl): 应在 data/_workspaces/<plan>/ 或 data/services/<svc>/
- Python 脚本 (.py): 应在 src/ 或 data/_workspaces/<plan>/scripts/
- 运行时残留 (.log/.prefab/.pkl/.db/.sqlite): 应在 data/_runtime/ 或彻底删除
- 图示/截图/设计稿 (.png/.jpg/.svg): 合法可留 docs/ 内 (如 docs/plans/<plan>/figures/)
- 调研笔记 (.md, .ipynb): 计划目录的 spikes/ 子目录合法

【判断要点】
1. 文件扩展名 + 内容片段 → 推断它是什么 (脚本/数据/图示/笔记)
2. 路径上下文 (从哪个 plan 来) → 推断挪到对应工作区
3. 内容若像图示/设计稿 (虽大但合理) → target_path 可保留原 docs/ 路径, confidence 标 1.0
4. 内容若像数据/脚本/缓存 → target_path 给 data/_workspaces/<plan>/<sub>/ 形式
5. confidence 反映你对推断的把握:
   - 0.9+: 文件性质明显 (典型脚本头/明显 JSON 数据)
   - 0.7~0.9: 较有把握, 但可能有歧义
   - <0.7: 不确定, 调用方会降级到 quarantine

【输出 JSON 严格格式】
{
  "target_path": "<相对项目根的路径>",
  "confidence": <0.0~1.0 浮点>,
  "reason": "<一句话, ≤80 字>"
}

只输出 JSON, 不要其他文字, 不要 markdown fence.
"""


def _build_user_prompt(
    path: str,
    content: Optional[str],
    rule_id: str,
    rule_message: str,
) -> str:
    """构造单次 LLM 调用的 user prompt."""
    lines = [
        f"违规文件: {path}",
        f"触犯规则: {rule_id}",
        f"规则说明: {rule_message}",
        "",
    ]
    if content:
        lines.append("文件内容 (前 2000 字):")
        lines.append("```")
        # 注: 不预防性截断, 但 user prompt 给 LLM 的"提示性片段" — 不是被审查的全量数据.
        # 大文件场景应由调用方决定怎么传, 这里仅给截图式片段.
        lines.append(content[:2000] if len(content) > 2000 else content)
        lines.append("```")
    else:
        lines.append("(文件已删除或读不出内容)")
    lines.append("")
    lines.append("请判定 target_path / confidence / reason 并输出 JSON.")
    return "\n".join(lines)


def _parse_decision(text: str) -> Optional[RelocateDecision]:
    """从 LLM 输出解析 RelocateDecision. 失败返 None."""
    s = text.strip()
    # 容错 markdown fence (虽然 prompt 禁止, 但模型有时会加)
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
        if s.startswith("json"):
            s = s[4:].strip()
    try:
        obj = json.loads(s)
    except Exception as e:
        logger.warning("[relocate_judge] JSON 解析失败: %s · 原始: %s", e, text[:200])
        return None

    target = obj.get("target_path")
    conf = obj.get("confidence")
    reason = obj.get("reason", "")

    if not isinstance(target, str) or not target:
        logger.warning("[relocate_judge] target_path 缺失或非字符串: %s", obj)
        return None
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        logger.warning("[relocate_judge] confidence 不是数字: %s", obj)
        return None
    if not (0.0 <= conf_f <= 1.0):
        logger.warning("[relocate_judge] confidence 越界: %s", conf_f)
        return None

    return RelocateDecision(
        target_path=str(target).replace("\\", "/"),
        confidence=conf_f,
        reason=str(reason)[:200],
    )


def judge_relocate_target(
    path: str,
    content: Optional[str],
    rule_id: str,
    rule_message: str,
    model: str = _DEFAULT_MODEL,
) -> Optional[RelocateDecision]:
    """让 LLM 判定违规文件应挪到哪里.

    Args:
        path: 违规文件相对项目根的路径
        content: 文件内容片段 (None = 删除/不可读)
        rule_id: OMNI-035g 等
        rule_message: 规则触发的文字说明 (有助 LLM 理解为何违规)
        model: LLM 模型名, 默认 qwen-3.6-plus

    Returns:
        RelocateDecision 或 None (LLM 调用 / 解析失败).
        调用方 (拖车) 看到 None 应降级到 quarantine, 不静默挪文件.

    干跑模式:
        OMNI_GUARDIAN_DRY_RUN=1 → 不调 LLM, 返一条 mock decision (用于 CI / 离线测试)
    """
    # 干跑模式: 不调真 LLM, 返 mock (CI 用)
    if os.environ.get("OMNI_GUARDIAN_DRY_RUN") == "1":
        return RelocateDecision(
            target_path="data/_workspaces/dry-run/" + path.split("/")[-1],
            confidence=0.5,
            reason="OMNI_GUARDIAN_DRY_RUN=1 干跑, 未调 LLM",
            model=f"{model} (dry-run)",
        )

    try:
        from omnicompany.runtime.llm.llm import LLMClient
        client = LLMClient()
    except Exception as e:
        logger.warning("[relocate_judge] LLMClient 初始化失败: %s", e)
        return None

    user_prompt = _build_user_prompt(path, content, rule_id, rule_message)

    try:
        # LLMClient.call() 返 response 对象 (Anthropic 风格 .content[*].text 或 OpenAI 风格)
        # 照搬 evolve_signal._llm_generate_correction 的解析模式
        response = client.call(
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
            caller="guardian.relocate_judge",
        )
        if hasattr(response, "content"):
            text = "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
        else:
            text = str(response).strip()
    except Exception as e:
        logger.warning("[relocate_judge] LLM 调用失败: %s", e)
        return None

    if not text:
        logger.warning("[relocate_judge] LLM 返回空")
        return None

    return _parse_decision(text)


__all__ = ["RelocateDecision", "judge_relocate_target"]
