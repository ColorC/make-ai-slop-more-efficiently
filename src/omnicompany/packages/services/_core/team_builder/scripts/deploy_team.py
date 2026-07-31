# [OMNI] origin=claude-code domain=services/team_builder/scripts ts=2026-04-25T00:00:00Z type=tool
# [OMNI] material_id="material:core.team_builder.deployment_script.e2e_deploy.py"
"""Deploy 脚本 · 跑 team-builder → 产 registration_plan → 真落盘 + 追加 pipelines.py + smoke.

用法:
    python -m omnicompany.packages.services._core.team_builder.scripts.deploy_team --text "需求描述"

注: 2026-04-25 从 data/domains/team_builder/scratch/ 挪到此正规位置. retry 反馈/产物隔离/事件总线日志正在重构 (TEAM-BUILDER-REAL-PASS).

执行步骤:
    1. 调 team-builder 跑 E2E · 拿 registration_plan + code_package
    2. py_compile 再确认 (已由 Registrar 做, 这里兜底)
    3. DiskBus.write 所有 files 到 target_package_path
    4. 追加 pipeline_entry_code 到 core/pipelines.py (在 register_all 末尾前)
    5. 真 import + build_team/build_bindings smoke
    6. 若 smoke 失败 · 自动 rollback (删 src/ + 从 pipelines.py 撤回追加)

按 feedback_100pct_required_goes_to_skeleton · 骨架接管"部署前验证":
    - py_compile (Registrar 已做)
    - import smoke (本脚本做)
    - 失败 → 自动 rollback (不留破代码)
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import shutil
import sys
import traceback
from pathlib import Path

_ANCHOR = '    logger.debug("register_all: done")'


def _find_existing_entry(pipelines_text: str, name: str) -> bool:
    return f'name="{name}"' in pipelines_text


def _append_to_pipelines(pipeline_entry_code: str, team_name_cli: str) -> str:
    """把 pipeline_entry_code 追加到 core/pipelines.py · 返回 rollback 用的原文本."""
    from omnicompany.runtime.buses import DiskBus
    pipelines_path = Path("src/omnicompany/core/pipelines.py")
    original = pipelines_path.read_text(encoding="utf-8")
    if _find_existing_entry(original, team_name_cli):
        print(f"[skip] pipeline entry name={team_name_cli!r} 已存在, 不重复追加")
        return original
    if _ANCHOR not in original:
        raise RuntimeError(f"在 pipelines.py 找不到 anchor {_ANCHOR!r}")
    new_text = original.replace(_ANCHOR, pipeline_entry_code + "\n" + _ANCHOR, 1)
    # 走 DiskBus 真写 (workspace 宽松 · 强制 atomic)
    bus = DiskBus()
    bus.write(pipelines_path, new_text, atomic=True)
    print(f"[OK] 追加 pipeline entry 到 {pipelines_path} (+{len(new_text)-len(original)} chars)")
    return original


def _write_files(files: dict[str, str], target_path: str) -> Path:
    from omnicompany.runtime.buses import DiskBus
    root = Path(target_path.rstrip("/"))
    if root.exists():
        # 容忍 Guardian 自动 backfill 的 .omni/manifest.yaml 残留 (DISTRIBUTED-MANIFESTS-BACKFILL)
        # 若目录只含 .omni/ · 允许覆盖
        extant = [p for p in root.rglob("*") if p.is_file()]
        only_guardian_manifest = all(
            str(p.relative_to(root)).startswith(".omni") for p in extant
        )
        if not only_guardian_manifest:
            raise RuntimeError(f"target path 已存在且含业务文件, 拒覆盖: {root} · 手工清理后重试")
        print(f"[skip-clean] target {root} 只含 Guardian manifest, 就地合并")
    bus = DiskBus()
    for rel, content in files.items():
        p = root / rel
        bus.write(p, content, atomic=True)
    print(f"[OK] 写入 {len(files)} 文件到 {root}")
    return root


def _smoke_import(team_name: str) -> tuple[bool, str]:
    """真 import + build_team + build_bindings + 每 Worker 实例化."""
    for k in list(sys.modules):
        if team_name in k: del sys.modules[k]
    try:
        mod = importlib.import_module(f"omnicompany.packages.services.{team_name}")
        fmt = importlib.import_module(f"omnicompany.packages.services.{team_name}.formats")
        team_mod = importlib.import_module(f"omnicompany.packages.services.{team_name}.team")
        run_mod = importlib.import_module(f"omnicompany.packages.services.{team_name}.run")
    except Exception as e:
        return False, f"import: {type(e).__name__}: {e}\n{traceback.format_exc()[:500]}"

    try:
        from omnicompany.protocol.format import create_builtin_registry
        reg = create_builtin_registry()
        fmt.register_formats(reg)
    except Exception as e:
        return False, f"register_formats: {type(e).__name__}: {e}"

    try:
        team = team_mod.build_team()
        bindings = run_mod.build_bindings()
    except Exception as e:
        return False, f"build_team/bindings: {type(e).__name__}: {e}"

    if not bindings:
        return False, "bindings 为空"
    for n, w in bindings.items():
        if not hasattr(w, "run"):
            return False, f"worker {n} 无 run()"
    return True, f"OK · {len(team.nodes)} nodes / {len(team.edges)} edges / {len(bindings)} workers"


def _run_acceptance_tests(team_name: str) -> tuple[bool, str]:
    """真黑盒 gate: 跑 tests/teams/<team>/ 下所有合同测试. 任一 FAIL → rollback.

    这是 plan TEAM-BUILDER-REAL-PASS §0 铁律的实装:
    "做出来的工作流要能实实在在实现需求 · 不降门槛 · 最终黑盒实跑"
    """
    test_dir = Path(f"tests/teams/{team_name}")
    if not test_dir.exists():
        return True, f"skip · 无 tests/teams/{team_name}/ (requirement 未建档的 team 不作硬 gate)"
    import subprocess
    import os
    cmd = [sys.executable, "-m", "pytest", str(test_dir), "--team-mode=subprocess", "-v", "--tb=short"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace", env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "acceptance pytest timeout (>10 min)"
    if proc.returncode == 0:
        last = proc.stdout.strip().splitlines()[-1] if proc.stdout else ""
        return True, f"acceptance PASS · {last[:120]}"
    # Fail: dump last 30 lines of output for diagnosis
    tail = "\n".join((proc.stdout or "").splitlines()[-30:])
    return False, f"acceptance FAIL (rc={proc.returncode}) · tail:\n{tail[:2000]}"


def _rollback(pkg_root: Path | None, pipelines_original: str | None):
    from omnicompany.runtime.buses import DiskBus
    if pkg_root and pkg_root.exists():
        shutil.rmtree(pkg_root)
        print(f"[rollback] 删除 {pkg_root}")
    if pipelines_original is not None:
        DiskBus().write(Path("src/omnicompany/core/pipelines.py"), pipelines_original, atomic=True)
        print("[rollback] 恢复 pipelines.py 原文本")


async def main(text: str):
    # 1. 调 team-builder 走全 E2E (用 dispatch 走 SQLiteBus 真记录 events)
    from omnicompany.core.dispatch import dispatch
    from omnicompany.core.registry import discover
    from omnicompany.protocol.anchor import Verdict as _Verdict
    discover()  # 注册所有 pipelines (含 team-builder)
    print(f"[start] team-builder · input={text!r}")
    result = await dispatch("team-builder", {"text": text}, max_steps=1000)
    # dispatch 返回: Verdict 对象 (有 kind/output) 或直接 sink material dict
    if isinstance(result, _Verdict):
        print(f"[team-builder] verdict kind={result.kind}")
        if result.kind.value != "pass":
            print(f"[FAIL] team-builder: {result.diagnosis}")
            return 1
        reg_plan = result.output
    elif isinstance(result, dict):
        print(f"[team-builder] result is sink material dict (len={len(result)} keys)")
        reg_plan = result
    else:
        print(f"[FAIL] dispatch 返回未知类型: {type(result)} · {result!r:.200}")
        return 1
    if not isinstance(reg_plan, dict):
        print(f"[FAIL] registration_plan 非 dict: {type(reg_plan)}")
        return 2

    team_name = reg_plan.get("team_name")
    target_path = reg_plan.get("target_package_path")
    files_to_write = reg_plan.get("files_to_write") or []
    pipeline_entry_code = reg_plan.get("pipeline_entry_code") or ""
    if not (team_name and target_path and files_to_write and pipeline_entry_code):
        print(f"[FAIL] registration_plan 字段不齐: {list(reg_plan.keys())}")
        return 3

    # V3.2 (2026-04-24): Registrar.output['files'] 已保留完整 rel_path→content dict, 直接用
    files_dict = reg_plan.get("files") or {}
    if not isinstance(files_dict, dict) or not files_dict:
        # V3.1 兼容兜底 · 回退到 audit scrape
        print("[warn] reg_plan['files'] 缺失 · 回退 audit scrape (V3.1 兼容)")
        import re
        audit_dir = Path("data/_runtime/llm_audit")
        latest_audit = sorted([p for p in audit_dir.glob("*/*.jsonl") if p.name != "adhoc.jsonl"],
                              key=lambda p: p.stat().st_mtime)[-1]
        files_dict = {}
        _FILE_RE = re.compile(r"===FILE:\s*([^=\n]+?)\s*===\s*\n(.*?)\n===END===", re.DOTALL)
        with latest_audit.open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                try: d = json.loads(ln)
                except: continue
                if not d.get("caller","").startswith("CodeGeneratorLoopWorker"): continue
                for tc in (d.get("tool_calls") or []):
                    if tc.get("name") == "finish":
                        r = (tc.get("input") or {}).get("result")
                        if isinstance(r, str) and len(r) > 1000:
                            tmp = {m.group(1).strip(): m.group(2) for m in _FILE_RE.finditer(r)}
                            if tmp: files_dict = tmp
        if not files_dict:
            print("[FAIL] 既未从 reg_plan['files'] 也未从 audit 找到 files_dict")
            return 4
    print(f"[captured] {len(files_dict)} files from reg_plan['files']")

    # 2. 真落盘
    pkg_root = None; pipelines_original = None
    try:
        pkg_root = _write_files(files_dict, target_path)
        cli_name = team_name.replace("_", "-")
        pipelines_original = _append_to_pipelines(pipeline_entry_code, cli_name)
    except Exception as e:
        print(f"[FAIL] 落盘: {type(e).__name__}: {e}")
        _rollback(pkg_root, pipelines_original)
        return 5

    # 3. smoke import
    ok, msg = _smoke_import(team_name)
    if not ok:
        print(f"[FAIL] smoke: {msg}")
        _rollback(pkg_root, pipelines_original)
        return 6
    print(f"[OK] smoke: {msg}")

    # 4. acceptance gate · 真跑合同测试 (任一 FAIL → rollback)
    print(f"[...] acceptance: running pytest tests/teams/{team_name}/ ...")
    ok, msg = _run_acceptance_tests(team_name)
    if not ok:
        print(f"[FAIL] acceptance: {msg}")
        _rollback(pkg_root, pipelines_original)
        return 7
    print(f"[OK] {msg}")

    print(f"\n=== DEPLOY OK · team={team_name} · CLI: omni run {team_name.replace('_','-')} ===")
    return 0


async def main_with_retry(text: str, max_retries: int = 3) -> int:
    """主流程 + deploy-gate retry with feedback (L1 铁律步骤 4 的实装).

    可重试情况:
      - rc=7 (acceptance pytest FAIL): 抓 pytest 输出作反馈
      - RuntimeError("Pipeline halted at 'code_reviewer': ...") : 抓 CR 诊断作反馈
      - 其他异常: 不 retry
    """
    attempt = 1
    augmented_text = text
    while attempt <= max_retries:
        print(f"\n{'='*60}\n  deploy-gate attempt {attempt}/{max_retries}\n{'='*60}\n")
        failure_source = None  # "acceptance" | "code_reviewer" | None
        failure_detail = ""
        try:
            rc = await main(augmented_text)
            if rc == 0:
                print(f"\n=== SUCCESS on attempt {attempt} ===")
                return 0
            if rc == 7:
                failure_source = "acceptance"
                # 从最新 log 抓 acceptance tail
                runs_dir = Path("data/domains/team_builder/runs")  # 旧路径已挪 · 后续走事件总线
                logs = sorted(runs_dir.glob("csv_to_md_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                if logs:
                    raw = logs[0].read_text(encoding="utf-8", errors="replace")
                    if "[FAIL] acceptance:" in raw:
                        idx = raw.rfind("[FAIL] acceptance:")
                        failure_detail = raw[idx:idx + 2500]
            else:
                print(f"\n=== terminal FAIL (rc={rc}) · no retry ===")
                return rc
        except RuntimeError as e:
            msg = str(e)
            if "Pipeline halted at 'code_reviewer'" in msg or "Pipeline halted at 'code_aggregator'" in msg:
                failure_source = "code_reviewer"
                failure_detail = msg[:2500]
                print(f"\n[retry] attempt {attempt} halted at pipeline gate · will retry with feedback")
            else:
                print(f"\n=== non-retriable RuntimeError: {msg[:300]} ===")
                raise
        except Exception as e:  # noqa: BLE001
            print(f"\n=== non-retriable exception {type(e).__name__}: {e} ===")
            raise

        # 拼反馈进下轮
        feedback_header = "acceptance pytest FAIL" if failure_source == "acceptance" else "CodeReviewer (team_builder 内) FAIL"
        augmented_text = text + f"""

---

## ⚠️ 上次 ({attempt} / {max_retries}) 尝试失败 · 阶段: {feedback_header}

```
{failure_detail[:2000]}
```

**务必这次修正**:
- 字段名 / 字段值不一致 · 字面一致 (schema required 字段 Worker 代码 Verdict.output 必须都有)
- 边界 case (空行 / 特殊字符 / 错误路径) 必须覆盖
- 末尾 `\\n` / 空格 / `<br>` 等字面细节逐字节对齐预期
- Worker 读 input 的 key 必须 = FORMAT_IN Material schema.required 字段名
"""
        attempt += 1
    print(f"\n=== FAIL after {max_retries} attempts ===")
    return 7


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True, help="team 需求描述")
    parser.add_argument("--max-retries", type=int, default=3, help="acceptance FAIL 时最多重试次数")
    parser.add_argument("--no-retry", action="store_true", help="禁用 retry (单跑)")
    args = parser.parse_args()
    if args.no_retry:
        sys.exit(asyncio.run(main(args.text)))
    else:
        sys.exit(asyncio.run(main_with_retry(args.text, args.max_retries)))
