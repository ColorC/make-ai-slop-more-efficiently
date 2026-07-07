"""Forgiving material structure validators for BOSS SIGHT reviewstage.

Validators never reject a material. They only return warning records that are
stored in material.extra.structure_warnings and history.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


TEXT_KINDS = {"markdown", "html", "key_question", "custom_web_template", "webgame-spec"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _warning(code: str, message: str, *, path: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": code,
        "severity": "warning",
        "message": message,
    }
    if path:
        item["path"] = path
    return item


def _parse_json(content: str | None) -> tuple[Any | None, str | None]:
    if not content or not content.strip():
        return None, "content is empty"
    try:
        return json.loads(content), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"


def validate_material_structure(
    *,
    kind: str,
    title: str,
    inline_content: str | None,
    file_relpath: str | None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    extra = extra or {}
    title = (title or "").strip()
    content = inline_content or ""

    if len(title) < 5:
        warnings.append(_warning("title_too_short", "title is very short", path="title"))

    if kind in TEXT_KINDS and not content.strip() and not file_relpath:
        warnings.append(_warning("text_content_empty", "text material has no readable content", path="content"))

    if kind == "markdown" and content.strip():
        if not re.search(r"^#{1,6}\s+\S+", content, flags=re.MULTILINE):
            warnings.append(_warning("markdown_missing_heading", "markdown has no heading", path="inline_content"))
        # 仅匹配全大写的真占位标记;占位标记按惯例全大写。不加 IGNORECASE,
        # 否则正文里描述状态值的小写 todo/draft/done 等普通词会被误报。
        if re.search(r"\b(TODO|TBD|FIXME)\b", content):
            warnings.append(_warning("markdown_has_placeholder", "markdown contains TODO/TBD/FIXME markers", path="inline_content"))

    # live_url 型 html 材料的真内容是实时网页(iframe), inline_content 只是回退说明,
    # 不该按"完整 html 文档"校验。仅对纯 inline html 才查 fragment/script。
    if kind == "html" and content.strip() and not str(extra.get("live_url") or "").strip():
        lower = content.lower()
        if "<script" in lower:
            warnings.append(_warning("html_contains_script", "html contains script tags", path="inline_content"))
        if "<html" not in lower and "<body" not in lower and "<!doctype" not in lower:
            warnings.append(_warning("html_fragment_only", "html looks like a fragment without html/body root", path="inline_content"))

    if kind == "key_question":
        data, err = _parse_json(content)
        if err:
            warnings.append(_warning("key_question_invalid_json", err, path="inline_content"))
        elif not isinstance(data, dict):
            warnings.append(_warning("key_question_not_object", "key_question payload should be a JSON object", path="inline_content"))
        else:
            if not str(data.get("question") or "").strip():
                warnings.append(_warning("key_question_missing_question", "key_question.question is missing", path="question"))
            options = data.get("options")
            if options is not None and not isinstance(options, list):
                warnings.append(_warning("key_question_options_not_list", "key_question.options should be a list", path="options"))

    if kind == "custom_web_template":
        schema_id = str(extra.get("data_schema_id") or "").strip()
        data, err = _parse_json(content)
        if not schema_id:
            warnings.append(_warning("custom_template_missing_schema", "custom_web_template is missing extra.data_schema_id", path="extra.data_schema_id"))
        if err:
            warnings.append(_warning("custom_template_invalid_json", err, path="inline_content"))
        elif not isinstance(data, (dict, list)):
            warnings.append(_warning("custom_template_unexpected_json", "custom_web_template payload should be an object or list", path="inline_content"))
        elif schema_id == "branch_storyline_v1":
            nodes = data.get("nodes") if isinstance(data, dict) else None
            if not isinstance(nodes, list) or not nodes:
                warnings.append(_warning("branch_storyline_missing_nodes", "branch_storyline_v1 payload should include a non-empty nodes list", path="nodes"))
        elif schema_id == "filetree_diff_v1":
            files = data.get("files") if isinstance(data, dict) else None
            if not isinstance(files, list):
                warnings.append(_warning("filetree_diff_missing_files", "filetree_diff_v1 payload should include a files list", path="files"))

    # webgame-spec: 主体型审阅材料, 法定三件套(引导演示/文档/文件树 diff)。仅警告不拒绝。
    if kind == "webgame-spec":
        if content.strip() and not re.search(r"^#{1,6}\s+\S+", content, flags=re.MULTILINE):
            warnings.append(_warning("webgame_spec_missing_heading", "webgame-spec spec 报告没有标题(应是 wiki-core markdown 文档)", path="inline_content"))
        for key, label in (
            ("demo", "引导演示(tour / html live_url 材料)"),
            ("doc", "文档(wiki 文档页 / 材料)"),
            ("filetree_diff", "文件树 diff 兄弟材料"),
        ):
            if not str(extra.get(key) or "").strip():
                warnings.append(_warning(
                    f"webgame_spec_missing_{key}",
                    f"webgame-spec 缺三件套之一: {label} — 在 extra.{key} 给出材料 id 或链接",
                    path=f"extra.{key}",
                ))

    if kind == "image" and file_relpath:
        suffix = Path(file_relpath).suffix.lower()
        if suffix and suffix not in IMAGE_EXTS:
            warnings.append(_warning("image_unusual_extension", f"image file extension is unusual: {suffix}", path="file_relpath"))

    return warnings


# ── 阻断校验(kind ↔ 文件类型): validate_material_structure(仅警告)的硬性半边 ──────
# 2026-07-02 教训: .md 按 kind=static-report(=静态报告**网页**, HtmlMaterialView iframe
# 渲染)提交, 全链路绿灯, 审阅台里 markdown 原文被当 HTML 展示。kind 决定渲染器,
# 与内容格式不匹配必须在 store.create 咽喉阻断, 不能指望提交方望文生义选对。
KIND_FILE_EXTS: dict[str, set[str]] = {
    "image": IMAGE_EXTS,
    "markdown": {".md", ".markdown", ".mdx", ".txt"},
    "plan": {".md", ".markdown", ".mdx", ".txt"},
    "agent-workflow-report": {".md", ".markdown", ".mdx", ".txt"},
    "html": {".html", ".htm"},
    "static-report": {".html", ".htm"},
    "demo": {".html", ".htm"},
    "video": {".mp4", ".webm", ".mov", ".m4v"},
}

_WEB_INLINE_KINDS = {"html", "static-report", "demo"}


def validate_kind_file_compat(
    *,
    kind: str,
    inline_content: str | None,
    file_relpath: str | None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """kind 与内容格式的兼容性硬校验。返回 None=放行; 返回 message=阻断。

    宽松位(贴合本模块 forgiving 与 Format 可扩展设计):
    - KIND_FILE_EXTS 表外的 kind(如 Format 扩展 kind)一律放行;
    - 带 extra.live_url 的网页类放行 — 真内容是实时网页, inline/file 只是回退;
    - 文件无扩展名放行(提交层会按 kind 补默认后缀);
    - inline 路线只对网页类做轻量嗅探, 文本类 inline 不设限。
    """
    extra = extra or {}
    if str(extra.get("live_url") or "").strip():
        return None
    if file_relpath:
        allowed = KIND_FILE_EXTS.get(kind)
        suffix = Path(file_relpath).suffix.lower()
        if allowed is not None and suffix and suffix not in allowed:
            return (
                f"kind={kind} 期望 {'/'.join(sorted(allowed))} 文件, 收到 {suffix}。"
                "markdown 报告请用 kind=markdown(或 plan/agent-workflow-report); "
                "static-report/demo/html 是自包含 HTML 网页(或带 extra.live_url)。"
            )
        return None
    content = (inline_content or "").strip()
    if kind in _WEB_INLINE_KINDS and content:
        lower = content.lower()
        looks_html = "<html" in lower or "<!doctype" in lower or "<body" in lower
        looks_md = bool(re.search(r"^#{1,6}\s+\S+", content, flags=re.MULTILINE))
        if not looks_html and looks_md:
            return (
                f"kind={kind} 期望完整 HTML 网页, 但 inline 内容像 markdown"
                "(有 # 标题、无 <html>/<!doctype> 骨架)。markdown 报告请用 kind=markdown。"
            )
    return None


__all__ = ["TEXT_KINDS", "KIND_FILE_EXTS", "validate_material_structure", "validate_kind_file_compat"]
