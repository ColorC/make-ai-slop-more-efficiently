# [OMNI] origin=team_builder domain=services/csv_to_md/team ts=2026-04-25T00:00:00Z type=team
# [OMNI] material_id="material:utility.csv_to_md.team_topology.config.py"
"""csv_to_md Team · 拓扑声明 (team_builder 自动产出)."""
from __future__ import annotations

from omnicompany.protocol.anchor import (
    AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind, NodeMaturity, TeamEdge, TeamNode, TeamSpec,
)


def _anchor(node_id, fmt_in, fmt_out, *, vkind, desc, routes, maturity=NodeMaturity.GROWING):
    return TeamNode(
        id=node_id,
        kind=NodeKind.ANCHOR,
        maturity=maturity,
        anchor=AnchorSpec(
            id=f'a_{node_id}',
            name=node_id,
            format_in=fmt_in,
            format_out=fmt_out,
            validator=ValidatorSpec(id=f'v_{node_id}', kind=vkind, description=desc),
            routes=routes,
        ),
    )


def build_team() -> TeamSpec:
    """构建 csv_to_md Team."""
    nodes = []
    nodes.append(_anchor(
        'CsvReaderWorker', 'csv_to_md.file_input', 'csv_to_md.parsed_rows',
        vkind=ValidatorKind.HARD,
        desc="1. 读取 csv_to_md.file_input 得 {path: str, encoding: str}。\n2. DiskBus 验证文件存在性: 若 Path(path) 不存在 → Verdict(FAIL, diagnosis='文件不存在')。\n3. DiskBus.read(path, encoding=encoding) 读取文件全文; 若 UnicodeDecodeError → Verdict(PARTIAL, diagnosis='编码不匹配，建议重试或更换 encoding')。\n4. 使用 Python csv.reader(StringIO(content)) 解",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))
    nodes.append(_anchor(
        'MarkdownWriterWorker', 'csv_to_md.parsed_rows', 'csv_to_md.md_output',
        vkind=ValidatorKind.HARD,
        desc="HARD 规则 — 纯函数, 无 LLM, 规则驱动确定性:\n1. 从 csv_to_md.parsed_rows 取 headers (string[]) 和 rows (string[][]);\n2. 若 headers 为空 → 路由 FAIL (diagnosis: 'no headers');\n3. 对每个 cell 执行 GFM 转义: `|` → `\\|`, 换行符 `\\n`/`\\r` → `<br>`, 首尾去空格;\n4. 构建 GFM 表头: `| header[0] | header[1] | ... |`;\n5. 构建对齐分隔行: `| --- | --- | ... |",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))
    edges = []
    edges.append(TeamEdge(source="CsvReaderWorker", target="MarkdownWriterWorker", condition=VerdictKind.PASS))
    return TeamSpec(
        id='csv_to_md',
        name='csv_to_md',
        description='csv_to_md team',
        entry='CsvReaderWorker',
        nodes=nodes,
        edges=edges,
        tags=['csv_to_md', 'generated'],
    )
