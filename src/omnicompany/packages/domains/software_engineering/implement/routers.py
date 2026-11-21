# [OMNI] origin=claude-code domain=software_engineering/implement ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.implement.pipeline_routers.implementation.py"
"""sw_implement.routers — 独立实施管线 Router (v2: 共享 Format 数据结构)

5 个节点:
  1 HARD:      req_parser      → sw.task-input schema
  1 HARD:      codebase_scanner → sw.project-snapshot + sw.file-batch schema
  1 SOFT:      context_judge    → context-state (回路)
  1 SOFT/LLM:  implementor      → sw.change-set schema
  1 确定性:     report_emitter   → sw.report schema
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.domains.software_engineering._shared.common_formats import (
    truncate_file_content,
    needs_agent_loop,
    MAX_TREE_BYTES,
)

logger = logging.getLogger(__name__)

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
              "dist", "build", ".tox", ".pytest_cache", ".egg-info", "_graveyard"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ReqParser — sw.task-input 结构
# ═══════════════════════════════════════════════════════════════════════════════

class ReqParserRouter(Router):
    FORMAT_IN = "sw_implement.task"
    FORMAT_OUT = "sw_implement.snapshot"
    DESCRIPTION = "解析实施需求，输出 sw.task-input 结构"

    def run(self, input_data: Any) -> Verdict:
        # 兼容旧入口 (req_text) 和新入口 (task_text)
        task_text = (input_data.get("task_text")
                     or input_data.get("req_text") or "").strip()
        task_path = (input_data.get("task_path")
                     or input_data.get("req_path") or "").strip()
        project_dir = (input_data.get("project_dir") or "").strip()
        scope = input_data.get("scope", "feature")
        related_files = input_data.get("related_files", [])

        if not task_text and task_path:
            p = Path(task_path)
            if not p.exists():
                return Verdict(kind=VerdictKind.FAIL,
                               diagnosis=f"文件不存在: {task_path}")
            try:
                task_text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return Verdict(kind=VerdictKind.FAIL,
                               diagnosis=f"读取失败: {e}")

        if not task_text:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="需要 task_text 或 task_path")

        # 输出: sw.task-input 结构 + pipeline 上下文
        output = {
            # sw.task-input schema
            "task_text": task_text,
            "project_dir": project_dir,
            "task_type": "implement",
            "scope": scope,
            "related_files": related_files,
            # pipeline 上下文 (下游节点填充)
            "snapshot": {},
            "file_batch": [],
            "context": {"iteration": 0, "sufficient": False},
        }
        return Verdict(
            kind=VerdictKind.PASS, output=output,
            diagnosis=f"需求解析 ({len(task_text)} chars, scope={scope})",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CodebaseScanner — sw.project-snapshot + sw.file-content 结构
# ═══════════════════════════════════════════════════════════════════════════════

class CodebaseScannerRouter(Router):
    FORMAT_IN = "sw_implement.snapshot"
    FORMAT_OUT = "sw_implement.context-state"
    DESCRIPTION = "扫描项目目录，输出 project-snapshot + file-batch"

    def run(self, input_data: Any) -> Verdict:
        data = input_data
        project_dir = data.get("project_dir", "")

        if not project_dir:
            data["context"]["sufficient"] = True
            return Verdict(kind=VerdictKind.PASS, output=data,
                           diagnosis="无项目目录")

        base = Path(project_dir)
        if not base.exists():
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"目录不存在: {project_dir}")

        # ── 构建 sw.project-snapshot ──
        tree_lines = []
        key_files = []
        file_count = 0
        lang_counts: dict[str, int] = {}

        for root, dirs, files in os.walk(str(base)):
            depth = Path(root).relative_to(base).parts
            if len(depth) > 3:
                dirs.clear()
                continue
            dirs[:] = [d for d in sorted(dirs)
                       if d not in _SKIP_DIRS and not d.startswith(".")]
            indent = "  " * len(depth)
            tree_lines.append(f"{indent}{Path(root).name}/")
            for f in sorted(files)[:30]:
                fpath = Path(root) / f
                rel = str(fpath.relative_to(base)).replace("\\", "/")
                tree_lines.append(f"{indent}  {f}")
                file_count += 1
                ext = fpath.suffix.lower()
                if ext in (".py", ".js", ".ts", ".tsx", ".rs", ".go", ".java"):
                    lang_counts[ext] = lang_counts.get(ext, 0) + 1
                fl = f.lower()
                if fl in ("readme.md", "pyproject.toml", "setup.py",
                          "package.json", "main.py", "app.py", "__init__.py"):
                    key_files.append(rel)
                elif "config" in fl or fl.startswith("conftest"):
                    key_files.append(rel)
            if file_count > 500:
                break

        tree_text = "\n".join(tree_lines[:200])
        if len(tree_text) > MAX_TREE_BYTES:
            tree_text = tree_text[:MAX_TREE_BYTES] + "\n... (truncated)"
        primary_lang = max(lang_counts, key=lang_counts.get) if lang_counts else "unknown"

        data["snapshot"] = {
            "tree": tree_text,
            "primary_language": primary_lang,
            "file_count": file_count,
            "key_files": key_files[:25],
            "top_level_dirs": [d.name for d in base.iterdir()
                               if d.is_dir() and d.name not in _SKIP_DIRS
                               and not d.name.startswith(".")],
        }

        # ── 构建 sw.file-content 列表（关键文件）──
        # 包括 related_files + key_files
        target_files = list(data.get("related_files", []))
        for kf in key_files:
            if kf not in target_files:
                target_files.append(kf)

        file_batch = []
        for rel_path in target_files[:15]:
            full = base / rel_path
            if not full.exists() or not full.is_file():
                continue
            try:
                raw = full.read_text(encoding="utf-8", errors="replace")
                content, truncated = truncate_file_content(raw)
                imports = [l.strip() for l in raw.splitlines()[:50]
                          if l.strip().startswith(("import ", "from ", "require("))]
                sigs = re.findall(
                    r'^(?:def |class |function |async function |export ).*',
                    raw, re.MULTILINE)
                file_batch.append({
                    "path": rel_path,
                    "language": full.suffix.lstrip("."),
                    "size_bytes": len(raw),
                    "content": content,
                    "imports": imports[:15],
                    "signatures": sigs[:20],
                    "truncated": truncated,
                })
            except Exception:
                pass

        data["file_batch"] = file_batch

        return Verdict(
            kind=VerdictKind.PASS, output=data,
            diagnosis=f"扫描 {file_count} 文件, 读取 {len(file_batch)} 个 file-content",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ContextJudge — 上下文充分性（回路）
# ═══════════════════════════════════════════════════════════════════════════════

class ContextJudgeRouter(Router):
    FORMAT_IN = "sw_implement.context-state"
    FORMAT_OUT = "sw_implement.context-state"
    DESCRIPTION = "判断是否已收集足够上下文"

    def run(self, input_data: Any) -> Verdict:
        data = input_data
        ctx = data.get("context", {})
        iteration = ctx.get("iteration", 0)
        file_batch = data.get("file_batch", [])
        project_dir = data.get("project_dir", "")

        if not project_dir:
            ctx["sufficient"] = True
            data["context"] = ctx
            return Verdict(kind=VerdictKind.PASS, output=data,
                           diagnosis="无项目目录")

        issues = []
        if not file_batch:
            issues.append("未读取任何文件")
        if not data.get("snapshot", {}).get("tree"):
            issues.append("未扫描目录")

        # 检查 task_text 中提到的文件
        task_text = data.get("task_text", "")
        mentioned = re.findall(r'[\w/\\]+\.\w{1,5}', task_text)
        read_paths = {f["path"] for f in file_batch}
        for mf in mentioned[:10]:
            basename = Path(mf).name
            if not any(basename in rp for rp in read_paths):
                if iteration < 2:
                    data.setdefault("related_files", []).append(mf)
                    issues.append(f"提到 {basename} 但未读取")

        if issues and iteration < 2:
            ctx["iteration"] = iteration + 1
            ctx["sufficient"] = False
            data["context"] = ctx
            return Verdict(kind=VerdictKind.PARTIAL, output=data,
                           diagnosis=f"上下文不充分 ({len(issues)} 问题)")

        ctx["sufficient"] = True
        data["context"] = ctx
        return Verdict(kind=VerdictKind.PASS, output=data,
                       diagnosis=f"上下文充分: {len(file_batch)} 文件")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Implementor — sw.change-set 结构
# ═══════════════════════════════════════════════════════════════════════════════

_IMPL_SYSTEM = """\
你是一名高级软件工程师。根据需求和代码库上下文，生成所需的代码变更。

**规则：**
1. 遵循现有代码的架构模式和命名规范
2. 包含必要的测试
3. 处理边界条件和错误情况
4. 新建文件用 full_content，修改文件用 diff (unified format)

**输出 JSON 格式：**
```json
{
  "changes": [
    {
      "path": "相对路径",
      "action": "create|modify|delete",
      "full_content": "完整代码 (create 时)",
      "diff": "unified diff (modify 时)",
      "rationale": "变更理由"
    }
  ],
  "description": "变更集描述",
  "test_cmd": "验证命令 (可选)"
}
```
"""


class ImplementorRouter(Router):
    FORMAT_IN = "sw_implement.context-state"
    FORMAT_OUT = "sw_implement.changes"
    DESCRIPTION = "LLM 生成 change-set (agent_loop 节点)"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=8192,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        data = input_data
        snapshot = data.get("snapshot", {})
        file_batch = data.get("file_batch", [])

        # 构建已读文件上下文
        existing = ""
        for fc in file_batch[:5]:
            content = fc.get("content", "")[:2000]
            existing += f"\n## {fc['path']}\n```\n{content}\n```\n"

        prompt = f"""根据需求和现有代码实现变更:

## 需求
{data['task_text'][:8000]}

## 项目架构
- 主语言: {snapshot.get('primary_language', 'unknown')}
- 目录:
```
{snapshot.get('tree', '无')[:2000]}
```

## 现有代码
{existing[:6000]}

请输出 JSON (changes + description + test_cmd)。"""

        print("[*] Calling LLM for implementation...")
        try:
            client = self._make_client()
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=self._maybe_inject_reflection(_IMPL_SYSTEM),
            )
            text = resp.content[0].text

            # 反思：解析自评 + 信息不足拦截
            sa, text = self._parse_self_assessment(text)
            partial = self._check_reflection_partial(sa, text, data)
            if partial:
                return partial

            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
            else:
                result = json.loads(text)

            # ── 构建 sw.change-set ──
            changes = result.get("changes", [])
            change_set = {
                "changes": changes,
                "description": result.get("description", ""),
                "test_cmd": result.get("test_cmd", ""),
                "needs_agent_loop": needs_agent_loop(changes),
            }

            # 写入文件
            project_dir = data.get("project_dir", "")
            if project_dir:
                base = Path(project_dir)
                for c in changes:
                    if c["action"] == "delete":
                        fpath = base / c["path"]
                        if fpath.exists():
                            fpath.unlink()
                    elif c.get("full_content"):
                        fpath = base / c["path"]
                        fpath.parent.mkdir(parents=True, exist_ok=True)
                        # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                        fpath.write_text(c["full_content"], encoding="utf-8")

            data["change_set"] = change_set
            return Verdict(
                kind=VerdictKind.PASS, output=data,
                diagnosis=f"实施 {len(changes)} 变更",
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=data,
                           diagnosis=f"实施失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ReportEmitter — sw.report 结构
# ═══════════════════════════════════════════════════════════════════════════════

class ReportEmitterRouter(Router):
    FORMAT_IN = "sw_implement.changes"
    FORMAT_OUT = "sw_implement.report"
    DESCRIPTION = "汇总实施报告"

    def run(self, input_data: Any) -> Verdict:
        data = input_data
        change_set = data.get("change_set", {})
        changes = change_set.get("changes", [])

        # 兼容旧格式
        if not changes:
            changes = data.get("impl_files", [])

        lines = [
            "═" * 55,
            "🔧 IMPLEMENTATION REPORT",
            "═" * 55,
            "",
            f"Files changed: {len(changes)}",
            "",
        ]

        if changes:
            lines.append("── 文件变更 ──")
            for c in changes:
                action = c.get("action", "create")
                icon = {"create": "✨", "modify": "📝", "delete": "🗑️"}.get(action, "📄")
                lines.append(f"  {icon} [{action}] {c.get('path', 'unknown')}")
                if c.get("rationale"):
                    lines.append(f"      → {c['rationale']}")
            lines.append("")

        if change_set.get("description"):
            lines.append("── 摘要 ──")
            lines.append(change_set["description"])
            lines.append("")

        conclusion = "DONE"
        lines.append("── 结论 ──")
        lines.append(f"✅ {conclusion}")
        lines.append("═" * 55)

        report_text = "\n".join(lines)
        print(f"\n{report_text}\n")

        # ── 输出 sw.report 结构 ──
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "report_text": report_text,
                "report": report_text,       # 兼容旧字段
                "conclusion": conclusion,
                "metrics": {
                    "files_changed": len(changes),
                    "creates": sum(1 for c in changes if c.get("action") == "create"),
                    "modifies": sum(1 for c in changes if c.get("action") == "modify"),
                    "deletes": sum(1 for c in changes if c.get("action") == "delete"),
                },
                "change_set": change_set,
                "project_dir": data.get("project_dir", ""),
            },
            diagnosis=f"报告: {len(changes)} 文件变更",
        )
