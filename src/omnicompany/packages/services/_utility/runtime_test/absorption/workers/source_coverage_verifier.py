# [OMNI] origin=claude-code domain=services/absorption_runtime_test/workers ts=2026-04-27T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.absorption.source_coverage_verifier.agent.py"
"""SourceCoverageVerifierWorker — Worker #5 (路 4 · absorbing 特化).

升级版 (2026-04-27):
- 旧实现: LLM 自由用 glob/list_dir 探目录 + 自由判定"关键模块" — 主观成分大
- 新实现: 程序化排名 (引用数 + LOC) 产 top-K 候选池 → LLM 在候选里选 5-10 语义关键 + 给 reason
  - 候选池 = 客观度量 (减少 LLM 自评成分)
  - LLM 仅选语义关键子集 (LLM 该做的事)

仅适用 absorbing 类目标 (target 消费 repo_path 类源仓库).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    GlobRouter,
    ListDirRouter,
    ReadFileRouter,
    SingleToolRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SOURCE_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".kt",
    ".swift", ".scala", ".lua", ".sh",
)
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", "dist", "build", ".next", ".cache",
    "_archive", "_graveyard", "vendors",
}
_TOP_K_DEFAULT = 30
_MAX_SCAN_FILES = 5000
_MAX_FILE_LINES_FOR_REF_SCAN = 2000


def _walk_source_files(repo_root: Path) -> list[Path]:
    """递归收集 repo 内源文件 (跳过常见非源目录)."""
    out: list[Path] = []
    for path in repo_root.rglob("*"):
        if len(out) >= _MAX_SCAN_FILES:
            break
        if not path.is_file():
            continue
        # skip 隐藏/明显非源目录
        if any(part in _SKIP_DIRS or part.startswith(".") for part in path.parts):
            continue
        if path.suffix.lower() not in _SOURCE_EXTENSIONS:
            continue
        out.append(path)
    return out


def _rank_files_by_metrics(
    repo_root: Path, source_files: list[Path], top_k: int
) -> list[dict]:
    """程序化排名: 引用数 + LOC. 返 top-K 候选 [{file, loc, referenced_by_count, rank_score}]."""
    # 算 LOC
    loc_map: dict[Path, int] = {}
    text_cache: dict[Path, str] = {}  # 同时缓存内容供引用扫
    for p in source_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
            loc_map[p] = lines
            if lines <= _MAX_FILE_LINES_FOR_REF_SCAN:
                text_cache[p] = text
        except OSError:
            loc_map[p] = 0

    # 算引用数: 对每文件 basename (无扩展), 数其他源文件里出现次数
    # 单词边界正则避免 substring 误报
    basename_to_path: dict[str, Path] = {}
    for p in source_files:
        basename = p.stem  # 无扩展
        if not basename or basename.startswith("_") or basename in {"index", "main", "__init__"}:
            # 通用名跳过 (太多假阳性)
            continue
        # 同名取较深文件 (大致代表"专业"路径)
        if basename in basename_to_path:
            continue
        basename_to_path[basename] = p

    ref_count: dict[Path, int] = {p: 0 for p in source_files}
    for basename, target_path in basename_to_path.items():
        pattern = re.compile(rf"\b{re.escape(basename)}\b")
        for src_path, src_text in text_cache.items():
            if src_path == target_path:
                continue
            if pattern.search(src_text):
                ref_count[target_path] = ref_count.get(target_path, 0) + 1

    # 综合 score = referenced_by_count * 5 + sqrt(loc) (引用数权重大)
    import math

    candidates: list[dict] = []
    for p in source_files:
        loc = loc_map.get(p, 0)
        refs = ref_count.get(p, 0)
        score = refs * 5 + math.sqrt(loc + 1)
        # rel path
        try:
            rel = str(p.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            rel = str(p)
        candidates.append({
            "file": rel,
            "loc": loc,
            "referenced_by_count": refs,
            "rank_score": round(score, 2),
        })

    candidates.sort(key=lambda c: c["rank_score"], reverse=True)
    return candidates[:top_k]


_SYSTEM_PROMPT = """你是 absorption_runtime_test 路 4 源覆盖验证器.

任务: 收到一个程序化排名好的"候选关键模块池" (top-K 按引用数+LOC 排), 你只做两件事:
1. 在候选里挑 5-10 个**最语义关键**的模块 (架构入口/公共 API/调度器/核心逻辑等)
2. 对照目标团队跨次摸过的文件清单, 标 missed (你判关键但 target 没碰)

**禁**:
- 在候选池外提名模块 (违反"程序化候选"的设计意图)
- 用文件大小 / 引用数等度量代替语义判断 (那些已在候选池里)

**做的**:
- 看候选里每个文件路径 + 用 read_file 抽样验证 (可选)
- 用语义角度判: 这模块是不是架构上重要 / 公共 API / 高传染面

如果 sample_input 没含 repo_path → 设 applicable=false 直接 submit.

最后调 **submit_source_coverage_evidence**.

反模式: 全字段自然语言句子."""


class _PromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        runs_mirror = biz_input.get("_from_SampleRunsExecutorWorker") or {}
        runs = runs_mirror.get("runs") or biz_input.get("runs", [])
        meta_mirror = biz_input.get("_from_TargetIngressWorker") or {}
        sample_input = meta_mirror.get("sample_input") or biz_input.get("sample_input", {})

        repo_path = sample_input.get("repo_path") or sample_input.get("path") or ""

        # 收集目标团队碰过的文件 (跨次合集)
        touched = set()
        for r in runs:
            if r.get("verdict") not in ("PASS", "PARTIAL"):
                continue
            output = r.get("output") or {}
            for p in output.get("proposals", []):
                ref = p.get("reference_code") or {}
                if ref.get("file"):
                    touched.add(ref["file"])

        # 不可判分支
        if not repo_path or not Path(repo_path).is_dir():
            task = f"""## 源覆盖 · 不适用

sample_input 没含 repo_path 或路径不存在: '{repo_path}'.
直接调 **submit_source_coverage_evidence** 提交:
- applicable: false
- coverage_observation: "target 不消费 repo_path 类源仓库, 路 4 不适用"
- candidate_pool_size: 0
- 其他字段空"""
            return [{"role": "user", "content": task}]

        # ── 程序化排名候选池 ──
        repo_root = Path(repo_path)
        source_files = _walk_source_files(repo_root)
        if not source_files:
            task = f"""## 源覆盖 · 仓库无源文件

repo_path='{repo_path}' 没扫到源文件. 直接 submit:
- applicable: false
- coverage_observation: "仓库 {repo_path} 没扫到识别得了的源文件 (扩展名见 _SOURCE_EXTENSIONS)"
- candidate_pool_size: 0"""
            return [{"role": "user", "content": task}]

        candidates = _rank_files_by_metrics(repo_root, source_files, _TOP_K_DEFAULT)

        # 渲染候选池给 LLM
        cand_brief = json.dumps(candidates, ensure_ascii=False, indent=2)

        task = f"""## 源覆盖 · 适用

### 仓库根
- repo_path: `{repo_path}`
- 扫描源文件总数: {len(source_files)}

### 程序化排名候选池 (top {len(candidates)} 按 引用数+LOC 排)

```json
{cand_brief}
```

### 目标团队跨次摸过的文件 ({len(touched)} 个)
{chr(10).join(f'- {f}' for f in sorted(touched))}

### 操作

1. 在候选池里挑 5-10 个**语义最关键**的模块 (不要超出候选池)
2. 每个给 importance_reason (语义角度, 不是"行数大""引用多" — 那是程序化已算的)
3. 对照 target 摸过的文件, 标 missed
4. 调 submit_source_coverage_evidence

提交字段:
- applicable: true
- key_modules_identified: list[{{file, importance_reason, ranked_metrics}}]
  - file 必须在候选池里
  - ranked_metrics 透传候选池里的 {{loc, referenced_by_count, rank_score}}
- key_modules_total: 你挑的数
- key_modules_touched_by_target: list[str] (目标摸过的关键模块)
- key_modules_missed_by_target: list[str] (核心 — 你判关键但 target 没碰)
- coverage_pct: touched / total
- coverage_observation (≥30 字符): 自然语言段子, 评 target 覆盖情况
- candidate_pool_size: {len(candidates)} (透传)

注: 不要列超 10 个关键模块, 只挑你判最关键的."""

        return [{"role": "user", "content": task}]


class SubmitSourceCoverageEvidenceRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "submit_source_coverage_evidence"
    DESCRIPTION: ClassVar[str] = "Submit source coverage evidence."
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "applicable": {"type": "boolean"},
            "key_modules_identified": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "importance_reason": {"type": "string"},
                        "ranked_metrics": {"type": "object"},
                    },
                    "required": ["file", "importance_reason"],
                },
            },
            "key_modules_total": {"type": "integer", "minimum": 0},
            "key_modules_touched_by_target": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_modules_missed_by_target": {
                "type": "array",
                "items": {"type": "string"},
            },
            "coverage_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "coverage_observation": {"type": "string", "minLength": 30},
            "candidate_pool_size": {"type": "integer", "minimum": 0},
        },
        "required": ["applicable", "coverage_observation"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args, ctx) -> str:
        return f"submitted source_coverage_evidence: applicable={args.get('applicable')}"


class _ExtractResult(ExtractResultRouter):
    def extract(self, *, final_text, messages, turn_count, stop_reason) -> Verdict:
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_source_coverage_evidence"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            inp.setdefault("key_modules_identified", [])
                            inp.setdefault("key_modules_total", len(inp["key_modules_identified"]))
                            inp.setdefault("key_modules_touched_by_target", [])
                            inp.setdefault("key_modules_missed_by_target", [])
                            inp.setdefault("coverage_pct", 0.0)
                            inp.setdefault("candidate_pool_size", 0)
                            return Verdict(
                                kind=VerdictKind.PASS,
                                output=dict(inp),
                                diagnosis=f"路 4 evidence: applicable={inp.get('applicable')} pool={inp.get('candidate_pool_size')}",
                                confidence=0.9,
                            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={},
            diagnosis=f"未调 submit_source_coverage_evidence (turns={turn_count})",
        )


class SourceCoverageVerifierWorker(AgentNodeLoop):
    DESCRIPTION: ClassVar[str] = "路 4 源覆盖 (absorbing 特化) · 程序化排名 top-K + LLM 选语义关键 + 看 target 漏."
    FORMAT_IN: ClassVar[list[str]] = [
        "absorption_runtime_test.sample_runs",
        "absorption_runtime_test.target_metadata",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "absorption_runtime_test.source_coverage_evidence"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter,
        GlobRouter,
        ListDirRouter,
        SubmitSourceCoverageEvidenceRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus
        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any):
        return _PromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any):
        return _ExtractResult(bus=bus)
