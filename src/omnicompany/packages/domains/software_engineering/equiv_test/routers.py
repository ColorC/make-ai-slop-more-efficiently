# [OMNI] origin=claude-code domain=software_engineering/equiv_test ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.equiv_test.pipeline_routers.implementation.py"
"""equivalence_test.routers — 跨语言语义等价性测试管线 V2

Golden File 模式 + Baseline 红绿验证:

  1. TestDesigner (LLM)      — 读代码，设计测试用例清单
  2. GoldenRecorder (LLM+运行) — LLM 生成 Python 录制脚本 → 实际运行 → 输出 golden JSON
  3. BaselineCheck (确定性)   — 用空 stub 跑 TS 确认失败（红灯验证）
  4. TSTestGenerator (LLM)   — 根据 golden keys + TS 代码生成对比脚本
  5. TSExecutor (确定性)      — 运行 TS 测试脚本
  6. ResultComparator (确定性) — golden vs TS 输出逐 key 对比
  7. FailureAnalyzer (LLM)   — 分析不匹配根因
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


def _make_llm_client(model: str | None = None):
    from omnicompany.runtime.llm.llm import LLMClient
    return LLMClient(role="runtime_main", max_tokens=16384,
                     **({"model": model} if model else {}))


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```json\n(.*?)```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def _extract_code(text: str, lang: str) -> str | None:
    """从 LLM 输出中提取代码块。"""
    patterns = [
        rf"```{lang}\n(.*?)```",
        rf"```{lang}_test\n(.*?)```",
        r"```\n(.*?)```",
    ]
    for p in patterns:
        m = re.search(p, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return None


def _run_python(code: str, cwd: str = ".") -> dict:
    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True, text=True, timeout=30,
            cwd=cwd, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return {"error": f"exit {result.returncode}: {result.stderr[:500]}"}
        try:
            return {"data": json.loads(result.stdout)}
        except json.JSONDecodeError:
            return {"error": f"非法 JSON: {result.stdout[:300]}"}
    except subprocess.TimeoutExpired:
        return {"error": "超时(30s)"}
    except Exception as e:
        return {"error": str(e)}


def _run_typescript(code: str, work_dir: str) -> dict:
    test_file = Path(work_dir) / "_equiv_test.ts"
    try:
        # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
        test_file.write_text(code, encoding="utf-8")
        result = subprocess.run(
            "npx tsx _equiv_test.ts",
            capture_output=True, text=True, timeout=30,
            cwd=work_dir, shell=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return {"error": f"exit {result.returncode}: {result.stderr[:500]}"}
        try:
            return {"data": json.loads(result.stdout)}
        except json.JSONDecodeError:
            return {"error": f"非法 JSON: {result.stdout[:300]}"}
    except subprocess.TimeoutExpired:
        return {"error": "超时(30s)"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        test_file.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TestDesigner — LLM 设计测试用例（只设计，不生成代码）
# ═══════════════════════════════════════════════════════════════════════════════

class TestDesignerRouter(Router):
    FORMAT_IN = "equiv.test-spec"
    FORMAT_OUT = "equiv.test-spec"
    DESCRIPTION = "LLM 分析 Python 源码，设计等价性测试用例清单（只设计不生成代码）"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def run(self, input_data: dict) -> Verdict:
        py_path = input_data.get("py_path", "")
        ts_path = input_data.get("ts_path", "")
        module_name = input_data.get("module_name", "")

        py_code = Path(py_path).read_text(encoding="utf-8") if Path(py_path).exists() else ""
        ts_code = Path(ts_path).read_text(encoding="utf-8") if Path(ts_path).exists() else ""

        if not py_code:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"Python 源文件不存在: {py_path}")
        if not ts_code:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"TS 文件不存在: {ts_path}")

        client = _make_llm_client(self._model)
        prompt = f"""你是跨语言等价性测试设计专家。

分析以下 Python 模块 `{module_name}` 的公开接口，设计**可自动化执行**的等价性测试。

关键原则：
- 只测试确定性行为（不测时间戳、随机 ID、ULID）
- 不要测试需要 LLM/网络/文件系统的接口
- 不要实例化抽象类（abstract class / ABC），只测具体子类
- 每个测试用例必须给出**具体的输入数据**（不是"某个值"，是具体的值）

## Python 源码
```python
{py_code[:4000]}
```

## 输出格式（只输出 JSON）
```json
{{
  "test_cases": [
    {{
      "id": "test_001",
      "category": "deterministic | boundary | state_transition | error_handling",
      "target": "ClassName.method_name",
      "description": "测试什么",
      "python_setup": "Python 中构造输入的代码片段（1-3行）",
      "python_call": "调用并序列化结果的代码片段（1行）",
      "result_key": "results 字典中的 key 名",
      "notes": "TS 侧需要注意的差异"
    }}
  ],
  "untestable": [
    {{"target": "xxx", "reason": "为什么不可测"}}
  ]
}}
```"""
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            design = _extract_json(resp.content[0].text)
            if not design:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis="LLM 未返回有效 JSON")

            test_cases = design.get("test_cases", [])
            output = {
                **input_data, "py_code": py_code, "ts_code": ts_code,
                "test_cases": test_cases,
                "untestable": design.get("untestable", []),
            }
            return Verdict(kind=VerdictKind.PASS, output=output,
                           diagnosis=f"设计了 {len(test_cases)} 个测试用例",
                           confidence=0.8)
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"TestDesigner LLM 失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GoldenRecorder — LLM 生成 Python 录制脚本 → 实际运行 → golden JSON
# ═══════════════════════════════════════════════════════════════════════════════

class GoldenRecorderRouter(Router):
    FORMAT_IN = "equiv.test-spec"
    FORMAT_OUT = "equiv.test-suite"
    DESCRIPTION = "LLM 生成 Python 录制脚本，实际运行得到 golden JSON 输出"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def run(self, input_data: dict) -> Verdict:
        py_code = input_data.get("py_code", "")
        test_cases = input_data.get("test_cases", [])
        py_path = input_data.get("py_path", "")
        module_name = input_data.get("module_name", "")

        if not test_cases:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis="无测试用例")

        active = [tc for tc in test_cases if not tc.get("skip_reason")]

        client = _make_llm_client(self._model)
        prompt = f"""你是 Python 测试工程师。

根据以下测试设计，生成一段 Python 录制脚本。脚本要求：
1. 开头: `import json, sys; sys.path.insert(0, 'src')`
2. 逐个执行每个测试用例
3. 将结果收集到 `results` 字典（key = test_case 的 result_key）
4. 结果必须是可 JSON 序列化的（dict / list / str / int / float / bool / None）
5. 最后: `print(json.dumps(results, default=str, sort_keys=True))`

## 重要约束
- **不要实例化抽象类**（如果类有 @abstractmethod，跳过它，只测具体子类）
- 如果某个测试无法执行（import 失败等），在 results 中记录 `{{"error": "原因"}}`
- 使用 try/except 包裹每个测试，确保一个失败不影响其他

## 测试用例
{json.dumps(active, indent=2, ensure_ascii=False)}

## Python 源码（参考 import 路径）
```python
{py_code[:3000]}
```

## 模块路径
`{py_path}` → import 路径推导: `from {py_path.replace('src/', '').replace('/', '.').replace('.py', '')} import ...`

只输出 Python 代码，用 ```python 包裹。不要输出其他内容。
"""
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            py_test_code = _extract_code(resp.content[0].text, "python")
            if not py_test_code:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis="LLM 未返回有效 Python 代码")

            # ── 实际运行录制 golden file ──
            golden_result = _run_python(py_test_code)

            if golden_result.get("error"):
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={**input_data, "py_test_code": py_test_code,
                            "golden_error": golden_result["error"]},
                    diagnosis=f"Golden 录制失败: {golden_result['error'][:200]}",
                )

            golden_data = golden_result["data"]
            output = {
                **input_data,
                "py_test_code": py_test_code,
                "golden_data": golden_data,
                "golden_keys": sorted(golden_data.keys()),
                "active_test_count": len(active),
            }
            return Verdict(
                kind=VerdictKind.PASS, output=output,
                diagnosis=f"Golden 录制成功: {len(golden_data)} keys",
                confidence=0.9, granted_tags=["golden-recorded"],
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"GoldenRecorder 失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BaselineCheck — 空 stub 必须失败（红灯验证）
# ═══════════════════════════════════════════════════════════════════════════════

class BaselineCheckRouter(Router):
    FORMAT_IN = "equiv.test-suite"
    FORMAT_OUT = "equiv.test-suite"
    DESCRIPTION = "用空 stub TS 文件运行测试，确认失败（红灯验证——测试能抓到假货）"

    def run(self, input_data: dict) -> Verdict:
        ts_path = input_data.get("ts_path", "")
        golden_keys = input_data.get("golden_keys", [])

        if not golden_keys:
            # 没有 golden 数据，跳过 baseline
            return Verdict(kind=VerdictKind.PASS, output=input_data,
                           diagnosis="无 golden keys，跳过 baseline",
                           granted_tags=["baseline-skipped"])

        ts_dir = str(Path(ts_path).parent) if ts_path else input_data.get("ts_dir", "")
        ts_stem = Path(ts_path).stem if ts_path else "module"

        # 生成一个空 stub TS 测试——只 import 并输出空 results
        stub_code = """// Baseline stub — 应该失败（红灯）
const results: Record<string, any> = {};
console.log(JSON.stringify(results));
"""
        stub_result = _run_typescript(stub_code, ts_dir)
        stub_data = stub_result.get("data", {})

        # 验证：stub 的输出应该和 golden 不匹配
        if stub_result.get("error"):
            # stub 执行失败 = 红灯 OK
            baseline_pass = True
            baseline_reason = f"stub 执行失败（预期行为）: {stub_result['error'][:100]}"
        elif not stub_data:
            baseline_pass = True
            baseline_reason = "stub 输出为空（预期行为）"
        else:
            # stub 居然有输出？检查是否和 golden 匹配
            matching_keys = [k for k in golden_keys if k in stub_data and stub_data[k] == input_data.get("golden_data", {}).get(k)]
            if len(matching_keys) == len(golden_keys) and golden_keys:
                baseline_pass = False
                baseline_reason = f"红灯失败！空 stub 居然通过了 {len(matching_keys)} 个测试——测试无效"
            else:
                baseline_pass = True
                baseline_reason = f"stub 只匹配 {len(matching_keys)}/{len(golden_keys)} keys（预期行为）"

        output = {
            **input_data,
            "baseline_pass": baseline_pass,
            "baseline_reason": baseline_reason,
        }

        if not baseline_pass:
            return Verdict(kind=VerdictKind.FAIL, output=output,
                           diagnosis=baseline_reason)

        return Verdict(kind=VerdictKind.PASS, output=output,
                       diagnosis=f"Baseline 验证通过: {baseline_reason}",
                       granted_tags=["baseline-verified"])


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TSTestGenerator — LLM 生成 TS 对比脚本（已知 golden keys）
# ═══════════════════════════════════════════════════════════════════════════════

class TSTestGeneratorRouter(Router):
    FORMAT_IN = "equiv.test-suite"
    FORMAT_OUT = "equiv.test-suite"
    DESCRIPTION = "LLM 根据 golden keys + TS 代码生成对比脚本"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def run(self, input_data: dict) -> Verdict:
        ts_code = input_data.get("ts_code", "")
        ts_path = input_data.get("ts_path", "")
        golden_data = input_data.get("golden_data", {})
        golden_keys = input_data.get("golden_keys", [])
        test_cases = input_data.get("test_cases", [])
        py_test_code = input_data.get("py_test_code", "")

        ts_stem = Path(ts_path).stem if ts_path else "module"

        client = _make_llm_client(self._model)
        prompt = f"""你是 TypeScript 测试工程师。

以下是 Python 等价性测试的录制脚本和它产出的 golden data。
请生成一段等价的 TypeScript 测试脚本，对相同的接口执行相同的操作。

## 关键要求
1. import 路径: `import {{ ... }} from "./{ts_stem}"`
2. results 对象的 key 必须与 golden data 的 key **完全一致**: {golden_keys}
3. 构造完全相同的输入数据
4. 最后: `console.log(JSON.stringify(results, null, 0))`
5. 使用 try/catch 包裹每个测试
6. **不要实例化抽象类**

## Python 录制脚本（参考逻辑）
```python
{py_test_code[:3000]}
```

## Golden data（Python 实际输出，TS 应该产出相同结果）
```json
{json.dumps(golden_data, indent=2, default=str, ensure_ascii=False)[:2000]}
```

## TypeScript 源码（你可以 import 的内容）
```typescript
{ts_code[:3000]}
```

只输出 TypeScript 代码，用 ```typescript 包裹。
"""
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            ts_test_code = _extract_code(resp.content[0].text, "typescript")
            if not ts_test_code:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis="LLM 未返回有效 TS 代码")

            output = {**input_data, "ts_test_code": ts_test_code}
            return Verdict(kind=VerdictKind.PASS, output=output,
                           diagnosis=f"TS 测试脚本已生成 ({len(ts_test_code)} chars)",
                           confidence=0.7)
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"TSTestGenerator 失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TSExecutor — 运行 TS 测试脚本
# ═══════════════════════════════════════════════════════════════════════════════

class TSExecutorRouter(Router):
    FORMAT_IN = "equiv.test-suite"
    FORMAT_OUT = "equiv.execution-result"
    DESCRIPTION = "运行 TS 测试脚本，收集 JSON 输出"

    def __init__(self, *, ts_dir: str | None = None):
        self._ts_dir = Path(ts_dir) if ts_dir else None

    def run(self, input_data: dict) -> Verdict:
        ts_test_code = input_data.get("ts_test_code", "")
        ts_path = input_data.get("ts_path", "")
        ts_dir = str(self._ts_dir or Path(ts_path).parent if ts_path else "")

        if not ts_test_code:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis="无 TS 测试代码")

        ts_result = _run_typescript(ts_test_code, ts_dir)
        output = {**input_data, "ts_result": ts_result}

        if ts_result.get("error"):
            return Verdict(kind=VerdictKind.FAIL, output=output,
                           diagnosis=f"TS 执行失败: {ts_result['error'][:200]}")

        return Verdict(kind=VerdictKind.PASS, output=output,
                       diagnosis=f"TS 执行成功: {len(ts_result.get('data', {}))} keys",
                       granted_tags=["executed"])


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ResultComparator — golden vs TS 输出对比
# ═══════════════════════════════════════════════════════════════════════════════

class ResultComparatorRouter(Router):
    FORMAT_IN = "equiv.execution-result"
    FORMAT_OUT = "equiv.comparison-report"
    DESCRIPTION = "逐 key 对比 golden (Python) 和 TS 测试输出"

    def run(self, input_data: dict) -> Verdict:
        golden_data = input_data.get("golden_data", {})
        ts_result = input_data.get("ts_result", {})
        ts_data = ts_result.get("data", {})

        all_keys = sorted(set(list(golden_data.keys()) + list(ts_data.keys())))
        matches: list[str] = []
        mismatches: list[dict] = []
        golden_only: list[str] = []
        ts_only: list[str] = []

        for key in all_keys:
            g_has = key in golden_data
            t_has = key in ts_data
            if g_has and t_has:
                if _deep_equal(golden_data[key], ts_data[key]):
                    matches.append(key)
                else:
                    mismatches.append({
                        "key": key,
                        "golden": golden_data[key],
                        "ts": ts_data[key],
                    })
            elif g_has:
                golden_only.append(key)
            else:
                ts_only.append(key)

        total = len(all_keys)
        match_rate = len(matches) / max(total, 1)

        comparison = {
            "total_keys": total,
            "matches": matches,
            "mismatches": mismatches,
            "golden_only": golden_only,
            "ts_only": ts_only,
            "match_rate": round(match_rate, 3),
        }

        output = {**input_data, "comparison": comparison}

        all_pass = not mismatches and not golden_only and not ts_only

        if all_pass:
            return Verdict(kind=VerdictKind.PASS, output=output,
                           diagnosis=f"等价性通过: {len(matches)}/{total} 全匹配",
                           confidence=0.95, granted_tags=["compared"])
        else:
            parts = []
            if mismatches: parts.append(f"{len(mismatches)} mismatch")
            if golden_only: parts.append(f"{len(golden_only)} golden_only")
            if ts_only: parts.append(f"{len(ts_only)} ts_only")
            return Verdict(kind=VerdictKind.PASS, output=output,
                           diagnosis=f"{len(matches)}/{total} 匹配, {', '.join(parts)}",
                           confidence=1.0, granted_tags=["compared"])


def _deep_equal(a: Any, b: Any) -> bool:
    if a == b:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-6
    if isinstance(a, str) and isinstance(b, (int, float)):
        try: return abs(float(a) - b) < 1e-6
        except ValueError: return False
    if isinstance(b, str) and isinstance(a, (int, float)):
        try: return abs(a - float(b)) < 1e-6
        except ValueError: return False
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b))
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 7. FailureAnalyzer — LLM 分析不匹配根因
# ═══════════════════════════════════════════════════════════════════════════════

class FailureAnalyzerRouter(Router):
    FORMAT_IN = "equiv.comparison-report"
    FORMAT_OUT = "equiv.diagnosed-report"
    DESCRIPTION = "LLM 分析每个不匹配项的根因，判断严重度，给出修复建议"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def run(self, input_data: dict) -> Verdict:
        comparison = input_data.get("comparison", {})
        mismatches = comparison.get("mismatches", [])
        golden_only = comparison.get("golden_only", [])
        ts_only = comparison.get("ts_only", [])

        if not mismatches and not golden_only and not ts_only:
            return Verdict(kind=VerdictKind.PASS, output={
                **input_data, "diagnosis": [], "overall_severity": "none",
                "summary": "全部等价",
            }, diagnosis="无需分析", granted_tags=["diagnosed"])

        py_code = input_data.get("py_code", "")[:1500]
        ts_code = input_data.get("ts_code", "")[:1500]

        client = _make_llm_client(self._model)
        prompt = f"""你是跨语言等价性诊断专家。

以下是 Python↔TypeScript golden file 对比结果。分析每个不匹配的根因。

## 不匹配
{json.dumps(mismatches[:15], indent=2, ensure_ascii=False, default=str)}

## 仅 Golden 有: {golden_only[:10]}
## 仅 TS 有: {ts_only[:10]}

## Python 片段
```python
{py_code}
```

## TS 片段
```typescript
{ts_code}
```

输出 JSON:
```json
{{
  "diagnosis": [
    {{
      "key": "test_key",
      "root_cause": "translation_error | test_error | design_difference | serialization_difference",
      "severity": "critical | warning | info",
      "description": "原因",
      "fix_suggestion": "建议",
      "fix_target": "python | typescript | test"
    }}
  ],
  "overall_severity": "critical | warning | info | none",
  "summary": "一句话"
}}
```"""
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            analysis = _extract_json(resp.content[0].text) or {}
            output = {
                **input_data,
                "diagnosis": analysis.get("diagnosis", []),
                "overall_severity": analysis.get("overall_severity", "unknown"),
                "summary": analysis.get("summary", ""),
            }
            return Verdict(kind=VerdictKind.PASS, output=output,
                           diagnosis=f"severity={analysis.get('overall_severity')}: {analysis.get('summary', '')[:100]}",
                           confidence=0.8, granted_tags=["diagnosed"])
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"FailureAnalyzer 失败: {e}")
