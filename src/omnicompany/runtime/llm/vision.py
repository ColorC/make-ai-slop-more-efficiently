# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-09T16:30:00Z
# [OMNI] material_id="material:runtime.llm.vision_multimodal_shim.wrapper.implementation.py"
"""Image content helper — thin shim over LLMClient for multimodal calls.

Architectural note (2026-04-09):
  This module used to wrap its own anthropic.Anthropic(...) client and
  manually assemble messages. That was an independent "vision API" path that
  duplicated LLMClient's already-complete multimodal support
  (`_anthropic_msgs_to_openai` in runtime/llm/llm.py handles Anthropic image
  content blocks → OpenAI image_url blocks, for every provider).

  The independent path had two costs:
    1. Routers had to choose between "LLMClient" (text) and "call_vision"
       (images), splitting the mental model and diverging code paths.
    2. It defaulted to the weak qwen3-vl-flash vision model, which
       frequently misidentifies pixel art entities (observed 2026-04-09:
       calls an armored red knight "Iron Golem"). The quality model
       qwen3.6-plus is natively multimodal and was available all along.

  This rewrite keeps the public surface (`call_vision`, `call_vision_multi`,
  `VisionResult`) for backward compatibility but internally routes every
  call through `LLMClient.call()` with standard Anthropic-format content
  blocks. Default role is `vision_quality` (= qwen3.6-plus).

  Routers that want vision capability should preferably:

      from omnicompany.runtime.llm.llm import LLMClient
      from omnicompany.runtime.llm.vision import image_block

      client = LLMClient.for_role("vision_quality")
      resp = client.call(
          messages=[{"role": "user", "content": [
              image_block("screenshot.png"),
              {"type": "text", "text": "describe this"},
          ]}],
          system="...",
          caller="my_router",
      )

  …which is identical to any other LLMClient.call(). The `call_vision`
  helper exists only for legacy callers; new code should build the content
  block inline.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 8_000_000  # 8 MB per image


@dataclass
class VisionResult:
    success: bool
    text: str = ""
    model_used: str = ""
    tokens_used: int = 0
    error: str = ""
    raw: dict = field(default_factory=dict)


def encode_image(path: str | Path) -> str | None:
    """Base64-encode a local image file. Returns None on failure."""
    p = Path(path)
    if not p.exists():
        logger.warning("vision: image not found: %s", p)
        return None
    data = p.read_bytes()
    if len(data) > _MAX_IMAGE_BYTES:
        logger.warning("vision: image too large (%d bytes), skipping: %s", len(data), p)
        return None
    return base64.b64encode(data).decode("utf-8")


def image_block(path: str | Path, media: str = "image/png") -> dict | None:
    """Build an Anthropic-format image content block from a local file path.

    LLMClient's OpenAI adapter converts this to `{"type":"image_url","image_url":...}`
    automatically. Returns None if the file can't be encoded.
    """
    b64 = encode_image(path)
    if b64 is None:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": b64},
    }


def call_vision(
    image_path: str | Path | None,
    prompt: str,
    system: str = "",
    caller: str = "vision",
    role: str = "vision_quality",
    max_tokens: int = 600,
) -> VisionResult:
    """Single-image multimodal call — thin wrapper over LLMClient.call().

    Args:
        image_path: local PNG/JPG path, or None for text-only
        prompt: the text instruction
        system: system prompt
        caller: caller id (for tracing)
        role: default `vision_quality` (qwen3.6-plus, strong multimodal).
              Fall back to `vision` (qwen3-vl-flash) only for cost reasons
              where hallucinations on pixel art are acceptable.
        max_tokens: currently unused (reserved for future budgeting)
    """
    from omnicompany.runtime.llm.llm import LLMClient

    if image_path is not None:
        block = image_block(image_path)
        if block is None:
            return VisionResult(success=False, error=f"Failed to encode {image_path}")
        content: list | str = [block, {"type": "text", "text": prompt}]
    else:
        content = prompt

    try:
        client = LLMClient.for_role(role)
        resp = client.call(
            messages=[{"role": "user", "content": content}],
            system=system or "你是一个视觉分析专家。",
            caller=caller,
        )
        text = resp.content[0].text if resp.content else ""
        tokens = getattr(resp.usage, "input_tokens", 0) + getattr(resp.usage, "output_tokens", 0)
        return VisionResult(success=True, text=text, model_used=client.model, tokens_used=tokens)
    except Exception as e:
        logger.warning("vision call failed: %s", e)
        return VisionResult(success=False, error=str(e))


def call_vision_multi(
    images: Iterable[tuple[str | Path, str]],
    prompt: str,
    system: str = "",
    caller: str = "vision.multi",
    role: str = "vision_quality",
    max_tokens: int = 800,
) -> VisionResult:
    """Multi-image multimodal call.

    `images` is an iterable of (path, label) tuples. Each image is sent with
    its label as a preceding text block, then the final prompt is appended.
    """
    from omnicompany.runtime.llm.llm import LLMClient

    content: list[dict] = []
    for p, label in images:
        if label:
            content.append({"type": "text", "text": label})
        block = image_block(p)
        if block is None:
            return VisionResult(success=False, error=f"Failed to encode {p}")
        content.append(block)
    content.append({"type": "text", "text": prompt})

    try:
        client = LLMClient.for_role(role)
        resp = client.call(
            messages=[{"role": "user", "content": content}],
            system=system or "你是一个视觉分析专家。",
            caller=caller,
        )
        text = resp.content[0].text if resp.content else ""
        tokens = getattr(resp.usage, "input_tokens", 0) + getattr(resp.usage, "output_tokens", 0)
        return VisionResult(success=True, text=text, model_used=client.model, tokens_used=tokens)
    except Exception as e:
        logger.warning("vision multi call failed: %s", e)
        return VisionResult(success=False, error=str(e))
