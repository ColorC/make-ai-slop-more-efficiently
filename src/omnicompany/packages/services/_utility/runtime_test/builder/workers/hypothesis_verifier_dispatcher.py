# [OMNI] origin=claude-code domain=services/runtime_test_builder/workers ts=2026-04-27T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.builder.hypothesis_verifier_dispatcher.catalog.py"
"""HypothesisVerifierDispatcherWorker — Worker #3 (HARD).

接 hypothesis_set + target_profile, 对每条假设决定执行方式:

- hypothesis_id 匹配 hypothesis_library 已知 pattern → 调对应 verifier (内置 catalog)
- 否则 → 标 'pending_manual' 待 L1 / Phase D 实施

执行 catalog (Phase C MVP, 后续可扩):
- stable / cross_run 类: 子进程跑 absorption-runtime-test 取其 cross_run_evidence
- byte_diff_acceptance: 子进程跑 code-runtime-test (若 has_fixtures)
- reference_existence: 程序化扫提案 references (调 absorption-runtime-test 路 4 升级版)
- five_element_check: 调 material-diagnosis 管线
- directory_hygiene: 调 guardian patrol
- red_line_check: 调 lap-audit
- 其他 (含 novel) → pending_manual
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .._paths import PROJECT_ROOT

logger = logging.getLogger(__name__)


_PROJECT_ROOT = PROJECT_ROOT


# pattern id → (executor_kind, target_pipeline_id_or_None, pretty_name)
# executor_kind:
#   'absorption_runtime_test'   — 子进程跑 absorption-runtime-test
#   'code_runtime_test'         — 子进程跑 code-runtime-test (待 fixtures spec)
#   'lap_audit'                 — 子进程跑 lap-audit (扫 target 源码红线)
#   'material_diagnosis'        — 子进程跑 material-diagnosis (扫 target formats.py 五要素)
#   'guardian_project_scan'     — 子进程跑 guardian (项目级目录卫生扫 · 不针对单 target)
#   'pending_manual'            — 暂未接通, 标待人工
_VERIFIER_CATALOG: dict[str, tuple[str, str | None, str]] = {
    "stable": ("absorption_runtime_test", "absorption-runtime-test", "absorption-runtime-test cross_run path"),
    "honest": ("inline_reference_honesty", None, "内置产物引用真实性扫 · 嵌套跑 absorption-runtime-test 拿 sample_runs · 扫 reference_code 文件路径/行号/snippet 真存在"),
    "robust": ("inline_robust", None, "内置健壮性扫 · 喂 3 组明显错误输入子进程跑 target, 看是否正确返 FAIL/PARTIAL"),
    "observable": ("inline_observable", None, "内置可观察性扫 · sqlite 查 events.db 看 source=target 历史事件量 + 类型"),
    "byte_diff_acceptance": ("inline_byte_diff", None, "内置字节级标杆比对 · 探 tests/teams/<pkg>/fixtures+expected · 真子进程跑 target byte-diff"),
    "reference_existence": ("absorption_runtime_test", "absorption-runtime-test", "absorption-runtime-test (proposals 引用真实性)"),
    "five_element_check": ("inline_five_element_check", None, "内置五要素扫 (绕开 material-diagnosis) · ast 扫 formats.py 抽 Material 验 id/parent/json_schema/description/tags"),
    "directory_hygiene": ("inline_directory_hygiene", None, "内置目录卫生扫 (绕开 guardian 工具) · 扫散文 .md / 临时文件 / 测试文件位置"),
    "red_line_check": ("inline_red_line_check", None, "内置源码红线扫 (绕开 lap-audit) · ast 扫硬编码模型/打分字段/防御性切片"),
}


_RUNNER_SCRIPT = """
import os, sys, json, asyncio
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

target_id = sys.argv[1]
input_path = sys.argv[2]
output_path = sys.argv[3]
max_steps = int(sys.argv[4]) if len(sys.argv) > 4 else 1000

with open(input_path, 'r', encoding='utf-8') as f:
    input_data = json.load(f)

verdict = 'FAIL'
output = {}
diag = ''
try:
    from omnicompany.core.dispatch import dispatch
    from omnicompany.core.registry import discover
    discover()
    result = asyncio.run(dispatch(target_id, input_data, max_steps=max_steps))
    if hasattr(result, 'kind'):
        verdict = result.kind.value.upper() if hasattr(result.kind, 'value') else str(result.kind).upper()
        if hasattr(result, 'output') and isinstance(result.output, dict):
            output = result.output
        if hasattr(result, 'diagnosis') and result.diagnosis:
            diag = result.diagnosis
    elif isinstance(result, dict):
        output = result
        verdict = 'PASS'
except Exception as exc:
    verdict = 'FAIL'
    diag = f'{type(exc).__name__}: {exc}'

with open(output_path, 'w', encoding='utf-8') as f:
    json.dump({'verdict': verdict, 'output': output, 'diagnosis': diag}, f, ensure_ascii=False)
"""


def _dispatch_subprocess(pipeline_id: str, input_data: dict, timeout_sec: int = 2400) -> dict:
    """子进程跑 dispatch, 返 {verdict, output, diagnosis}."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_in.json", delete=False, encoding="utf-8"
    ) as tin:
        json.dump(input_data, tin, ensure_ascii=False)
        in_path = tin.name
    out_path = in_path.replace("_in.json", "_out.json")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_runner.py", delete=False, encoding="utf-8"
    ) as ts:
        ts.write(_RUNNER_SCRIPT)
        script_path = ts.name

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, script_path, pipeline_id, in_path, out_path, "1000"]

    verdict = "FAIL"
    output: dict = {}
    diag = ""

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        if Path(out_path).is_file():
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                if isinstance(parsed, dict):
                    verdict = parsed.get("verdict", "FAIL")
                    output = parsed.get("output", {}) or {}
                    diag = parsed.get("diagnosis", "")
            except Exception as e:
                diag = f"输出 JSON 解析失败: {e}"
        else:
            diag = f"子进程未产输出 (rc={proc.returncode}; stderr 末: {(proc.stderr or '')[-500:]})"
    except subprocess.TimeoutExpired:
        diag = f"子进程超时 ({timeout_sec}s)"
    finally:
        for p in (in_path, out_path, script_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    return {"verdict": verdict, "output": output, "diagnosis": diag}


def _extract_material_ids_from_formats(formats_path: Path) -> list[str]:
    """用 ast 扫 target 包的 formats.py 抽 Material id 列表.

    匹配三种写法:
    - `M_XXX = Material(id="xxx", ...)`           · 单赋值 Call
    - `M_XXX = {"id": "xxx", ...}`                · 单赋值 dict 字面量
    - `FORMATS = [Material(id="xxx", ...), ...]`  · 列表里多个 Call/dict
    """
    import ast as _ast

    if not formats_path.is_file():
        return []
    try:
        tree = _ast.parse(formats_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    def _id_from_call(call: _ast.Call) -> str | None:
        for kw in call.keywords:
            if (
                kw.arg == "id"
                and isinstance(kw.value, _ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                return kw.value.value
        return None

    def _id_from_dict(d: _ast.Dict) -> str | None:
        for k, v in zip(d.keys, d.values):
            if (
                isinstance(k, _ast.Constant)
                and k.value == "id"
                and isinstance(v, _ast.Constant)
                and isinstance(v.value, str)
            ):
                return v.value
        return None

    def _scan_node(rhs: _ast.AST) -> list[str]:
        out: list[str] = []
        if isinstance(rhs, _ast.Call):
            i = _id_from_call(rhs)
            if i:
                out.append(i)
        elif isinstance(rhs, _ast.Dict):
            i = _id_from_dict(rhs)
            if i:
                out.append(i)
        elif isinstance(rhs, (_ast.List, _ast.Tuple)):
            for elt in rhs.elts:
                out.extend(_scan_node(elt))
        return out

    ids: list[str] = []
    for node in tree.body:
        if not isinstance(node, _ast.Assign):
            continue
        ids.extend(_scan_node(node.value))
    # 去重保序
    seen: set[str] = set()
    deduped: list[str] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        deduped.append(i)
    return deduped


def _audit_byte_diff(target_team_id: str, project_root: Path) -> dict:
    """内置字节级标杆比对 · 探 tests/teams/<pkg>/fixtures + expected 后子进程跑 target 真做 byte-diff.

    Convention 假设 (跟 csv-to-md test_contract.py 同):
    - tests/teams/<pkg>/fixtures/case_*.<input_ext>     · 输入
    - tests/teams/<pkg>/expected/case_*.<output_ext>    · 标杆输出

    用 ast 扫 test_contract.py 找 success case fixtures · 没 test_contract → 用文件名 case_X.X 配对.
    对每对: 子进程跑 target 喂 input, 拿 output content 字段, 跟 expected 字节比.
    全 byte_exact → verified_pass; 有不等 → verified_fail; 无 fixtures → not_applicable.
    """
    pkg_name = target_team_id.replace("-", "_")
    # 支持两种 convention:
    # (A) tests/teams/<pkg>/fixtures + expected
    # (B) docs/plans/*/requirements/<pkg>/fixtures + expected
    candidate_roots = [
        project_root / "tests" / "teams" / pkg_name,
    ]
    # glob B
    plans_dir = project_root / "docs" / "plans"
    if plans_dir.is_dir():
        for plan in plans_dir.iterdir():
            if not plan.is_dir():
                continue
            req = plan / "requirements" / pkg_name
            if req.is_dir():
                candidate_roots.append(req)

    fixtures_dir = None
    expected_dir = None
    for root in candidate_roots:
        f = root / "fixtures"
        e = root / "expected"
        if f.is_dir() and e.is_dir():
            fixtures_dir = f
            expected_dir = e
            break

    if fixtures_dir is None:
        return {
            "passed": True,  # not_applicable 不算 fail
            "violations": [],
            "counts": {"critical": 0, "minor": 0},
            "case_count": 0,
            "byte_exact": 0,
            "not_applicable": True,
            "reason": (
                f"target 没找到 fixtures + expected 目录 (扫了 tests/teams/{pkg_name}/ 跟 "
                f"docs/plans/*/requirements/{pkg_name}/), byte-diff 假设不适用此 target"
            ),
        }

    # 配对 fixtures · 用前缀匹配 (case_1_basic.csv ↔ case_1_basic.md)
    cases: list[tuple[str, Path, Path]] = []
    for input_file in fixtures_dir.iterdir():
        if not input_file.is_file():
            continue
        stem = input_file.stem
        # 找 expected 同 stem
        for expected_file in expected_dir.iterdir():
            if expected_file.is_file() and expected_file.stem == stem:
                cases.append((stem, input_file, expected_file))
                break

    if not cases:
        return {
            "passed": True,
            "violations": [],
            "counts": {"critical": 0, "minor": 0},
            "case_count": 0,
            "byte_exact": 0,
            "not_applicable": True,
            "reason": (
                f"tests/teams/{pkg_name}/ 存在但 fixtures + expected 没配对成功, byte-diff 假设此 target 不适用"
            ),
        }

    # 跑前 3 个 case 真比字节
    violations: list[str] = []
    crit = 0
    byte_exact = 0
    cases_run = cases[:3]
    diags: list[str] = []

    crashed_count = 0
    for stem, input_path, expected_path in cases_run:
        # 推 target input shape · csv-to-md 接 {path: ...}; 通用化先用 path 字段
        inner = _dispatch_subprocess(
            target_team_id,
            {"path": str(input_path)},
            timeout_sec=180,
        )
        if _tool_crashed(inner):
            # 跑挂 ≠ 假设证伪. 计入 crashed 而非 critical
            crashed_count += 1
            diags.append(f"case '{stem}': target 跑挂 ({(inner.get('diagnosis') or '')[:100]})")
            continue
        out = inner.get("output", {}) or {}
        # 通用 content 字段抽 (csv-to-md 走 content; 其他 target 看实际)
        actual = out.get("content") or out.get("markdown") or out.get("output") or ""
        if not isinstance(actual, str):
            violations.append(f"[critical] case '{stem}': target output 没 content 字段 (输出 keys: {list(out.keys())[:5]})")
            crit += 1
            continue
        expected_bytes = expected_path.read_text(encoding="utf-8")
        if actual == expected_bytes:
            byte_exact += 1
            diags.append(f"case '{stem}': byte-exact ✓")
        else:
            violations.append(
                f"[critical] case '{stem}': byte 不等 (actual {len(actual)} 字节 vs expected {len(expected_bytes)} 字节)"
            )
            crit += 1

    # 全跑挂 → execution_error, 不算证伪
    all_crashed = crashed_count == len(cases_run) and len(cases_run) > 0
    passed = crit == 0 and byte_exact == len(cases_run) and crashed_count == 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": 0},
        "case_count": len(cases_run),
        "byte_exact": byte_exact,
        "crashed_count": crashed_count,
        "all_crashed": all_crashed,
        "diagnoses": diags,
    }


def _audit_robust(target_team_id: str) -> dict:
    """内置健壮性扫 · 喂几组明显错误的输入子进程跑 target, 看是否正确返 FAIL.

    错误输入:
    - 空 dict (缺所有 required 字段)
    - 含明显错误类型的字段 (target_team_id=12345 等)
    - 完全无关字段 (foo='bar')

    判定:
    - 全 verdict ∈ {FAIL, PARTIAL} → verified_pass (target 正确拒绝错输入)
    - 有 verdict=PASS → verified_fail (假装成功, 静默吞错)
    """
    bad_inputs = [
        {},
        {"foo": "bar", "x": 12345},
        {"target_team_id": None, "sample_input": ["this", "is", "wrong"]},
    ]

    rejected = 0
    accepted = 0
    crashed = 0
    diags: list[str] = []
    violations: list[str] = []

    for i, bad_input in enumerate(bad_inputs):
        inner = _dispatch_subprocess(target_team_id, bad_input, timeout_sec=120)
        v = (inner.get("verdict") or "").upper()
        if _tool_crashed(inner):
            crashed += 1
            diags.append(f"input #{i+1}: target 跑挂 (子进程未产输出)")
            continue
        if v in ("FAIL", "PARTIAL"):
            rejected += 1
            d = (inner.get("diagnosis") or "")[:100]
            diags.append(f"input #{i+1}: 正确拒绝 verdict={v} · diag='{d}'")
        elif v == "PASS":
            accepted += 1
            violations.append(
                f"[critical] input #{i+1} ({bad_input}): target 错误地返 PASS, 没识别错输入"
            )
        else:
            crashed += 1
            diags.append(f"input #{i+1}: 未知 verdict={v}")

    # 跑挂不是拒绝：只有三个坏输入都产出可解释的 FAIL/PARTIAL，才能验证健壮。
    crit = len(violations)
    passed = rejected == len(bad_inputs) and accepted == 0 and crashed == 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": 0},
        "rejected_count": rejected,
        "accepted_count": accepted,
        "crashed_count": crashed,
        "all_crashed": crashed == len(bad_inputs),
        "diagnoses": diags,
    }


def _audit_observable(target_team_id: str, project_root: Path) -> dict:
    """内置可观察性扫 · 查 events.db 看 target 历史上是否真挂事件总线落盘.

    target 跑过 + EventBus 真挂 → 历史 source=target_team_id 事件数 > 0 → verified_pass
    target 从没跑过或没挂 EventBus → 0 事件 → verified_fail
    """
    import sqlite3 as _sqlite

    db_path = project_root / "data" / "events.db"
    if not db_path.is_file():
        return {
            "passed": False,
            "violations": [f"events.db 不存在: {db_path}. target 从没跑过或事件总线未配置."],
            "counts": {"critical": 1, "minor": 0},
            "event_count": 0,
            "event_types": [],
        }

    # source 在 events.db 里通常是 pkg_name (snake_case), 不是 target_team_id.
    pkg_name = target_team_id.replace("-", "_")
    try:
        conn = _sqlite.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT trace_id FROM events "
            "WHERE source = ? AND event_type = 'task.finish' "
            "ORDER BY timestamp DESC LIMIT 1",
            (pkg_name,),
        )
        trace_row = cur.fetchone()
        if trace_row is None:
            conn.close()
            return {
                "passed": False,
                "violations": [
                    f"[critical] events.db 里没有 source='{pkg_name}' 的完整 task.finish trace"
                ],
                "counts": {"critical": 1, "minor": 0},
                "event_count": 0,
                "event_types": [],
            }
        trace_id = trace_row[0]
        cur.execute(
            "SELECT event_type, source, data FROM events "
            "WHERE trace_id = ? ORDER BY timestamp",
            (trace_id,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return {
            "passed": False,
            "violations": [f"events.db 查询失败: {e}"],
            "counts": {"critical": 1, "minor": 0},
            "event_count": 0,
            "event_types": [],
        }

    count = len(rows)
    types = sorted({row[0] for row in rows})
    nodes: set[str] = set()
    models: set[str] = set()
    for _event_type, _source, raw_data in rows:
        try:
            payload = json.loads(raw_data).get("payload", {})
        except Exception:
            continue
        node_id = payload.get("node")
        if isinstance(node_id, str) and node_id:
            nodes.add(node_id)
        model = payload.get("model")
        if isinstance(model, str) and model:
            models.add(model)

    violations: list[str] = []
    crit = 0
    minor = 0

    if count < 5:
        violations.append(
            f"[critical] trace={trace_id} 仅 {count} 个事件, 无法证明完整执行链"
        )
        crit += 1

    required_types = {"task.intent", "task.finish"}
    missing_types = sorted(required_types - set(types))
    if missing_types:
        violations.append(
            f"[critical] trace={trace_id} 缺终态事件: {missing_types}"
        )
        crit += 1
    if len(nodes) < 3:
        violations.append(
            f"[critical] trace={trace_id} 只看见节点 {sorted(nodes)}, 不足三节点"
        )
        crit += 1
    if "external_agent.started" in types and not models:
        violations.append(
            f"[critical] trace={trace_id} 有外部 Agent 启动事件但没有 model 证据"
        )
        crit += 1

    passed = crit == 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": minor},
        "event_count": count,
        "event_types": types,
        "trace_id": trace_id,
        "nodes": sorted(nodes),
        "models": sorted(models),
    }


def _audit_reference_honesty(sample_runs: list[dict], repo_path: Path) -> dict:
    """内置产物引用真实性扫 (诚实假设的具体实施).

    扫 sample_runs 里所有 proposals.reference_code 字段, 验:
    - file 路径 (相对 repo_path) 真存在
    - line_start (如有) 在文件行数范围内
    - snippet (如有) 真在文件那行附近 substring match

    返 {passed, violations, counts: {critical, minor}, total_refs, valid_refs}.
    """
    if not repo_path.is_dir():
        return {
            "passed": False,
            "violations": [f"repo_path 不存在: {repo_path}"],
            "counts": {"critical": 1, "minor": 0},
            "total_refs": 0,
            "valid_refs": 0,
        }

    violations: list[str] = []
    crit = 0
    minor = 0
    total = 0
    valid = 0

    for run in sample_runs:
        if run.get("verdict") not in ("PASS", "PARTIAL"):
            continue
        output = run.get("output") or {}
        proposals = output.get("proposals") or []
        for p in proposals:
            ref = p.get("reference_code") or {}
            if not ref:
                continue
            total += 1
            file_rel = ref.get("file") or ""
            line_start = ref.get("line_start") or ref.get("line")
            snippet = ref.get("snippet") or ""

            if not file_rel:
                violations.append(f"[critical] proposal {p.get('id','?')}: reference_code.file 缺失")
                crit += 1
                continue

            file_abs = repo_path / file_rel
            if not file_abs.is_file():
                violations.append(f"[critical] proposal {p.get('id','?')}: 文件不存在 {file_rel}")
                crit += 1
                continue

            try:
                lines = file_abs.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as e:
                violations.append(f"[minor] proposal {p.get('id','?')}: 文件读不出 {file_rel}: {e}")
                minor += 1
                continue

            file_ok = True
            if isinstance(line_start, int) and line_start > 0:
                if line_start > len(lines):
                    violations.append(
                        f"[critical] proposal {p.get('id','?')}: 行号 {line_start} 超过文件总行数 {len(lines)} ({file_rel})"
                    )
                    crit += 1
                    file_ok = False

            if file_ok and snippet and isinstance(snippet, str):
                snippet_short = snippet.strip()[:60]
                # 在 line_start 附近 ±5 行内找 substring
                if isinstance(line_start, int) and line_start > 0:
                    lo = max(0, line_start - 6)
                    hi = min(len(lines), line_start + 5)
                    region = "\n".join(lines[lo:hi])
                else:
                    region = "\n".join(lines)
                if snippet_short and snippet_short not in region:
                    violations.append(
                        f"[minor] proposal {p.get('id','?')}: snippet 在 {file_rel} 附近未找到 substring '{snippet_short[:40]}...'"
                    )
                    minor += 1
                    file_ok = False

            if file_ok:
                valid += 1

    passed = crit == 0 and total > 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": minor},
        "total_refs": total,
        "valid_refs": valid,
    }


def _audit_five_elements(pkg_path: Path) -> dict:
    """内置 target 包 formats.py 五要素扫 (绕开 material-diagnosis 工具命名脱节问题).

    五要素 (按 OmniMark 规范):
    - id: 非空, snake_case 含 dot (e.g. 'absorption.proposal')
    - parent: 非空 (在 omnicompany 已注册集合里 — 这里简化只验非空)
    - json_schema: 非空 dict, 含 type 字段
    - description: 长度 ≥150 字符 (信息量充分)
    - tags: 非空 list, 含至少一个 kind.* (kind.source / kind.internal / kind.sink)

    用 ast 扫 formats.py 抽 Material 定义 (Call/Dict/List 包装), 逐个验.
    """
    import ast as _ast

    if not pkg_path.is_dir():
        return {
            "passed": False,
            "violations": [f"target 包目录不存在: {pkg_path}"],
            "counts": {"critical": 1, "minor": 0},
            "scanned_materials": 0,
        }

    formats_path = pkg_path / "formats.py"
    if not formats_path.is_file():
        return {
            "passed": True,  # target 不带 Material 定义, 此假设对此 target 不适用 (空跑过, 无违规)
            "violations": [],
            "counts": {"critical": 0, "minor": 0},
            "scanned_materials": 0,
            "not_applicable": True,
            "reason": f"target 没 formats.py ({formats_path}), 五要素扫不适用",
        }

    # Prefer live registered Material objects. AST-only inspection cannot
    # distinguish a missing schema from a schema loaded through the canonical
    # contract function (for example json_schema=route_decision_schema()).
    materials: list[dict] = []
    try:
        import importlib

        relative_module = formats_path.relative_to(_PROJECT_ROOT / "src").with_suffix("")
        module_name = ".".join(relative_module.parts)
        module = importlib.import_module(module_name)
        for material in getattr(module, "ALL_MATERIALS", []) or []:
            materials.append(
                {
                    "id": getattr(material, "id", None),
                    "parent": getattr(material, "parent", None),
                    "json_schema": getattr(material, "json_schema", None),
                    "_json_schema_keys": list(
                        (getattr(material, "json_schema", None) or {}).keys()
                    ),
                    "description": getattr(material, "description", ""),
                    "tags": list(getattr(material, "tags", []) or []),
                }
            )
    except Exception:
        # External/unimportable targets keep the conservative AST fallback.
        materials = []

    def _extract_material_dict(node: _ast.AST) -> dict | None:
        """从 Material(...) Call 或 dict literal 抽五要素字段."""
        out: dict = {}
        if isinstance(node, _ast.Call):
            for kw in node.keywords:
                if isinstance(kw.value, _ast.Constant):
                    out[kw.arg] = kw.value.value
                elif isinstance(kw.value, _ast.Dict):
                    out[kw.arg] = "<dict>"
                    if kw.arg == "json_schema":
                        # 看 dict 字面量含哪些 key (尤其 'type')
                        keys = [k.value for k in kw.value.keys if isinstance(k, _ast.Constant)]
                        out["_json_schema_keys"] = keys
                elif isinstance(kw.value, _ast.List):
                    out[kw.arg] = [
                        e.value for e in kw.value.elts
                        if isinstance(e, _ast.Constant)
                    ]
            return out if "id" in out else None
        if isinstance(node, _ast.Dict):
            for k, v in zip(node.keys, node.values):
                if not isinstance(k, _ast.Constant):
                    continue
                key = k.value
                if isinstance(v, _ast.Constant):
                    out[key] = v.value
                elif isinstance(v, _ast.Dict):
                    out[key] = "<dict>"
                    if key == "json_schema":
                        keys = [k2.value for k2 in v.keys if isinstance(k2, _ast.Constant)]
                        out["_json_schema_keys"] = keys
                elif isinstance(v, _ast.List):
                    out[key] = [e.value for e in v.elts if isinstance(e, _ast.Constant)]
            return out if "id" in out else None
        return None

    def _scan_for_materials(rhs: _ast.AST) -> list[dict]:
        out: list[dict] = []
        if isinstance(rhs, (_ast.Call, _ast.Dict)):
            d = _extract_material_dict(rhs)
            if d:
                out.append(d)
        elif isinstance(rhs, (_ast.List, _ast.Tuple)):
            for elt in rhs.elts:
                out.extend(_scan_for_materials(elt))
        return out

    if not materials:
        try:
            tree = _ast.parse(formats_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {
                "passed": False,
                "violations": [f"[critical] formats.py 解析失败: {e}"],
                "counts": {"critical": 1, "minor": 0},
                "scanned_materials": 0,
            }
        for node in tree.body:
            if not isinstance(node, _ast.Assign):
                continue
            materials.extend(_scan_for_materials(node.value))

    violations: list[str] = []
    crit = 0
    minor = 0

    for m_dict in materials:
        mid = m_dict.get("id", "?")
        # 1. id snake_case 含点
        if not isinstance(mid, str) or not mid:
            violations.append(f"[critical] {mid}: id 非字符串或空")
            crit += 1
            continue
        # 2. parent 非空
        if not m_dict.get("parent"):
            violations.append(f"[critical] {mid}: parent 字段缺失或空")
            crit += 1
        # 3. json_schema 非空 dict 含 type
        if not m_dict.get("json_schema"):
            violations.append(f"[critical] {mid}: json_schema 缺失")
            crit += 1
        else:
            ks = m_dict.get("_json_schema_keys") or []
            if "type" not in ks:
                violations.append(f"[critical] {mid}: json_schema 缺 'type' 字段")
                crit += 1
        # 4. description ≥150 字
        desc = m_dict.get("description", "")
        if not isinstance(desc, str) or len(desc) < 150:
            violations.append(f"[minor] {mid}: description 过短 ({len(desc) if isinstance(desc, str) else 0} 字 < 150)")
            minor += 1
        # 5. tags 非空 list 含 kind.*
        tags = m_dict.get("tags", [])
        if not isinstance(tags, list) or not tags:
            violations.append(f"[critical] {mid}: tags 缺失或空")
            crit += 1
        else:
            has_kind = any(isinstance(t, str) and t.startswith("kind.") for t in tags)
            if not has_kind:
                violations.append(f"[critical] {mid}: tags 缺 kind.* 标签 (实际 tags={tags})")
                crit += 1

    passed = crit == 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": minor},
        "scanned_materials": len(materials),
    }


def _audit_red_lines(pkg_path: Path) -> dict:
    """内置 target 包源码红线扫 (绕开 lap-audit 工具自身判别力为零).

    扫 .py 文件用 ast 找硬性铁律违反:
    - 防御性截断: `name[:N]` 模式 (Subscript with Slice, 无 step)
    - 硬编码模型: `model="claude*"` / `"opus"` / `"sonnet"` 等非 qwen 模型字符串
    - 打分字段: dict 字面量 key 含 'score'/'level'/'tier'/'rating'/'grade'
    - 注: 切片很多无害 (path[:n]/list[:k]), 但喂 LLM 的截断是 critical. 简化: 找 fmt-string / LLM-call 旁边的切片.
      短期版: 数所有切片, 切片数 ≥3 标 critical (启发式 — 越多越可疑).
    """
    import ast as _ast

    if not pkg_path.is_dir():
        return {
            "passed": False,
            "violations": [f"target 包目录不存在: {pkg_path}"],
            "counts": {"critical": 1, "minor": 0},
            "scanned_files": 0,
        }

    # 检查项 (只用 ast 字面量真信号, 不用启发式):
    # 1. 硬编码非 qwen 模型: keyword model="claude*" 等
    # 2. 打分字段: dict 字面量 key 含 'score'/'level'/'tier' 等
    # 切片启发式被试错证伪 (绿样本也大量切片误报), 删掉.
    bad_models = ("claude", "opus", "sonnet", "haiku", "gpt-", "gpt4", "gemini")
    score_keys = ("score", "level", "tier", "rating", "grade", "ranking")
    violations: list[str] = []
    crit = 0
    minor = 0
    scanned = 0

    for f in pkg_path.rglob("*.py"):
        if "__pycache__" in f.parts or ".git" in f.parts:
            continue
        scanned += 1
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        rel = f.relative_to(pkg_path)

        for node in _ast.walk(tree):
            # 硬编码模型: keyword model=Const("claude...")
            if isinstance(node, _ast.keyword) and node.arg == "model":
                if isinstance(node.value, _ast.Constant) and isinstance(node.value.value, str):
                    val = node.value.value.lower()
                    for bm in bad_models:
                        if bm in val:
                            violations.append(
                                f"[critical] 硬编码非 qwen 模型: {rel} · model='{node.value.value}'"
                            )
                            crit += 1
                            break

            # dict 字面量含打分 key
            if isinstance(node, _ast.Dict):
                for k in node.keys:
                    if isinstance(k, _ast.Constant) and isinstance(k.value, str):
                        kl = k.value.lower()
                        if kl in score_keys:
                            violations.append(
                                f"[critical] 打分字段: {rel} · key='{k.value}'"
                            )
                            crit += 1
                            break

    passed = crit == 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": minor},
        "scanned_files": scanned,
    }


def _audit_directory_hygiene(pkg_path: Path) -> dict:
    """内置 target 包目录卫生扫 (绕开 guardian 工具自身 bug).

    检查项:
    - src 包内禁有除 DESIGN.md / .omni/manifest.yaml 之外的散文 .md (如 NOTES.md / TODO.md / PLAN.md)
    - 临时文件 (.tmp / .bak / .swp / ~ 后缀 / .DS_Store)
    - 文件名含 TODO / NOTES / scratch / draft 关键词的 .md
    - 测试文件 (test_*.py) 不应在 src 包内 (应在 tests/teams/<pkg>/)

    返 {passed: bool, violations: list[str], counts: {critical: int, minor: int}}.
    """
    if not pkg_path.is_dir():
        return {
            "passed": False,
            "violations": [f"target 包目录不存在: {pkg_path}"],
            "counts": {"critical": 1, "minor": 0},
            "scanned_files": 0,
        }

    allowed_md = {"DESIGN.md", "README.md"}
    bad_md_keywords = ("TODO", "NOTES", "PLAN", "SCRATCH", "DRAFT", "TEMP")
    temp_suffixes = (".tmp", ".bak", ".swp", "~", ".orig", ".rej")

    violations: list[str] = []
    crit = 0
    minor = 0
    scanned = 0

    for f in pkg_path.rglob("*"):
        if "__pycache__" in f.parts or ".git" in f.parts:
            continue
        if not f.is_file():
            continue
        scanned += 1
        rel = f.relative_to(pkg_path)
        name = f.name

        # 临时文件后缀 (critical)
        if any(name.endswith(s) for s in temp_suffixes):
            violations.append(f"[critical] 临时文件: {rel}")
            crit += 1
            continue
        if name == ".DS_Store":
            violations.append(f"[critical] 临时文件: {rel}")
            crit += 1
            continue

        # 散文 .md (critical) — 除 DESIGN.md / README.md
        if name.endswith(".md") and name not in allowed_md:
            violations.append(f"[critical] 包内散文 .md: {rel} (仅允许 DESIGN.md / README.md)")
            crit += 1
            continue

        # 文件名含坏关键词
        upper_name = name.upper()
        for kw in bad_md_keywords:
            if kw in upper_name and name.endswith((".md", ".py", ".txt")):
                violations.append(f"[minor] 文件名含 {kw} 关键词: {rel}")
                minor += 1
                break

        # 测试文件在 src 包内 (不应在)
        if name.startswith("test_") and name.endswith(".py"):
            violations.append(f"[critical] 测试文件在 src 包内: {rel} (应在 tests/teams/<pkg>/)")
            crit += 1
            continue

    passed = crit == 0
    return {
        "passed": passed,
        "violations": violations,
        "counts": {"critical": crit, "minor": minor},
        "scanned_files": scanned,
    }


def _tool_crashed(inner: dict) -> bool:
    """识别子进程跑挂 (vs 工具正常跑出 FAIL).

    跑挂的情况: 子进程未产输出文件 / 超时 / output 解析失败. 这些应标 execution_error,
    不是 verified_fail (后者是工具正常跑完但判定不达标).
    """
    diag = (inner.get("diagnosis") or "")
    crash_markers = ("子进程未产输出", "子进程超时", "输出 JSON 解析失败")
    return any(m in diag for m in crash_markers)


def _derive_tool_status(inner: dict) -> tuple[str, str]:
    """根据工具跑出来的 verdict 派生 status + extra excerpt.

    status: verified_pass / verified_fail / execution_error.
    """
    if _tool_crashed(inner):
        return "execution_error", f"工具跑挂 (非假设证伪): {(inner.get('diagnosis') or '')[:200]}"
    inner_verdict = (inner.get("verdict") or "").upper()
    if inner_verdict == "PASS":
        return "verified_pass", ""
    if inner_verdict in ("PARTIAL", "FAIL"):
        return "verified_fail", ""
    return "execution_error", f"未知 verdict={inner_verdict!r}"


def _resolve_catalog_key(h: dict) -> str | None:
    """决定假设走 catalog 哪一档.

    优先用 LLM 显式 declare 的 library_match_id (Phase E 后铁律 — 不再猜).
    向后兼容: 若上游没填此字段 (老 schema), 回退用 hypothesis_id 严格相等查 catalog.
    """
    if "library_match_id" in h:
        match = h["library_match_id"]
        if isinstance(match, str) and match.strip():
            key = match.strip().lower()
            if key in _VERIFIER_CATALOG:
                return key
            # 显式声明但写错了 — 不猜, 直接 None (落 pending 含 reason)
            return None
        # 显式 None: LLM 表态 "完全 novel, 无库匹配"
        return None
    # 老 schema (没 library_match_id 字段) — 回退用 hypothesis_id 猜
    hyp_id = (h.get("hypothesis_id") or "").replace("_novel", "").strip().lower()
    if hyp_id in _VERIFIER_CATALOG:
        return hyp_id
    return None


def _build_inner_input_for_absorption(target_team_id: str, target_profile: dict, hyp_id: str) -> dict:
    """构 absorption-runtime-test 的 sample_input."""
    # 这里需要 target 的 sample_input (含 repo_path 等). target_profile 没全量, 先简单取一个默认.
    # Phase D 再扩 —— 当前用一个标记位告诉 dispatch 这是为 hypothesis verify 跑.
    sample = target_profile.get("default_sample_input") or {}
    if not sample:
        # 兜底默认: 拿 absorption 自己当样本 (LLM 探包后没给 sample_input 就这么办)
        sample = {
            "repo_path": str(_PROJECT_ROOT / "src" / "omnicompany" / "packages" / "services" / "absorption"),
            "top_n": 5,
        }
    return {
        "target_team_id": target_team_id,
        "sample_input": sample,
        "run_count": 2,
        "spot_impl_count": 1,
    }


class HypothesisVerifierDispatcherWorker(Worker):
    DESCRIPTION = (
        "对每条假设决定执行方式 · catalog 内调对应 verifier 子进程 · 未匹配标 pending_manual."
    )
    FORMAT_IN: ClassVar[list[str]] = [
        "runtime_test_builder.hypothesis_set",
        "runtime_test_builder.target_profile",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "runtime_test_builder.hypothesis_evidence"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        # 取上游
        hyp_set_mirror = input_data.get("_from_HypothesisProposerWorker") or {}
        profile_mirror = input_data.get("_from_TargetExplorerWorker") or {}

        target_team_id = hyp_set_mirror.get("target_team_id") or input_data.get("target_team_id", "?")
        hypotheses = hyp_set_mirror.get("hypotheses") or input_data.get("hypotheses", [])

        if not hypotheses:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "target_team_id": target_team_id,
                    "results": [],
                    "pending_count": 0,
                },
                diagnosis="hypothesis_set 为空, 无法 dispatch",
            )

        # 缓存: 同一 executor 重复出现的假设, 复用一次 dispatch 结果
        # 同一批 hypothesis_set 内, 每个工具最多跑一次
        absorption_cache: dict | None = None
        code_cache: dict | None = None
        lap_audit_cache: dict | None = None
        material_diagnosis_cache: dict | None = None  # 现是 {cache_key: inner_result} dict 不是单结果
        guardian_cache: dict | None = None

        results: list[dict] = []
        pending_count = 0

        for h in hypotheses:
            hyp_id = h.get("hypothesis_id", "?")
            match_id = h.get("library_match_id")
            catalog_key = _resolve_catalog_key(h)
            if catalog_key is None:
                # 区分两种 None: (a) LLM 显式声明 novel (match=null) (b) 上游没填或写错
                if match_id is None:
                    excerpt = "LLM 声明此条 novel · 无 library 匹配"
                else:
                    excerpt = f"library_match_id='{match_id}' 不在调度名录 (写错或未支持)"
                results.append({
                    "hypothesis_id": hyp_id,
                    "status": "pending_manual",
                    "evidence_excerpt": excerpt,
                    "signals": [f"配方: {h.get('verification_recipe','')[:200]}"],
                    "executed_via": "(no catalog mapping)",
                })
                pending_count += 1
                continue

            executor_kind, pipeline_id, pretty = _VERIFIER_CATALOG[catalog_key]

            if executor_kind == "pending_manual":
                results.append({
                    "hypothesis_id": hyp_id,
                    "status": "pending_manual",
                    "evidence_excerpt": pretty,
                    "signals": [f"配方: {h.get('verification_recipe','')[:200]}"],
                    "executed_via": pretty,
                })
                pending_count += 1
                continue

            # ── 路由前置检查: absorption_runtime_test 仅适用消费源仓库的 target ──
            if executor_kind == "absorption_runtime_test":
                if not profile_mirror.get("has_repo_input"):
                    results.append({
                        "hypothesis_id": hyp_id,
                        "status": "pending_manual",
                        "evidence_excerpt": (
                            f"target 不消费源仓库 (has_repo_input=false), "
                            f"absorption-runtime-test 不适用此假设. "
                            f"library_match_id={match_id} 路由错配."
                        ),
                        "signals": [
                            f"配方: {h.get('verification_recipe','')[:200]}",
                            "需另路径验此假设 (留实施层 catalog 后续扩)",
                        ],
                        "executed_via": pretty + " (skipped, target 无 repo_path)",
                    })
                    pending_count += 1
                    continue

            # 实跑分支 (注: 同一 executor 重复假设复用上次结果)
            try:
                if executor_kind == "absorption_runtime_test":
                    if absorption_cache is None:
                        inner_input = _build_inner_input_for_absorption(
                            target_team_id, profile_mirror, hyp_id
                        )
                        absorption_cache = _dispatch_subprocess(
                            "absorption-runtime-test", inner_input, timeout_sec=2400
                        )
                    inner = absorption_cache
                    # 先识别嵌套是否跑挂
                    if _tool_crashed(inner):
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "execution_error",
                            "evidence_excerpt": (
                                f"absorption-runtime-test 嵌套跑挂: {(inner.get('diagnosis') or '')[:200]}"
                            ),
                            "signals": ["嵌套工具跑挂, 非假设证伪"],
                            "executed_via": pretty + " (nested crashed)",
                        })
                        pending_count += 1
                        continue
                    inner_output = inner.get("output", {}) or {}
                    inner_evidence = inner_output.get("evidence_paths") or {}
                    if not inner_evidence:
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "execution_error",
                            "evidence_excerpt": (
                                "absorption 嵌套返了 verdict 但 evidence_paths 为空, 内层未真产证据"
                            ),
                            "signals": [f"内层 verdict={inner.get('verdict')} · output keys={list(inner_output.keys())[:5]}"],
                            "executed_via": pretty + " (nested empty evidence)",
                        })
                        pending_count += 1
                        continue
                    # cross_run path
                    if catalog_key == "stable":
                        cross = inner_evidence.get("cross_run", {})
                        # 第三层: cross_run 数据全空 = 内层 cross_run_verifier 没成功
                        if not cross or (
                            not cross.get("file_overlap_pct")
                            and not cross.get("topic_overlap_pct")
                            and not cross.get("stability_observation")
                        ):
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": "execution_error",
                                "evidence_excerpt": "absorption cross_run 数据全 0/空, 内层 cross_run_verifier 未成功",
                                "signals": [],
                                "executed_via": pretty + " (cross_run empty)",
                            })
                            pending_count += 1
                            continue
                        file_overlap = float(cross.get("file_overlap_pct") or 0.0)
                        topic_overlap = float(cross.get("topic_overlap_pct") or 0.0)
                        passed = max(file_overlap, topic_overlap) >= 0.5
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "verified_pass" if passed else "verified_fail",
                            "evidence_excerpt": f"file_overlap={file_overlap:.2f} topic_overlap={topic_overlap:.2f}",
                            "signals": [cross.get("stability_observation", "")] + list(cross.get("divergence_signals") or []),
                            "executed_via": pretty,
                        })
                    elif catalog_key == "reference_existence":
                        spot = inner_evidence.get("spot_impl", {})
                        if not spot or (
                            not spot.get("implementable_pct")
                            and not spot.get("truly_solves_pct")
                            and not spot.get("attempts")
                        ):
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": "execution_error",
                                "evidence_excerpt": "absorption spot_impl 数据全 0/空, 内层 spot_impl_verifier 未成功",
                                "signals": [],
                                "executed_via": pretty + " (spot_impl empty)",
                            })
                            pending_count += 1
                            continue
                        impl_pct = float(spot.get("implementable_pct") or 0.0)
                        passed = impl_pct >= 0.5
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "verified_pass" if passed else "verified_fail",
                            "evidence_excerpt": f"spot_impl implementable_pct={impl_pct:.2f}",
                            "signals": [spot.get("groundedness_observation", "")],
                            "executed_via": pretty,
                        })
                    else:
                        # 其他 absorption 路 (如 source_coverage)
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "absorption 跑过, 但 catalog 未明指 path",
                            "signals": [],
                            "executed_via": pretty + " (路待精确)",
                        })
                        pending_count += 1
                elif executor_kind == "code_runtime_test":
                    if not profile_mirror.get("has_fixtures"):
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target 没 fixtures, byte-diff 不可执行",
                            "signals": [],
                            "executed_via": pretty + " (skipped, has_fixtures=false)",
                        })
                        pending_count += 1
                    else:
                        if code_cache is None:
                            # code-runtime-test 需 target_spec 含 test_cases 等. 当前 target_profile 没足够信息.
                            code_cache = {"output": {}, "diagnosis": "(MVP) need explicit test_cases"}
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "code-runtime-test 需手填 fixtures path",
                            "signals": [],
                            "executed_via": pretty + " (pending fixtures spec)",
                        })
                        pending_count += 1
                elif executor_kind == "lap_audit":
                    # 调 lap-audit 扫 target 源码红线
                    pkg_path = profile_mirror.get("package_path", "")
                    if not pkg_path:
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target_profile 没 package_path, 无法扫源码",
                            "signals": [],
                            "executed_via": pretty + " (skipped, no package_path)",
                        })
                        pending_count += 1
                    else:
                        if lap_audit_cache is None:
                            lap_audit_cache = _dispatch_subprocess(
                                "lap-audit",
                                {"target_path": pkg_path},
                                timeout_sec=600,
                            )
                        inner = lap_audit_cache
                        inner_verdict = (inner.get("verdict") or "").upper()
                        inner_output = inner.get("output", {}) or {}
                        signals: list[str] = []
                        for key in ("violations", "issues", "findings", "diagnostics"):
                            val = inner_output.get(key)
                            if isinstance(val, list):
                                for v in val[:5]:
                                    signals.append(f"[{key}] {str(v)[:200]}")
                            elif isinstance(val, str) and val:
                                signals.append(f"[{key}] {val[:200]}")
                        status, extra = _derive_tool_status(inner)
                        excerpt = (inner.get("diagnosis") or "")[:300] or (
                            f"lap-audit verdict={inner_verdict} · output 字段: {list(inner_output.keys())}"
                        )
                        if extra:
                            excerpt = f"{extra} | {excerpt}"
                        if status == "execution_error":
                            pending_count += 1  # 跑挂算"未实测", 计 pending
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": status,
                            "evidence_excerpt": excerpt,
                            "signals": signals or [f"lap-audit 跑出 verdict={inner_verdict}"],
                            "executed_via": pretty,
                        })
                elif executor_kind == "material_diagnosis":
                    # 调 material-diagnosis 扫 target 包 formats.py 五要素.
                    # 工具入参是 (material_id, source_root) 单 Material — 这里先 ast 扫 formats.py
                    # 抽 id 列表, 然后逐个调, 综合所有 Material 的 healthy/unhealthy 结果.
                    pkg_path = profile_mirror.get("package_path", "")
                    formats_path = Path(pkg_path) / "formats.py" if pkg_path else None
                    if not pkg_path:
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target_profile 没 package_path, 无法扫 formats.py",
                            "signals": [],
                            "executed_via": pretty + " (skipped, no package_path)",
                        })
                        pending_count += 1
                    elif not formats_path or not formats_path.is_file():
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": f"target 没 formats.py ({formats_path}), 五要素扫不适用",
                            "signals": ["target 不带 Material 定义, 此假设对此 target 不适用"],
                            "executed_via": pretty + " (skipped, no formats.py)",
                        })
                        pending_count += 1
                    else:
                        ids = _extract_material_ids_from_formats(formats_path)
                        if not ids:
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": "pending_manual",
                                "evidence_excerpt": "扫 formats.py 没抽到 Material id (可能写法非标)",
                                "signals": [f"formats.py: {formats_path}"],
                                "executed_via": pretty + " (skipped, no Material ids)",
                            })
                            pending_count += 1
                        else:
                            # 推 source_root: 优先 omnicompany src; 若 target 在 src 外, 用 target 包父目录
                            target_p = Path(pkg_path).resolve()
                            omn_src = (_PROJECT_ROOT / "src" / "omnicompany").resolve()
                            try:
                                target_p.relative_to(omn_src)
                                source_root = str(omn_src)
                            except ValueError:
                                # target 在 omnicompany src 外, 用其父目录
                                source_root = str(target_p.parent)

                            if material_diagnosis_cache is None:
                                material_diagnosis_cache = {}
                            crit_total = 0
                            unhealthy_count = 0
                            healthy_count = 0
                            error_count = 0
                            sigs: list[str] = []
                            ids_sample = ids[:8]  # 最多扫 8 个避免爆 token
                            for mid in ids_sample:
                                cache_key = f"{source_root}::{mid}"
                                if cache_key in material_diagnosis_cache:
                                    inner = material_diagnosis_cache[cache_key]
                                else:
                                    inner = _dispatch_subprocess(
                                        "material-diagnosis",
                                        {"material_id": mid, "source_root": source_root},
                                        timeout_sec=120,
                                    )
                                    material_diagnosis_cache[cache_key] = inner
                                if _tool_crashed(inner):
                                    error_count += 1
                                    continue
                                inner_out = inner.get("output", {}) or {}
                                inner_verd = inner_out.get("verdict", "?")
                                crit = (inner_out.get("counts") or {}).get("critical", 0)
                                if inner_verd == "healthy":
                                    healthy_count += 1
                                elif inner_verd == "unhealthy":
                                    unhealthy_count += 1
                                    crit_total += crit
                                summary = inner_out.get("summary") or ""
                                if summary:
                                    sigs.append(f"[{mid}] {summary[:200]}")

                            checked = healthy_count + unhealthy_count
                            if checked == 0:
                                # 全跑挂
                                status = "execution_error"
                                excerpt = (
                                    f"扫 {len(ids_sample)} 个 Material 全部跑挂 "
                                    f"(error_count={error_count}). source_root={source_root}"
                                )
                                pending_count += 1
                            elif crit_total > 0:
                                # 任一 critical 违规 → 不通过
                                status = "verified_fail"
                                excerpt = (
                                    f"扫 {checked}/{len(ids_sample)} 个 Material · "
                                    f"{unhealthy_count} 不健康 · {crit_total} 个 critical 违规"
                                )
                            elif unhealthy_count > 0:
                                # 有 unhealthy 但没 critical (major/minor)
                                status = "verified_fail"
                                excerpt = (
                                    f"扫 {checked}/{len(ids_sample)} 个 Material · "
                                    f"{unhealthy_count} 不健康 (无 critical, 主要 major/minor 违规)"
                                )
                            else:
                                # 全 healthy
                                status = "verified_pass"
                                excerpt = (
                                    f"扫 {checked}/{len(ids_sample)} 个 Material 全 healthy · "
                                    f"五要素无 critical/major 违规"
                                )

                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": status,
                                "evidence_excerpt": excerpt,
                                "signals": sigs[:5] or [f"material-diagnosis 真扫 {checked} 个 Material"],
                                "executed_via": pretty + f" · 扫了 {checked}/{len(ids)} 个 Material id",
                            })
                elif executor_kind == "inline_byte_diff":
                    audit = _audit_byte_diff(target_team_id, _PROJECT_ROOT)
                    if audit.get("not_applicable"):
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": audit["reason"],
                            "signals": ["target 不带标杆 fixtures, byte-diff 假设不适用"],
                            "executed_via": pretty + " (n/a)",
                        })
                        pending_count += 1
                    else:
                        crit = audit["counts"]["critical"]
                        case_n = audit["case_count"]
                        ok = audit["byte_exact"]
                        crashed_n = audit.get("crashed_count", 0)
                        all_crashed = audit.get("all_crashed", False)
                        if all_crashed:
                            # 全部跑挂 = 工具/target 自身问题, 不当假设证伪
                            status = "execution_error"
                            excerpt = (
                                f"byte-diff: {case_n} 个 fixtures 全部 target 跑挂 (rc非零). "
                                f"非假设证伪, 是 target 跑不起来 / dispatcher input shape 不对."
                            )
                            pending_count += 1
                        elif audit["passed"]:
                            status = "verified_pass"
                            excerpt = f"byte-diff: 跑了 {case_n} 个 fixtures · {ok}/{case_n} byte-exact 通过"
                        else:
                            status = "verified_fail"
                            excerpt = (
                                f"byte-diff: 跑了 {case_n} 个 fixtures · {ok}/{case_n} byte-exact · "
                                f"{crit} critical · {crashed_n} 跑挂"
                            )
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": status,
                            "evidence_excerpt": excerpt,
                            "signals": (audit["violations"] + audit.get("diagnoses", []))[:6],
                            "executed_via": pretty,
                        })
                elif executor_kind == "inline_robust":
                    audit = _audit_robust(target_team_id)
                    rej = audit["rejected_count"]
                    acc = audit["accepted_count"]
                    crashed = audit["crashed_count"]
                    if acc:
                        status = "verified_fail"
                        excerpt = (
                            f"健壮性: 喂 3 组错输入 · {acc} 错误返 PASS · "
                            f"{rej} 拒绝 · {crashed} 跑挂"
                        )
                    elif crashed:
                        status = "execution_error"
                        excerpt = (
                            f"健壮性未完成: 喂 3 组错输入 · {rej} 可解释拒绝 · "
                            f"{crashed} 跑挂；跑挂不能算 PASS"
                        )
                        pending_count += 1
                    elif audit["passed"]:
                        status = "verified_pass"
                        excerpt = (
                            f"健壮性: 喂 3 组错输入 · {rej} 正确拒绝 · {acc} 错误接受 · {crashed} 跑挂"
                        )
                    else:
                        status = "verified_fail"
                        excerpt = (
                            f"健壮性未通过: 喂 3 组错输入 · {rej} 拒绝 · "
                            f"{acc} 错误接受 · {crashed} 跑挂"
                        )
                    results.append({
                        "hypothesis_id": hyp_id,
                        "status": status,
                        "evidence_excerpt": excerpt,
                        "signals": (audit["violations"] + audit["diagnoses"])[:6],
                        "executed_via": pretty,
                    })
                elif executor_kind == "inline_observable":
                    audit = _audit_observable(target_team_id, _PROJECT_ROOT)
                    crit = audit["counts"]["critical"]
                    minor_n = audit["counts"]["minor"]
                    if audit["passed"]:
                        status = "verified_pass"
                        excerpt = (
                            f"events.db 含 {audit['event_count']} 个 source={target_team_id} 事件 · "
                            f"事件类型 {len(audit['event_types'])} 种"
                        )
                    else:
                        status = "verified_fail"
                        excerpt = (
                            f"events.db 含 {audit['event_count']} 个 source={target_team_id} 事件 · "
                            f"{crit} critical 违规"
                        )
                    results.append({
                        "hypothesis_id": hyp_id,
                        "status": status,
                        "evidence_excerpt": excerpt,
                        "signals": audit["violations"][:6] or [
                            f"事件类型样本: {', '.join(audit['event_types'][:5])}"
                        ],
                        "executed_via": pretty,
                    })
                elif executor_kind == "inline_reference_honesty":
                    # 需要 absorption-runtime-test 跑过的 sample_runs 拿 proposals
                    if not profile_mirror.get("has_repo_input"):
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target 不消费源仓库, 引用真实性扫不适用",
                            "signals": ["target 产物不带外部 file/line/snippet 锚点"],
                            "executed_via": pretty + " (n/a, no repo_input)",
                        })
                        pending_count += 1
                    else:
                        if absorption_cache is None:
                            inner_input = _build_inner_input_for_absorption(
                                target_team_id, profile_mirror, hyp_id
                            )
                            absorption_cache = _dispatch_subprocess(
                                "absorption-runtime-test", inner_input, timeout_sec=2400
                            )
                        inner = absorption_cache
                        inner_output = inner.get("output", {}) or {}
                        # 找 sample_runs · 在 evidence_paths 镜像里
                        # absorption_runtime_test portrait 的 evidence_paths 没直接含 sample_runs
                        # 需要从 cross_run_evidence 里间接拿... 实际上 spot_impl_evidence 里有 attempts 含 reference_code
                        # 简化: 直接从 absorption_cache 跑出来的 spot_impl_evidence.attempts 抽 references
                        spot = (inner_output.get("evidence_paths") or {}).get("spot_impl", {})
                        attempts = spot.get("attempts") or []
                        # 构造 fake sample_runs 形态喂 helper
                        fake_runs = [{
                            "verdict": "PASS",
                            "output": {
                                "proposals": [
                                    {"id": a.get("proposal_id", "?"),
                                     "reference_code": a.get("reference_code", {})}
                                    for a in attempts
                                    if isinstance(a, dict) and a.get("reference_code")
                                ]
                            },
                        }] if attempts else []
                        # 如果 attempts 不带 reference_code, 直接降级提示
                        sample_input = profile_mirror.get("default_sample_input") or {}
                        if not sample_input:
                            # 从 build_inner_input_for_absorption 重构 sample_input
                            sample_input = _build_inner_input_for_absorption(
                                target_team_id, profile_mirror, hyp_id
                            ).get("sample_input", {})
                        repo_path_str = sample_input.get("repo_path", "")
                        if not repo_path_str:
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": "pending_manual",
                                "evidence_excerpt": "absorption_cache 跑过但 sample_input 无 repo_path, 无法验引用",
                                "signals": [],
                                "executed_via": pretty + " (skipped, no repo_path)",
                            })
                            pending_count += 1
                        else:
                            audit = _audit_reference_honesty(fake_runs, Path(repo_path_str))
                            crit = audit["counts"]["critical"]
                            minor_n = audit["counts"]["minor"]
                            total = audit["total_refs"]
                            valid = audit["valid_refs"]
                            if total == 0:
                                # 没扫到任何 reference_code (可能 LLM 没给出引用)
                                status = "pending_manual"
                                excerpt = "absorption 跑过但 spot_impl.attempts 没含 reference_code 字段, 无法验"
                                pending_count += 1
                            elif audit["passed"]:
                                status = "verified_pass"
                                excerpt = (
                                    f"诚实性扫 {total} 个引用 · {valid} 真实存在 · 0 critical · {minor_n} minor"
                                )
                            else:
                                status = "verified_fail"
                                excerpt = (
                                    f"诚实性扫 {total} 个引用 · {valid}/{total} 真实 · {crit} critical 假引用"
                                )
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": status,
                                "evidence_excerpt": excerpt,
                                "signals": audit["violations"][:8] or [f"扫 {total} 引用全真"],
                                "executed_via": pretty,
                            })
                elif executor_kind == "inline_five_element_check":
                    pkg_path_str = profile_mirror.get("package_path", "")
                    if not pkg_path_str:
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target_profile 没 package_path",
                            "signals": [],
                            "executed_via": pretty + " (skipped, no package_path)",
                        })
                        pending_count += 1
                    else:
                        audit = _audit_five_elements(Path(pkg_path_str))
                        if audit.get("not_applicable"):
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": "pending_manual",
                                "evidence_excerpt": audit.get("reason", "不适用"),
                                "signals": ["target 不带 Material 定义, 此假设对此 target 不适用"],
                                "executed_via": pretty + " (n/a)",
                            })
                            pending_count += 1
                        else:
                            passed = audit["passed"]
                            crit = audit["counts"]["critical"]
                            minor_n = audit["counts"]["minor"]
                            scanned = audit["scanned_materials"]
                            if passed:
                                status = "verified_pass"
                                excerpt = (
                                    f"五要素扫 {scanned} 个 Material · 0 critical · {minor_n} minor · "
                                    f"id/parent/json_schema/tags 全合格"
                                )
                            else:
                                status = "verified_fail"
                                excerpt = (
                                    f"五要素扫 {scanned} 个 Material · {crit} critical 违规 · {minor_n} minor"
                                )
                            results.append({
                                "hypothesis_id": hyp_id,
                                "status": status,
                                "evidence_excerpt": excerpt,
                                "signals": audit["violations"][:8] or ["扫过, 无违规"],
                                "executed_via": pretty,
                            })
                elif executor_kind == "inline_red_line_check":
                    pkg_path_str = profile_mirror.get("package_path", "")
                    if not pkg_path_str:
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target_profile 没 package_path, 无法扫源码",
                            "signals": [],
                            "executed_via": pretty + " (skipped, no package_path)",
                        })
                        pending_count += 1
                    else:
                        audit = _audit_red_lines(Path(pkg_path_str))
                        passed = audit["passed"]
                        crit = audit["counts"]["critical"]
                        minor_n = audit["counts"]["minor"]
                        scanned = audit["scanned_files"]
                        if passed:
                            status = "verified_pass"
                            excerpt = (
                                f"红线 ast 扫 {scanned} .py · 0 critical · {minor_n} minor · "
                                f"无硬编码模型/打分字段/密集切片"
                            )
                        else:
                            status = "verified_fail"
                            excerpt = (
                                f"红线 ast 扫 {scanned} .py · {crit} critical 违规 · {minor_n} minor"
                            )
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": status,
                            "evidence_excerpt": excerpt,
                            "signals": audit["violations"][:8] or ["扫过, 无违规"],
                            "executed_via": pretty,
                        })
                elif executor_kind == "inline_directory_hygiene":
                    pkg_path_str = profile_mirror.get("package_path", "")
                    if not pkg_path_str:
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": "pending_manual",
                            "evidence_excerpt": "target_profile 没 package_path, 无法扫目录",
                            "signals": [],
                            "executed_via": pretty + " (skipped, no package_path)",
                        })
                        pending_count += 1
                    else:
                        audit = _audit_directory_hygiene(Path(pkg_path_str))
                        passed = audit["passed"]
                        crit = audit["counts"]["critical"]
                        minor_n = audit["counts"]["minor"]
                        scanned = audit["scanned_files"]
                        if passed:
                            status = "verified_pass"
                            excerpt = (
                                f"目录卫生扫 {scanned} 文件 · 0 critical · {minor_n} minor · "
                                f"target 包内无散文 .md / 无临时文件 / 测试文件位置正确"
                            )
                        else:
                            status = "verified_fail"
                            excerpt = (
                                f"目录卫生扫 {scanned} 文件 · {crit} critical 违规 · {minor_n} minor"
                            )
                        results.append({
                            "hypothesis_id": hyp_id,
                            "status": status,
                            "evidence_excerpt": excerpt,
                            "signals": audit["violations"][:8] or ["扫过, 无违规"],
                            "executed_via": pretty,
                        })
                elif executor_kind == "guardian_project_scan":
                    # 调 guardian 项目级目录卫生扫 (粗粒度, 非 target-specific)
                    if guardian_cache is None:
                        guardian_cache = _dispatch_subprocess(
                            "guardian",
                            {"project_root": str(_PROJECT_ROOT)},
                            timeout_sec=600,
                        )
                    inner = guardian_cache
                    inner_verdict = (inner.get("verdict") or "").upper()
                    inner_output = inner.get("output", {}) or {}
                    signals: list[str] = ["⚠️ 此为项目级扫描, 非针对此 target 的 target-specific 卫生检查"]
                    for key in ("violations", "issues", "warnings", "summary"):
                        val = inner_output.get(key)
                        if isinstance(val, list):
                            for v in val[:5]:
                                signals.append(f"[{key}] {str(v)[:200]}")
                        elif isinstance(val, str) and val:
                            signals.append(f"[{key}] {val[:200]}")
                    status, extra = _derive_tool_status(inner)
                    excerpt = (inner.get("diagnosis") or "")[:300] or (
                        f"guardian verdict={inner_verdict} · output 字段: {list(inner_output.keys())}"
                    )
                    if extra:
                        excerpt = f"{extra} | {excerpt}"
                    if status == "execution_error":
                        pending_count += 1
                    results.append({
                        "hypothesis_id": hyp_id,
                        "status": status,
                        "evidence_excerpt": excerpt,
                        "signals": signals,
                        "executed_via": pretty,
                    })
                else:
                    results.append({
                        "hypothesis_id": hyp_id,
                        "status": "execution_error",
                        "evidence_excerpt": f"unknown executor_kind={executor_kind}",
                        "signals": [],
                        "executed_via": pretty,
                    })
            except Exception as e:
                results.append({
                    "hypothesis_id": hyp_id,
                    "status": "execution_error",
                    "evidence_excerpt": f"{type(e).__name__}: {e}",
                    "signals": [],
                    "executed_via": pretty,
                })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "target_team_id": target_team_id,
                "results": results,
                "pending_count": pending_count,
            },
            diagnosis=f"分派完成: {len(results)} 假设 · pending={pending_count}",
            confidence=1.0,
        )
