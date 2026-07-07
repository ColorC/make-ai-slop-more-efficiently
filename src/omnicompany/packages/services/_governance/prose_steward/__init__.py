# [OMNI] origin=claude-code domain=services/_governance/prose_steward ts=2026-06-27T00:00:00Z type=material
# [OMNI] summary="语言治理部门(语义空间健康治理 轨二)。治三类语言病:非中文泄漏(lang)/术语不一致·代称·易过时(term)/惜字如金(compress)。单一术语真源 docs/standards/prose_terms.yaml, 三检查器全派生。"
# [OMNI] why="非中文泄漏会让后续场景突然变英文;术语漂移;惜字如金只作者懂。批量+定时治, 确定性圈候选+性价比模型精判降误报。"
# [OMNI] tags=governance,language,terminology,semantic-space-health
# [OMNI] material_id="material:governance.prose_steward.__init__.py"
"""prose_steward — 语言治理部门(语义空间健康治理 · 轨二)。

三检查器(都从 docs/standards/prose_terms.yaml 单一真源派生):
  - lang.py     : 非中文泄漏(中文段里的非白名单英文 token, LLM 复判该保留/改中文)。
  - term.py     : 术语一致/代称混乱/易过时引用(确定性从真源生成命中, 长尾交 LLM)。
  - compress.py : 惜字如金(确定性展开已知缩写 + textstat 筛可疑段, LLM 只建议)。
"""
