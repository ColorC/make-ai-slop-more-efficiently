# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.signals.self_awareness_engine.implementation.py"
"""MirrorNode — 系统的 0 号节点：自我认知引擎

理论对应：
  03§三   永远激活的镜像算子，输入自身源代码，输出自我认知字典
  终点§5   冷启动中 MirrorNode 最先初始化
  终点§6   自我认知 = Value(omnicompany.markdown.self_concept)

核心机制：
  1. AST 扫描 omnicompany/src 关键模块 → 提取类/函数/类型签名
  2. 通过结构化 prompt 让 LLM 生成自我认知 Markdown
  3. 以源码 hash 作为缓存 key，代码不变则不重复调用 LLM
  4. 进化修改代码后 invalidate() → 下次 get_current_concept() 自动刷新

输入 Format: fs.directory.omnicompany_src
输出 Format: omnicompany.markdown.self_concept
"""

from __future__ import annotations

import ast
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)

# 需要扫描的关键模块（按层次组织）
_KEY_MODULES: list[tuple[str, str]] = [
    # (相对路径, 层次标签)
    # Protocol — 语义原语层
    ("protocol/format.py", "Protocol"),
    ("protocol/anchor.py", "Protocol"),
    ("protocol/pipeline.py", "Protocol"),
    # Runtime — 执行与路由层（当前生产版本）
    ("runtime/runner.py", "Runtime"),
    ("runtime/agent_loop.py", "Runtime"),
    ("runtime/semantic_router.py", "Runtime"),   # 主路由+痛觉系统（生产）
    ("runtime/boltzmann_router.py", "Runtime"),
    ("runtime/tool_executor.py", "Runtime"),
    ("runtime/guardian.py", "Runtime"),
    ("runtime/meta_guardian.py", "Runtime"),
    ("runtime/diag_engine.py", "Runtime"),
    # Evolution — 进化引擎层
    ("evolution/strategy.py", "Evolution"),
    ("evolution/runner.py", "Evolution"),
    ("evolution/pioneer.py", "Evolution"),
    # NOTE: route_graph.py and pain_system.py are RETIRED/LEGACY — excluded intentionally
]

# 自我认知生成 prompt —— 结构化输出，最大化单次 LLM 调用的信息密度
_CONCEPT_SYSTEM = (
    "你是一个严谨的系统架构分析器。"
    "基于给定的源代码签名，输出结构化 Markdown 自我认知文档。"
    "不要编造不存在的模块或功能。只基于签名中能看到的信息。"
)

_CONCEPT_PROMPT_TEMPLATE = """\
你是 OmniCompany 系统的自我认知引擎（MirrorNode）。
以下是你自身源代码的关键模块签名：

{module_summaries}

---

源码哈希: {src_hash}
扫描时间: {scan_time}

请用 Markdown 生成一份**精确、结构化**的自我认知文档。要求：

1. **我是什么**（一句话定位 omnicompany 系统的本质）
2. **架构层次**（协议层 / 总线层 / 运行时 / 进化引擎 — 每层列出关键类）
3. **核心能力**
   - 类型系统与语义路由（semantic_router.py，含 embedding + BM25 + pain 积累）
   - 管线执行与工具调度（runner.py + agent_loop.py + tool_executor.py）
   - 痛觉系统：pain_score 在 semantic_nodes 表中，由 record_outcome() 更新（非 pain_system.py）
   - 守护与诊断（guardian.py + meta_guardian.py + diag_engine.py）
   - 进化/变异能力（evolution/）
4. **关键数据流**（user_request → intent → route → tool → result 的链路）
5. **当前限制**（基于签名中能看到的缺失或标记为 TODO 的部分）
6. **同构特性**（运行时/进化/元进化是否共用相同基础设施）

输出要求：
- 纯 Markdown，不要代码块包裹
- 每个章节 2-4 行，总长度不超过 800 字
- 精确：只描述签名中可见的内容，不推测"""


class MirrorNode:
    """镜像算子 — 系统的 0 号节点

    永远保持激活。监控 omnicompany/src 源代码变化，
    源码哈希变化时通过 LLM 重新生成自我认知字典。

    语义网络中的核心价值：
    MirrorNode 的输出经 Truth Injection 注入到每次 LLM 调用中，
    使得 Agent 的每一步推断都携带全局架构视野。
    这是"单次信号 > 大规模训练"的具体体现——
    一份精确的自我认知文档，比 1000 次无方向的 LLM 调用更有信息价值。
    """

    def __init__(
        self,
        src_root: Path,
        llm_client: "LLMClient | None" = None,
        cache_path: Path | None = None,
    ):
        self.src_root = Path(src_root)
        self.llm = llm_client
        self.cache_path = cache_path or self.src_root / "data" / "self_concept.md"
        self._last_hash: str = ""
        self._concept: str = ""

        self._try_load_cache()

    def get_current_concept(self) -> str:
        """获取当前自我认知字典（带缓存）。

        缓存策略：源码哈希不变则返回缓存，变化则重新生成。
        """
        current_hash = self._compute_src_hash()
        if current_hash == self._last_hash and self._concept:
            return self._concept

        logger.info(
            "Source hash changed (%s -> %s), regenerating self-concept...",
            self._last_hash[:8] or "empty", current_hash[:8],
        )
        self._concept = self._generate_concept(current_hash)
        self._last_hash = current_hash
        self._persist()
        return self._concept

    def invalidate(self) -> None:
        """进化修改了代码后强制刷新。

        下次 get_current_concept() 会重新扫描 + 生成。
        """
        self._last_hash = ""

    def get_module_signatures(self) -> dict[str, list[str]]:
        """提取所有关键模块的 AST 签名（公开给测试/调试用）。"""
        result: dict[str, list[str]] = {}
        for mod_rel, layer in _KEY_MODULES:
            full = self.src_root / "src" / "omnicompany" / mod_rel
            if not full.exists():
                continue
            sigs = self._extract_signatures(full)
            result[f"[{layer}] {mod_rel}"] = sigs
        return result

    # ────────────────────────────────────────────────
    # Internal
    # ────────────────────────────────────────────────

    def _compute_src_hash(self) -> str:
        """计算源代码目录的聚合哈希（只扫描 .py 文件）。"""
        hasher = hashlib.sha256()
        src_dir = self.src_root / "src" / "omnicompany"
        if not src_dir.exists():
            src_dir = self.src_root
        for py_file in sorted(src_dir.rglob("*.py")):
            try:
                hasher.update(py_file.read_bytes())
            except (OSError, PermissionError):
                continue
        return hasher.hexdigest()[:16]

    def _extract_signatures(self, filepath: Path) -> list[str]:
        """从单个 Python 文件提取类、函数、数据类签名。"""
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return [f"[parse error: {filepath.name}]"]

        sigs: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                bases = [self._name_of(b) for b in node.bases]
                base_str = f"({', '.join(bases)})" if bases else ""
                sigs.append(f"class {node.name}{base_str}")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not item.name.startswith("_"):
                            args = self._func_args(item)
                            sigs.append(f"  .{item.name}({args})")
            elif isinstance(item := node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not item.name.startswith("_"):
                    args = self._func_args(item)
                    sigs.append(f"def {item.name}({args})")
        return sigs[:30]

    @staticmethod
    def _name_of(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return "?"

    @staticmethod
    def _func_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = []
        for a in node.args.args:
            if a.arg == "self":
                continue
            annotation = ""
            if a.annotation and isinstance(a.annotation, ast.Name):
                annotation = f": {a.annotation.id}"
            args.append(f"{a.arg}{annotation}")
        if len(args) > 4:
            return ", ".join(args[:3]) + ", ..."
        return ", ".join(args)

    def _generate_concept(self, src_hash: str) -> str:
        """通过 LLM 生成自我认知文档。

        这是语义网络中信息密度最高的操作之一：
        一次 LLM 调用，将散布在数十个模块中的结构信息
        浓缩为一份 ~800 字的结构化认知文档。
        """
        if self.llm is None:
            return self._generate_offline_concept(src_hash)

        module_summaries = self._build_module_summaries()
        scan_time = datetime.now(timezone.utc).isoformat()

        prompt = _CONCEPT_PROMPT_TEMPLATE.format(
            module_summaries=module_summaries,
            src_hash=src_hash,
            scan_time=scan_time,
        )

        try:
            response = self.llm.call(
                messages=[{"role": "user", "content": prompt}],
                system=_CONCEPT_SYSTEM,
            )
            concept = ""
            for block in (response.content or []):
                if hasattr(block, "text"):
                    concept = block.text.strip()
                    break
            if concept:
                logger.info(
                    "Self-concept generated: %d chars, hash=%s",
                    len(concept), src_hash[:8],
                )
                return concept
        except Exception as e:
            logger.warning("LLM concept generation failed: %s", e)

        return self._generate_offline_concept(src_hash)

    def _build_module_summaries(self) -> str:
        """构建结构化的模块签名摘要（LLM prompt 的输入）。"""
        sections: list[str] = []
        for mod_rel, layer in _KEY_MODULES:
            full = self.src_root / "src" / "omnicompany" / mod_rel
            if not full.exists():
                continue
            sigs = self._extract_signatures(full)
            if sigs:
                sig_text = "\n".join(f"  {s}" for s in sigs)
                sections.append(f"### [{layer}] {mod_rel}\n{sig_text}")
        return "\n\n".join(sections)

    def _generate_offline_concept(self, src_hash: str) -> str:
        """无 LLM 时的降级方案：直接从 AST 签名生成简要文档。"""
        lines = [
            "# OmniCompany Self-Concept (offline mode)",
            "",
            f"Source hash: {src_hash}",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Module Signatures",
            "",
        ]
        for mod_rel, layer in _KEY_MODULES:
            full = self.src_root / "src" / "omnicompany" / mod_rel
            if not full.exists():
                continue
            sigs = self._extract_signatures(full)
            lines.append(f"### [{layer}] {mod_rel}")
            for s in sigs:
                lines.append(f"- {s}")
            lines.append("")
        return "\n".join(lines)

    def _persist(self) -> None:
        """持久化自我认知到磁盘缓存。"""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            header = (
                f"<!-- Generated: {datetime.now(timezone.utc).isoformat()} "
                f"hash: {self._last_hash} -->\n\n"
            )
            self.cache_path.write_text(
                header + self._concept, encoding="utf-8",
            )
        except (OSError, PermissionError) as e:
            logger.warning("Failed to persist self-concept: %s", e)

    def _try_load_cache(self) -> None:
        """启动时尝试从磁盘加载缓存的自我认知。"""
        if not self.cache_path.exists():
            return
        try:
            text = self.cache_path.read_text(encoding="utf-8")
            if "hash:" in text:
                import re
                m = re.search(r"hash:\s*([a-f0-9]+)", text)
                if m:
                    self._last_hash = m.group(1)
                    body_start = text.find("-->")
                    if body_start >= 0:
                        self._concept = text[body_start + 3:].strip()
                        logger.info(
                            "Loaded cached self-concept (hash=%s, %d chars)",
                            self._last_hash[:8], len(self._concept),
                        )
        except Exception:
            pass
