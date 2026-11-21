# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.rules.format_router_observability.scanner.py"
"""Guardian 规则 — Format/Router 可观测性 (OMNI-017/018/019/020)。

OMNI-017: package 有 pipeline.py 但 core/pipelines.py 没注册它
OMNI-018: routers.py 里的 Router 子类没被同 package 的 pipeline.py / run.py 引用
OMNI-019: Format 的 description 字段太短(< 100 字符)
OMNI-020: Router 类的 DESCRIPTION 太短(< 50 字符)或缺 FORMAT_IN/OUT
"""
from __future__ import annotations

import ast as _ast
import re
from pathlib import Path

from ._base import FileContext, GuardianRule, _is_external

_PIPELINES_PY_CACHE: dict | None = None  # (mtime, content)


def _read_pipelines_py(root: Path) -> str:
    """读取 core/pipelines.py 内容（cached by mtime）。"""
    global _PIPELINES_PY_CACHE
    p = root / "src" / "omnicompany" / "core" / "pipelines.py"
    if not p.exists():
        return ""
    mtime = p.stat().st_mtime
    if _PIPELINES_PY_CACHE and _PIPELINES_PY_CACHE[0] == mtime:
        return _PIPELINES_PY_CACHE[1]
    try:
        content = p.read_text(encoding="utf-8")
        _PIPELINES_PY_CACHE = (mtime, content)
        return content
    except OSError:
        return ""


def _check_format_not_observable(ctx: FileContext) -> bool:
    """OMNI-017: 该 package 有 pipeline.py 但 core/pipelines.py 没注册它。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("/pipeline.py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if "/packages/vendors/" in p:
        return False

    # 提取 package 名: src/omnicompany/packages/<layer>/<name>/pipeline.py
    rest = p[len("src/omnicompany/packages/"):]
    parts = rest.split("/")
    if len(parts) < 3:
        return False
    layer, name = parts[0], parts[1]
    if layer not in ("domains", "services"):
        return False

    # 子 package 跳过（只检查顶层 package 的 pipeline.py）
    if parts[2] != "pipeline.py":
        return False

    # 从 ctx.abs_path 反推 root
    abs_path = Path(ctx.abs_path)
    try:
        idx = abs_path.parts.index("src")
        root = Path(*abs_path.parts[:idx])
    except ValueError:
        return False

    pipelines_content = _read_pipelines_py(root)
    if not pipelines_content:
        return False

    target = f"omnicompany.packages.{layer}.{name}"
    return target not in pipelines_content


_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(\s*(\w+)\s*\)", re.MULTILINE)


def _check_router_not_observable(ctx: FileContext) -> bool:
    """OMNI-018: routers.py 里的 Router 子类没被同 package 的 pipeline.py / run.py 引用。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("/routers.py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if "/packages/vendors/" in p:
        return False
    if ctx.content is None:
        return False

    # 找该文件里所有 class XxxRouter(...) 定义
    classes = []
    for m in _CLASS_RE.finditer(ctx.content):
        cls_name, base = m.group(1), m.group(2)
        if base in ("Router",) or cls_name.endswith("Router"):
            classes.append(cls_name)
    if not classes:
        return False

    # 在同 package 找 pipeline.py / run.py / build_bindings.py
    abs_path = Path(ctx.abs_path)
    pkg_dir = abs_path.parent
    sibling_files = [
        pkg_dir / "pipeline.py",
        pkg_dir / "run.py",
        pkg_dir / "__init__.py",
    ]
    for f in pkg_dir.iterdir():
        if f.is_file() and f.suffix == ".py" and f != abs_path and f not in sibling_files:
            sibling_files.append(f)

    # 任何 sibling 文件提到这个 Router 类名 → 算被引用
    referenced = set()
    for sib in sibling_files:
        if not sib.exists():
            continue
        try:
            text = sib.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for cls in classes:
            if cls in text:
                referenced.add(cls)
        if len(referenced) == len(classes):
            return False  # 全部被引用，不命中

    return len(referenced) < len(classes)


_FORMAT_CALL_RE = re.compile(
    r"Format\s*\(\s*[^)]*?id\s*=\s*[^,)]+(?P<rest>.*?)\)",
    re.DOTALL,
)
_DESC_KW_RE = re.compile(r'description\s*=\s*["\'](.*?)["\']', re.DOTALL)


def _check_format_thin_description(ctx: FileContext) -> bool:
    """OMNI-019: Format(...) 的 description < 100 字符。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("/formats.py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if "/packages/vendors/" in p:
        return False
    if ctx.content is None:
        return False

    descs = _DESC_KW_RE.findall(ctx.content)
    if not descs:
        return False
    thin = [d for d in descs if len(d) < 100]
    return len(thin) > 0


def _check_router_thin_description(ctx: FileContext) -> bool:
    """OMNI-020: Router 类的 DESCRIPTION < 50 或 缺 FORMAT_IN/OUT。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("/routers.py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if "/packages/vendors/" in p:
        return False
    if ctx.content is None:
        return False

    if "class " not in ctx.content or "Router" not in ctx.content:
        return False

    n_classes = len(_CLASS_RE.findall(ctx.content))
    if n_classes == 0:
        return False

    short_desc_count = 0
    for m in re.finditer(r'DESCRIPTION\s*=\s*["\'](.*?)["\']', ctx.content, re.DOTALL):
        if len(m.group(1)) < 50:
            short_desc_count += 1

    n_in = ctx.content.count("FORMAT_IN")
    n_out = ctx.content.count("FORMAT_OUT")
    missing_io = (n_in < n_classes) or (n_out < n_classes)

    return short_desc_count > 0 or missing_io


# ─── OMNI-025: FORMAT_IN/OUT 使用 f-string ────────────────────────

def _check_fstring_format_id(ctx: FileContext) -> bool:
    """OMNI-025: FORMAT_IN/OUT 使用 f-string，Doctor 无法静态分析契约。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith(".py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if ctx.content is None:
        return False
    c = ctx.content
    if "FORMAT_IN" not in c and "FORMAT_OUT" not in c:
        return False
    try:
        tree = _ast.parse(c)
    except SyntaxError:
        return False
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, _ast.Assign):
                continue
            for t in stmt.targets:
                if isinstance(t, _ast.Name) and t.id in ("FORMAT_IN", "FORMAT_OUT"):
                    if isinstance(stmt.value, _ast.JoinedStr):
                        return True
    return False


# ─── OMNI-026: FORMAT_IN/OUT 为列表 ───────────────────────────────

def _check_list_format_id(ctx: FileContext) -> bool:
    """OMNI-026: FORMAT_IN/OUT 为列表，应是单一 Format ID 字符串。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith(".py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if ctx.content is None:
        return False
    c = ctx.content
    if "FORMAT_IN" not in c and "FORMAT_OUT" not in c:
        return False
    try:
        tree = _ast.parse(c)
    except SyntaxError:
        return False
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, _ast.Assign):
                continue
            for t in stmt.targets:
                if isinstance(t, _ast.Name) and t.id in ("FORMAT_IN", "FORMAT_OUT"):
                    if isinstance(stmt.value, _ast.List):
                        return True
    return False


# ─── OMNI-029: Router 绕过 SQLiteBus 直接调用检测 ──────────────────────────────


_RUNNER_RE = re.compile(r"\bPipelineRunner\b")
_ROUTER_BYPASS_BUS_EXEMPTIONS: tuple[str, ...] = (
    # The scanner source contains direct-run patterns by definition.
    "src/omnicompany/packages/services/_core/guardian/rules/observability.py",
    # LLM-CALL-UNIFICATION T13 (2026-06-13): this orchestrator directly
    # sequences evolution workflow stages, while its boundaries are now
    # published through packages.services._core.evolution.workflow.events.
    "src/omnicompany/packages/services/_core/evolution/workflow/orchestrator.py",
)
# 匹配 SomeXxxRouter().run( 或 self._xxx.run( 等模式
_DIRECT_RUN_RE = re.compile(
    r"(?:"
    r"[A-Z]\w*Router\s*\([^)]*\)\s*\.run\s*\("  # ClassRouter(...).run(
    r"|"
    r"self\._\w+\.run\s*\("                       # self._router.run(
    r")",
)


def _check_router_bypass_bus(ctx: FileContext) -> bool:
    """OMNI-029: 在非测试文件中，检测 Router.run() 直接调用且该文件不用 TeamRunner。

    确定性初筛（needs_judgment → LLM 复核确认是否真的绕过了 bus）：
    - 文件包含 Router().run( 或 self._x.run( 模式
    - 同一文件没有 TeamRunner 引用
    - 不是测试文件（tests/ 下允许直接调用单节点）
    - 是 packages/ 下的 Python 文件
    """
    p = ctx.path.replace("\\", "/")
    if not p.endswith(".py"):
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx):
        return False
    if p in _ROUTER_BYPASS_BUS_EXEMPTIONS:
        return False
    # 测试文件豁免
    if "/tests/" in p or p.endswith("_test.py") or p.endswith("test_.py"):
        return False
    c = ctx.content
    if not c:
        return False

    # 必须有 Router.run() 直接调用模式
    if not _DIRECT_RUN_RE.search(c):
        return False

    # 如果同文件有 TeamRunner，初筛不触发（LLM 判断）
    if _RUNNER_RE.search(c):
        return False

    # 至少 2 处直接调用才触发（避免单节点工具函数误报）
    matches = _DIRECT_RUN_RE.findall(c)
    return len(matches) >= 2


def _check_composite_component_missing(ctx: FileContext) -> bool:
    """OMNI-027: formats.py 中 composite Format 引用了同文件未定义的 component Format ID。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("formats.py") and "/formats/" not in p:
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx) or not ctx.content:
        return False
    c = ctx.content
    try:
        tree = _ast.parse(c)
    except SyntaxError:
        return False

    # 收集同文件中所有 Format(id="...") 定义的 ID
    defined_ids: set[str] = set()
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, _ast.Name) else (
            func.attr if isinstance(func, _ast.Attribute) else None
        )
        if name != "Format":
            continue
        for kw in node.keywords:
            if kw.arg == "id" and isinstance(kw.value, _ast.Constant):
                defined_ids.add(str(kw.value.value))

    # 检查 components=[...] 里是否有未定义的 ID
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, _ast.Name) else (
            func.attr if isinstance(func, _ast.Attribute) else None
        )
        if name != "Format":
            continue
        for kw in node.keywords:
            if kw.arg == "components" and isinstance(kw.value, _ast.List):
                for elt in kw.value.elts:
                    if isinstance(elt, _ast.Constant):
                        comp_id = str(elt.value)
                        if comp_id not in defined_ids:
                            return True
    return False


def _check_composite_no_intent(ctx: FileContext) -> bool:
    """OMNI-028: composite Format 的 description 未说明组合意图。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("formats.py") and "/formats/" not in p:
        return False
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if _is_external(ctx) or not ctx.content:
        return False
    c = ctx.content
    try:
        tree = _ast.parse(c)
    except SyntaxError:
        return False

    _INTENT_WORDS = ("由", "组合", "包含", "汇聚", "composed", "contains", "aggregates", "combines")

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, _ast.Name) else (
            func.attr if isinstance(func, _ast.Attribute) else None
        )
        if name != "Format":
            continue
        has_components = any(
            kw.arg == "components" and isinstance(kw.value, _ast.List) and kw.value.elts
            for kw in node.keywords
        )
        if not has_components:
            continue
        # 找 description 字段
        description = ""
        for kw in node.keywords:
            if kw.arg == "description" and isinstance(kw.value, _ast.Constant):
                description = str(kw.value.value)
                break
        if not any(word in description for word in _INTENT_WORDS):
            return True
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-017",
        name="format-not-observable",
        severity="HIGH",
        description="package 有 pipeline.py 但 core/pipelines.py 没注册它（Format/Router/事件不会进 dashboard 监视范围）",
        check=_check_format_not_observable,
        disposition=["warn"],
        message_template="{path}: 该 package 有 pipeline.py 但未在 core/pipelines.py 注册。Format / Router / event 都不会出现在 dashboard 或 'omni formats' / 'omni routers' 输出里。请在 core/pipelines.py 的 register_all() 加一个 PipelineEntry。",
    ),
    GuardianRule(
        id="OMNI-018",
        name="router-not-observable",
        severity="HIGH",
        description="routers.py 里定义的 Router 子类没被同 package 的 pipeline.py / run.py 引用",
        check=_check_router_not_observable,
        disposition=["warn"],
        message_template="{path}: 至少一个 Router 子类在 routers.py 定义但没被同 package 的 pipeline.py 或 run.py 引用。这是死代码，事件出不来。",
    ),
    GuardianRule(
        id="OMNI-019",
        name="format-thin-description",
        severity="INFO",
        description="Format 的 description 字段太短(< 100 字符)，没说清楚做什么/需要什么/产出什么",
        check=_check_format_thin_description,
        disposition=["warn"],
        message_template="{path}: 至少一个 Format 的 description < 100 字符。建议补充'是什么/包含哪些字段/上下游 Format 关系'的语义说明。",
    ),
    GuardianRule(
        id="OMNI-020",
        name="router-thin-description",
        severity="INFO",
        description="Router 类的 DESCRIPTION 太短(< 50 字符)或缺 FORMAT_IN/OUT 声明",
        check=_check_router_thin_description,
        disposition=["warn"],
        message_template="{path}: 至少一个 Router 类的 DESCRIPTION < 50 字符或缺 FORMAT_IN/FORMAT_OUT 声明。",
    ),
    GuardianRule(
        id="OMNI-025",
        name="format-id-fstring",
        severity="HIGH",
        description="FORMAT_IN/OUT 使用 f-string，Doctor 无法静态分析契约",
        check=_check_fstring_format_id,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的 FORMAT_IN/OUT 使用 f-string。"
            "请改为字符串字面量（直接写 'domain.format-id'）。"
            "f-string 导致 Doctor 将 FORMAT_IN 视为 None，签名检查失效。"
        ),
    ),
    GuardianRule(
        id="OMNI-026",
        name="format-id-list",
        severity="HIGH",
        description="FORMAT_IN/OUT 不得为列表，应是单一 Format ID 字符串",
        check=_check_list_format_id,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的 FORMAT_IN/OUT 为列表。"
            "Router 类的 FORMAT_IN 应始终是单一字符串。"
            "多源输入有两种正确实现：\n"
            "① 在 pipeline.py 的 AnchorSpec 中声明 format_in=[...] 并等待 fan-in；\n"
            "② 定义 composite Format（Format.components=[...]），FORMAT_IN 指向该复合 Format ID。"
        ),
    ),
    GuardianRule(
        id="OMNI-027",
        name="composite-format-missing-component",
        severity="HIGH",
        description="composite Format（有 components 字段）引用了同文件中未定义的 Format ID",
        check=_check_composite_component_missing,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的 composite Format 引用了未在同文件定义的 component Format ID。"
            "请确保所有 component 在 formats.py 中先行注册，或检查 Format ID 拼写。"
        ),
    ),
    GuardianRule(
        id="OMNI-028",
        name="composite-format-no-intent",
        severity="INFO",
        description="composite Format 的 description 未说明组合意图（缺乏'由...组成'等说明）",
        check=_check_composite_no_intent,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的 composite Format 有 components 字段，但 description 未说明组合意图。"
            "建议在 description 中加入'由 X/Y/Z 组成'或'汇聚 X 和 Y'等说明，"
            "以便 Doctor 叙事审计能理解该 Format 的设计意图。"
        ),
    ),
    GuardianRule(
        id="OMNI-029",
        name="router-bypass-bus",
        severity="CRITICAL",
        description="Router.run() 在 TeamRunner 外被直接调用，input/output 不进 SQLiteBus，违反完整可审计架构",
        check=_check_router_bypass_bus,
        disposition=["warn"],
        certainty="needs_judgment",
        message_template=(
            "{path} 疑似在 TeamRunner 外直接调用 Router.run()。\n"
            "【架构红线】所有 Router 的 input/output 必须通过 SQLiteBus 记录，无豁免权。\n"
            "正确方式：将 Router 链封装为 TeamSpec，通过 TeamRunner.run() 驱动。\n"
            "直接调用 SomeRouter().run(data) 会绕过事件总线，导致该节点的输入输出不可审计、\n"
            "无法回溯、无法验证 '上游 output = 下游 input' 的数据一致性。\n"
            "测试文件（tests/）中的单节点测试属于豁免，其余一律违规。"
        ),
    ),
]
