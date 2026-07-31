# [OMNI] origin=claude-code domain=services/absorption/snapshot.py ts=2026-04-08T12:00:00Z
# [OMNI] material_id="material:learning.absorption.omnicompany_self_capability_scanner.py"
"""absorption.snapshot — OmniCompany 自身能力扫描。

在没有 `omni capabilities` CLI 命令的前提下, 我们直接扫文件系统生成
OmniCompany 当前能力快照, 供 LandmarkPicker 的 LLM 做对照判定。

返回的 snapshot 含 5 类:
  1. packages            — 所有业务包 (services / domains / vendors) + docstring 摘要
  2. registered_pipelines — core/pipelines.py 中 register(PipelineEntry(name=...)) 出现的管线名
  3. routers              — 全仓范围内所有 class ...(Router) 定义 (含子类 LLMRouter/AgentNodeLoop)
  4. builtin_tools        — 新版 Agent 工具注册表中的工具名
  5. core_modules         — core/ 和 runtime/ 下的 .py 模块路径 (供 gap 对照)

本模块是 OmnicompanySnapshotRouter 的纯函数底层, 无副作用, 可独立测试。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """定位 omnicompany 仓库根。"""
    # 本文件位于 <root>/src/omnicompany/packages/services/absorption/snapshot.py
    # parents[5] = services, parents[6] = packages, parents[7] = omnicompany, parents[8] = src, parents[9] = <root>
    return Path(__file__).resolve().parents[5]


def _src_root() -> Path:
    return _project_root() / "src" / "omnicompany"


def scan_packages() -> dict[str, dict[str, Any]]:
    """扫 packages/ 下所有包, 提取 __init__.py docstring + pipeline.py 里的 id/name/description。

    Returns: {"services.absorption": {"docstring": "...", "pipeline_id": "...", ...}}
    """
    out: dict[str, dict[str, Any]] = {}
    pkg_root = _src_root() / "packages"
    if not pkg_root.exists():
        return out

    # 识别"叶子包": 有 __init__.py 且含 pipeline.py 或 routers.py
    for init_file in pkg_root.rglob("__init__.py"):
        pkg_dir = init_file.parent
        rel = pkg_dir.relative_to(pkg_root)
        # 跳过 __pycache__
        if "__pycache__" in rel.parts:
            continue
        has_pipeline = (pkg_dir / "pipeline.py").exists()
        has_routers = (pkg_dir / "routers.py").exists() or (pkg_dir / "routers").is_dir()
        if not (has_pipeline or has_routers):
            continue
        key = ".".join(rel.parts)
        try:
            text = init_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        docstring = _extract_docstring(text)
        entry: dict[str, Any] = {
            "docstring": docstring,
            "relative_path": str(rel).replace("\\", "/"),
        }
        if has_pipeline:
            try:
                pipe_text = (pkg_dir / "pipeline.py").read_text(encoding="utf-8", errors="replace")
                entry["pipeline_ids"] = _extract_pipeline_ids(pipe_text)
                entry["node_count"] = _count_pipeline_nodes(pipe_text)
            except Exception:
                pass
        out[key] = entry
    return out


def scan_registered_pipelines() -> list[dict[str, str]]:
    """扫 core/pipelines.py 的 register(PipelineEntry(name=..., description=...)) 调用。"""
    core_pipelines = _src_root() / "core" / "pipelines.py"
    if not core_pipelines.exists():
        return []
    try:
        text = core_pipelines.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    # 正则匹配 register(PipelineEntry( 开始到 )) 结束的 block (简单版)
    entries: list[dict[str, str]] = []
    # 找 name="xxx" 和紧邻的 description=
    for m in re.finditer(
        r'register\(\s*PipelineEntry\([^)]*?name\s*=\s*["\']([^"\']+)["\'].*?description\s*=\s*\(?\s*["\']([^"\']+)["\']',
        text,
        re.DOTALL,
    ):
        entries.append({"name": m.group(1), "description": m.group(2)[:200]})
    return entries


def scan_routers() -> list[dict[str, str]]:
    """扫 src/omnicompany 下所有 Router 类定义。

    Match: `class XxxRouter(Router):` / `class Xxx(LLMRouter):` / `class Xxx(AgentNodeLoop):`
    """
    out: list[dict[str, str]] = []
    src = _src_root()
    router_pat = re.compile(r"^class\s+(\w+)\s*\(\s*(Router|LLMRouter|AgentNodeLoop)\s*\)\s*:", re.MULTILINE)
    for py in src.rglob("*.py"):
        if "__pycache__" in py.parts or "_graveyard" in py.parts or "_archive" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in router_pat.finditer(text):
            class_name = m.group(1)
            base = m.group(2)
            # Try to grab DESCRIPTION class var (next ~30 lines)
            start = m.end()
            tail = text[start : start + 2000]
            desc_m = re.search(r'DESCRIPTION\s*[:=]\s*\(?\s*"([^"]+)"', tail)
            description = desc_m.group(1)[:200] if desc_m else ""
            rel = py.relative_to(src).as_posix()
            out.append({
                "class": class_name,
                "base": base,
                "file": rel,
                "description": description,
            })
    return out


def scan_builtin_tools() -> list[str]:
    """提取新版 Agent 工具注册表中的工具名。"""
    try:
        from omnicompany.dashboard import native_agent_tools  # noqa: F401
        from omnicompany.packages.services._core.agent.configurable import (
            TOOL_REGISTRY,
            auto_register_singletool_subclasses,
        )
    except Exception:
        return []
    auto_register_singletool_subclasses()
    return sorted(TOOL_REGISTRY.keys())


def scan_core_modules() -> list[str]:
    """列出 core/ 和 runtime/ 下所有 .py 模块的相对路径。"""
    out: list[str] = []
    src = _src_root()
    for sub in ("core", "runtime", "protocol", "bus"):
        d = src / sub
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            out.append(py.relative_to(src).as_posix())
    return sorted(out)


def _extract_docstring(text: str) -> str:
    """从 Python 模块源码中提取顶层 docstring (前 500 字符)。"""
    m = re.search(r'^\s*(?:#[^\n]*\n)*\s*(?:""")([\s\S]*?)(?:""")', text)
    if not m:
        m = re.search(r"^\s*(?:#[^\n]*\n)*\s*(?:''')([\s\S]*?)(?:''')", text)
    if not m:
        return ""
    doc = m.group(1).strip()
    return doc[:500]


def _extract_pipeline_ids(text: str) -> list[str]:
    """从 pipeline.py 中提取 TeamSpec(id="...") 的 id 值。"""
    ids = re.findall(r'TeamSpec\([^)]*?id\s*=\s*f?["\']([^"\']+)["\']', text, re.DOTALL)
    return list(set(ids))


def _count_pipeline_nodes(text: str) -> int:
    """粗略统计 pipeline.py 里 TeamNode 定义数。"""
    return len(re.findall(r"\bPipelineNode\(", text))


def build_snapshot() -> dict[str, Any]:
    """组装完整 OmniCompany 能力快照。"""
    snapshot = {
        "packages": scan_packages(),
        "registered_pipelines": scan_registered_pipelines(),
        "routers": scan_routers(),
        "builtin_tools": scan_builtin_tools(),
        "core_modules": scan_core_modules(),
    }
    return snapshot


def snapshot_stats(snapshot: dict[str, Any]) -> dict[str, int]:
    """返回各类数量, 用于 diagnosis 消息。"""
    return {
        "packages": len(snapshot.get("packages", {})),
        "registered_pipelines": len(snapshot.get("registered_pipelines", [])),
        "routers": len(snapshot.get("routers", [])),
        "builtin_tools": len(snapshot.get("builtin_tools", [])),
        "core_modules": len(snapshot.get("core_modules", [])),
    }
