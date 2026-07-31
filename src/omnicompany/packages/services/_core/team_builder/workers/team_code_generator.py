# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-24T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.code_generator_loop.engine.py"
# OMNI-024 ALLOW: 域内私有 worker, 一类一文件经 workers/__init__ 聚合出口, 类实现与本文件模块级 prompt/helper/装配紧耦合, 迁入共享 routers.py 会割裂内聚
"""CodeGeneratorLoopWorker — Phase 8 · AgentNodeLoop (2026-04-24 · 铁律对齐 fix).

**V3.1 修正 (2026-04-24)**:
2026-04-23 V3 首跑 FAIL 根因 (event.db + llm_audit 确认, 非推测):
  - output_tokens=16895 > max_tokens=16384 · LLM 调 finish(input={}) 空参数结束
  - response_text 仅 67 chars · 产物丢失
  - 命中 `memory/feedback_agent_loop_finish_delivery.md` 已立铁律

按 "堵不如疏" 四步改:
  1. 自定义 FinishRouter · reason + result 双必填 (schema 强制)
  2. 产物改 ===FILE=== 块文本 (不再 JSON dict · 节省 ~30% 转义 token)
  3. ExtractResult 缺文件 → PARTIAL + needs_retry (不 FAIL)
  4. Worker.run() 补产循环 · 带 "缺 X 文件" 反馈唤醒 LLM (参考 hifi_mockup)

Worker 协议 (composite fan-in · 5 路 · 不变):
  FORMAT_IN  = [team_design, workspace_spec, worker_design_detailed,
                material_design_detailed, design_validation_report]
  FORMAT_OUT = team_builder.material.code_package (team_name + target_package_path + files dict)
  (Registrar 契约不变, 仅内部产出协议改为文本块解析)

仅 design_validation_report.overall != FAIL 时激活.
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
    GrepRouter,
    ListDirRouter,
    ReadFileRouter,
    SingleToolRouter,
)
from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_team_package_path,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.protocol.anchor import Verdict, VerdictKind


_REQUIRED_FILES = ("formats.py", "team.py", "run.py", "__init__.py", "DESIGN.md")


# ═══════════════════════════════════════════════════════════════════════
# _CodeGenFinishRouter — 覆盖默认 finish, 强制 reason + result (堵不如疏 step 1)
# ═══════════════════════════════════════════════════════════════════════

class _CodeGenFinishRouter(SingleToolRouter):
    """强制 reason + result 双必填的 finish tool.

    TOOL_NAME == "finish" 让 AgentNodeLoop.__init__ 不再补默认 FinishRouter.
    """
    TOOL_NAME: ClassVar[str] = "finish"
    DESCRIPTION: ClassVar[str] = (
        "Complete the task. BOTH 'reason' AND 'result' are required. "
        "'result' must contain the full deliverables as plain text with "
        "===MANIFEST=== block listing all files, followed by ===FILE: <name>=== / ===END=== "
        "blocks (one per file) with complete file content. "
        "Empty 'result' or placeholder ('Done'/'See above') will NOT actually end the task — "
        "the Worker's structural check will detect incomplete output and wake you up to continue."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "One-sentence reason. Examples: 'all files produced' / "
                    "'partial, still need formats.py and DESIGN.md' / "
                    "'blocked: cannot determine target_path'. Empty reason = retry."
                ),
                "minLength": 1,
            },
            "result": {
                "type": "string",
                "description": (
                    "FULL deliverables text. MUST start with ===MANIFEST=== block listing all "
                    "generated files (one per line), followed by one ===FILE: <name>=== / ===END=== "
                    "block per file with complete content. "
                    "Placeholder like 'See above' or 'Done' triggers Worker retry."
                ),
                "minLength": 200,
            },
        },
        "required": ["reason", "result"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        # loop.py L199 直接取 tool_args['result'] 作 final_text, 本方法实际上不会被调用.
        return f"[finish invoked] reason={args.get('reason', '')}"


# ═══════════════════════════════════════════════════════════════════════
# System prompt — 堵不如疏 + 两种交付方式 + 反例
# ═══════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是 team_builder 第 8 阶段 · CodeGeneratorLoop agent.

## 职责
依完整设计 (team_design + worker_detailed × N + material_detailed × M + workspace_spec)
产出**完整可运行 Python 代码**. 不直接落盘, 提交文本块给下游 Registrar.

## 工具
- read_file / grep / glob / list_dir: **必用** · 看 packages/services/doctor/ 等 similar team 真代码,
  学 import 风格 + Worker 基类继承 + register_formats 模式 + build_team/build_bindings 签名
- finish: 提交完整产物文本 (见下文交付协议)

## 产物文件清单 (manifest 必列)

必填:
- `formats.py`: Material 定义 + `def register_formats(registry)` 函数
- `team.py`: `def build_team() -> TeamSpec` 返回 TeamSpec (含 nodes + edges)
- `run.py`: `def build_bindings(input_dict=None)` 返回 `dict[str, Worker]`
- `workers/__init__.py`: 导出所有 Worker 类 + ALL_WORKERS list
- `workers/<worker_name>.py` × N: 每个 Worker 类实装
- `DESIGN.md`: OMNI-034 七节 (状态/核心目的/核心接口/架构规则/数据流/已知局限/参考资料)
- `__init__.py`: package 入口
- `.omni/workspace.yaml`: 来自 workspace_spec

## 硬约束

### Worker 基类
```python
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

class FooWorker(Worker):
    DESCRIPTION = "..."  # >= 20 字符
    FORMAT_IN = "material_id"  # 或 list[str]
    FORMAT_OUT = "material_id"

    def run(self, input_data: dict) -> Verdict:
        ...
        return Verdict(kind=VerdictKind.PASS, output={...})
```

### ServiceBus 铁律 (白黑名单 · 反例逐条 · 不允许演绎)

**必须走 ServiceBus (仅以下 3 种)**:
1. **写磁盘** → `DiskBus.write(path, content)` / `DiskBus.append(path, content)` / `DiskBus.copy(src, dst)` / `DiskBus.delete(path)`
2. **subprocess** → `BashBus.run(cmd, cwd=...)` (真启动子进程, 如 git/npm/gradle/python 脚本)
3. **HTTP 请求** → `WebBus.fetch(url, ...)` (真对外网络调用, 如 LLM API / 第三方 API)

**不得走 ServiceBus (直接用 Python 内建)**:
- **读磁盘** → `Path(path).read_text(encoding='utf-8')` / `open(path, 'r')` (DiskBus **没有** read 方法)
- **打印 stdout / stderr** → `print(text)` / `sys.stdout.write(...)` · 禁 `BashBus.run("echo ...")` / `BashBus.run("cat <<EOF ...")`
- **日志** → `logging.getLogger(__name__).info(...)` · 禁走 Bus
- **纯 Python 处理** (csv.DictReader / json.loads / re / pathlib 只读) 直接用

**反例 · LLM 常犯 (骨架会 lint 拦)**:
```python
# ❌ 反例 1: 用 BashBus 打印 stdout
bash_bus.run("cat <<EOF\n" + text + "\nEOF", cwd=...)  # print(text) 就够
# ❌ 反例 2: 用 DiskBus 读文件
disk_bus.read_file(path)  # DiskBus 没 read! 用 Path(path).read_text()
# ❌ 反例 3: 用 WebBus 本地通信
web_bus.fetch("http://localhost:...")  # 本地/IPC 不用 WebBus
```

**判据**: ServiceBus 存在的理由是**审计 + 权限 + 防危险**. 只读文件 / print / 日志 **没风险**, 不需审计, 直接用. 只有"改系统状态"(写/启进程/访外网)才走 Bus.

### OmniMark 头
每 Python 文件首行:
```
# [OMNI] origin=claude-code domain=services/<team_name>/<file> ts=YYYY-MM-DDTHH:MM:SSZ type=<worker|team|config>
```

### FORMAT_IN / FORMAT_OUT
**严格复用** material_detailed 里的 material_id (逐字节一致).

## 诚实
- 不糊弄: 每 Worker.run() 要有真实现 · 不允许 `pass` 或 `raise NotImplementedError`
- 不虚构: 不 import 不存在的模块
- 不超纲: 不实现设计外的功能

## 交付协议 (堵不如疏 · 两种合法方式)

### 方式 A: 纯 text 输出 (推荐 · 最稳)
最后一轮响应只输出 text (含 ===MANIFEST=== + ===FILE=== 块), 不调任何工具.

### 方式 B: 调 finish(reason, result)
```
finish(
    reason="all files produced" | "partial, still need X" | "blocked: <cause>",
    result="===MANIFEST===\\nformats.py\\nteam.py\\n...\\n===END===\\n\\n===FILE: formats.py===\\n...\\n===END===\\n..."
)
```

- **reason 必填** (简短说明你的状态, 不是 prompt 回显)
- **result 必填** (完整产物文本, 不是"Done"或"See above")

### 输出格式规范 (两种方式都用这个格式)

```
===MANIFEST===
formats.py
team.py
run.py
__init__.py
workers/__init__.py
workers/<name1>.py
workers/<name2>.py
DESIGN.md
.omni/workspace.yaml
===END===

===FILE: formats.py===
# [OMNI] origin=claude-code domain=services/<team_name>/formats ts=... type=format
<完整代码>
===END===

===FILE: team.py===
# [OMNI] origin=claude-code domain=services/<team_name>/team ts=... type=team
<完整代码>
===END===

... (一个 ===FILE=== 块 per file, 所有 manifest 里的文件都要有对应块)
```

### 完善度由 Worker 客观判定 (不是 LLM 自述)

Worker 会用**结构检查**判断产物完善度:
- manifest 列的每个文件是否都有对应 ===FILE=== 块
- 5 必填文件 (formats.py / team.py / run.py / __init__.py / DESIGN.md) 是否齐
- 每个 .py 文件是否含 OmniMark 头
- DESIGN.md 是否含 OMNI-034 七节关键词

**不完善 = Worker 会带"你说 reason=X 但 judge 发现还缺 Y"的消息重新唤醒你继续**.
所以:
- ✅ 用 finish 结束是 OK 的, reason 真实说明状态
- ✅ 产物不完整也可以 finish, 说明 reason="partial, still need X"
- ❌ 糊弄的 reason + 空 result = 被唤醒重做, 浪费 turns
- ❌ text 里说"让我来创建..." 然后 finish() 没填 result = 丢产物反例

### 反例 (过往 FAIL 真实案例)
- LLM 调 `finish({})` 空参数 → 产物 0 文件 FAIL (2026-04-23 V3 首跑)
- LLM 调 `finish(result="See above")` 占位 → 结构判据 0 files FAIL

### 自检清单 (交付前默念)
1. 我这轮产出是否含完整 ===MANIFEST=== 块?
2. 每个 manifest 列的文件都有对应 ===FILE=== / ===END=== 包裹块?
3. 5 必填文件 (formats/team/run/__init__/DESIGN) 齐全?
4. 每个 .py 文件首行是 `# [OMNI] ...` 头?
5. reason 是真实状态 (非 prompt 回显 / 非"done")?

## 调研铁律
先 grep / read_file 调研 `src/omnicompany/packages/services/doctor/` (或其他 similar team) 的
真实 formats.py / team.py / run.py / workers/ 结构, 学 import + 签名 + Worker 基类继承.

**绝对不允许**:
- 跳过调研直接写代码 (会产幻觉 import)
- 用 `pass` 或 `raise NotImplementedError` 糊弄 Worker.run()
- 虚构不存在的 import
"""


# ═══════════════════════════════════════════════════════════════════════
# 文本块解析 (参考 hifi_mockup 的 ===FILE=== / ===MANIFEST=== 协议)
# ═══════════════════════════════════════════════════════════════════════

_MANIFEST_RE = re.compile(r"===MANIFEST===\s*\n(.*?)\n===END===", re.DOTALL)
_FILE_BLOCK_RE = re.compile(
    r"===FILE:\s*([^=\n]+?)\s*===\s*\n(.*?)\n===END===", re.DOTALL
)


def _parse_manifest(text: str) -> list[str]:
    if not text:
        return []
    m = _MANIFEST_RE.search(text)
    if not m:
        return []
    return [line.strip() for line in m.group(1).splitlines() if line.strip()]


def _parse_file_blocks(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    blocks = []
    for m in _FILE_BLOCK_RE.finditer(text):
        name = m.group(1).strip()
        content = m.group(2)
        if name and content:
            blocks.append((name, content))
    return blocks


# ═══════════════════════════════════════════════════════════════════════
# Prompt builder — 含补产模式 retry 注入
# ═══════════════════════════════════════════════════════════════════════

class _CodeGeneratorPromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        team_design = biz_input.get("_from_team_architect") or {}
        workspace_spec = biz_input.get("_from_workspace_designer") or {}
        design_validation = biz_input.get("_from_design_validator") or {}

        workers_detailed: list = []
        materials_detailed: list = []
        for key, val in biz_input.items():
            if isinstance(key, str) and isinstance(val, dict):
                if key.startswith("_from_worker_designer"):
                    ds = val.get("details")
                    if isinstance(ds, list):
                        workers_detailed = ds
                elif key.startswith("_from_material_designer"):
                    ds = val.get("details")
                    if isinstance(ds, list):
                        materials_detailed = ds

        # 补产模式上下文 (堵不如疏 · step 4)
        retry_missing = biz_input.get("_retry_missing") or []
        retry_existing = biz_input.get("_retry_existing_files") or []
        retry_structural = biz_input.get("_retry_structural_issues") or []
        retry_empty_first = bool(biz_input.get("_retry_empty_first"))
        in_retry = bool(retry_missing or retry_structural or retry_empty_first)

        task = f"""## team_design 骨架

```json
{json.dumps(team_design, ensure_ascii=False, indent=2)[:2500]}
```

## workspace_spec

```json
{json.dumps(workspace_spec, ensure_ascii=False, indent=2)}
```

## worker_design_detailed x {len(workers_detailed)}

```json
{json.dumps(workers_detailed, ensure_ascii=False, indent=2)[:4500]}
```

## material_design_detailed x {len(materials_detailed)}

```json
{json.dumps(materials_detailed, ensure_ascii=False, indent=2)[:4500]}
```

## design_validation_report (overall = PASS, 可安全生成)

{json.dumps({k: v for k, v in design_validation.items() if k != "_meta"}, ensure_ascii=False, indent=2)[:1200]}
"""

        if in_retry:
            task += f"""

---

## ⚠️ 补产模式 (上轮产物不完整)

上一轮的结果:
- 空产出 (首产调 finish 没传 result): {retry_empty_first}
- 已产出文件: {retry_existing if retry_existing else '(无)'}
- 缺失文件: {retry_missing}
- 结构问题: {retry_structural if retry_structural else '(无)'}

**操作要求**:
- 聚焦补产**缺失文件** + 修复**结构问题**
- 已产出的文件可保留原样 (manifest 仍列出, ===FILE=== 块给出原内容或扩写)
- 特别注意: 交付时 `finish(reason, result)` 的 `result` 字段**必填**, 不能为空或占位
- 首产是空调用 finish() 的 → 这次务必按 system prompt "交付协议" 产出完整 ===MANIFEST=== + ===FILE=== 块
"""
        else:
            task += """

---

## 操作要求
1. 先 grep / read_file 调研 `src/omnicompany/packages/services/doctor/` 的真实代码结构
2. 按 system prompt 硬约束产出**完整** Python 代码
3. 按交付协议 (===MANIFEST=== + ===FILE=== 块) 产出, 推荐方式 A (纯 text) 或方式 B (finish with reason+result)
"""

        return [{"role": "user", "content": task}]


# ═══════════════════════════════════════════════════════════════════════
# ExtractResult — 文本块解析 + 结构判据 + PARTIAL+needs_retry
# ═══════════════════════════════════════════════════════════════════════

_OMNI_MARK_RE = re.compile(r"^#\s*\[OMNI\]", re.MULTILINE)

# 骨架 lint: LLM 过度演绎 ServiceBus 铁律的已知反模式
# 反例来源: 2026-04-24 V3 真跑 · csv_reader 用 DiskBus.read_file / stdout_sink 用 BashBus.run("cat EOF")
# 匹配 heuristic: 按 import + 调用模式综合判 (LLM 常见变量名 bash_bus / disk_bus / web_bus, 但也可能别名)
_BUS_ABUSE_PATTERNS = (
    # DiskBus 没 read/open (只读用 Path.read_text)
    (re.compile(r"\.read_file\s*\("), "伪 DiskBus.read_file · 该方法不存在! 只读用 Path(...).read_text() / open(...,'r')"),
    (re.compile(r"\bdisk_bus\s*\.\s*read[\w_]*\s*\("), "DiskBus 无 read* 方法 · 只读走 Path.read_text() 不过 Bus"),
    (re.compile(r"\bdisk_bus\s*\.\s*open\s*\("), "DiskBus 无 open · 只读用 Path.read_text(), 写用 DiskBus.write()"),
    # BashBus 被用来打印/echo/cat (应直接 print)
    (re.compile(r"\.run\s*\(\s*f?['\"][^'\"]{0,60}\bcat\s*<<"), "BashBus 禁 heredoc 打印 stdout · print(text) 替代"),
    (re.compile(r"\.run\s*\(\s*f?['\"][^'\"]{0,60}\b(cat|echo|printf)\s+[\"'$]"), "BashBus 禁 echo/cat/printf 打印 stdout · print(text) 替代"),
    # WebBus 本地通信 (localhost / 127.0.0.1 不走 WebBus)
    (re.compile(r"\bweb_bus\s*\.\s*\w+\s*\(\s*['\"]?(http://)?(localhost|127\.0\.0\.1)"), "WebBus 禁本地通信 · localhost/IPC 不需审计"),
)


def _lint_service_bus_abuse(py_files: list[tuple[str, str]]) -> list[str]:
    """扫所有 .py 文件 · 查 LLM 过度演绎 ServiceBus 的已知反模式.
    返回 issues list (空 = 无反模式). lint 由骨架执行, 不靠 prompt 引导.
    """
    issues = []
    for name, content in py_files:
        if not isinstance(content, str) or not name.endswith(".py"):
            continue
        for pat, hint in _BUS_ABUSE_PATTERNS:
            for m in pat.finditer(content):
                line_num = content[:m.start()].count("\n") + 1
                issues.append(f"{name}:{line_num} · {hint}")
    return issues

# OMNI-034 七节规范名 (权威见 docs/standards/design_md_template.md + guardian/rules/design_md.py::_REQUIRED_SECTIONS):
#   1. 状态  2. 核心目的  3. 核心接口  4. 架构决策  5. 数据流 / 拓扑  6. 已知局限  7. 参考资料
# → 同义词列表 (骨架容忍 LLM 语言差异)
# 铁律 (feedback_100pct_required_goes_to_skeleton): 必做的约束 = 骨架, 不靠 LLM 对齐字串
_DESIGN_SECTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "状态": ("状态", "Status", "status", "状态 / 健康度", "状态/健康度"),
    "核心目的": ("核心目的", "目的", "Purpose", "purpose", "Goal"),
    "核心接口": ("核心接口", "接口", "Interface", "interface", "API", "Interfaces"),
    "架构决策": ("架构决策", "架构规则", "架构约束", "架构", "Rules", "rules", "Constraints", "Architecture", "Decisions"),
    "数据流 / 拓扑": ("数据流 / 拓扑", "数据流/拓扑", "数据流", "拓扑", "Dataflow", "Data Flow", "Flow", "Topology", "数据流 / 契约", "数据流/契约"),
    "已知局限": ("已知局限", "局限", "Limitations", "limitations", "Known Issues", "已知问题"),
    "参考资料": ("参考资料", "参考", "References", "references", "Refs"),
}
_DESIGN_SECTIONS = tuple(_DESIGN_SECTION_SYNONYMS.keys())


def _normalize_design_md(content: str) -> tuple[str, list[str]]:
    """规范化 DESIGN.md 章节名: 任一同义词 → 规范名 (骨架接管约束).

    返回 (规范化后的 content, 仍缺失的规范名列表)
    """
    if not content:
        return content, list(_DESIGN_SECTIONS)

    normalized = content
    # 扫所有 "^## <title>" heading, 若 title 命中某同义词 → 改为规范名
    heading_re = re.compile(r"^(##+)\s+(.+?)\s*$", re.MULTILINE)

    def _replace(m):
        prefix = m.group(1)
        title = m.group(2).strip()
        for canonical, synonyms in _DESIGN_SECTION_SYNONYMS.items():
            if title in synonyms or any(syn in title for syn in synonyms if len(syn) >= 2):
                if title != canonical:
                    return f"{prefix} {canonical}"
                break
        return m.group(0)

    normalized = heading_re.sub(_replace, normalized)

    # 检查规范名是否齐全 (现在用规范名字面扫)
    missing = [sect for sect in _DESIGN_SECTIONS if sect not in normalized]
    return normalized, missing


def _check_design_md(content: str) -> list[str]:
    """返回缺失的 OMNI-034 章节规范名列表 (先规范化再查). 兼容旧调用点."""
    _, missing = _normalize_design_md(content)
    return missing


class _CodeGeneratorExtractResult(ExtractResultRouter):
    def __init__(self, *, bus: Any):
        super().__init__(bus=bus)
        # run-scoped 运行时上下文 (Worker.run 注入, 用于推断 team_name / target_path)
        self._run_context: dict = {}

    def set_run_context(self, *, team_name: str, target_package_path: str) -> None:
        self._run_context = {
            "team_name": team_name,
            "target_package_path": target_package_path,
        }

    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
        team_name = self._run_context.get("team_name") or ""
        target_path = self._run_context.get("target_package_path") or ""

        # 1. 先从 final_text 解析
        files = _parse_file_blocks(final_text)
        manifest_files = _parse_manifest(final_text)

        # 2. fallback: 从 messages (assistant text blocks) 扫
        if not files or not manifest_files:
            for msg in reversed(messages):
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if isinstance(content, str):
                    if not files:
                        files = _parse_file_blocks(content)
                    if not manifest_files:
                        manifest_files = _parse_manifest(content)
                    if files and manifest_files:
                        break

        produced_names = [name for name, _ in files]

        # 3. 空产出 → PARTIAL + needs_retry + empty_first_run
        if not files:
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={
                    "team_name": team_name,
                    "target_package_path": target_path,
                    "file_blocks": [],
                    "produced_files": [],
                    "missing_files": list(_REQUIRED_FILES),
                    "structural_issues": [],
                    "needs_retry": True,
                    "empty_first_run": True,
                    "_meta": {
                        "worker": "CodeGeneratorLoopWorker",
                        "stage": "v3_agent_loop",
                        "turn_count": turn_count,
                        "stop_reason": stop_reason,
                        "final_text_preview": (final_text or "")[:500],
                    },
                },
                diagnosis=f"空产出 · turns={turn_count} stop={stop_reason} · 触发补产 (meta: LLM 可能调 finish 空参数)",
            )

        # 4. 必填文件齐全性
        produced_basenames = {Path(n).name for n in produced_names}
        missing_required = [f for f in _REQUIRED_FILES if f not in produced_basenames]

        # 5. manifest 对应性
        missing_from_manifest = [
            f for f in manifest_files if f not in produced_names and Path(f).name not in produced_basenames
        ]

        # 6. 结构检查 + DESIGN.md 章节名骨架规范化
        structural_issues: list[str] = []
        design_md_idx = -1
        design_md_normalized = None
        for idx, (fname, content) in enumerate(files):
            base = Path(fname).name
            # Python 文件需 OMNI 头
            if base.endswith(".py"):
                if not _OMNI_MARK_RE.search(content or ""):
                    structural_issues.append(f"{base} 缺 OMNI 头")
                if not content or len(content.strip()) < 50:
                    structural_issues.append(f"{base} 内容过短 (<50 char · 疑似残篇)")
            if base == "DESIGN.md":
                design_md_idx = idx
                # 骨架接管章节名规范化: LLM 写 "架构约束"/"Rules"/... → 规范名
                normalized, miss_sect = _normalize_design_md(content or "")
                design_md_normalized = normalized
                if miss_sect:
                    structural_issues.append(f"DESIGN.md 缺 OMNI-034 章节: {miss_sect}")

        # 骨架 lint: ServiceBus 过度演绎反模式 (2026-04-24 V3 实测出 2 类虚构)
        bus_issues = _lint_service_bus_abuse(files)
        if bus_issues:
            structural_issues.extend(bus_issues)

        # 把规范化的 DESIGN.md 写回 files (骨架覆盖 LLM 原写法)
        if design_md_idx >= 0 and design_md_normalized is not None:
            fname, _ = files[design_md_idx]
            files[design_md_idx] = (fname, design_md_normalized)

        all_missing = list({*missing_required, *missing_from_manifest})
        needs_retry = bool(all_missing or structural_issues)

        # 7. 组 files dict (Registrar 契约)
        files_dict: dict[str, str] = {}
        for name, content in files:
            # 保留相对路径 (如 workers/foo.py)
            rel = name.strip()
            files_dict[rel] = content

        base_output = {
            "team_name": team_name,
            "target_package_path": target_path,
            "files": files_dict,
            "file_blocks": list(files),
            "produced_files": produced_names,
            "missing_files": all_missing,
            "structural_issues": structural_issues,
            "needs_retry": needs_retry,
            "_meta": {
                "worker": "CodeGeneratorLoopWorker",
                "stage": "v3_agent_loop",
                "turn_count": turn_count,
                "stop_reason": stop_reason,
                "file_count": len(files_dict),
                "total_bytes": sum(len(c) for c in files_dict.values() if isinstance(c, str)),
                "manifest_files": manifest_files,
            },
        }

        if needs_retry:
            diag = (
                f"code_package 不完整 · 缺文件 {all_missing} · "
                f"结构问题 {structural_issues[:2]}..."
                if structural_issues
                else f"code_package 缺文件 {all_missing}"
            )
            return Verdict(kind=VerdictKind.PARTIAL, output=base_output, diagnosis=diag)

        # 全齐
        return Verdict(
            kind=VerdictKind.PASS,
            output=base_output,
            diagnosis=(
                f"code_package · {len(files_dict)} 文件齐 · "
                f"{base_output['_meta']['total_bytes']} bytes · turns={turn_count}"
            ),
        )


# ═══════════════════════════════════════════════════════════════════════
# CodeGeneratorLoopWorker — 补产循环 (堵不如疏 step 4)
# ═══════════════════════════════════════════════════════════════════════

class CodeGeneratorLoopWorker(AgentNodeLoop):
    """Phase 8 · AgentNodeLoop · 依完整设计产出 Python 代码 files dict (不落盘) · V3.1.

    V3.1 (2026-04-24) 对齐 feedback_agent_loop_finish_delivery 铁律:
    - _CodeGenFinishRouter 覆盖默认 finish (reason + result 双必填)
    - 产物改 ===FILE=== 块文本 (节省 token)
    - PARTIAL + needs_retry 补产循环 (MAX_RETRIES=2)
    """

    FORMAT_IN: ClassVar = [
        "team_builder.material.team_design",
        "team_builder.material.workspace_spec",
        "team_builder.material.worker_design_detailed",
        "team_builder.material.material_design_detailed",
        "team_builder.material.design_validation_report",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.code_package"
    DESCRIPTION: ClassVar[str] = (
        "Phase 8 · AgentNodeLoop · 依 5 路 fan-in 完整设计产出可运行 Python 代码 files dict "
        "(formats/team/run/workers/DESIGN/workspace.yaml) · 不落盘交 Registrar · "
        "V3.1 对齐铁律 (强 finish schema + ===FILE=== 块 + 补产循环)."
    )
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter,
        _CodeGenFinishRouter,  # 覆盖默认 FinishRouter · 强制 reason + result
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT
    MAX_RETRIES: ClassVar[int] = 2

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus
        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _CodeGeneratorPromptBuilder:
        return _CodeGeneratorPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _CodeGeneratorExtractResult:
        return _CodeGeneratorExtractResult(bus=bus)

    def _infer_team_context(self, input_data: Any) -> tuple[str, str]:
        """从 team_design / workspace_spec 推 team_name + target_package_path."""
        if not isinstance(input_data, dict):
            return "", ""
        team_design = input_data.get("_from_team_architect") or {}
        workspace_spec = input_data.get("_from_workspace_designer") or {}
        team_name = (
            team_design.get("team_name")
            or workspace_spec.get("team_name")
            or input_data.get("team_name")
            or ""
        )
        target = (
            workspace_spec.get("target_package_path")
            or team_design.get("target_package_path")
        )
        if not isinstance(team_name, str) or not team_name:
            raise ValueError("team_name is required")
        return team_name, canonical_team_package_path(
            target,
            team_name=team_name,
        )

    async def run(self, input_data: Any) -> Verdict:
        # 条件激活: design_validation_report.overall != FAIL
        if isinstance(input_data, dict):
            dv = input_data.get("_from_design_validator") or {}
            overall = dv.get("overall") if isinstance(dv, dict) else None
            if overall == "FAIL":
                return Verdict(
                    kind=VerdictKind.PASS,
                    output=None,  # 不 emit · 下游自然 skip
                    diagnosis="skip · design_validation overall=FAIL, 不生成代码",
                )

        # 注入 run-scope context 给 extract_result (team_name / target_package_path)
        try:
            team_name, target_path = self._infer_team_context(input_data)
        except ValueError as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"team package location invalid: {exc}",
            )
        if hasattr(self._extract_result, "set_run_context"):
            self._extract_result.set_run_context(
                team_name=team_name, target_package_path=target_path,
            )

        # 首产
        verdict = await super().run(input_data)

        # 补产循环 (触发条件: needs_retry 且有缺失文件或结构问题)
        retries = 0
        while (
            verdict.kind in (VerdictKind.PARTIAL, VerdictKind.FAIL)
            and isinstance(verdict.output, dict)
            and verdict.output.get("needs_retry")
            and (verdict.output.get("missing_files") or verdict.output.get("structural_issues"))
            and retries < self.MAX_RETRIES
        ):
            retries += 1
            missing = verdict.output.get("missing_files") or []
            existing = verdict.output.get("produced_files") or []
            structural = verdict.output.get("structural_issues") or []
            empty_first = bool(verdict.output.get("empty_first_run"))

            retry_input = dict(input_data) if isinstance(input_data, dict) else {}
            retry_input["_retry_missing"] = missing
            retry_input["_retry_existing_files"] = existing
            retry_input["_retry_structural_issues"] = structural
            retry_input["_retry_empty_first"] = empty_first

            retry_verdict = await super().run(retry_input)

            # 合并 file_blocks (新旧 merge, 同名后胜 · 本轮补产为准)
            old_blocks = verdict.output.get("file_blocks") or []
            new_blocks = (retry_verdict.output or {}).get("file_blocks") or []
            merged_map: dict[str, str] = {}
            for name, content in old_blocks:
                merged_map[name] = content
            for name, content in new_blocks:
                merged_map[name] = content  # 新内容覆盖同名

            merged_blocks = list(merged_map.items())
            merged_basenames = {Path(n).name for n in merged_map.keys()}
            still_missing = [f for f in _REQUIRED_FILES if f not in merged_basenames]

            # 重跑结构检查 on merged + 骨架规范化 DESIGN.md 章节 + ServiceBus lint
            structural_issues: list[str] = []
            design_md_normalized = None
            for fname, content in merged_blocks:
                base = Path(fname).name
                if base.endswith(".py"):
                    if not _OMNI_MARK_RE.search(content or ""):
                        structural_issues.append(f"{base} 缺 OMNI 头")
                    if not content or len(content.strip()) < 50:
                        structural_issues.append(f"{base} 内容过短")
                if base == "DESIGN.md":
                    normalized, miss_sect = _normalize_design_md(content or "")
                    design_md_normalized = (fname, normalized)
                    if miss_sect:
                        structural_issues.append(f"DESIGN.md 缺 OMNI-034 章节: {miss_sect}")
            # ServiceBus 过度演绎 lint
            structural_issues.extend(_lint_service_bus_abuse(merged_blocks))

            # 骨架覆盖 LLM 章节名 → 规范名
            if design_md_normalized is not None:
                fname, normalized = design_md_normalized
                merged_map[fname] = normalized
                merged_blocks = list(merged_map.items())

            needs_retry_next = bool(still_missing or structural_issues)

            files_dict_merged: dict[str, str] = dict(merged_map)
            base_output = {
                "team_name": team_name,
                "target_package_path": target_path,
                "files": files_dict_merged,
                "file_blocks": merged_blocks,
                "produced_files": list(merged_map.keys()),
                "missing_files": still_missing,
                "structural_issues": structural_issues,
                "needs_retry": needs_retry_next,
                "_meta": {
                    "worker": "CodeGeneratorLoopWorker",
                    "stage": "v3_agent_loop",
                    "retries": retries,
                    "file_count": len(files_dict_merged),
                    "total_bytes": sum(len(c) for c in files_dict_merged.values() if isinstance(c, str)),
                },
            }

            if not needs_retry_next:
                verdict = Verdict(
                    kind=VerdictKind.PASS,
                    output=base_output,
                    diagnosis=(
                        f"code_package · {len(files_dict_merged)} 文件齐 · "
                        f"{base_output['_meta']['total_bytes']} bytes · "
                        f"补产 {retries}x 后 PASS"
                    ),
                )
                break
            verdict = Verdict(
                kind=VerdictKind.PARTIAL,
                output=base_output,
                diagnosis=(
                    f"code_package 补产 {retries}x 仍缺 {still_missing} "
                    f"结构 {structural_issues[:2]}"
                ),
            )

        return verdict
