# [OMNI] origin=omnicompany domain=omnicompany/core ts=2026-04-05T00:00:00Z type=infrastructure status=active
# [OMNI] summary="OmniMark 文件头规范的解析与注入实现"
# [OMNI] why="所有受管文件需要可追溯的身份标记, 出问题时知道是谁/什么时候/为什么写的"
# [OMNI] tags=omnimark,header,provenance,registry
# [OMNI] material_id="material:omnicompany.core.omnimark.header_parser.provenance.py"
"""OmniMark — 统一文件身份头标签 (解析 + 注入)

详见 docs/standards/omni-header.md.

2026-05-01 加强 (用户拍板): summary / why / tags 三个核心管理字段加入必填集.
多行 [OMNI] 头支持: 从第一个 [OMNI] 行开始, 连续的 [OMNI] 行都被合并解析.
长值用引号: summary / why 这类自由文本字段用双引号包裹.
tags 用逗号分隔: tags=tag1,tag2,tag3.

必填字段: origin, ts, summary, why, tags
条件必填: agent (LLM 产生时), trace + node (管线产生时)
可选字段: domain, status (默认 active), type, module

与 patrol.py 中旧格式 (created_by=, intent=) 的关系:
  旧格式仍可解析 (KV 解析器宽容), 但新写入统一使用本规范.
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ─── 字段规范 ────────────────────────────────────────────────────

ORIGIN_VALUES = frozenset({
    "human",
    "claude-code",
    "ai-ide",          # 2026-05-01 用户拍板的新称呼, 跟 claude-code 等价但语义化
    "workflow-factory",
    "skill-import",
    "sw-implement",
    "sw-tdd",
    "lang-rewrite",
    "guardian-stamp",
    "omnicompany",   # 框架自身产生
    "unknown",
})

STATUS_VALUES = frozenset({
    "active",
    "deprecated",
    "quarantined",
    "pending-review",
})


# ─── 数据结构 ────────────────────────────────────────────────────

@dataclass
class OmniMarkFields:
    """解析出的 OmniMark 字段集合."""

    origin: str = "unknown"
    domain: str = ""
    agent: str = ""
    ts: str = ""
    trace: str = ""
    node: str = ""
    status: str = "active"

    # v2 新增字段 (docs/standards/omni-header.md)
    type: str = ""    # 实体类型 (router/format/pipeline/doc/test/script/scratch/config)
    module: str = ""  # 注册系统 module 路径 (供注册系统快速定位实体)

    # 2026-05-04 CORE-SELF-STABILITY plan 第一阶段: 自我画像铆钉 — service 锚
    belongs_to_service: str = ""  # 属于哪个 service (跟 packages/services/(_*/)?<service>/ 路径锚定)

    # 2026-05-01 用户拍板核心管理字段 (这是什么 / 谁写的 / 内容简洁描述 / 为什么写 / tags)
    # "这是什么" 由 type 承担; "谁写的" 由 origin + agent + trace 联合承担
    # 下面三个补齐用户列的核心字段
    summary: str = ""           # 内容简洁描述 (一两句话讲这文件干嘛)
    why: str = ""               # 为什么写在这个位置 (语义理由)
    tags: tuple = ()            # 分类/搜索/归档用的标签集合

    # 兼容旧格式字段 (patrol.py 早期使用)
    created_by: str = ""
    intent: str = ""

    # 额外 KV (未知字段不丢失)
    extra: dict = field(default_factory=dict)

    def is_canonical(self) -> bool:
        """是否符合新规范 (有 origin 和 ts)."""
        return bool(self.origin and self.ts)

    def is_canonical_v3(self) -> bool:
        """是否符合 2026-05-01 用户拍板的核心五字段要求."""
        return bool(
            self.origin
            and self.ts
            and self.type
            and self.summary
            and self.why
            and self.tags
        )

    def to_comment_lines(self, comment_prefix: str = "#") -> list[str]:
        """渲染为多行注释格式. 第一行放短字段, 后续行放长字段.

        长字段 (summary / why) 用双引号包裹, tags 用逗号分隔.
        comment_prefix 是 "#" (Python/shell) 或 "<!--" (HTML/Markdown).
        """
        suffix = " -->" if comment_prefix.startswith("<!--") else ""

        # 第一行: 短字段
        short_parts = [f"origin={self.origin}"]
        if self.domain:
            short_parts.append(f"domain={self.domain}")
        if self.type:
            short_parts.append(f"type={self.type}")
        if self.agent:
            short_parts.append(f"agent={self.agent}")
        if self.ts:
            short_parts.append(f"ts={self.ts}")
        if self.trace:
            short_parts.append(f"trace={self.trace}")
        if self.node:
            short_parts.append(f"node={self.node}")
        if self.status and self.status != "active":
            short_parts.append(f"status={self.status}")
        if self.module:
            short_parts.append(f"module={self.module}")
        if self.belongs_to_service:
            short_parts.append(f"belongs_to_service={self.belongs_to_service}")

        lines = [f"{comment_prefix} [OMNI] " + " ".join(short_parts) + suffix]

        # 后续行: 长字段 (summary / why) 各占一行, 用引号包裹
        if self.summary:
            lines.append(f'{comment_prefix} [OMNI] summary="{_escape_quote(self.summary)}"' + suffix)
        if self.why:
            lines.append(f'{comment_prefix} [OMNI] why="{_escape_quote(self.why)}"' + suffix)
        if self.tags:
            tags_str = ",".join(self.tags)
            lines.append(f"{comment_prefix} [OMNI] tags={tags_str}" + suffix)

        return lines

    def to_comment_line(self) -> str:
        """向后兼容: 单行渲染 (只含短字段). 新代码请用 to_comment_lines()."""
        return self.to_comment_lines()[0]


def _escape_quote(s: str) -> str:
    """保证双引号包裹的字符串里不出现裸双引号."""
    return s.replace('"', "'")


# ─── 正则 ────────────────────────────────────────────────────────

# 匹配行中的 [OMNI] 标记 (支持 Python # 注释和 HTML <!-- --> 注释)
_OMNI_LINE_RE = re.compile(
    r"(?:#|<!--)\s*\[OMNI\]\s+(.+?)(?:-->)?\s*$",
    re.IGNORECASE,
)

# KV 解析: 支持双引号包裹 (用于 summary / why 这种含空格的长值) 和无引号 (短值)
_KV_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')


# ─── 解析 ────────────────────────────────────────────────────────

def parse_omnimark(content_or_path: str | Path) -> Optional[OmniMarkFields]:
    """从文件内容或路径中解析 OmniMark 头.

    扫描前 30 行 (规范要求头在文件顶部, 现在支持多行 [OMNI] 头).
    无头返回 None; 有头但字段不全返回 OmniMarkFields (部分填充).

    多行 [OMNI] 头处理:
      从第一个 [OMNI] 行开始, 连续的 [OMNI] 行都被合并解析.
      一旦遇到非 [OMNI] 非空白注释行, 停止 (避免吃 docstring 内嵌示例).
    """
    if isinstance(content_or_path, Path):
        try:
            text = content_or_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    else:
        text = content_or_path

    if not text:
        return None

    head_lines = text.splitlines()[:30]
    all_kvs: dict[str, str] = {}
    found = False
    started = False

    for line in head_lines:
        m = _OMNI_LINE_RE.search(line)
        if m:
            found = True
            started = True
            for match in _KV_RE.finditer(m.group(1)):
                k = match.group(1)
                v = match.group(2) if match.group(2) is not None else match.group(3)
                # 多行头里同名字段后写的覆盖前写的
                all_kvs[k] = v
            continue
        # 已经开始读 [OMNI] 行后, 一旦遇到非 [OMNI] 行, 停止
        # (避免 docstring 里的 [OMNI] 示例覆盖真头)
        if started:
            stripped = line.strip()
            # 允许 shebang/coding 在中间? 不允许 — 规范说 [OMNI] 头连续
            if stripped == "":
                # 连续的 [OMNI] 头允许夹空行? 不允许, 规范要紧凑
                break
            break

    if not found:
        return None

    known = {
        "origin", "domain", "agent", "ts", "trace", "node", "status",
        "type", "module",                          # v2 字段
        "summary", "why", "tags",                  # 2026-05-01 用户拍板核心字段
        "belongs_to_service",                      # 2026-05-04 CORE-SELF-STABILITY 第一阶段: service 锚
        "created_by", "intent",                    # 兼容旧格式
    }
    extra = {k: v for k, v in all_kvs.items() if k not in known}

    tags_raw = all_kvs.get("tags", "")
    tags_tuple = tuple(t.strip() for t in tags_raw.split(",") if t.strip()) if tags_raw else ()

    return OmniMarkFields(
        origin=all_kvs.get("origin", "unknown"),
        domain=all_kvs.get("domain", ""),
        agent=all_kvs.get("agent", ""),
        ts=all_kvs.get("ts", ""),
        trace=all_kvs.get("trace", ""),
        node=all_kvs.get("node", ""),
        status=all_kvs.get("status", "active"),
        type=all_kvs.get("type", ""),
        module=all_kvs.get("module", ""),
        belongs_to_service=all_kvs.get("belongs_to_service", ""),
        summary=all_kvs.get("summary", ""),
        why=all_kvs.get("why", ""),
        tags=tags_tuple,
        created_by=all_kvs.get("created_by", ""),
        intent=all_kvs.get("intent", ""),
        extra=extra,
    )


# ─── 注入 ────────────────────────────────────────────────────────

def stamp_file(
    path: str | Path,
    origin: str = "",     # 2026-05-01 改: 留空时自动填 "ai-ide" (跟 session 关联)
    domain: str = "",
    agent: str = "",      # 2026-05-01 改: 留空时自动填 "ai-ide-<session_short>" 跟踪
    trace: str = "",
    node: str = "",
    status: str = "active",
    type: str = "",    # noqa: A002  # v2 新增: 实体类型
    module: str = "",  # v2 新增: 注册系统 module 路径
    summary: str = "",   # 2026-05-01 用户拍板: 内容简洁描述
    why: str = "",       # 2026-05-01 用户拍板: 为什么写
    tags: tuple | list = (),  # 2026-05-01 用户拍板: 标签
    overwrite: bool = False,
) -> bool:
    """向文件注入 OmniMark 头 (若已有头且 overwrite=False 则跳过).

    2026-05-01 行为变更:
    - origin 留空时默认填 "ai-ide" (假定是 AI IDE 在写, 显式传 "human" 等覆盖)
    - agent 留空时尝试调 session 模块拿当前 session 短 ID, 自动填 "ai-ide-<short>"
      session 不可用时降级填空字符串

    Returns:
        True  — 成功注入 (或已有头且跳过)
        False — 文件不存在或写入失败
    """
    p = Path(path)
    if not p.exists():
        return False

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    # 已有 OmniMark 且不强制覆盖 → 跳过
    if parse_omnimark(content) is not None and not overwrite:
        return True

    # 默认 origin 是 ai-ide (假设是 AI IDE 在写, 调用方显式传 human 等可覆盖)
    if not origin:
        origin = "ai-ide"

    # 默认 agent 跟当前 session 关联 (调 session 模块, 失败降级)
    if not agent:
        try:
            from .session import current_writer_identity
            agent = current_writer_identity(start=p.parent)
            # 如果 session 拿不到, current_writer_identity 返回 "ai-ide-unknown",
            # 跟默认 origin 重复, 索性留空
            if agent == "ai-ide-unknown":
                agent = ""
        except Exception:
            agent = ""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = OmniMarkFields(
        origin=origin,
        domain=domain or _infer_domain(p),
        agent=agent,
        ts=ts,
        trace=trace,
        node=node,
        status=status,
        type=type,
        module=module,
        summary=summary,
        why=why,
        tags=tuple(tags) if tags else (),
    )

    # 根据文件后缀决定注释前缀
    if p.suffix in {".md", ".html", ".htm"}:
        comment_prefix = "<!--"
    elif p.suffix in {".yaml", ".yml"}:
        comment_prefix = "#"
    else:
        comment_prefix = "#"

    header_lines = fields.to_comment_lines(comment_prefix=comment_prefix)
    new_content = _inject_header_lines(content, header_lines, p.suffix)

    try:
        p.write_text(new_content, encoding="utf-8")
        return True
    except OSError:
        return False


def _infer_domain(path: Path) -> str:
    """从文件路径推断 domain (尽力而为)."""
    parts = path.parts
    # src/omnicompany/packages/<ns>/<domain>/... → "<ns>/<domain>"
    try:
        idx = parts.index("packages")
        if idx + 2 < len(parts):
            return f"{parts[idx+1]}/{parts[idx+2]}"
        elif idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    # src/omnicompany/runtime/... → "runtime"
    if "runtime" in parts:
        return "omnicompany/runtime"
    if "core" in parts:
        return "omnicompany/core"
    return ""


def _inject_header_lines(content: str, header_lines: list[str], suffix: str) -> str:
    """在文件顶部合适位置插入多行 header.

    插入规则:
    - 跳过 shebang (#!)
    - 跳过 coding 声明 (# -*- coding ...)
    - 在模块 docstring 之前 (Python 惯例是 docstring 在最前,
      但 [OMNI] 在 docstring 之前让 parse 更快)
    """
    lines = content.splitlines(keepends=True)
    insert_at = 0

    for i, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped.startswith("#!") or "coding" in stripped:
            insert_at = i + 1
        else:
            break

    new_header = "".join(h + "\n" for h in header_lines)
    lines.insert(insert_at, new_header)
    return "".join(lines)


# 向后兼容 — 旧调用方可能用 _inject_header (单行版本)
def _inject_header(content: str, header_line: str, suffix: str) -> str:
    """向后兼容的单行注入. 新代码请用 _inject_header_lines."""
    return _inject_header_lines(content, [header_line], suffix)


# ─── 文件指纹 ────────────────────────────────────────────────────

def file_fingerprint(path: str | Path) -> str:
    """计算文件内容的 sha256 指纹 (OmniTow 用于检测文件变动)."""
    p = Path(path)
    try:
        data = p.read_bytes()
        return "sha256:" + hashlib.sha256(data).hexdigest()[:16]
    except OSError:
        return "sha256:error"


# ─── 数据产物 sidecar (I-20 data-provenance, 2026-04-23) ────────
#
# 动机: 代码文件可以内嵌 [OMNI] 注释头, 但数据产物 (JSON / JSONL / Markdown 报告 /
# .db 二进制) 无法或不便内嵌. 统一用 sidecar `<path>.omni.json` 存署名元数据.
#
# 核心字段: written_by (写入者 class path 或 CLI 命令) + ts. 下游 (Guardian LLM)
# 基于合法写入者白名单判 "此数据是否来自合法入口".
#
# 2026-05-01 加强: sidecar 也加 summary / why / tags 三字段 (跟代码文件头对齐).
#
# Plan §二.9 "统一合法入口 + 其余皆违规" 范式的落地基础设施.


_SIDECAR_SUFFIX = ".omni.json"
_SIDECAR_VERSION = "1.1"   # 2026-05-01 升级到 1.1, 加 summary/why/tags


@dataclass
class DataProvenance:
    """数据产物署名. 最小字段: kind + written_by + ts.

    2026-05-01 加 summary / why / tags 三个核心管理字段 (跟代码文件头对齐).
    """

    kind: str = "data"                    # 固定 "data" (区别于代码文件的 kind)
    written_by: str = ""                  # 写入者标识: "<module>.<class>" 或 "cli:<cmd>" 等
    ts: str = ""                          # ISO8601 写入时间
    run_id: Optional[str] = None          # 归属 pipeline run (若适用)
    job_id: Optional[str] = None          # 归属 MaterialDispatcher job
    trace: Optional[str] = None           # 归属 trace id
    source_path: Optional[str] = None     # 写入逻辑所在源码 (便于溯源)
    ttl_days: Optional[int] = None        # 过期天数建议 (供 OMNI-049 老化消费)
    origin: str = "omnicompany"           # 默认项目来源, 区分 vendors/外部导入数据
    version: str = _SIDECAR_VERSION       # sidecar schema 版本

    # 2026-05-01 用户拍板核心管理字段
    summary: str = ""                     # 内容简洁描述
    why: str = ""                         # 为什么写
    tags: tuple = ()                      # 标签 (序列化时转 list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None and v != ""}
        # tags 是 tuple, 序列化要变 list
        if "tags" in d:
            d["tags"] = list(d["tags"])
            if not d["tags"]:
                del d["tags"]
        return d


def sidecar_path(data_path: Path | str) -> Path:
    """data file → sidecar path (`<data>.omni.json`)."""
    p = Path(data_path)
    return p.with_suffix(p.suffix + _SIDECAR_SUFFIX) if p.suffix else p.with_name(p.name + _SIDECAR_SUFFIX)


def write_data_sidecar(
    data_path: Path | str,
    written_by: str,
    *,
    run_id: Optional[str] = None,
    job_id: Optional[str] = None,
    trace: Optional[str] = None,
    source_path: Optional[str] = None,
    ttl_days: Optional[int] = None,
    origin: str = "omnicompany",
    summary: str = "",       # 2026-05-01 加
    why: str = "",           # 2026-05-01 加
    tags: tuple | list = (), # 2026-05-01 加
    overwrite: bool = True,
) -> Path:
    """给 data_path 写 `.omni.json` sidecar, 返回 sidecar 路径.

    不改 data_path 本体. 失败抛 OSError.
    """
    import json
    prov = DataProvenance(
        kind="data",
        written_by=written_by,
        ts=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        job_id=job_id,
        trace=trace,
        source_path=source_path,
        ttl_days=ttl_days,
        origin=origin,
        summary=summary,
        why=why,
        tags=tuple(tags) if tags else (),
    )
    sc = sidecar_path(data_path)
    if sc.exists() and not overwrite:
        return sc
    sc.parent.mkdir(parents=True, exist_ok=True)
    sc.write_text(
        json.dumps(prov.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return sc


def read_data_sidecar(data_path: Path | str) -> Optional[DataProvenance]:
    """读 data_path 对应的 sidecar. 不存在返回 None."""
    import json
    sc = sidecar_path(data_path)
    if not sc.exists():
        return None
    try:
        obj = json.loads(sc.read_text(encoding="utf-8"))
        # tags 可能是 list, 转回 tuple
        if "tags" in obj and isinstance(obj["tags"], list):
            obj["tags"] = tuple(obj["tags"])
        return DataProvenance(**{k: v for k, v in obj.items() if k in DataProvenance.__dataclass_fields__})
    except Exception:
        return None


def is_sidecar_path(path: Path | str) -> bool:
    """判 path 自己是 sidecar 文件 (不参与 OMNI-047/048 等扫描)."""
    return str(path).endswith(_SIDECAR_SUFFIX)
