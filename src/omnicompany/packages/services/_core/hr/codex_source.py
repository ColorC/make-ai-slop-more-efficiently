# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module
# [OMNI] summary="LLM 人事系统 codex 会话采集器: 从 ~/.codex/sessions/**/rollout-*.jsonl 里解析出
#   HrEvent(write/command/stray 等); codex 无独立 Write/Edit 工具字段, 目标路径靠命令文本启发式解析"
# [OMNI] why="P1 五棒并行开发 C 棒(风险最高): codex 独占 13.2GB/87% 语料, 一切操作走通用 exec 壳层,
#   路径没有结构化字段, 必须解析内嵌 shell/JS 命令文本; 实测发现的意外好消息是 patch_apply_end
#   event_msg 直接给出结构化 changes:{绝对路径:{...}}, 是比命令文本正则可靠得多的写信号"
# [OMNI] tags=hr,codex,attribution,heuristic-parse
"""codex 会话采集器 —— LLM 人事系统 P1-C 棒。

权威依据: docs/plans/agent-framework/[2026-08-17]LLM-HR-PERFORMANCE-ATTRIBUTION/
          P1-implementation-contract.md + P0-coverage-report.md V3/V4 + plan.md §1.5。

真实语料实测结构(本模块编写前用只读探针对 ~/.codex/sessions 做的核实, 不是照抄计划原文猜的):

  行类型: session_meta / turn_context / response_item / event_msg / world_state / compacted
  (顶层恒为 `{"timestamp":..., "type":<行类型>, "payload": {...}}`)

  session_meta.payload:
    - "id" = 本文件(本 thread)自己的会话 id, 且与文件名 rollout-...-<id>.jsonl 的尾部 UUID
      完全一致(实测比对多个样本恒成立) —— 因此本模块用**文件名**作为 session_id 的第一来源,
      不依赖 session_meta 是否落在当前增量读取窗口内, 更稳。
    - "session_id" 字段容易误导: 顶层会话里它等于 "id"; 但 **subagent 会话里它等于父会话的 id**,
      不是自己的 —— 这是本模块用文件名而非该字段做 session_id 的直接原因。
    - "thread_source": "user" | "subagent"。subagent 时 "parent_thread_id" 直接给出父会话 id
      (与 "source.subagent.thread_spawn.parent_thread_id" 重复), 这是**结构化字段直读**,
      不是启发式 —— P0 报告没覆盖到这条(它只采样了小文件的前几行), 是本次新增实证。
    - "cwd": 会话级 cwd, 逐 turn_context 也会给(通常相同)。

  turn_context.payload: "model"(逐 turn, 会话中途可能切换)/"cwd"/"approval_policy"。
    实测 3835/3837 个样本 approval_policy="never"(危险全权限沙箱, 无审批环节) —— 意味着
    codex 在本机语料里几乎不产生结构化的"审批拒绝"信号, 见下方"denial 未实现"说明。

  response_item.payload 的可执行/可写相关子类型(实测出现频次由高到低):
    - function_call: name="shell_command"(最主要) / "js"(mcp__node_repl 命名空间, 内联 JS,
      走 fs.readFileSync/writeFileSync) / "update_plan" / "wait" / "send_message" /
      "spawn_agent" / "wait_agent" / "list_agents" / "get_goal" / "create_goal" /
      "update_goal" / "followup_task" / "view_image"。后面这些是 agent 编排/元工具, 与文件系统
      无关, 本模块不产出事件(不制造噪音)。
      "arguments" 字段是 **JSON 编码的字符串**(需要二次 json.loads), shell_command 的
      arguments 形如 {"command": "<Powershell/bash 源码>", "workdir": "...", "timeout_ms": N}。
    - custom_tool_call: name="exec" —— 团队消息提到的
      "tools.shell_command({command:...})" 就是这个: "input" 字段是**JS 源码文本**(不是 JSON!),
      里面可能 0/1/多次调用 tools.shell_command({...}), 也可能是纯 JS 计算/反射
      (如 ALL_TOOLS.filter(...)), 完全不含任何 shell 调用。
    - function_call_output / custom_tool_call_output: 执行结果, 用 call_id 关联对应的调用;
      文本里含 "Exit code: N" 可判 verdict, custom_tool_call_output 的 output 是分块列表。
    - **没有观测到任何 `name=="apply_patch"` 的 function_call/custom_tool_call**
      (对约 120 个文件 / 数千条 function_call 采样, 命中 0 次) —— 与计划原文"apply_patch"
      的猜测不同, 真正的写文件主通道是下面这个事件, 不是一个叫 apply_patch 的工具调用。

  event_msg.payload.type == "patch_apply_end"(实测 ~1920 次/120 文件样本, 是主力写信号):
    直接给出 `{"success": bool, "stdout": "...", "stderr": "...",
              "changes": {"<绝对路径>": {"type": "add"|"update"|"delete", ...}, ...}}`
    —— 路径是字典的 key, 已是绝对路径, 不需要文本解析。**target_confidence="direct"**
    (2026-08-17 主会话裁决改判: 首版按契约初稿保守填了 "parsed", 主会话复核后认为
    schema 里 "direct" 的定义"目标路径来自结构化字段"完全覆盖这个信号, 不是文本解析,
    裁决改判——见交付说明)。命令文本正则解析出来的(shell_command/exec 里的
    Set-Content/Copy-Item/... 等)仍然是 "parsed", 两者在同一 provider 内并存,
    是本模块唯一一处 target_confidence 会给到 "direct" 的路径。

  event_msg.payload.type == "mcp_tool_call_end": node_repl(js 工具) 的执行结果，
    "result" 字段是 {"Ok": {...}} 或 {"Err": {...}}，可判 verdict。

  denial(守卫/权限拒绝)：本模块**未实现结构化 denial 识别**。原因如实说明：抽样约 3837 条
  turn_context 里 approval_policy="never" 占 3835 条, "on-request" 仅 2 条；对 "reject"/
  "denied"/"abort" 等关键词做全文匹配后人工核对，命中的全部是系统 prompt 里提到这些词的
  说明性文本(如 memory/skills 指令原文)，不是真实拒绝事件，误报率 100%——如果强行按关键词
  产出 denial 事件，会重演 plan.md §1.5 第 5 条"事故账本被讨论事故的会话污染"的错误。
  唯一观测到的、语义相关的结构化信号是 event_msg.type=="turn_aborted"(reason:"interrupted"),
  但那语义是"用户/系统中断了这一轮"，不等于"被守卫拒绝"，宁可不产出也不用错误语义冒充。

流式读取: 本模块内置最小化的按字节水位线逐行读取(_iter_new_lines), 不依赖尚不存在的
`hr/_jsonl_util.py`(由 A 棒同时在写, 契约要求本棒不碰它)。**用二进制模式逐行迭代文件对象
本身来读**(不是 .read() 整份或整段字节后再 split), 这样常量内存不随文件体积增长——
codex 最大单文件实测 1658 MB, 若参照 token_ledger 现有 _jsonl_util.read_new_lines()
的"整段增量一次性 .read() 再 split"写法, 首次全量扫描该文件会整段吃进 ~1.7GB 内存,
不满足 P1 契约"峰值 RSS 恒定~20MB"的硬约束, 所以本模块没有复用那个实现, 是有意为之。
"""

from __future__ import annotations

import json
import ntpath
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .schema import HrEvent, CollectResult, validate_event

# ── 本模块内置最小流式读取(hr/_jsonl_util.py 落地后按交付说明里的备注切换) ──────


def _line_count_before(path: Path, offset: int) -> int:
    """Count completed JSONL rows before a byte watermark, in bounded memory."""
    remaining = max(0, offset)
    count = 0
    with path.open("rb") as fh:
        while remaining:
            chunk = fh.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            count += chunk.count(b"\n")
            remaining -= len(chunk)
    return count


def _iter_new_lines(
    path: Path, *, prev_offset: int, prev_lineno: int
) -> Iterator[tuple[str, int, int]]:
    """按字节水位线, 用文件对象的行迭代协议逐行流式读取(常量内存, 绝不整份 .read())。

    yields (行文本, 该行读完后的绝对字节偏移, 真实 1-based 行号)。
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    start = prev_offset if 0 <= prev_offset <= size else 0
    if start >= size:
        return
    with path.open("rb") as fh:
        if start:
            fh.seek(start)
        lineno = prev_lineno
        for raw_line in fh:
            offset = fh.tell()
            lineno += 1
            text = raw_line.decode("utf-8", errors="replace").strip()
            if text:
                yield text, offset, lineno


def _iter_rollout_files(root: Path) -> list[Path]:
    """root 语义(2026-08-17 主会话统一裁决): 该 CLI 的**安装根**(生产传 ``~/.codex``),
    不是会话子目录。子路径由本函数内部写死拼接(``sessions/**/rollout-*.jsonl``),
    调用方不需要也不应该记住五家 CLI 各自不同的会话子路径形状。"""
    sessions_root = root / "sessions"
    if not sessions_root.is_dir():
        return []
    return sorted(sessions_root.glob("**/rollout-*.jsonl"))


_SESSION_ID_FROM_FILENAME_RE = re.compile(
    r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


def _session_id_from_filename(path: Path) -> str:
    m = _SESSION_ID_FROM_FILENAME_RE.search(path.name)
    return m.group(1) if m else ""


# ── 写目标启发式解析(本棒核心产出, 独立可测) ──────────────────────────────

# 已知的 PowerShell 虚拟驱动前缀 —— 不是文件系统路径, 命中即拒绝, 防"Remove-Item Env:X"
# 这类被误判成"删了个文件"的假阳性。
_PS_VIRTUAL_DRIVES = frozenset(
    {"env", "function", "alias", "variable", "cert", "wsman",
     "hklm", "hkcu", "hkcr", "hku", "hkcc",
     "hkey_local_machine", "hkey_current_user", "hkey_classes_root",
     "hkey_users", "hkey_current_config"}
)

_NULL_SINKS = frozenset({"$null", "/dev/null", "nul", "-", "&1", "&2"})


def _strip_quotes(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


_UNRESOLVED_VAR_RE = re.compile(r"\$[A-Za-z_{]")


def _looks_like_real_path(token: str) -> bool:
    """过滤掉 PS 虚拟驱动($Env:X)、null 汇(sink)、括号子表达式、残留未解析变量引用等
    明显不是"一个具体文件系统路径"的 token。"""
    if not token:
        return False
    low = token.lower()
    if low in _NULL_SINKS:
        return False
    if token.startswith("("):
        # 审计实测抓到的真实假阳性: `-Path (Join-Path $a $b)` 这种括号子表达式参数,
        # 旧版正则只取到空白前第一个词, 截出一坨"(Join-Path"这种半截垃圾——本函数不做
        # 表达式求值, 见到左括号开头直接拒绝, 不猜。
        return False
    if _UNRESOLVED_VAR_RE.search(token):
        # 未解析的变量引用不一定在 token 开头——PowerShell 字符串插值常见形如
        # "...chrome-capture-$stamp"(前缀是字面量、$stamp 是运行期动态值, 比如
        # Get-Date 的输出), 审计实测抓到过这类"半字面量半变量"被当成确定路径报出来。
        # 只要 token 里任何位置还残留 $标识符/${, 就说明没能完全归一化成一个具体值,
        # 一律不产出(调用方在 _resolve_local_assignments 已经尝试替换过一轮字面量赋值,
        # 这里还有残留说明是动态值, 不是能查到的静态赋值)。
        return False
    m = re.match(r"^([A-Za-z][A-Za-z_]*):", token)
    if m and m.group(1).lower() in _PS_VIRTUAL_DRIVES:
        return False
    if "%" in token or "~" in token.split(ntpath.sep)[0]:
        # 残留的 %ENV% 展开符号或 ~ 家目录缩写, 本函数不猜真实值
        return False
    return True


_ASSIGN_RE = re.compile(
    r"""(?:^|[;\n])\s*(?:var|let|const)?\s*[\$]?(\w+)\s*=\s*
        (?:Path\(\s*)?r?["']([^"']+)["']\)?""",
    re.VERBOSE,
)


def _resolve_local_assignments(text: str, token: str) -> str:
    """极轻量的同一段命令/脚本内变量求值: 只认"变量 = 字面量字符串"这一种形状。
    不追 Join-Path/字符串拼接/环境变量展开——那些解析不了就如实留着 $ 前缀,
    交给 _looks_like_real_path 拒绝掉, 不去猜。
    """
    if not token.startswith("$") and not re.match(r"^\w+$", token):
        return token
    var_name = token.lstrip("$")
    matches = _ASSIGN_RE.findall(text)
    for name, literal in matches:
        if name == var_name:
            return literal
    return token


def _normalize_target(raw: str, cwd: str, script_text: str) -> str | None:
    token = _strip_quotes(raw.strip())
    if not token:
        return None
    token = _resolve_local_assignments(script_text, token)
    token = _strip_quotes(token)
    if not _looks_like_real_path(token):
        return None
    try:
        if ntpath.isabs(token):
            return ntpath.normpath(token)
        if cwd:
            return ntpath.normpath(ntpath.join(cwd, token))
        # 相对路径且 cwd 未知: 归一不了, 按"宁可漏"不产出目标(而不是拼一个假的绝对路径)
        return None
    except (ValueError, TypeError):
        return None


# 重定向: 要求操作符前面紧邻空白/行首/;|&(不是数字、不是标识符字符), 后面紧邻空白再跟 token。
# 这条"前必须是空白"的要求是审计真实语料后加的硬约束——最初"排除数字/&紧邻"那版在真实
# 语料里把 JS 箭头函数 `=>`(=紧邻)、C#/TS 泛型 `List<Item>`(标识符字符紧邻)、`->` 之类
# 全部误判成了 shell 重定向, 对 35 条随机抽样人工核对的结果是 35 条几乎全错(见交付说明的
# "误判率自评"一节)——`=`/`-`/标识符字符统统不在允许的前导集合里, 一次性堵掉了这整类假阳性。
_REDIRECT_RE = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=[;|&]))(>>?)(?!&)\s+([^\s|;&\r\n]+)")

# apply_patch 统一 diff 格式的标记——命中说明这段文本是"提交一个补丁"的载体(不管是走
# tools.apply_patch(...) 直接调用, 还是走 $patch=@'*** Begin Patch...'@ 变量后续处理),
# 真正的写入结果由 event_msg.patch_apply_end 结构化字段负责(见 _handle_event_msg), 这里
# 如果再用命令文本正则去猜, 只会在 diff 正文里(充满普通代码, `>` 泛型/箭头函数密度极高)
# 制造大量误判——审计实测命中的 35 条假阳性里将近一半就是这个模式, 所以直接整段放弃解析。
_APPLY_PATCH_MARKER_RE = re.compile(r"\*\*\* (?:Begin Patch|Update File|Add File|Delete File):")
_APPLY_PATCH_CALL_RE = re.compile(r"tools\.apply_patch\s*\(")
_APPLY_PATCH_TARGET_RE = re.compile(
    r"(?:^|(?<!\\)\\n|\r?\n)\*\*\* (Add|Update|Delete) File: (.+?)(?=(?<!\\)\\n|\r?\n)"
    r"|(?:^|(?<!\\)\\n|\r?\n)\*\*\* Move to: (.+?)(?=(?<!\\)\\n|\r?\n)"
)


def _extract_apply_patch_targets(text: str) -> list[tuple[str, str]]:
    if not _APPLY_PATCH_CALL_RE.search(text):
        return []
    targets: list[tuple[str, str]] = []
    for match in _APPLY_PATCH_TARGET_RE.finditer(text):
        operation = match.group(1) or "Move"
        raw_path = (match.group(2) or match.group(3) or "").strip()
        # JS string literals double Windows backslashes; this is a path-only
        # field from a patch header, so unescaping doubled separators is safe.
        raw_path = raw_path.replace("\\\\", "\\")
        if raw_path:
            targets.append((raw_path, f"apply_patch:{operation.lower()}"))
    return targets

# PowerShell here-string(@'...'@ / @"..."@) 与 bash heredoc(<<'TAG' ... TAG 等)的正文
# 本身只是"被写进某处的数据"(常见后续消费: 赋给变量、传给 apply_patch、当 python/node
# 脚本喂给解释器), 不是活的 shell 语法——正文里出现的 `>`/`cp`/`rm` 等只是普通代码/文本内容,
# 不代表外层 shell 真的做了重定向或调了这些命令。跑 shell 级正则(重定向/cmdlet/unix)前
# 先把这些正文整体挖掉(保留脏文本给 python/node 脚本正则单独扫, 因为那些正则找的是
# `open(...)`/`writeFileSync(...)`这类专有语法, 不会被普通 `>` 字符污染)。
_HEREDOC_BLOCK_RE = re.compile(
    r"@'.*?'@|@\".*?\"@|<<-?\s*['\"]?(\w+)['\"]?\r?\n.*?\r?\n\s*\1\b",
    re.DOTALL,
)


def _mask_heredoc_bodies(text: str) -> str:
    return _HEREDOC_BLOCK_RE.sub(" <<HEREDOC>> ", text)

def _quote_mask(text: str) -> list[bool]:
    """逐字符标记"处于单/双引号字符串内部"(粗粒度、只分单/双引号两态, 不追完整语法)。

    审计真实语料时抓到的假阳性(见交付说明): `Add-Content` 这个词作为**日志正文里的英文
    描述**("repeated Add-Content attempts failed...")出现在一个双引号字符串里, 被
    cmdlet 正则当成了一次真实调用。区分"这是活代码"还是"这是被写进某处的文本内容"的
    最低成本信号就是"当前有没有落在一段引号字符串里"——单引号内按 PowerShell 规则用
    `''` 转义(不结束字符串), 双引号内按 PowerShell 规则用反引号转义, 两条规则对 bash
    双引号(反斜杠转义)也大致够用, 只是做粗判, 不追求对齐完整 shell 语法。
    """
    n = len(text)
    mask = [False] * n
    state: str | None = None
    i = 0
    while i < n:
        ch = text[i]
        if state is None:
            if ch in ("'", '"'):
                state = ch
                mask[i] = True
            i += 1
            continue
        mask[i] = True
        if state == "'" and ch == "'":
            if i + 1 < n and text[i + 1] == "'":
                mask[i + 1] = True
                i += 2
                continue
            state = None
            i += 1
            continue
        if state == '"' and ch == "`":
            if i + 1 < n:
                mask[i + 1] = True
            i += 2
            continue
        if state == '"' and ch == '"':
            state = None
            i += 1
            continue
        i += 1
    return mask


def _find_statement_end(text: str, start: int, mask: list[bool], limit: int = 240) -> int:
    """从 start 起最多扫 limit 字符, 找第一个**不在引号内**的语句边界(;/换行/|)。

    审计抓到的第二类假阳性: 旧版"cmdlet 名之后开一个固定 240 字符窗口找具名参数"会
    直接越过当前语句的 `;` 分隔符, 把后面**另一个不相关 cmdlet** 的 -LiteralPath 参数
    错栽到当前这个 cmdlet 头上(例: Remove-Item Env:X; ...; Get-FileHash -LiteralPath
    $report 被错配成 Remove-Item 的目标)。窗口必须先卡在语句边界, 不能跨语句找参数。
    """
    end = min(len(text), start + limit)
    i = start
    while i < end:
        if not mask[i] and text[i] in ";\n|":
            return i
        i += 1
    return end


# 各 cmdlet 的具名参数优先, 找不到具名参数时退化为紧跟其后的第一个 token(位置参数)。
_PS_WRITE_CMDLETS: tuple[tuple[str, bool], ...] = (
    # (cmdlet 正则名, 是否取 -Destination 而非 -Path 系)
    ("Set-Content", False),
    ("Add-Content", False),
    ("Out-File", False),
    ("Export-Csv", False),
    ("Export-Clixml", False),
    ("Tee-Object", False),
    ("New-Item", False),
    ("Copy-Item", True),
    ("Move-Item", True),
    ("Remove-Item", False),
)


def _extract_ps_cmdlet_targets(text: str, mask: list[bool] | None = None) -> list[tuple[str, str]]:
    mask = mask if mask is not None else _quote_mask(text)
    out: list[tuple[str, str]] = []
    for cmdlet, prefer_dest in _PS_WRITE_CMDLETS:
        for m in re.finditer(rf"\b{re.escape(cmdlet)}\b", text):
            if mask[m.start()]:
                continue  # 落在引号字符串里的同名文本(日志原文/注释), 不是真实调用
            window_end = _find_statement_end(text, m.end(), mask)
            window = text[m.end(): window_end]
            flags_to_try = ("-Destination",) if prefer_dest else ("-LiteralPath", "-Path", "-FilePath")
            found = None
            for flag in flags_to_try:
                # (?<!\S) 而不是 \b: 真实语料实测抓到的 bug——"-Path" 不带边界检查时会
                # 命中 "Join-Path"/"Split-Path"/"Test-Path"/"Resolve-Path" 这类**外部
                # cmdlet 名字尾部的同名后缀**(例: `Out-File (Join-Path $output 'x.json')`
                # 被错配成 `-Path $output`, 丢了真正的文件名后缀)。具名参数前面必须是
                # 空白或语句起点, 不能是另一个标识符字符的中间。
                fm = re.search(rf"(?<!\S){flag}\s+([^\s;|]+)", window, re.IGNORECASE)
                if fm:
                    found = fm.group(1)
                    break
            basis_kind = "named-flag"
            if found is None:
                # 位置参数退化: 取紧跟 cmdlet 名之后、且不是另一个 "-xxx" 具名参数的首个 token
                pm = re.match(r"\s+(?!-)([^\s;|]+)", window)
                if pm:
                    found = pm.group(1)
                    basis_kind = "positional-guess"
            if found is not None:
                out.append((found, f"cmdlet:{cmdlet}({basis_kind})"))
    return out


_UNIX_WRITE_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)(cp|mv|mkdir|rm)\s+((?:-\S+\s+)*)(\S+)(?:\s+(\S+))?"
)


def _extract_unix_targets(text: str, mask: list[bool] | None = None) -> list[tuple[str, str]]:
    mask = mask if mask is not None else _quote_mask(text)
    out: list[tuple[str, str]] = []
    for m in _UNIX_WRITE_CMD_RE.finditer(text):
        if mask[m.start(1)]:
            continue  # 同样是"引号字符串里的普通文本, 不是活代码"这类假阳性
        cmd, _flags, arg1, arg2 = m.group(1), m.group(2), m.group(3), m.group(4)
        if cmd in ("cp", "mv"):
            if arg2:
                out.append((arg2, f"unix:{cmd}(dest)"))
        else:  # mkdir / rm
            out.append((arg1, f"unix:{cmd}"))
    return out


_OPEN_WRITE_MODE_RE = re.compile(
    r"""open\(\s*(?:r?["']([^"']+)["']|\$?(\w+))\s*,\s*["']([wWaAxX][a-zA-Z+]*)["']"""
)
_WRITE_TEXT_RE = re.compile(r"""\$?(\w+)\s*\.\s*write_text\(""")
_WRITE_FILE_SYNC_RE = re.compile(
    r"""writeFileSync\(\s*(?:["'`]([^"'`]+)["'`]|(\w+))"""
)
_WRITE_FILE_ASYNC_RE = re.compile(
    r"""\bwriteFile\(\s*(?:["'`]([^"'`]+)["'`]|(\w+))"""
)


def _extract_script_targets(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _OPEN_WRITE_MODE_RE.finditer(text):
        literal, var = m.group(1), m.group(2)
        token = literal if literal is not None else f"${var}"
        out.append((token, "python:open(write-mode)"))
    for m in _WRITE_TEXT_RE.finditer(text):
        out.append((f"${m.group(1)}", "python:write_text"))
    for m in _WRITE_FILE_SYNC_RE.finditer(text):
        literal, var = m.group(1), m.group(2)
        token = literal if literal is not None else f"${var}"
        out.append((token, "node:writeFileSync"))
    for m in _WRITE_FILE_ASYNC_RE.finditer(text):
        literal, var = m.group(1), m.group(2)
        token = literal if literal is not None else f"${var}"
        out.append((token, "node:writeFile"))
    return out


def extract_write_targets(
    command_text: str, cwd: str, *, source: str = "shell"
) -> list[tuple[str, str]]:
    """命令/脚本文本 → 写目标路径的启发式解析(本棒核心产出, 纯函数, 独立可测)。

    覆盖: shell 重定向(> />>, 排除 2>/2>&1/>&1/>$null 等噪音)、PowerShell 写类 cmdlet
    (Set-Content/Add-Content/Out-File/Export-Csv/Export-Clixml/Tee-Object/New-Item/
    Copy-Item/Move-Item/Remove-Item)、Unix cp/mv/mkdir/rm、内联 Python open(...,'w')/
    .write_text(、内联 Node writeFileSync/writeFile。

    宁可漏不可错: 拿不准归一化(相对路径且 cwd 未知、残留未解析的 $变量/%ENV%、
    PS 虚拟驱动如 Env:/HKLM: 等)一律不产出该条, 不拼一个可能错误的路径。

    Args:
        source: "shell"(默认)= command_text 是真实 shell/PowerShell 命令文本(如
            function_call.shell_command 的 command 字段、或从 exec JS 包装里抠出来的
            内嵌 tools.shell_command 调用)。"js" = command_text 是**JS/TS 源码本身**
            (如 function_call name=="js" 的 code 字段、mcp_tool_call_end 的 code 字段、
            custom_tool_call(exec) 整段 input 的兜底扫描)——这种情况下**不跑重定向/
            cmdlet/unix 这三组 shell 级正则**, 因为真实语料审计实测证实 JS/TS 源码里的
            比较运算符(`a > b`)、类型标注、模板字符串反引号内容, 跟 shell 重定向语法
            在字符层面完全无法区分, 硬跑只会产出大量误判(如把 `cr.top` 这种属性访问
            当成重定向目标); JS/TS 唯一安全能查的专有写语法是 writeFileSync/writeFile,
            这条走 _extract_script_targets, 两种 source 都会跑。

    Returns:
        [(归一化绝对路径, 解析依据简述), ...] —— 依据只做简短标注, 不回填命令原文。
    """
    if not command_text:
        return []

    if _APPLY_PATCH_MARKER_RE.search(command_text):
        raw_hits = _extract_apply_patch_targets(command_text)
        results: list[tuple[str, str]] = []
        seen_paths: set[str] = set()
        for raw_token, basis in raw_hits:
            normalized = _normalize_target(raw_token, cwd, command_text)
            if normalized is not None and normalized.lower() not in seen_paths:
                seen_paths.add(normalized.lower())
                results.append((normalized, basis))
        return results

    raw_hits: list[tuple[str, str]] = []

    if source == "shell":
        # shell 级正则(重定向/PS cmdlet/unix cp-mv-mkdir-rm)只在"挖掉 heredoc 正文"之后的
        # 文本上跑——heredoc 正文本身只是数据, 不是活的 shell 语法(见 _mask_heredoc_bodies)。
        shell_level_text = _mask_heredoc_bodies(command_text)
        # 引号字符串内部的同名文本(日志原文里提到"rm"/"Add-Content"这类词、或字符串里
        # 恰好有个 `>` 字符)不是活代码——见 _quote_mask 的审计背景说明, 一次算好全文复用。
        quote_mask = _quote_mask(shell_level_text)

        for m in _REDIRECT_RE.finditer(shell_level_text):
            if quote_mask[m.start(1)]:
                continue
            raw_hits.append((m.group(2), f"redirect:{m.group(1)}"))

        raw_hits.extend(_extract_ps_cmdlet_targets(shell_level_text, quote_mask))
        raw_hits.extend(_extract_unix_targets(shell_level_text, quote_mask))

    # python/node 脚本目标正则找的是专有语法(open(...)/writeFileSync(...)), 不会被普通
    # `>` 字符或比较运算符污染, 两种 source 都跑, 且在原文(不挖 heredoc)上跑, 这样才能
    # 看到 heredoc/模板字符串正文里的真实脚本。
    raw_hits.extend(_extract_script_targets(command_text))

    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_tok, basis in raw_hits:
        normalized = _normalize_target(raw_tok, cwd, command_text)
        if normalized is None:
            continue
        key = (normalized.lower(), basis.split("(")[0])
        if key in seen:
            continue
        seen.add(key)
        results.append((normalized, basis))
    return results


# ── custom_tool_call(name=="exec") 的 JS 包装层: 提取内嵌 tools.shell_command({...}) ──


def _extract_embedded_shell_calls(js_text: str) -> list[tuple[str, str | None]]:
    """从 exec 包装的 JS 源码里找出全部 tools.shell_command({...}) 调用, 返回
    [(command文本, workdir或None), ...]。用字符串感知的括号配对(跳过引号内的括号),
    避免命令文本本身含括号/正则时把调用边界切错。

    找不到任何可解析的 shell_command 调用时返回空列表(调用方据此判"这段 JS 没有可提
    取的写目标", 不代表 JS 本身没写文件——见 extract_write_targets 对纯 JS 文本的
    补充扫描, 两者互不替代)。
    """
    calls: list[tuple[str, str | None]] = []
    for m in re.finditer(r"tools\.shell_command\(", js_text):
        start = m.end()
        arg_text = _read_balanced_call_args(js_text, start)
        if arg_text is None:
            continue
        parsed = _parse_shell_command_arg_object(arg_text)
        if parsed is not None:
            cmd, workdir = parsed
            calls.append((cmd, workdir))
    return calls


def _read_balanced_call_args(text: str, start: int) -> str | None:
    """从 start(紧跟左括号之后)起, 做字符串感知的括号深度匹配, 找到对应的右括号。
    字符串感知: 处于 '/"/` 字符串内部时不计括号深度, 正确跳过转义字符。
    """
    depth = 1
    i = start
    n = len(text)
    in_str: str | None = None
    while i < n and depth > 0:
        ch = text[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ("'", '"', "`"):
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start:i]
        i += 1
    return None


def _parse_shell_command_arg_object(arg_text: str) -> tuple[str, str | None] | None:
    """arg_text 是 tools.shell_command(<这里>) 的参数文本, 典型形如
    '{"command":"...","workdir":"...","timeout_ms":10000}'。

    该文本此时已经是从上一层 json.loads() 解码出来的 python 字符串(即 JS 源码原文本身
    的转义规则, 不是外层 transcript jsonl 的转义规则), 对绝大多数"字面量 command 字符串"
    的调用而言它本身就是合法 JSON, 直接 json.loads 即可, 比正则更不容易被内部转义引号
    (如命令里的 rg 正则 \\"..\\" )搞错边界。json.loads 失败时(shorthand 属性
    `{command, workdir:...}`、模板字符串、变量拼接等 JS-only 语法)按"宁可漏"放弃,
    不用正则硬凑。
    """
    stripped = arg_text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    cmd = obj.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    workdir = obj.get("workdir")
    return cmd, workdir if isinstance(workdir, str) else None


# ── verdict 判定 ──────────────────────────────────────────────────────────

_EXIT_CODE_RE = re.compile(r"Exit code:\s*(-?\d+)")


def _verdict_from_exec_output(output: Any) -> str:
    """从 function_call_output / custom_tool_call_output 的 output 字段判 verdict。

    只有当能明确数出**恰好一个** "Exit code: N" 时才下结论(0→applied, 非0→error);
    找不到、或者同一个输出里混了多个 exit code(如一个 exec 块里 Promise.all 了多个
    shell_command, 没法把某个 code 精确归到某一条我们抽出来的写目标)一律 unknown,
    不猜。
    """
    if isinstance(output, str):
        text = output
    elif isinstance(output, list):
        text = "\n".join(b.get("text", "") for b in output if isinstance(b, dict))
    else:
        return "unknown"
    matches = _EXIT_CODE_RE.findall(text)
    if len(matches) == 1:
        return "applied" if matches[0] == "0" else "error"
    if len(matches) > 1:
        return "unknown"
    if "Script failed" in text:
        return "error"
    if "Script completed" in text:
        return "applied"
    return "unknown"


def _verdict_from_mcp_result(result: Any) -> str:
    if isinstance(result, dict):
        if "Ok" in result:
            return "applied"
        if "Err" in result:
            return "error"
    return "unknown"


# ── 主采集流程 ────────────────────────────────────────────────────────────


@dataclass
class _PendingCall:
    kind: str  # "write" | "command"
    tool: str
    ts: str
    cwd: str
    is_subagent: bool
    parent_session_id: str
    model: str
    model_confidence: str
    session_id: str
    source_path: str
    lineno: int
    operation_id: str = ""
    target: str = ""
    target_confidence: str = "none"
    note: str = ""
    multi_target_ambiguous: bool = False
    extra_events: list[HrEvent] = field(default_factory=list)  # 一次调用命中多个目标时, 首个进主槽位其余存这里


def _pending_to_dict(call: _PendingCall) -> dict[str, Any]:
    return asdict(call)


def _pending_from_dict(raw: Any) -> _PendingCall | None:
    if not isinstance(raw, dict):
        return None
    data = dict(raw)
    extras: list[HrEvent] = []
    known_event_fields = set(HrEvent.__dataclass_fields__)
    for item in data.pop("extra_events", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            extras.append(HrEvent(**{key: value for key, value in item.items() if key in known_event_fields}))
        except TypeError:
            continue
    known = set(_PendingCall.__dataclass_fields__)
    try:
        return _PendingCall(**{key: value for key, value in data.items() if key in known}, extra_events=extras)
    except TypeError:
        return None


def _make_event(
    *, ts, provider, session_id, kind, evidence_ref, model, model_confidence,
    parent_session_id, is_subagent, cwd, tool, target, target_confidence, verdict, note,
    operation_id="",
) -> HrEvent:
    return HrEvent(
        ts=ts, provider=provider, session_id=session_id, kind=kind, evidence_ref=evidence_ref,
        operation_id=operation_id,
        model=model, model_confidence=model_confidence, parent_session_id=parent_session_id,
        is_subagent=is_subagent, cwd=cwd, tool=tool, target=target,
        target_confidence=target_confidence, verdict=verdict, note=note,
    )


def collect_codex_sessions(
    root: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> CollectResult:
    """扫 root(codex 安装根)下 sessions/**/rollout-*.jsonl, 按字节水位线增量解析出 HrEvent。

    Args:
        root: **codex 安装根**(测试传 tmp_path fixture, 生产传 ``Path.home()/".codex"``)——
            不是会话子目录, 子路径("sessions")由本模块内部拼接(2026-08-17 主会话统一裁决:
            五家 collector 的 root 参数语义统一为"该 CLI 安装根", 子路径各家不同, 写死在
            各自 collector 内部, 不要求调用方记住)。
        watermark_state: 上次 collect 返回的 new watermark(每文件字节偏移 + 逐会话延续状态:
            当前 model/cwd/is_subagent/parent_session_id), 省略则从头全量扫描。
    """
    watermark_state = watermark_state or {}
    new_state: dict[str, Any] = dict(watermark_state)
    result = CollectResult(provider="codex")

    for path in _iter_rollout_files(root):
        result.files_seen += 1
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            result.files_failed += 1
            if len(result.failures) < 20:
                result.failures.append(f"{key}: stat failed: {exc}")
            continue

        prev_offset = int(watermark_state.get(key, 0) or 0)
        if size == 0:
            result.files_empty += 1
            new_state[key] = 0
            new_state[f"{key}::line"] = 0
            new_state[f"{key}::pending"] = []
            continue

        session_id = _session_id_from_filename(path)
        cwd = str(watermark_state.get(f"{key}::cwd", "") or "")
        current_model = str(watermark_state.get(f"{key}::model", "") or "")
        model_confidence = str(watermark_state.get(f"{key}::model_confidence", "unknown") or "unknown")
        is_subagent = bool(watermark_state.get(f"{key}::is_subagent", False))
        parent_session_id = str(watermark_state.get(f"{key}::parent_session_id", "") or "")

        was_reset = size < prev_offset
        effective_prev_offset = 0 if was_reset else prev_offset
        if was_reset:
            previous_lineno = 0
        else:
            stored_lineno = watermark_state.get(f"{key}::line")
            previous_lineno = (
                int(stored_lineno)
                if isinstance(stored_lineno, int) and stored_lineno >= 0
                else _line_count_before(path, effective_prev_offset)
            )
        latest_lineno = previous_lineno
        pending: dict[str, _PendingCall] = {}
        if not was_reset:
            raw_pending = watermark_state.get(f"{key}::pending")
            if isinstance(raw_pending, list):
                for item in raw_pending:
                    restored = _pending_from_dict(item)
                    if restored is not None and restored.operation_id:
                        pending[restored.operation_id] = restored
        restored_ids = set(pending)

        try:
            for line_text, offset, lineno in _iter_new_lines(
                path, prev_offset=effective_prev_offset, prev_lineno=previous_lineno
            ):
                result.lines_scanned += 1
                new_state[key] = offset
                latest_lineno = lineno
                try:
                    rec = json.loads(line_text)
                except json.JSONDecodeError:
                    if len(result.failures) < 20:
                        result.failures.append(f"{key}#L{lineno}: bad json line, skipped")
                    continue
                if not isinstance(rec, dict):
                    continue

                rtype = rec.get("type")
                ts = rec.get("timestamp") or ""
                payload = rec.get("payload")
                payload = payload if isinstance(payload, dict) else {}

                if isinstance(payload.get("model"), str) and payload["model"]:
                    current_model = payload["model"]
                    model_confidence = "high"

                if rtype == "session_meta":
                    p_cwd = payload.get("cwd")
                    if isinstance(p_cwd, str) and p_cwd:
                        cwd = p_cwd
                    thread_source = payload.get("thread_source")
                    if thread_source == "subagent":
                        is_subagent = True
                        parent = payload.get("parent_thread_id")
                        if not parent:
                            source = payload.get("source")
                            if isinstance(source, dict):
                                sub = source.get("subagent")
                                if isinstance(sub, dict):
                                    spawn = sub.get("thread_spawn")
                                    if isinstance(spawn, dict):
                                        parent = spawn.get("parent_thread_id")
                        if isinstance(parent, str) and parent:
                            parent_session_id = parent
                    file_id = payload.get("id")
                    if isinstance(file_id, str) and file_id and not session_id:
                        session_id = file_id
                    continue

                if rtype == "turn_context":
                    p_cwd = payload.get("cwd")
                    if isinstance(p_cwd, str) and p_cwd:
                        cwd = p_cwd
                    continue

                if rtype == "event_msg":
                    _handle_event_msg(
                        payload, pending=pending, result=result, path=path, offset=lineno,
                        ts=ts, session_id=session_id, model=current_model,
                        model_confidence=model_confidence, parent_session_id=parent_session_id,
                        is_subagent=is_subagent, cwd=cwd,
                    )
                    continue

                if rtype == "response_item":
                    _handle_response_item(
                        payload, pending=pending, result=result, path=path, offset=lineno,
                        ts=ts, session_id=session_id, model=current_model,
                        model_confidence=model_confidence, parent_session_id=parent_session_id,
                        is_subagent=is_subagent, cwd=cwd,
                    )
                    continue
                # world_state / compacted: 会话级快照/压缩标记, 与 HR 归因字段无关, 跳过。

            # 本批次结束: pending 里没等到对应 output 的调用, 如实降级为 unknown 落盘
            # (不是吞掉/丢弃——契约禁止悄悄丢事件)。
            for operation_id, call in pending.items():
                if operation_id not in restored_ids:
                    _finalize_pending(call, verdict="unknown", result=result)

        except OSError as exc:
            result.files_failed += 1
            if len(result.failures) < 20:
                result.failures.append(f"{key}: read failed: {exc}")
            continue

        result.files_parsed += 1
        new_state[key] = size
        new_state[f"{key}::line"] = latest_lineno
        new_state[f"{key}::cwd"] = cwd
        new_state[f"{key}::model"] = current_model
        new_state[f"{key}::model_confidence"] = model_confidence
        new_state[f"{key}::is_subagent"] = is_subagent
        new_state[f"{key}::parent_session_id"] = parent_session_id
        new_state[f"{key}::pending"] = [_pending_to_dict(call) for call in pending.values()]

    result.watermark = new_state
    return result


def _finalize_pending(call: _PendingCall, *, verdict: str, result: CollectResult) -> None:
    evidence_ref = f"{call.source_path}#L{call.lineno}"
    ev = _make_event(
        ts=call.ts, provider="codex", session_id=call.session_id, kind=call.kind,
        evidence_ref=evidence_ref, model=call.model, model_confidence=call.model_confidence,
        operation_id=call.operation_id,
        parent_session_id=call.parent_session_id, is_subagent=call.is_subagent, cwd=call.cwd,
        tool=call.tool, target=call.target, target_confidence=call.target_confidence,
        verdict=verdict, note=call.note,
    )
    problems = validate_event(ev)
    if problems:
        if len(result.failures) < 20:
            result.failures.append(f"{evidence_ref}: invalid event dropped: {problems}")
        return
    result.events.append(ev)
    for extra in call.extra_events:
        # 同一调用命中多个写目标时, 其余目标复用相同 verdict/身份字段, 只是路径不同。
        extra_ev = HrEvent(**{**extra.to_dict(), "verdict": verdict})
        extra_problems = validate_event(extra_ev)
        if extra_problems:
            if len(result.failures) < 20:
                result.failures.append(f"{evidence_ref}: invalid extra event dropped: {extra_problems}")
            continue
        result.events.append(extra_ev)


def _new_pending(
    *, kind, tool, ts, cwd, is_subagent, parent_session_id, model, model_confidence,
    session_id, path: Path, offset: int, operation_id="",
) -> _PendingCall:
    return _PendingCall(
        kind=kind, tool=tool, ts=ts, cwd=cwd, is_subagent=is_subagent,
        parent_session_id=parent_session_id, model=model, model_confidence=model_confidence,
        session_id=session_id, source_path=str(path), lineno=offset, operation_id=operation_id,
    )


def _apply_targets(call: _PendingCall, targets: list[tuple[str, str, str]]) -> None:
    """targets: [(归一化路径, 依据, 该目标实际用的 cwd), ...]。

    第三项(cwd)是因为审计实测抓到的一个真实 bug: custom_tool_call(exec) 里一段 JS 可能
    并发调用多个 tools.shell_command, 每次调用有**各自独立的 workdir**(不一定等于外层
    session 级 cwd)——早期版本落盘事件的 cwd 字段固定用外层 cwd, 导致相对路径明明是按
    A 目录解析出来的, 事件上却记着 B 目录, 元数据自相矛盾。这里让每个目标各自带自己
    实际参与解析用的 cwd, 写回对应事件, 不再用一个笼统的外层 cwd 糊弄所有目标。
    """
    if not targets:
        call.kind = "command"
        call.target = ""
        call.target_confidence = "none"
        return
    call.kind = "write"
    first_path, first_basis, first_cwd = targets[0]
    call.target = first_path
    call.target_confidence = "parsed"
    call.note = first_basis[:80]
    if first_cwd:
        call.cwd = first_cwd
    if len(targets) > 1:
        call.multi_target_ambiguous = True
        for extra_path, extra_basis, extra_cwd in targets[1:]:
            call.extra_events.append(
                _make_event(
                    ts=call.ts, provider="codex", session_id=call.session_id, kind="write",
                    evidence_ref=f"{call.source_path}#L{call.lineno}", model=call.model,
                    operation_id=call.operation_id,
                    model_confidence=call.model_confidence, parent_session_id=call.parent_session_id,
                    is_subagent=call.is_subagent, cwd=(extra_cwd or call.cwd), tool=call.tool, target=extra_path,
                    target_confidence="parsed", verdict="unknown", note=extra_basis[:80],
                )
            )


def _handle_response_item(
    payload: dict, *, pending: dict[str, _PendingCall], result: CollectResult, path: Path,
    offset: int, ts: str, session_id: str, model: str, model_confidence: str,
    parent_session_id: str, is_subagent: bool, cwd: str,
) -> None:
    ptype = payload.get("type")
    call_id = payload.get("call_id")

    if ptype in ("function_call", "local_shell_call"):
        name = payload.get("name") or ("shell_command" if ptype == "local_shell_call" else "")
        if name not in ("shell_command", "js"):
            return  # update_plan/wait/send_message/spawn_agent/... 与文件系统无关, 不产出事件
        args_raw = payload.get("arguments")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            if len(result.failures) < 20:
                result.failures.append(f"{path}#L{offset}: bad arguments json for {name}, skipped")
            return
        if not isinstance(args, dict):
            return

        call_cwd = args.get("workdir") if isinstance(args.get("workdir"), str) else cwd
        call = _new_pending(
            kind="command", tool=name, ts=ts, cwd=call_cwd, is_subagent=is_subagent,
            parent_session_id=parent_session_id, model=model, model_confidence=model_confidence,
            session_id=session_id, path=path, offset=offset, operation_id=str(call_id or ""),
        )

        if name == "shell_command":
            command_text = args.get("command")
            if isinstance(command_text, str):
                targets = extract_write_targets(command_text, call_cwd)
                _apply_targets(call, [(p, b, call_cwd) for p, b in targets])
        elif name == "js":
            code_text = args.get("code")
            if isinstance(code_text, str):
                targets = extract_write_targets(code_text, call_cwd, source="js")
                _apply_targets(call, [(p, b, call_cwd) for p, b in targets])

        if isinstance(call_id, str) and call_id:
            pending[call_id] = call
        else:
            _finalize_pending(call, verdict="unknown", result=result)
        return

    if ptype == "custom_tool_call" and payload.get("name") == "exec":
        js_text = payload.get("input")
        js_text = js_text if isinstance(js_text, str) else ""
        call = _new_pending(
            kind="command", tool="exec", ts=ts, cwd=cwd, is_subagent=is_subagent,
            parent_session_id=parent_session_id, model=model, model_confidence=model_confidence,
            session_id=session_id, path=path, offset=offset, operation_id=str(call_id or ""),
        )

        embedded = _extract_embedded_shell_calls(js_text)
        all_targets: list[tuple[str, str, str]] = []
        if embedded:
            for cmd_text, workdir in embedded:
                effective_cwd = workdir or cwd
                all_targets.extend(
                    (p, b, effective_cwd) for p, b in extract_write_targets(cmd_text, effective_cwd)
                )
            call.multi_target_ambiguous = len(embedded) > 1
        # 不管有没有找到内嵌 shell_command, 都再对整段 JS 文本扫一遍(fs.writeFileSync 等
        # 可能不经过 tools.shell_command 直接出现在 JS 里)——这次用外层 session 级 cwd,
        # 因为这条路径不经过任何带自己 workdir 的内嵌调用。
        all_targets.extend(
            (p, b, cwd) for p, b in extract_write_targets(js_text, cwd, source="js")
        )
        # 去重(路径+依据前缀)
        dedup: list[tuple[str, str, str]] = []
        seen_keys: set[str] = set()
        for p, b, c in all_targets:
            k = f"{p.lower()}|{b.split('(')[0]}"
            if k not in seen_keys:
                seen_keys.add(k)
                dedup.append((p, b, c))
        _apply_targets(call, dedup)
        if len(embedded) > 1 and dedup:
            # 一段 JS 里并发/连续调用了多个 shell_command, 单个 call_id 对应的 output
            # 没法精确归因到某一个子调用的 exit code —— 见 _verdict_from_exec_output
            # 的"多个 exit code 一律 unknown"处理, 这里仅标注依据。
            call.note = (call.note + ";multi-shell-in-one-exec")[:120]

        if isinstance(call_id, str) and call_id:
            pending[call_id] = call
        else:
            _finalize_pending(call, verdict="unknown", result=result)
        return

    if ptype == "function_call_output":
        if isinstance(call_id, str) and call_id in pending:
            call = pending.pop(call_id)
            verdict = _verdict_from_exec_output(payload.get("output"))
            _finalize_pending(call, verdict=verdict, result=result)
        return

    if ptype == "custom_tool_call_output":
        if isinstance(call_id, str) and call_id in pending:
            call = pending.pop(call_id)
            verdict = _verdict_from_exec_output(payload.get("output"))
            _finalize_pending(call, verdict=verdict, result=result)
        return
    # message / reasoning 等: 与写入归因无关, 跳过。


def _handle_event_msg(
    payload: dict, *, pending: dict[str, _PendingCall], result: CollectResult, path: Path,
    offset: int, ts: str, session_id: str, model: str, model_confidence: str,
    parent_session_id: str, is_subagent: bool, cwd: str,
) -> None:
    ptype = payload.get("type")

    if ptype == "patch_apply_end":
        changes = payload.get("changes")
        success = bool(payload.get("success"))
        verdict = "applied" if success else "error"
        if isinstance(changes, dict) and changes:
            for raw_path, change_info in changes.items():
                change_type = ""
                if isinstance(change_info, dict):
                    change_type = str(change_info.get("type", ""))
                try:
                    norm_path = ntpath.normpath(raw_path) if ntpath.isabs(raw_path) else raw_path
                except (ValueError, TypeError):
                    norm_path = raw_path
                ev = _make_event(
                    ts=ts, provider="codex", session_id=session_id, kind="write",
                    evidence_ref=f"{path}#L{offset}", model=model, model_confidence=model_confidence,
                    operation_id=str(payload.get("call_id") or f"patch@{offset}"),
                    parent_session_id=parent_session_id, is_subagent=is_subagent, cwd=cwd,
                    # target_confidence="direct"(主会话 2026-08-17 裁决改判, 不是 "parsed"):
                    # changes 的 key 本身就是绝对路径, 不经过任何命令文本正则——完全符合
                    # schema 里 "direct = 目标路径来自结构化字段" 的定义, 不是启发式解析。
                    tool="patch_apply", target=norm_path, target_confidence="direct",
                    verdict=verdict, note=f"patch_apply_end:{change_type}"[:80],
                )
                problems = validate_event(ev)
                if problems:
                    if len(result.failures) < 20:
                        result.failures.append(f"{path}#L{offset}: invalid patch_apply event dropped: {problems}")
                    continue
                result.events.append(ev)
        else:
            # success=False 且没有 changes(比如 patch 语法本身就不合法, 一个文件都没碰到过):
            # 如实记一条 command 级失败, 不编造目标路径。
            ev = _make_event(
                ts=ts, provider="codex", session_id=session_id, kind="command",
                evidence_ref=f"{path}#L{offset}", model=model, model_confidence=model_confidence,
                operation_id=str(payload.get("call_id") or f"patch@{offset}"),
                parent_session_id=parent_session_id, is_subagent=is_subagent, cwd=cwd,
                tool="patch_apply", target="", target_confidence="none",
                verdict=verdict, note="patch_apply_end:no-changes",
            )
            problems = validate_event(ev)
            if not problems:
                result.events.append(ev)
        return

    if ptype == "mcp_tool_call_end":
        call_id = payload.get("call_id")
        invocation = payload.get("invocation")
        invocation = invocation if isinstance(invocation, dict) else {}
        tool_name = invocation.get("tool") or "mcp"
        args = invocation.get("arguments")
        args = args if isinstance(args, dict) else {}
        code_text = args.get("code")
        verdict = _verdict_from_mcp_result(payload.get("result"))
        targets: list[tuple[str, str]] = []
        if isinstance(code_text, str):
            targets = extract_write_targets(code_text, cwd, source="js")
        if targets:
            for norm_path, basis in targets:
                ev = _make_event(
                    ts=ts, provider="codex", session_id=session_id, kind="write",
                    evidence_ref=f"{path}#L{offset}", model=model, model_confidence=model_confidence,
                    operation_id=str(call_id or ""),
                    parent_session_id=parent_session_id, is_subagent=is_subagent, cwd=cwd,
                    tool=f"mcp:{tool_name}", target=norm_path, target_confidence="parsed",
                    verdict=verdict, note=basis[:80],
                )
                problems = validate_event(ev)
                if not problems:
                    result.events.append(ev)
                elif len(result.failures) < 20:
                    result.failures.append(f"{path}#L{offset}: invalid mcp write event dropped: {problems}")
        # 没有 targets 时不产出 command 级事件——mcp js 调用量大且多数是纯读取/反射,
        # 逐条落盘会制造大量与"文件被谁改了"无关的噪音(call_id 也已被 output 消耗,
        # 不会再落进 pending 里悬空)。这条与 shell_command/exec 的"无目标也落 command
        # 事件"策略不同, 差异原因见交付说明。
        if call_id in pending:
            pending.pop(call_id, None)
        return
    # task_started/user_message/agent_message/token_count/task_complete/item_completed/
    # sub_agent_activity/web_search_end/thread_settings_applied/context_compacted/
    # turn_aborted: 与写入归因无关或缺乏可靠结构化 denial 语义(见模块 docstring), 跳过。


__all__ = [
    "extract_write_targets",
    "collect_codex_sessions",
]
