# DESIGN.md — csv_to_md

## 状态

本 team 处于 **设计完成 / 待实现** 阶段。两个 Worker（CsvReaderWorker 与 MarkdownWriterWorker）已完成规则级深化（rule_spec 已定稿），三份 Material（file_input、parsed_rows、md_output）已完成 JSON Schema 与 5-element description 定义。当前尚未生成 Python 实现代码，下一步进入代码生成阶段。

## 核心目的

csv_to_md 的核心目的是将本地磁盘上的 CSV 文件安全、确定性地转换为符合 GFM（GitHub Flavored Markdown）规范的表格字符串。该管线由两个纯函数式 HARD Worker 串联完成：先由 CsvReaderWorker 读取并解析 CSV 文件，剥离出结构化表头与数据行；再由 MarkdownWriterWorker 对每个 cell 执行 GFM 转义（`\|`、`<br>` 等）并拼装最终表格。整个流程无 LLM 参与，保证输出结果的字节级可复现性。

## 核心接口

### 核心接口
| Worker | 输入 Material | 输出 Material | 路由策略 |
|---|---|---|---|
| CsvReaderWorker | `csv_to_md.file_input` `{path, encoding}` | `csv_to_md.parsed_rows` `{headers[], rows[][]}` | PASS→next(MarkdownWriterWorker); FAIL→halt(max 2); PARTIAL→retry(max 1) |
| MarkdownWriterWorker | `csv_to_md.parsed_rows` `{headers[], rows[][]}` | `csv_to_md.md_output` `{content: str}` | PASS→emit; FAIL→halt(max 0); PARTIAL→retry(max 1) |

### Material 协议

- **`csv_to_md.file_input`**（source）: `{"path": str, "encoding": str?}` — 声明待解析 CSV 的路径与编码，由 CLI 注入。
- **`csv_to_md.parsed_rows`**（intermediate）: `{"headers": [str], "rows": [[str]]}` — 承载规整后的表头列表与数据矩阵，短行补空串、超长行截断。
- **`csv_to_md.md_output`**（sink）: `{"content": str}` — 最终 GFM 表格字符串，强制包含 content 字段，满足字节级对齐要求。

## 架构决策

1. **纯 HARD Worker 管线，零 LLM 依赖**：两个 Worker 均标记为 HARD，使用 Python 标准库（`csv`, `io`, `pathlib`）和确定性字符串操作，确保同一输入永远产生同一输出，适合自动化测试与回归验证。
2. **短行补齐 + 超长截断策略**：CsvReaderWorker 以首行 headers 的长度为基准，对数据行执行右补空串（补齐至等长）和截断（超出行裁剪），避免因 CSV 不规范导致的列错位问题，代价是可能静默丢弃尾部多余字段。
3. **三段式 Verdict 路由**：采用 PASS / FAIL / PARTIAL 三态判定。FAIL 对应不可恢复错误（文件不存在、headers 为空），PARTIAL 对应可重试的软错误（编码不匹配、解析异常），支持有限次数自动重试，避免无限循环。
4. **默认左对齐分隔行**：MarkdownWriterWorker 的对齐分隔行统一使用 `| --- |`（左对齐），不根据数据类型推断对齐方式，保持输出简单且可预测，用户如有对齐需求可在后续 worker 中扩展。
5. **GFM 转义最小集**：仅转义 `|` → `\|` 和换行符 `\n/\r` → `<br>`，不处理表格内嵌套 Markdown 语法（如链接、图片），在安全性与完整性之间取折中，优先保证表格结构不被破坏。

## 数据流 / 拓扑

```
[CLI / JSON 入口]
       │
       │ inject {"path": "...", "encoding": "utf-8"}
       ▼
  csv_to_md.file_input
       │
       │ consumed by
       ▼
┌──────────────────────┐
│   CsvReaderWorker    │
│  (HARD, 纯函数)      │
│                      │
│ 1. DiskBus 验证存在  │
│ 2. read(path, enc)   │
│ 3. csv.reader 解析   │
│ 4. 短补/长截/strip   │
└──────────┬───────────┘
           │ Verdict PASS
           │ output: {headers[], rows[][]}
           ▼
  csv_to_md.parsed_rows
           │
           │ consumed by
           ▼
┌──────────────────────┐
│  MarkdownWriterWorker│
│  (HARD, 纯函数)      │
│                      │
│ 1. GFM 转义 cell     │
│ 2. 构建表头行        │
│ 3. 构建 --- 分隔行   │
│ 4. 构建数据行        │
│ 5. 列数一致性校验    │
└──────────┬───────────┘
           │ Verdict PASS → emit
           │ output: {content: str}
           ▼
  csv_to_md.md_output
           │
           ▼
     [终端 / 下游消费]
```

异常路径简述：
- `CsvReaderWorker` 文件不存在 → FAIL (halt, max_retries=2)
- `CsvReaderWorker` 编码不匹配 → PARTIAL (retry, max_retries=1)
- `MarkdownWriterWorker` headers 为空 → FAIL (halt, max_retries=0)

## 已知局限

1. **编码猜测风险**：用户提供的 `encoding` 与实际文件编码不一致时，`UnicodeDecodeError` 触发 PARTIAL 而非 FAIL，重试后可能解析出乱码数据而不自知。未集成 `chardet` 等编码自动检测库。
2. **非标准 CSV 兼容性有限**：Python 标准库 `csv.reader` 对含嵌入换行、引号转义不规范的 CSV 可能产生错位行；对 BOM 头（`\ufeff`）需手动处理，否则首列 header 会带不可见字符。
3. **空行 / 注释行处理粗糙**：当前规则未区分空行与有效数据行，空行会被当作包含空字符串的合法行输出；CSV 中 `#` 注释行也无特殊处理，会被当作普通数据解析。
4. **对齐分隔行硬编码左对齐**：`| --- |` 固定左对齐，无法根据列内容类型（数字右对齐、标题居中）自动推断，如需精细对齐需新增配置项或第三个 Worker。
5. **GFM 转义不完整**：仅转义 `|` 和换行符，不处理 `[]`、`()` 等可能干扰 Markdown 渲染的字符；表格内嵌套 Markdown（链接、代码块）可能产生非预期渲染结果。

## 参考资料

- `docs/standards/OMNI-034.md` — Team DESIGN.md 七节规范（状态 / 核心目的 / 核心接口 / 架构决策 / 数据流 / 已知局限 / 参考资料）
- `docs/standards/OMNI-029.md` — Worker 规则规范（HARD/SOFT 分类、Verdict 路由、rule_spec 格式）
- `docs/standards/OMNI-031.md` — Material 协议规范（JSON Schema、5-element description、producer/consumer 生命周期）
- `docs/standards/OMNI-027.md` — GFM 表格语法标准（管道符分隔、对齐分隔行、cell 转义规则）
- `docs/standards/OMNI-033.md` — Workspace 目录结构与 write_prefix 规范