# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-28T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.vision_inspect.screenshot_analyzer.py"
"""ScreenshotInspectRouter · 让 text-mode agent 借 vision LLM 看截图.

用例: PlayerAgent (qwen3.6-plus text mode) 调用 playwright_probe 留截图后,
能问"这张截图里的按钮文字看得见吗 / bench 8 槽是并排还是纵列 / 是否像 hifi mockup"
等纯视觉问题. 本 tool 内部用 qwen3-vl-flash (vision, $0.02/$0.21 - 极便宜)
读图 + 答, 返 text 给 agent.

设计:
  - 不改 agent loop messages 协议 (那要改 prompt builder + LLM client image 入参,
    工程量 ≥ 4h). 这里走"工具内置 vision call"路径, 1 文件搞定.
  - 单次 vision call 平均 ~$0.005 — 轻量.
  - 安全: screenshot_path 必须在 ToolContext.allowed_screenshot_roots 下.

返:
  text — vision LLM 答 (LLM agent 收作 tool result, 当文字处理).
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)

# Vision LLM 默认 (the_company the_company API). qwen3-vl-flash 最便宜, qwen3.6-plus 也支持
_DEFAULT_VISION_MODEL = "qwen3-vl-flash"
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB 上限 (避免大截图爆 LLM context)


def _path_under(path: Path, roots: tuple) -> bool:
    if not roots:
        return False
    p = path.resolve()
    for r in roots:
        try:
            p.relative_to(Path(r).resolve())
            return True
        except ValueError:
            continue
    return False


class ScreenshotInspectRouter(SingleToolRouter):
    """Vision LLM 看截图答问. 让 text agent 间接读图."""

    TOOL_NAME: ClassVar[str] = "screenshot_inspect"
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_bytes",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()
    DESCRIPTION: ClassVar[str] = (
        "用 vision LLM 看一张截图回答问题. 适合: 颜色 / 对比度 / 布局 / "
        "字是否可见 / 元素是否对齐 / 跟设计稿是否相像 等纯视觉问题, "
        "DOM 文本看不出. 单次 ~¥0.04. 截图必须在 allowed_screenshot_roots 内."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "screenshot_path": {
                "type": "string",
                "description": "截图本地路径 (PNG/JPEG, ≤ 4MB). 必须在 allowed_screenshot_roots 下.",
            },
            "question": {
                "type": "string",
                "description": (
                    "你想问的问题. 要具体, 例: '主屏 bench 8 槽是并排还是纵列?', "
                    "'难度选择 modal 的 3 个按钮文字看得见吗?', "
                    "'底部 3 个按钮的 label 是什么文字?', "
                    "'iframe 是否真的全屏占满?'."
                ),
            },
            "model": {
                "type": "string",
                "description": "可选, 默认 qwen3-vl-flash. 用 qwen3.6-plus 更准但贵.",
            },
        },
        "required": ["screenshot_path", "question"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        screenshot_path = args.get("screenshot_path") or ""
        question = args.get("question") or ""
        model = args.get("model") or _DEFAULT_VISION_MODEL

        if not screenshot_path:
            raise ToolExecutionError("screenshot_path 必填")
        if not question:
            raise ToolExecutionError("question 必填")

        path = Path(screenshot_path)
        if not path.exists():
            raise ToolExecutionError(f"截图不存在: {screenshot_path}")
        if not path.is_file():
            raise ToolExecutionError(f"非文件: {screenshot_path}")

        # 安全检查: 必须在 allowed_screenshot_roots 下
        roots = getattr(ctx, "allowed_screenshot_roots", ()) or ()
        if not _path_under(path, roots):
            raise ToolExecutionError(
                f"截图路径不在 allowed_screenshot_roots 内. roots={roots}, path={path}"
            )

        # 大小 + 格式检查
        size = path.stat().st_size
        if size > _MAX_IMAGE_BYTES:
            raise ToolExecutionError(f"截图太大 ({size} bytes > {_MAX_IMAGE_BYTES}); 缩小再试")

        suffix = path.suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime = mime_map.get(suffix)
        if not mime:
            raise ToolExecutionError(f"不支持的图片格式: {suffix} (用 png/jpg/jpeg/webp)")

        # 读 + base64
        try:
            with path.open("rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            raise ToolExecutionError(f"读截图失败: {e}")

        # 构造 vision messages (OpenAI/Anthropic 兼容格式)
        # 实际 LLMClient 内部走 OpenAI 协议, content 列表里 image_url type 是标准
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"请仔细看这张截图回答问题, 用中文, 简洁但具体. "
                            f"如果问到对比度/可见性等, 描述你真实看到的 (不要假设).\n\n"
                            f"问题: {question}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ]

        try:
            from omnicompany.runtime.llm.llm import LLMClient  # lazy
            llm = LLMClient(model=model, tools=[])
            response = llm.call(messages=messages, system="你是视觉验收员, 看截图回答 UI 问题.")
            # 解析 response.content (Anthropic 格式) 或 .choices[0].message.content (OpenAI)
            text = ""
            if hasattr(response, "content") and isinstance(response.content, list):
                for block in response.content:
                    if hasattr(block, "type") and block.type == "text":
                        text += block.text
            elif hasattr(response, "choices"):
                msg = response.choices[0].message
                text = getattr(msg, "content", "") or ""
            else:
                text = str(response)

            if not text.strip():
                return "[vision LLM 返回空, 可能模型不支持 vision 或图片解析失败]"
            return f"[vision_inspect:{model}] {text.strip()}"
        except Exception as e:
            logger.exception("ScreenshotInspectRouter LLM call 失败")
            raise ToolExecutionError(f"vision LLM 调用失败 ({type(e).__name__}): {e}")


class ScreenshotCompareRouter(SingleToolRouter):
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_bytes",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    """Vision LLM 看 2 张截图 (实物 + baseline), 答相似度 + 具体差距.

    用例: W6 invariants `verify_method=vision_llm_compare_to_baseline`. 比 SSIM 多看
    "风格是否对齐" 类无法 pixel diff 的差距. 单次 ~¥0.06.
    """

    TOOL_NAME: ClassVar[str] = "screenshot_compare"
    DESCRIPTION: ClassVar[str] = (
        "用 vision LLM 看 2 张截图 (实物 + baseline) 答相似度 + 具体差距. "
        "适合: 跟 hifi 设计稿对照 / 跟真 demogame 截图对照 / 跟前一版本对照. "
        "比单图 inspect 更适合 '布局/风格是否一致'. 单次 ~¥0.06. "
        "两张图都必须在 allowed_screenshot_roots 内."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "actual_path": {
                "type": "string",
                "description": "实物截图 path (PNG/JPEG, ≤ 4MB)",
            },
            "baseline_path": {
                "type": "string",
                "description": "baseline / 设计稿 / 真目标截图 path",
            },
            "question": {
                "type": "string",
                "description": (
                    "你想让 LLM 看双图后答的问题. 例: "
                    "'实物棋盘是否跟 baseline 一样用了 demogame cellmap 风格 (含 banned/block 过渡)?', "
                    "'实物商店按钮位置是否跟 baseline 一致?', "
                    "'估计实物跟 baseline 的视觉相似度 (0-100%) 并列出 3 个最大差距点.'"
                ),
            },
            "model": {
                "type": "string",
                "description": "可选, 默认 qwen3-vl-flash. 用 qwen3.6-plus 更准但贵.",
            },
        },
        "required": ["actual_path", "baseline_path", "question"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        actual_path = args.get("actual_path") or ""
        baseline_path = args.get("baseline_path") or ""
        question = args.get("question") or ""
        model = args.get("model") or _DEFAULT_VISION_MODEL

        if not actual_path or not baseline_path:
            raise ToolExecutionError("actual_path + baseline_path 必填")
        if not question:
            raise ToolExecutionError("question 必填")

        roots = getattr(ctx, "allowed_screenshot_roots", ()) or ()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

        encoded = []
        for label, p_str in (("actual", actual_path), ("baseline", baseline_path)):
            p = Path(p_str)
            if not p.exists() or not p.is_file():
                raise ToolExecutionError(f"{label} 不存在或非文件: {p_str}")
            if not _path_under(p, roots):
                raise ToolExecutionError(f"{label} 路径不在 allowed_screenshot_roots: {p}")
            size = p.stat().st_size
            if size > _MAX_IMAGE_BYTES:
                raise ToolExecutionError(f"{label} 太大 ({size} bytes); 缩小再试")
            mime = mime_map.get(p.suffix.lower())
            if not mime:
                raise ToolExecutionError(f"{label} 格式不支持: {p.suffix}")
            try:
                with p.open("rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
            except Exception as e:
                raise ToolExecutionError(f"{label} 读失败: {e}")
            encoded.append((label, mime, b64))

        # 构造 messages: 第 1 张 actual, 第 2 张 baseline, 文本说明 + 问题
        content = [
            {
                "type": "text",
                "text": (
                    f"请看 2 张截图. **第 1 张是实物**, **第 2 张是 baseline (目标 / 设计稿 / 真 demogame)**. "
                    f"对比答问题, 用中文, 具体到元素 (不要笼统说 '差不多').\n\n"
                    f"问题: {question}"
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:{encoded[0][1]};base64,{encoded[0][2]}"}},
            {"type": "image_url", "image_url": {"url": f"data:{encoded[1][1]};base64,{encoded[1][2]}"}},
        ]
        messages = [{"role": "user", "content": content}]

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            llm = LLMClient(model=model, tools=[])
            response = llm.call(
                messages=messages,
                system="你是视觉验收员, 对比两张截图 (实物 vs baseline), 找具体差距, 估相似度.",
            )
            text = ""
            if hasattr(response, "content") and isinstance(response.content, list):
                for block in response.content:
                    if hasattr(block, "type") and block.type == "text":
                        text += block.text
            elif hasattr(response, "choices"):
                msg = response.choices[0].message
                text = getattr(msg, "content", "") or ""
            else:
                text = str(response)

            if not text.strip():
                return "[vision LLM 返回空, 可能模型不支持 vision 或图片解析失败]"
            return f"[vision_compare:{model}] {text.strip()}"
        except Exception as e:
            logger.exception("ScreenshotCompareRouter LLM call 失败")
            raise ToolExecutionError(f"vision LLM 调用失败 ({type(e).__name__}): {e}")
