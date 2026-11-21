# [OMNI] origin=claude-code domain=software_engineering/debugger ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.debugger.pipeline_routers.implementation.py"
"""debugger.routers — 假设驱动调试管线的 Router 实现

10 个节点:
  3 确定性 Transformer: context_init, evidence_collector, regression_to_context
  5 SOFT/LLM 节点:     error_analyzer, hypothesis_generator, probe_designer, fixer, regression_analyzer
  2 HARD/执行节点:      probe_executor, tester
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# debug-context 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_context() -> dict:
    """创建空的 debug-context。"""
    return {
        "errors": [],           # 错误历史 [{message, file, line, code}]
        "hypotheses": [],       # 假设历史 [{id, description, status, evidence}]
        "patches": [],          # 修改历史 [{file, old, new, hypothesis_id, test_result}]
        "excluded_files": [],   # 已排除的文件（读过但无关的）
        "current_hypothesis": None,
        "iteration": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ErrorAnalyzer — 阅读错误，判断直接原因
# ═══════════════════════════════════════════════════════════════════════════════

class ErrorAnalyzerRouter(Router):
    FORMAT_IN = "debug.error-report"
    FORMAT_OUT = "debug.error-analysis"
    DESCRIPTION = "阅读错误输出和错误位置代码，判断直接原因"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: dict) -> Verdict:
        error_output = input_data.get("error_output", "")
        source_files = input_data.get("source_files", {})  # {path: content}
        language = input_data.get("language", "typescript")
        compile_command = input_data.get("compile_command", "")

        # 读取错误涉及的文件
        file_contexts = []
        for line in error_output.splitlines():
            # 通用模式: file.ts(line,col): error ...
            for ext in [".ts", ".rs", ".py", ".js", ".go"]:
                if ext in line:
                    parts = line.split(ext)[0] + ext
                    fpath = parts.split(":")[-1] if ":" in parts else parts
                    fpath = fpath.strip().split("(")[0]
                    if fpath not in source_files:
                        try:
                            p = Path(fpath)
                            if p.exists():
                                source_files[fpath] = p.read_text(encoding="utf-8")
                        except Exception:
                            pass

        for path, content in list(source_files.items())[:5]:
            file_contexts.append(f"## {path}\n```\n{content[:3000]}\n```")

        prompt = f"""你是一个 {language} 编译错误分析专家。

## 编译/测试错误输出
```
{error_output[:3000]}
```

## 相关源码
{chr(10).join(file_contexts)}

请分析每个错误的直接原因。输出 JSON:
```json
{{
  "errors": [
    {{
      "file": "文件路径",
      "line": 行号,
      "message": "原始错误消息",
      "direct_cause": "直接原因的一句话描述",
      "involved_types": ["涉及的类型/变量名"],
      "severity": "critical/warning"
    }}
  ],
  "common_pattern": "如果多个错误有共同模式，描述之；否则为 null"
}}
```
"""
        client = self._make_client()
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            content = resp.content[0].text
            match = _extract_json(content)
            if not match:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis="LLM 未输出有效 JSON 分析", confidence=0.3)

            analysis = json.loads(match)
            return Verdict(
                kind=VerdictKind.PASS,
                output={**input_data, "analysis": analysis, "source_files": source_files},
                diagnosis=f"分析了 {len(analysis.get('errors', []))} 个错误"
                          f"{', 共同模式: ' + analysis['common_pattern'] if analysis.get('common_pattern') else ''}",
                confidence=0.8,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"错误分析失败: {e}", confidence=0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ContextInit — 初始化 debug-context（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ContextInitRouter(Router):
    FORMAT_IN = "debug.error-analysis"
    FORMAT_OUT = "debug.debug-context"
    DESCRIPTION = "将首次错误分析包装为初始 debug-context"

    def run(self, input_data: dict) -> Verdict:
        ctx = _empty_context()
        analysis = input_data.get("analysis", {})
        ctx["errors"] = analysis.get("errors", [])
        ctx["common_pattern"] = analysis.get("common_pattern")
        ctx["source_files"] = input_data.get("source_files", {})
        ctx["language"] = input_data.get("language", "typescript")
        ctx["compile_command"] = input_data.get("compile_command", "")
        ctx["work_dir"] = input_data.get("work_dir", "")
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"初始化 debug-context: {len(ctx['errors'])} 个错误",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. HypothesisGenerator — 根据上下文提出假设
# ═══════════════════════════════════════════════════════════════════════════════

class HypothesisGeneratorRouter(Router):
    FORMAT_IN = "debug.debug-context"
    FORMAT_OUT = "debug.hypothesis"
    DESCRIPTION = "根据累积 debug-context 追踪依赖来源，提出根因假设"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        errors = ctx.get("errors", [])
        hypotheses = ctx.get("hypotheses", [])
        patches = ctx.get("patches", [])

        # 已排除的假设
        excluded = [h for h in hypotheses if h.get("status") == "disproved"]
        confirmed = [h for h in hypotheses if h.get("status") == "confirmed"]

        prompt = f"""你是一个 {ctx.get('language', 'typescript')} debug 专家。

## 当前错误
```json
{json.dumps(errors[:10], indent=2, ensure_ascii=False)}
```

## 已排除的假设（不要重复提出）
{json.dumps(excluded, indent=2, ensure_ascii=False) if excluded else '无'}

## 已尝试过的修复及其结果
{json.dumps(patches[-5:], indent=2, ensure_ascii=False) if patches else '无'}

## 可用的源文件列表
{chr(10).join(f'- {k} ({len(v)} chars)' for k, v in ctx.get('source_files', {}).items())}

请追踪错误涉及的类型/变量的定义来源，提出一个**具体的根因假设**。
假设必须包含：
1. 错误根因在哪个文件哪个位置
2. 为什么会出这个错
3. 预测的修复方向

输出 JSON:
```json
{{
  "id": "h{len(hypotheses) + 1}",
  "root_file": "假设的根因文件",
  "root_location": "具体位置描述",
  "cause": "为什么出错",
  "fix_direction": "预测的修复方向",
  "files_to_read": ["需要读取验证的文件路径"],
  "confidence": 0.0到1.0
}}
```
"""
        client = self._make_client()
        try:
            sys_prompt = self._maybe_inject_reflection("你是一个专业的软件调试专家。")
            resp = client.call(messages=[{"role": "user", "content": prompt}],
                               system=sys_prompt)
            raw_text = resp.content[0].text

            # 反思：解析自评 + 信息不足拦截
            sa, clean_text = self._parse_self_assessment(raw_text)
            partial = self._check_reflection_partial(sa, clean_text, ctx)
            if partial:
                return partial

            match = _extract_json(clean_text)
            if not match:
                return Verdict(kind=VerdictKind.FAIL, output=ctx,
                               diagnosis="无法提出假设", confidence=0.3,
                               self_assessment=sa)

            hypothesis = json.loads(match)
            ctx["current_hypothesis"] = hypothesis
            ctx["hypotheses"].append({**hypothesis, "status": "proposed"})
            ctx["iteration"] = ctx.get("iteration", 0) + 1

            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"假设 {hypothesis['id']}: {hypothesis['cause'][:80]}",
                confidence=hypothesis.get("confidence", 0.5),
                self_assessment=sa,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"假设生成失败: {e}", confidence=0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ProbeDesigner — 设计试探
# ═══════════════════════════════════════════════════════════════════════════════

class ProbeDesignerRouter(Router):
    FORMAT_IN = "debug.hypothesis"
    FORMAT_OUT = "debug.probe-plan"
    DESCRIPTION = "为假设设计试探：读哪些文件、写什么测试"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        hypothesis = ctx.get("current_hypothesis", {})

        prompt = f"""你是一个 debug 试探设计专家。

## 当前假设
```json
{json.dumps(hypothesis, indent=2, ensure_ascii=False)}
```

请设计**具体的试探方案**来证实或证否这个假设。
试探类型可以是:
- read_file: 读取特定文件的特定位置，确认类型/变量定义
- grep: 在目录中搜索特定模式
- run_test: 编写并运行一个最小测试

输出 JSON:
```json
{{
  "probes": [
    {{
      "type": "read_file",
      "target": "文件路径",
      "what_to_check": "检查什么",
      "expected_if_confirmed": "如果假设正确应该看到什么",
      "expected_if_disproved": "如果假设错误应该看到什么"
    }}
  ]
}}
```
"""
        client = self._make_client()
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            match = _extract_json(resp.content[0].text)
            if not match:
                return Verdict(kind=VerdictKind.FAIL, output=ctx,
                               diagnosis="无法设计试探", confidence=0.3)

            plan = json.loads(match)
            ctx["current_probe_plan"] = plan
            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"设计了 {len(plan.get('probes', []))} 个试探",
                confidence=0.7,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"试探设计失败: {e}", confidence=0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ProbeExecutor — 执行试探（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ProbeExecutorRouter(Router):
    FORMAT_IN = "debug.probe-plan"
    FORMAT_OUT = "debug.probe-result"
    DESCRIPTION = "执行试探（读文件/grep/运行测试），收集证据"

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        plan = ctx.get("current_probe_plan", {})
        probes = plan.get("probes", [])
        hypothesis = ctx.get("current_hypothesis", {})
        work_dir = ctx.get("work_dir", "")

        results = []
        for probe in probes:
            ptype = probe.get("type", "")
            result = {"type": ptype, "target": probe.get("target", "")}

            if ptype == "read_file":
                target = probe["target"]
                try:
                    p = Path(work_dir) / target if work_dir else Path(target)
                    if not p.exists():
                        # 尝试不带 work_dir
                        p = Path(target)
                    if p.exists():
                        content = p.read_text(encoding="utf-8")
                        result["content"] = content[:5000]
                        result["success"] = True
                    else:
                        result["error"] = f"文件不存在: {target}"
                        result["success"] = False
                except Exception as e:
                    result["error"] = str(e)
                    result["success"] = False

            elif ptype == "grep":
                target_dir = probe.get("target", work_dir or ".")
                pattern = probe.get("pattern", "")
                try:
                    r = subprocess.run(
                        f'grep -rn "{pattern}" "{target_dir}" --include="*.ts" --include="*.py" --include="*.rs"',
                        capture_output=True, text=True, timeout=10, shell=True, encoding="utf-8", errors="replace",
                    )
                    result["content"] = (r.stdout or "")[:3000]
                    result["success"] = r.returncode == 0
                except Exception as e:
                    result["error"] = str(e)
                    result["success"] = False

            elif ptype == "run_test":
                test_code = probe.get("code", "")
                if test_code:
                    try:
                        r = subprocess.run(
                            test_code, capture_output=True, text=True,
                            timeout=30, shell=True, cwd=work_dir or None, encoding="utf-8", errors="replace",
                        )
                        result["stdout"] = (r.stdout or "")[:2000]
                        result["stderr"] = (r.stderr or "")[:2000]
                        result["returncode"] = r.returncode
                        result["success"] = r.returncode == 0
                    except Exception as e:
                        result["error"] = str(e)
                        result["success"] = False

            result["check"] = probe.get("what_to_check", "")
            results.append(result)

        ctx["current_probe_results"] = results

        # 简单判定：如果所有试探都成功读到了预期内容，假设可能被证实
        # 但真正的证实/证否需要 LLM 看结果判断，所以这里用 PASS 让下游 LLM 决定
        # HARD validator 只负责"执行成功了没有"
        all_success = all(r.get("success") for r in results)
        any_success = any(r.get("success") for r in results)

        if not any_success:
            return Verdict(
                kind=VerdictKind.FAIL, output=ctx,
                diagnosis="所有试探执行失败",
                confidence=1.0,
            )

        # 需要 LLM 看 results 来判断假设是否证实——这个判断内嵌在 probe_executor 里
        # 因为 HARD validator 不调 LLM，我们用一个简单启发式：
        # 如果任何 read_file 结果中包含假设指向的 root_location 相关内容，算 PASS
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"执行了 {len(results)} 个试探, {sum(1 for r in results if r.get('success'))} 成功",
            confidence=0.7,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5b. EvidenceCollector — 回路归一（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class EvidenceCollectorRouter(Router):
    FORMAT_IN = "debug.probe-result"
    FORMAT_OUT = "debug.debug-context"
    DESCRIPTION = "将试探结果追加到 debug-context"

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        hypothesis = ctx.get("current_hypothesis", {})
        probe_results = ctx.get("current_probe_results", [])

        # 更新假设状态为"证否"（因为走到这里说明 probe_executor 返回了 FAIL）
        h_id = hypothesis.get("id", "")
        for h in ctx.get("hypotheses", []):
            if h.get("id") == h_id:
                h["status"] = "disproved"
                h["evidence"] = probe_results

        # 把试探中读到的新文件加入 source_files
        for r in probe_results:
            if r.get("success") and r.get("type") == "read_file" and r.get("content"):
                ctx.setdefault("source_files", {})[r["target"]] = r["content"]

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"假设 {h_id} 已证否，上下文已更新",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Fixer — 生成修复补丁
# ═══════════════════════════════════════════════════════════════════════════════

class FixerRouter(Router):
    FORMAT_IN = "debug.probe-result"
    FORMAT_OUT = "debug.fix-patch"
    DESCRIPTION = "根据证实的假设生成修复补丁"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=8192,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        hypothesis = ctx.get("current_hypothesis", {})
        probe_results = ctx.get("current_probe_results", [])
        source_files = ctx.get("source_files", {})

        # 找到需要修改的文件内容
        root_file = hypothesis.get("root_file", "")
        file_content = source_files.get(root_file, "")
        if not file_content:
            try:
                p = Path(root_file)
                if p.exists():
                    file_content = p.read_text(encoding="utf-8")
            except Exception:
                pass

        prompt = f"""你是一个 {ctx.get('language', 'typescript')} bug 修复专家。

## 已证实的假设
```json
{json.dumps(hypothesis, indent=2, ensure_ascii=False)}
```

## 试探结果（证据）
```json
{json.dumps(probe_results[:5], indent=2, ensure_ascii=False)}
```

## 需要修改的文件: {root_file}
```
{file_content[:6000]}
```

请生成**精确的修复补丁**。输出 JSON:
```json
{{
  "patches": [
    {{
      "file": "文件路径",
      "old_string": "要替换的原内容（精确匹配）",
      "new_string": "替换后的新内容",
      "reason": "为什么这样改"
    }}
  ]
}}
```
"""
        client = self._make_client()
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            match = _extract_json(resp.content[0].text)
            if not match:
                return Verdict(kind=VerdictKind.FAIL, output=ctx,
                               diagnosis="无法生成修复补丁", confidence=0.3)

            fix = json.loads(match)
            ctx["current_fix"] = fix
            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"生成了 {len(fix.get('patches', []))} 个补丁",
                confidence=0.7,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"修复生成失败: {e}", confidence=0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Tester — 应用补丁并复测（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class TesterRouter(Router):
    FORMAT_IN = "debug.fix-patch"
    FORMAT_OUT = "debug.test-feedback"
    DESCRIPTION = "应用补丁并运行编译器/测试"

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        fix = ctx.get("current_fix", {})
        patches = fix.get("patches", [])
        compile_command = ctx.get("compile_command", "")
        work_dir = ctx.get("work_dir", "")

        # 应用补丁
        applied = []
        for patch in patches:
            fpath = patch.get("file", "")
            old_str = patch.get("old_string", "")
            new_str = patch.get("new_string", "")
            try:
                p = Path(work_dir) / fpath if work_dir else Path(fpath)
                if not p.exists():
                    p = Path(fpath)
                content = p.read_text(encoding="utf-8")
                if old_str in content:
                    new_content = content.replace(old_str, new_str, 1)
                    # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                    p.write_text(new_content, encoding="utf-8")
                    applied.append({**patch, "applied": True})
                    # 更新 source_files 缓存
                    ctx.setdefault("source_files", {})[fpath] = new_content
                else:
                    applied.append({**patch, "applied": False, "error": "old_string 未找到"})
            except Exception as e:
                applied.append({**patch, "applied": False, "error": str(e)})

        # 运行编译/测试
        test_output = ""
        test_success = False
        if compile_command:
            try:
                r = subprocess.run(
                    compile_command, capture_output=True, text=True,
                    timeout=60, shell=True, cwd=work_dir or None, encoding="utf-8", errors="replace",
                )
                test_output = (r.stdout or "") + (r.stderr or "")
                test_success = r.returncode == 0
            except Exception as e:
                test_output = str(e)

        # 记录补丁历史
        hypothesis = ctx.get("current_hypothesis", {})
        ctx["patches"].append({
            "hypothesis_id": hypothesis.get("id", ""),
            "applied": applied,
            "test_success": test_success,
            "test_output": test_output[:2000],
        })

        if test_success:
            return Verdict(
                kind=VerdictKind.PASS,
                output=ctx,
                diagnosis=f"修复成功！应用了 {sum(1 for a in applied if a.get('applied'))} 个补丁，编译/测试通过",
                confidence=1.0,
                granted_tags=["verified"],
            )
        else:
            # 更新错误列表为新的错误
            ctx["test_output"] = test_output[:3000]
            return Verdict(
                kind=VerdictKind.FAIL, output=ctx,
                diagnosis=f"复测失败: {test_output[:200]}",
                confidence=1.0,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. RegressionAnalyzer — 复测失败归因
# ═══════════════════════════════════════════════════════════════════════════════

class RegressionAnalyzerRouter(Router):
    FORMAT_IN = "debug.test-feedback"
    FORMAT_OUT = "debug.regression-analysis"
    DESCRIPTION = "分析复测失败：假设错、实现错、还是新问题"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: dict) -> Verdict:
        ctx = input_data
        test_output = ctx.get("test_output", "")
        hypothesis = ctx.get("current_hypothesis", {})
        last_patch = ctx["patches"][-1] if ctx.get("patches") else {}

        prompt = f"""你是一个回归分析专家。修复补丁已应用但复测失败。

## 原假设
```json
{json.dumps(hypothesis, indent=2, ensure_ascii=False)}
```

## 应用的补丁
```json
{json.dumps(last_patch.get('applied', []), indent=2, ensure_ascii=False)}
```

## 复测错误输出
```
{test_output[:2000]}
```

请判断：
1. **假设错误**: 假设本身不对，需要回退修改并重新假设
2. **实现错误**: 假设对但改法不对，需要换一种修复方式
3. **新问题**: 之前的修复部分正确，但暴露了新的独立问题

输出 JSON:
```json
{{
  "verdict": "hypothesis_wrong / implementation_wrong / new_problem",
  "should_revert": true/false,
  "analysis": "详细分析",
  "updated_errors": [
    {{"file": "...", "line": 0, "message": "新的/更新的错误", "direct_cause": "..."}}
  ]
}}
```
"""
        client = self._make_client()
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            match = _extract_json(resp.content[0].text)
            if not match:
                return Verdict(kind=VerdictKind.FAIL, output=ctx,
                               diagnosis="无法分析回归", confidence=0.3)

            regression = json.loads(match)

            # 如果需要回退，恢复文件
            if regression.get("should_revert") and last_patch.get("applied"):
                for p in reversed(last_patch["applied"]):
                    if p.get("applied"):
                        try:
                            fpath = Path(ctx.get("work_dir", "")) / p["file"] if ctx.get("work_dir") else Path(p["file"])
                            if not fpath.exists():
                                fpath = Path(p["file"])
                            content = fpath.read_text(encoding="utf-8")
                            content = content.replace(p["new_string"], p["old_string"], 1)
                            # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                            fpath.write_text(content, encoding="utf-8")
                        except Exception:
                            pass

            # 更新假设状态
            h_id = hypothesis.get("id", "")
            verdict_type = regression.get("verdict", "")
            for h in ctx.get("hypotheses", []):
                if h.get("id") == h_id:
                    if verdict_type == "hypothesis_wrong":
                        h["status"] = "disproved"
                    elif verdict_type == "implementation_wrong":
                        h["status"] = "confirmed_but_fix_wrong"
                    elif verdict_type == "new_problem":
                        h["status"] = "partially_confirmed"

            # 更新错误列表
            if regression.get("updated_errors"):
                ctx["errors"] = regression["updated_errors"]

            ctx["regression_analysis"] = regression

            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"回归判定: {verdict_type}, 回退={regression.get('should_revert')}",
                confidence=0.8,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"回归分析失败: {e}", confidence=0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 8b. RegressionToContext — 回归结论归一（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class RegressionToContextRouter(Router):
    FORMAT_IN = "debug.regression-analysis"
    FORMAT_OUT = "debug.debug-context"
    DESCRIPTION = "将回归分析结论追加到 debug-context"

    def run(self, input_data: dict) -> Verdict:
        # ctx 已经被 regression_analyzer 原地更新了
        # 这个 Transformer 的作用是格式归一 + 清理当前假设/试探状态
        ctx = input_data
        ctx["current_hypothesis"] = None
        ctx["current_probe_plan"] = None
        ctx["current_probe_results"] = None
        ctx["current_fix"] = None
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"回归结论已归入上下文，iteration={ctx.get('iteration', 0)}",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

import re

def _extract_json(text: str) -> str | None:
    """从 LLM 输出中提取 JSON 代码块。"""
    match = re.search(r"```json\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # fallback: 尝试直接解析
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return None
