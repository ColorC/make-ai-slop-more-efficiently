# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-02T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.read_image.attach_to_next_user.py"
"""ReadImageRouter · 让多模态主 agent (qwen3.6-plus) 直接读图.

跟 ScreenshotInspectRouter 的区别:
  - ScreenshotInspect: 调子 vision LLM (qwen3-vl-flash) 帮主 agent 转译成文字, 主 agent
    收到的是文字描述 — 多模态上下文丢失
  - ReadImage: 把图本身塞下一条 user message 让主 agent 直接看 — 多模态上下文保留

实现细节:
  - tool 不直接返图 (OpenAI 协议下 tool message content 必须是 string)
  - tool 返一句 ACK 字符串 + 把 image attachment 挂到 ctx.pending_image_attachments
  - loop.py 在 tool_result_blocks 拼好后, 检测 ctx.pending_image_attachments 非空, 追加
    一条 user message 含 Anthropic image block (LLMClient 内部转 OpenAI image_url)

安全:
  - path 必须在 ctx.allowed_image_roots (跟 allowed_screenshot_roots 共用) 下
  - 单图 ≤ 4 MB, 单轮累计 ≤ 12 MB
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

_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_TURN_BYTES = 12 * 1024 * 1024

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


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


class ReadImageRouter(SingleToolRouter):
    """主 agent 直接看图 (多模态). 把图塞下一条 user message."""

    TOOL_NAME: ClassVar[str] = "read_image"
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_bytes",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()
    DESCRIPTION: ClassVar[str] = (
        "直接把一张图塞进你的下一轮 user message, 让你 (多模态主 agent) 自己看. "
        "适合: 看 figma frame 截图 / lark 内嵌策划图 / collab platform画板预览 等需要多模态上下文连续的场景. "
        "跟 screenshot_inspect 的区别: 后者调子模型转译成文字给你; 本工具让你直接看原图. "
        "图必须 ≤ 4 MB, 路径在 allowed_image_roots 下. 单轮累计 ≤ 12 MB."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "图片本地路径 (PNG/JPEG/WEBP/GIF). 必须在 allowed_image_roots 下.",
            },
            "note": {
                "type": "string",
                "description": (
                    "可选, 一句话标注: 这张图你为什么要看 / 你想从中看出什么. "
                    "会作为 user message text 跟图一起出现, 帮你下一轮聚焦."
                ),
            },
        },
        "required": ["path"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        path_arg = args.get("path") or ""
        note = (args.get("note") or "").strip()

        if not path_arg:
            raise ToolExecutionError("path 必填")

        path = Path(path_arg)
        if not path.exists():
            raise ToolExecutionError(f"图片不存在: {path_arg}")
        if not path.is_file():
            raise ToolExecutionError(f"非文件: {path_arg}")

        # 路径白名单 (复用 screenshot 同字段; 业务侧把 _test_assets / _scratch / demogame-knowledge-base 加进去)
        roots = (
            getattr(ctx, "allowed_image_roots", None)
            or getattr(ctx, "allowed_screenshot_roots", None)
            or ()
        )
        if not _path_under(path, tuple(roots)):
            raise ToolExecutionError(
                f"图片路径不在 allowed_image_roots / allowed_screenshot_roots 内. "
                f"roots={roots}, path={path}"
            )

        size = path.stat().st_size
        if size > _MAX_IMAGE_BYTES:
            raise ToolExecutionError(
                f"图片太大 ({size} bytes > {_MAX_IMAGE_BYTES}); 缩小再读"
            )

        suffix = path.suffix.lower()
        mime = _MIME_BY_SUFFIX.get(suffix)
        if not mime:
            raise ToolExecutionError(
                f"不支持的图片格式: {suffix} (用 png/jpg/jpeg/webp/gif)"
            )

        try:
            with path.open("rb") as f:
                raw = f.read()
        except Exception as e:
            raise ToolExecutionError(f"读图失败: {e}")

        b64 = base64.b64encode(raw).decode("ascii")

        # 挂 ctx 让 loop 在 tool_result 后追加 user image message
        pending = getattr(ctx, "pending_image_attachments", None)
        if pending is None:
            pending = []
            try:
                setattr(ctx, "pending_image_attachments", pending)
            except Exception:
                # ctx 可能是 frozen dataclass; 退而记 attribute on dict
                logger.warning(
                    "ReadImageRouter: ctx 不可写 attr, image attach 会丢. "
                    "升级 ToolContext 或 ctx 子类."
                )
                return f"[READ_IMAGE_FAILED] ctx 不可写, 无法挂 attach. path={path_arg}"

        # 单轮累计 byte 上限
        total = sum(len(a.get("base64", "")) * 3 // 4 for a in pending) + len(raw)
        if total > _MAX_TURN_BYTES:
            return (
                f"[READ_IMAGE_REJECTED] 单轮累计图片体积超 {_MAX_TURN_BYTES} bytes "
                f"(已挂 {len(pending)} 张, 本张 {size} bytes 会让总量爆). "
                f"先消化已挂的图再读新图."
            )

        pending.append(
            {
                "path": str(path.resolve()),
                "name": path.name,
                "mime": mime,
                "base64": b64,
                "size": size,
                "note": note,
            }
        )

        return (
            f"[IMAGE_QUEUED] {path.name} ({size} bytes, {mime}) — "
            f"已挂到下一轮 user message. 你下一轮会**直接看到图**, 不要再调 read_image 看同一张图. "
            f"备注: {note or '(无)'}"
        )
