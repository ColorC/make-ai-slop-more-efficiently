# [OMNI] origin=claude-code ts=2026-08-04 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.qoder_token_plan_settings.py"
"""Qoder CLI 的默认模型接线 — 内置百炼 Qwen-3.8-Max (token-plan)。

默认模型
========

dashboard/PTY 启动的 qodercli 统一用 ``-m bailian/qwen3.8-max-tp`` ——
即 ``qodercli --list-models`` 里的
``Qwen-3.8-Max (Alibaba Cloud Model Studio - China)``。三段写法的真身
(2026-08-15 ``--list-models`` 与运行日志实测):

- providerId 是 ``bailian`` (账号内置的阿里云百炼路由), 不是外部 provider
  ``aliyun-bailian``;
- modelId 是 ``qwen3.8-max-tp`` —— ``-tp`` 后缀与大小写都是 ID 的一部分,
  写成 ``qwen3.8-max`` 或改大小写都会被 CLI 静默回退到 ``lite``
  (实测: 错误名时 stderr 出 "using \"lite\" instead");
- 组合 key ``bailian/qwen3.8-max-tp`` 可直接传给 ``-m``。

为什么不再注入受管 settings 文件
================================

历史版本曾用 ``--settings <受管文件>`` 注入外部 provider ``aliyun-bailian``
跑 ``deepseek-v4-pro`` (需要 ``DASHSCOPE_TOKEN_PLAN_API_KEY``)。换成
``bailian/qwen3.8-max-tp`` 后它是账号内置路由: 只要 ``qodercli login`` 过
就能用, 无需 settings、无需环境变量 (2026-08-15 headless 实测: 不带
``--settings``、unset 掉 token-plan key, ``-m bailian/qwen3.8-max-tp``
仍正常出结果)。受管 settings 机制随之整体移除, 避免误把一个带 apiKey 的
``providers.bailian`` 块盖到内置路由上。

前置条件 (本模块管不了)
=======================

账号必须 ``qodercli login`` 过 (浏览器 OAuth, 无非交互开关, 需用户本人跑
一次)。未登录时 CLI 直接拒绝启动, 与模型选择无关。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# providerId / modelId 的大小写与连字符都是契约, 见模块 docstring; 写错会被
# qodercli 静默回退到 lite, 不要手改。
QODER_PROVIDER_ID = "bailian"
QODER_DEFAULT_MODEL_ID = "qwen3.8-max-tp"
QODER_DEFAULT_MODEL_KEY = f"{QODER_PROVIDER_ID}/{QODER_DEFAULT_MODEL_ID}"
QODER_DEFAULT_MODEL_DISPLAY_NAME = (
    "Qwen-3.8-Max (Alibaba Cloud Model Studio - China)"
)


def status() -> dict[str, Any]:
    """给 dashboard / CLI 看的接线状态."""
    return {
        "provider": "qoder",
        "provider_id": QODER_PROVIDER_ID,
        "default_model": QODER_DEFAULT_MODEL_KEY,
        "default_model_display_name": QODER_DEFAULT_MODEL_DISPLAY_NAME,
        "settings_injection": False,
    }


__all__ = [
    "QODER_DEFAULT_MODEL_DISPLAY_NAME",
    "QODER_DEFAULT_MODEL_ID",
    "QODER_DEFAULT_MODEL_KEY",
    "QODER_PROVIDER_ID",
    "status",
]
