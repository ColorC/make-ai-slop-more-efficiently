# [OMNI] origin=team_builder domain=services/csv_to_md/formats ts=2026-04-25T00:00:00Z type=config
# [OMNI] material_id="material:utility.csv_to_md.material_definitions.config.py"
"""csv_to_md Team · Material 定义 (团队 builder 自动产出).

Material description 五要素: 内容语义 / 字段含义 / 上游承诺 / 下游用途 / 最小样例.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry

M_CSV_TO_MD_FILE_INPUT = Material(
    id='csv_to_md.file_input',
    name='csv_to_md.file_input',
    description='CSV-to-Markdown 管线的入口协议，声明待解析的 CSV 文件路径与读取编码，是整个 csv_to_md team 的外部触发信号 (kind.source)，无上游 producer Worker，由 CLI (`omni run csv-to-md -i path=<csv_file> [-i encoding=<enc>]`) 或 JSON 模式 (`omni run csv-to-md -j \'{"path":..., "encoding":...}\'`) 注入。 path (string, required): CSV 文件的绝对或相对路径，指向磁盘上待读取的 .csv 文件；encoding (string, default \'utf-8\'): 指定读取该 CSV 时使用的字符编码，常用值包括 utf-8、gbk、latin-1 等，若省略则默认按 utf-8 解析。 作为 source Material，无 producer Worker。CLI 入口层承诺: path 字段必须存在且为合法非空字符串；encoding 若提供则必须是 Python 标准库 codecs 可识别的编码名称。文件本身是否存在、编码是否匹配内容，交由 CsvReaderWorker 在运行时校验并产出 FAIL Verdict。 CsvReaderWorker 作为唯一 consumer，读取 path 指向的文件，按 encoding 指定的编码解码，使用 csv 标准库解析表头与数据行，处理文件不存在 (FileNotFoundError) 与编码错误 (UnicodeDecodeError) 等异常，输出结构化行数据 (header list + rows list of dicts/arrays) 给下游 MarkdownWriterWorker。 {"path',
    parent='requirement',
    json_schema={'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'CSV 文件绝对或相对路径'}, 'encoding': {'type': 'string', 'default': 'utf-8', 'description': 'CSV 文件编码，默认 utf-8'}}, 'required': ['path']},
    tags=['csv_to_md', 'generated', 'kind.source'],
)

M_CSV_TO_MD_PARSED_ROWS = Material(
    id='csv_to_md.parsed_rows',
    name='csv_to_md.parsed_rows',
    description='csv_to_md 管线的中间产物，承载 CsvReaderWorker 从原始 CSV 文件解析后剥离出的结构化表头列表与逐行数据矩阵，是纯数据协议层，不含任何 Markdown 格式信息，供下游 Writer 进行 GFM 表格渲染。 headers: 字符串数组，按 CSV 首行从左到右顺序记录列名，已经过 CSV 解析器剥离外层引号与转义（如字段内逗号、引号已正确还原）。rows: 二维字符串数组，每个子数组代表一行数据，长度必须与 headers 一致；空单元格表示为空字符串 "" 而非 null/None/省略；字段内原始换行符保留在字符串值中（未做 <br> 替换，那是 Writer 的职责）。 CsvReaderWorker 承诺：(1) headers 长度 ≥ 1（空 CSV / 无表头视为解析失败，走 Verdict.FAIL 路径，不产出本 Material）；(2) 每行 rows[i] 的 len 严格等于 len(headers)，不足补空串、多余截断；(3) 所有字符串值已按指定 encoding 正确解码，字段内的 CSV 转义（双引号 ""→"、字段内逗号、字段内换行）已还原为字面值；(4) rows 保持原始行序，不做排序/过滤/去重。 MarkdownWriterWorker 消费本 Material 时：(1) 用 headers 生成 GFM 表格首行；(2) 生成 | --- | 分隔行；(3) 遍历 rows 逐行渲染数据，对每个 cell 执行 GFM 转义（| → \\|，换行 → <br>）；(4) 保证末尾仅一个 \\n；(5) 若 rows 为空数组则输出仅含表头+分隔行的两行表格（对应需求用例3）。 {"headers": ["name", "age"], "rows": [["Alice", "30"], ["Bob',
    parent='doc',
    json_schema={'type': 'object', 'properties': {'headers': {'type': 'array', 'items': {'type': 'string'}, 'description': 'CSV 表头字段列表，按原始列序排列，每个元素为剥离引号/逗号转义后的纯字符串'}, 'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'string'}, 'description': '单行数据，元素数量必须与 headers 长度一致'}, 'description': '逐行数据矩阵，每行是与 headers 等长的字符串列表，空单元格为空串 ""'}}, 'required': ['headers', 'rows']},
    tags=['csv_to_md', 'generated', 'kind.internal'],
)

M_CSV_TO_MD_MD_OUTPUT = Material(
    id='csv_to_md.md_output',
    name='csv_to_md.md_output',
    description="csv_to_md team 的最终输出产物 (sink Material): 由 MarkdownWriterWorker 将 CsvReaderWorker 解析的结构化行数据按 GFM 语法规则渲染而成的 Markdown 表格字符串. 该 Material 代表整个 CSV→Markdown 转换流水线的终局结果, 被 CLI 打印到 stdout 并被验收脚本逐字节比对. content (required, string): GFM 表格的完整 markdown 文本, 必须严格满足 8 条硬约定 (表头行/分隔符行/数据行格式一致、| 转义、\\n→<br>、末尾单换行无 trailing 空白、空字段渲染为空串等). source_path (optional, string): 原始 CSV 文件路径, 用于追溯溯源. row_count (optional, integer): 转换的数据行数. column_count (optional, integer): 列数. MarkdownWriterWorker 承诺: ① content 字段必须存在且为合法字符串, 不允许 null/None/缺失; ② content 内容为严格 GFM 格式, 逐字节对齐需求 §3 的 8 条规则; ③ 分隔符行 | --- | 的列数与表头列数一致; ④ content 末尾有且仅有一个 \\n; ⑤ 字段内含 | 必须转义为 \\|, 字段内含换行必须渲染为 <br>; ⑥ 整体可复现: 同一 CSV 输入两次输出 byte-identical. 验收脚本 (acceptance.py) 通过 verdict.output['content'] 提取该字段, 与 expected/*.md 文件做逐字节 diff 比对, 任一不匹配即判定 FAIL. CLI har",
    parent='requirement',
    json_schema={'type': 'object', 'properties': {'content': {'type': 'string', 'description': 'GFM 表格 markdown 文本 (逐字节精确). 首行表头, 第二行分隔符 | --- |, 后续数据行; 字段内 | 转义为 \\|, 字段内换行渲染为 <br>, 末尾单个 \\n, 无 trailing 空白'}, 'source_path': {'type': 'string', 'description': '被转换的 CSV 文件路径 (便于追溯与调试)'}, 'row_count': {'type': 'integer', 'description': '数据行数 (不含表头), 0 表示仅表头'}, 'column_count': {'type': 'integer', 'description': '列数 (表头字段数)'}}, 'required': ['content']},
    tags=['csv_to_md', 'generated', 'kind.sink'],
)

ALL_MATERIALS = [M_CSV_TO_MD_FILE_INPUT, M_CSV_TO_MD_PARSED_ROWS, M_CSV_TO_MD_MD_OUTPUT]

def register_formats(registry: FormatRegistry) -> None:
    """注册 csv_to_md 所有 Material 到 registry."""
    for mat in ALL_MATERIALS:
        if not registry.is_registered(mat.id):
            try:
                registry.register(mat)
            except Exception:
                pass
