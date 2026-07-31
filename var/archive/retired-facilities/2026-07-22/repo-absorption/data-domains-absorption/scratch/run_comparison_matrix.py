# [OMNI] origin=claude-code purpose=comparison-matrix ts=2026-04-15
"""4 机制 × 4 场景对比矩阵实验.

场景 (每个 = 一个已知缺陷):
  S1 input 缺 self_portrait (结构性字段缺失)
  S2 DESCRIPTION 降质 ("处理数据")
  S3 上游 self_portrait 降到 10 字 (上下文饥饿)
  S4 工具/DESCRIPTION 偏离 (原生 Exp B 场景: DESCRIPTION 未提 local_list)

机制:
  M-REQ     REQUIRED_CONTEXT 事前拦截
  M-PROBE   probe 启动期 baseline (独立 LLM 看 FORMAT)
  M-PIG     piggyback tool-path (自由文本节点)
  M-POST    post_hoc (节点执行后读真实上下文)
  M-CRY     crystallize (agent loop 完成后 → SpecPatch)

每次跑输出:
  detected: 是否检测到缺陷
  evidence: 检测到的具体证据
  cost:     LLM 调用次数 (若适用)
  specific: 是否给出可操作的修复指引
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")


def _fresh_env(**kwargs):
    """重置相关 env var (每个场景独立跑)."""
    for k in ["OMNICOMPANY_INFO_AUDIT", "OMNICOMPANY_CRYSTALLIZE"]:
        os.environ.pop(k, None)
    for k, v in kwargs.items():
        os.environ[k] = v


def _print_section(s: str) -> None:
    print(f"\n{'=' * 70}\n{s}\n{'=' * 70}")


# ─── Scenario 测试器 ────────────────────────────────────────────────────

async def scenario_S1_req_missing_field(mechanism: str) -> dict:
    """S1: input 缺 `self_portrait` 字段 (节点级测试, 不跑全管线)."""
    from dotenv import load_dotenv
    load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

    # 根据机制开启不同开关
    if mechanism == "M-REQ":
        # 直接测 _check_required_context (节点级, 不跑全管线)
        from omnicompany.runtime.exec.runner import _check_required_context
        t0 = time.time()
        # 模拟: runner 给 learning_extractor 准备的 input_data 里没 self_portrait
        simulated_input = {
            "repo_name": "hermes-agent",
            "module_readings": [{"path": "x", "content": "..."}],
            # "self_portrait": 缺
        }
        required = ["self_portrait"]  # 假设给 LearningExtractor 加了声明
        missing = _check_required_context(simulated_input, required)
        elapsed = time.time() - t0
        detected = bool(missing)
        return {
            "mechanism": mechanism, "scenario": "S1",
            "detected": detected,
            "evidence": f"missing_required_context={missing}",
            "elapsed_s": round(elapsed, 3), "llm_calls": 0,
            "specific": detected,
        }

    elif mechanism == "M-POST":
        from omnicompany.packages.services.absorption.routers.learning_extractor import (
            LearningExtractorRouter,
        )
        # 直接调 probe, 模拟 post_hoc 看到的真实 prompt (含空 self_portrait)
        from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict
        t0 = time.time()
        # 模拟 learning_extractor 实际会收到的 user_msg: self_portrait 为空
        report = run_info_audit_probe_strict(
            format_in="absorption.module.code",
            format_out="absorption.learning",
            description=LearningExtractorRouter.DESCRIPTION,
            original_system="你是 Omnicompany 的学习分析师. 对每个模块判断 Omnicompany 能从中学到什么.",
            original_user_preview=(
                "# 学习提炼任务\n**Repo**: hermes-agent\n**模块数量**: 20 个\n\n"
                "## Omnicompany 自画像 (G1-G7 缺口)\n\n\n\n---\n\n"  # self_portrait 为空
                "## 模块代码\n\n### [P0] G1 — agent/error_classifier.py\n\n```python\ndef classify(err): ..."
            ),
            original_response_preview='{"findings": [{"gap_id": "G1", ...}]}',
        )
        elapsed = time.time() - t0
        concerns_missing = str(report.concerns) + " | " + str([m.description for m in report.missing_info])
        detected = any(kw in concerns_missing for kw in [
            "self_portrait", "自画像", "portrait", "缺口", "G1-G7", "缺口定义",
        ])
        return {"mechanism": mechanism, "scenario": "S1",
                "detected": detected,
                "evidence": f"suff={report.sufficiency.value} missing={len(report.missing_info)}. " + concerns_missing[:200],
                "elapsed_s": round(elapsed, 1), "llm_calls": 1,
                "specific": detected}

    elif mechanism == "M-PROBE":
        # probe 只看 FORMAT 描述, 不接触 input, 所以不应检测到 input 缺失
        from omnicompany.runtime.info_audit.startup_baseline import run_pipeline_probe_baseline
        from omnicompany.packages.services.absorption.pipeline import build_v3_pipeline
        pipeline = build_v3_pipeline()
        t0 = time.time()
        result = run_pipeline_probe_baseline(
            pipeline, include_kinds=("ANCHOR",), node_filter=["learning_extractor"],
        )
        elapsed = time.time() - t0
        r = result["per_node"].get("learning_extractor", {})
        concerns_and_missing = str(r.get("missing_info", "")) + str(r.get("concerns", ""))
        detected = "self_portrait" in concerns_and_missing or "自画像" in concerns_and_missing
        return {"mechanism": mechanism, "scenario": "S1",
                "detected": detected, "evidence": concerns_and_missing[:250],
                "elapsed_s": round(elapsed, 1), "llm_calls": 1,
                "specific": detected}

    else:
        return {"mechanism": mechanism, "scenario": "S1", "detected": False, "evidence": "N/A for this scenario"}


async def scenario_S2_degraded_description(mechanism: str) -> dict:
    """S2: 把 LearningExtractor 的 DESCRIPTION 改成 "处理数据" 这种无意义描述.

    probe 只看 FORMAT 描述 → 应该最敏感
    其他机制: post_hoc 看真实 prompt, 不一定察觉
    """
    from omnicompany.packages.services.absorption.routers.learning_extractor import (
        LearningExtractorRouter,
    )
    old_desc = LearningExtractorRouter.DESCRIPTION
    LearningExtractorRouter.DESCRIPTION = "处理数据"

    try:
        if mechanism == "M-PROBE":
            # 直接调 probe, 手传降质后的 description (绕过 pipeline 构造时固定的 anchor.validator.description)
            from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict
            t0 = time.time()
            report = run_info_audit_probe_strict(
                format_in="absorption.module.code",
                format_out="absorption.learning",
                description="处理数据",  # 刻意降质
            )
            elapsed = time.time() - t0
            concerns_missing = str(report.concerns) + str([m.description for m in report.missing_info])
            # 降质后应该有更多 critical missing / suff 更低
            n_missing = len(report.missing_info)
            n_critical = sum(1 for m in report.missing_info if m.critical)
            # "detected" 判定: missing_info 非空 + 提到 "描述" / "任务" / 空泛
            detected = n_missing >= 2 or ("描述" in concerns_missing and "不" in concerns_missing)
            return {"mechanism": mechanism, "scenario": "S2",
                    "detected": detected,
                    "evidence": f"sufficiency={report.sufficiency.value}, missing={n_missing}, critical={n_critical}. " + concerns_missing[:200],
                    "elapsed_s": round(elapsed, 1), "llm_calls": 1,
                    "specific": detected}
        elif mechanism == "M-POST":
            # post_hoc 对 "处理数据" 这种描述的 insight
            from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict
            t0 = time.time()
            # 模拟: post_hoc 里用真实上下文 + 降质描述
            report = run_info_audit_probe_strict(
                format_in="absorption.module.code",
                format_out="absorption.learning",
                description="处理数据",
                original_system="提炼学习发现 (模拟真 prompt)",
                original_user_preview="[{'path': 'a.py', 'content': 'def foo(): pass'}]",
                original_response_preview='{"findings": [...]}',
            )
            elapsed = time.time() - t0
            n_missing = len(report.missing_info)
            detected = n_missing >= 1
            return {"mechanism": mechanism, "scenario": "S2",
                    "detected": detected,
                    "evidence": f"sufficiency={report.sufficiency.value}, missing={n_missing}. " + str(report.concerns)[:200],
                    "elapsed_s": round(elapsed, 1), "llm_calls": 1,
                    "specific": detected}
        elif mechanism == "M-REQ":
            return {"mechanism": mechanism, "scenario": "S2",
                    "detected": False,
                    "evidence": "REQUIRED_CONTEXT 不检查 DESCRIPTION 质量", "elapsed_s": 0, "llm_calls": 0}
        else:
            return {"mechanism": mechanism, "scenario": "S2", "detected": False, "evidence": "N/A"}
    finally:
        LearningExtractorRouter.DESCRIPTION = old_desc


async def scenario_S3_truncated_upstream(mechanism: str) -> dict:
    """S3: self_portrait 降到 10 字 (上下文饥饿, 节点级测试)."""
    if mechanism == "M-POST":
        from omnicompany.packages.services.absorption.routers.learning_extractor import (
            LearningExtractorRouter,
        )
        from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict
        t0 = time.time()
        # 模拟极短 self_portrait
        report = run_info_audit_probe_strict(
            format_in="absorption.module.code",
            format_out="absorption.learning",
            description=LearningExtractorRouter.DESCRIPTION,
            original_system="你是 Omnicompany 的学习分析师. 对每个模块判断 Omnicompany 能从中学到什么.",
            original_user_preview=(
                "# 学习提炼任务\n**Repo**: hermes-agent\n**模块数量**: 20 个\n\n"
                "## Omnicompany 自画像 (G1-G7 缺口)\n\n缺口很多\n\n---\n\n"  # self_portrait 极短
                "## 模块代码\n\n### [P0] G1 — agent/error_classifier.py\n\n```python\ndef classify(err): ..."
            ),
            original_response_preview='{"findings": []}',
        )
        elapsed = time.time() - t0
        all_txt = str(report.concerns) + " | " + str([m.description for m in report.missing_info])
        detected = any(kw in all_txt for kw in [
            "简略", "稀薄", "不充分", "过短", "不完整", "缺口很多", "自画像", "G1-G7 的定义", "具体内容",
            "partial", "insufficient",
        ]) or report.sufficiency.value in ("partial", "insufficient")
        return {"mechanism": mechanism, "scenario": "S3",
                "detected": detected,
                "evidence": f"suff={report.sufficiency.value} missing={len(report.missing_info)}. " + all_txt[:200],
                "elapsed_s": round(elapsed, 1), "llm_calls": 1, "specific": detected}

    elif mechanism == "M-PROBE":
        return {"mechanism": mechanism, "scenario": "S3",
                "detected": False,
                "evidence": "probe 不接触真实 input, 结构上不应检测", "elapsed_s": 0, "llm_calls": 0}

    elif mechanism == "M-REQ":
        return {"mechanism": mechanism, "scenario": "S3",
                "detected": False,
                "evidence": "REQUIRED_CONTEXT 只检查字段存在性, 不检查内容质量", "elapsed_s": 0, "llm_calls": 0}

    else:
        return {"mechanism": mechanism, "scenario": "S3", "detected": False, "evidence": "N/A"}


def _print_row(r: dict) -> None:
    mark = "✓" if r.get("detected") else "·"
    spec = "(specific)" if r.get("specific") else ""
    print(f"  {mark} {r['mechanism']:10s} {spec:11s} cost={r.get('llm_calls','?'):>3}  t={r.get('elapsed_s','?'):>5}s  ev: {r.get('evidence','')[:120]}")


async def main():
    results = []

    _print_section("S1 · input 缺 self_portrait  (结构性字段缺失)")
    for m in ["M-REQ", "M-PROBE", "M-POST"]:
        r = await scenario_S1_req_missing_field(m)
        _print_row(r)
        results.append(r)

    _print_section("S2 · DESCRIPTION 降质为 '处理数据'")
    for m in ["M-REQ", "M-PROBE", "M-POST"]:
        r = await scenario_S2_degraded_description(m)
        _print_row(r)
        results.append(r)

    _print_section("S3 · self_portrait 上下文饥饿 (10 字)")
    for m in ["M-REQ", "M-PROBE", "M-POST"]:
        r = await scenario_S3_truncated_upstream(m)
        _print_row(r)
        results.append(r)

    _print_section("矩阵总览")
    out = Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/comparison_matrix.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  written: {out}")


if __name__ == "__main__":
    asyncio.run(main())
