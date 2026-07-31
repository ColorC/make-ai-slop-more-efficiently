from pathlib import Path

def patch(path, pairs):
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert t.count(old) == 1, f"{path}: not unique/found: {old[:70]!r} (count={t.count(old)})"
        t = t.replace(old, new)
    p.write_text(t, encoding="utf-8")
    print("patched", path)

# design (D-201)
patch("src/omnicompany/packages/domains/software_engineering/design/formats.py", [
    ('        description="设计审查任务: 设计提案文本 + 项目目录 + 审查目标",\n        parent="sw.task-input",\n    ),',
     '        description="设计审查任务: 设计提案文本 + 项目目录 + 审查目标",\n        parent="sw.task-input",\n        tags=["kind.source"],\n    ),'),
    ('tags=["scanned"],', 'tags=["scanned", "kind.internal"],'),
    ('tags=["stateful", "accumulating"],', 'tags=["stateful", "accumulating", "kind.internal"],'),
    ('tags=["analyzed"],', 'tags=["analyzed", "kind.internal"],'),
    ('parent="sw.llm-review",\n        tags=["reviewed"],',
     'parent="sw.llm-review",\n        tags=["reviewed", "kind.internal"],'),
    ('tags=["finalized"],', 'tags=["finalized", "kind.sink"],'),
])

# equiv_test (D-204)
patch("src/omnicompany/packages/domains/software_engineering/equiv_test/formats.py", [
    ('        description="等价性测试规格：Python 源文件路径 + TS 翻译路径 + 接口清单 + 测试类型要求",\n        parent="requirement",\n    ),',
     '        description="等价性测试规格：Python 源文件路径 + TS 翻译路径 + 接口清单 + 测试类型要求",\n        parent="requirement",\n        tags=["kind.source"],\n    ),'),
    ('tags=["structured", "executable"],', 'tags=["structured", "executable", "kind.internal"],'),
    ('tags=["structured", "executable", "executed"],', 'tags=["structured", "executable", "executed", "kind.internal"],'),
    ('tags=["structured", "executable", "executed", "compared"],', 'tags=["structured", "executable", "executed", "compared", "kind.internal"],'),
    ('tags=["structured", "executable", "executed", "compared", "diagnosed"],', 'tags=["structured", "executable", "executed", "compared", "diagnosed", "kind.sink"],'),
])

# generated (D-207)
patch("src/omnicompany/packages/domains/software_engineering/generated/formats.py", [
    ('tags=["sw"],\n    json_schema={\n        "type": "object",\n        "properties": {\n            "text": {',
     'tags=["sw", "kind.source"],\n    json_schema={\n        "type": "object",\n        "properties": {\n            "text": {'),
    ('tags=["sw"],\n    json_schema={\n        "type": "object",\n        "properties": {\n            "status": {',
     'tags=["sw", "kind.internal"],\n    json_schema={\n        "type": "object",\n        "properties": {\n            "status": {'),
    ('tags=["sw"],\n    json_schema={\n        "type": "object",\n        "properties": {\n            "word_count": {',
     'tags=["sw", "kind.sink"],\n    json_schema={\n        "type": "object",\n        "properties": {\n            "word_count": {'),
])

# implement (D-209)
patch("src/omnicompany/packages/domains/software_engineering/implement/formats.py", [
    ('        description="实施任务: 需求文本 + 项目目录 + 范围 + 相关文件",\n        parent="sw.task-input",\n    ),',
     '        description="实施任务: 需求文本 + 项目目录 + 范围 + 相关文件",\n        parent="sw.task-input",\n        tags=["kind.source"],\n    ),'),
    ('tags=["scanned"],', 'tags=["scanned", "kind.internal"],'),
    ('tags=["stateful", "accumulating"],', 'tags=["stateful", "accumulating", "kind.internal"],'),
    ('tags=["generated"],', 'tags=["generated", "kind.internal"],'),
    ('tags=["finalized"],', 'tags=["finalized", "kind.sink"],'),
])

# lang_rewrite (D-212)
patch("src/omnicompany/packages/domains/software_engineering/lang_rewrite/formats.py", [
    ('        description="待改写的 Python 源文件及其元数据：路径、AST 摘要、公开接口列表、内部依赖",\n        parent="code",\n    ),',
     '        description="待改写的 Python 源文件及其元数据：路径、AST 摘要、公开接口列表、内部依赖",\n        parent="code",\n        tags=["kind.source"],\n    ),'),
    ('tags=["structured", "dependency-resolved"],', 'tags=["structured", "dependency-resolved", "kind.internal"],'),
    ('tags=["structured", "dependency-resolved", "translation-ready"],', 'tags=["structured", "dependency-resolved", "translation-ready", "kind.internal"],'),
    ('tags=["translated"],', 'tags=["translated", "kind.internal"],'),
    ('tags=["translated", "type-checked"],', 'tags=["translated", "type-checked", "kind.internal"],'),
    ('tags=["translated", "type-checked", "style-checked"],', 'tags=["translated", "type-checked", "style-checked", "kind.internal"],'),
    ('tags=["translated", "type-checked", "style-checked", "signature-verified"],', 'tags=["translated", "type-checked", "style-checked", "signature-verified", "kind.internal"],'),
    ('tags=["translated", "type-checked", "style-checked", "behavioral-tested"],', 'tags=["translated", "type-checked", "style-checked", "behavioral-tested", "kind.internal"],'),
    ('tags=["translated", "type-checked", "semantically-verified"],', 'tags=["translated", "type-checked", "semantically-verified", "kind.sink"],'),
])

# lang_rewrite_verifier (D-215)
patch("src/omnicompany/packages/domains/software_engineering/lang_rewrite_verifier/formats.py", [
    ('tags=["smoke", "test-plan"],', 'tags=["smoke", "test-plan", "kind.internal"],'),
    ('tags=["smoke", "verified"],', 'tags=["smoke", "verified", "kind.sink"],'),
])

# plan (D-219)
patch("src/omnicompany/packages/domains/software_engineering/plan/formats.py", [
    ('        description="设计文档/需求: 文本内容 + 来源路径 + 项目目录",\n        parent="sw.task-input",\n    ),',
     '        description="设计文档/需求: 文本内容 + 来源路径 + 项目目录",\n        parent="sw.task-input",\n        tags=["kind.source"],\n    ),'),
    ('tags=["scanned"],', 'tags=["scanned", "kind.internal"],'),
    ('tags=["stateful", "accumulating"],', 'tags=["stateful", "accumulating", "kind.internal"],'),
    ('tags=["mapped"],', 'tags=["mapped", "kind.internal"],'),
    ('tags=["drafted"],', 'tags=["drafted", "kind.internal"],'),
    ('parent=f"{DOMAIN}.draft",\n        tags=["reviewed"],',
     'parent=f"{DOMAIN}.draft",\n        tags=["reviewed", "kind.internal"],'),
    ('tags=["validated", "finalized"],', 'tags=["validated", "finalized", "kind.sink"],'),
])

# review (D-222)
patch("src/omnicompany/packages/domains/software_engineering/review/formats.py", [
    ('        description="待审查的代码差异: git diff 输出或直接 diff 文本，包含描述信息和来源(git SHA/直接文本)",\n        parent="tool-observation",\n    ),',
     '        description="待审查的代码差异: git diff 输出或直接 diff 文本，包含描述信息和来源(git SHA/直接文本)",\n        parent="tool-observation",\n        tags=["kind.source"],\n    ),'),
    ('tags=["context-gathered"],', 'tags=["context-gathered", "kind.internal"],'),
    ('tags=["context-gathered", "tests-scanned"],', 'tags=["context-gathered", "tests-scanned", "kind.internal"],'),
    ('tags=["stateful", "accumulating"],', 'tags=["stateful", "accumulating", "kind.internal"],'),
    ('parent=f"{DOMAIN}.review-context",\n        tags=["reviewed"],',
     'parent=f"{DOMAIN}.review-context",\n        tags=["reviewed", "kind.internal"],'),
    ('tags=["reviewed", "validated"],', 'tags=["reviewed", "validated", "kind.internal"],'),
    ('tags=["reported"],', 'tags=["reported", "kind.sink"],'),
])

# tdd (D-225)
patch("src/omnicompany/packages/domains/software_engineering/tdd/formats.py", [
    ('        description="TDD 计划输入: 分步计划文本 + 项目目录",\n        parent="sw.task-input",\n    ),',
     '        description="TDD 计划输入: 分步计划文本 + 项目目录",\n        parent="sw.task-input",\n        tags=["kind.source"],\n    ),'),
    ('tags=["generated"],', 'tags=["generated", "kind.internal"],'),
    ('tags=["executed"],', 'tags=["executed", "kind.internal"],'),
    ('tags=["finalized"],', 'tags=["finalized", "kind.sink"],'),
])

# verify (D-228)
patch("src/omnicompany/packages/domains/software_engineering/verify/formats.py", [
    ('        description="待验证的声称: claim + verify_cmd + work_dir + expect_pattern",\n        parent="sw.task-input",\n    ),',
     '        description="待验证的声称: claim + verify_cmd + work_dir + expect_pattern",\n        parent="sw.task-input",\n        tags=["kind.source"],\n    ),'),
    ('tags=["env-checked"],', 'tags=["env-checked", "kind.internal"],'),
    ('tags=["executed"],', 'tags=["executed", "kind.internal"],'),
    ('parent=f"{DOMAIN}.execution",\n        tags=["analyzed"],',
     'parent=f"{DOMAIN}.execution",\n        tags=["analyzed", "kind.internal"],'),
    ('tags=["analyzed", "supplemental-planned"],', 'tags=["analyzed", "supplemental-planned", "kind.internal"],'),
    ('tags=["reported"],', 'tags=["reported", "kind.sink"],'),
    ('tags=["stateful", "accumulating"],', 'tags=["stateful", "accumulating", "kind.internal"],'),
])
