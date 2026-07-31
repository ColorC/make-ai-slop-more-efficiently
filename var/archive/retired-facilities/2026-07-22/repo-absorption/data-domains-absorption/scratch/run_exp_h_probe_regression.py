# [OMNI] origin=claude-code purpose=exp-h ts=2026-04-15
"""Exp H: probe baseline 能否检测 DESCRIPTION 退化.

步骤:
  1. 正常 DESCRIPTION → 跑 probe → 记录 missing_info 数量 + 关键词
  2. 降质 DESCRIPTION ("处理数据") → 跑 probe → 对比变化
  3. 测量: missing_info 是否增加? sufficiency 是否下降? 关键词有变化吗?

额外测试:
  4. 恢复 DESCRIPTION + 增加 FORMAT 层信息 → probe 应"变好"

验证的假设: probe 能作为规范质量回归检测的自动 CI。
"""
import json, sys, os, time
sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict

FORMAT_IN = "absorption.module.code"
FORMAT_OUT = "absorption.learning"

DESCRIPTIONS = [
    ("GOOD — 当前真实描述",
     "V3 学习提炼：LLM 单次调用，分析模块代码，判断 what_it_does / delta / action / portability，产出带证据的发现"),
    ("BAD — 降质",
     "处理数据"),
    ("BETTER — 增强版（增加 G1-G7 定义引用 + portability 量表）",
     "V3 学习提炼：基于 G1-G7 缺口维度（G1工具鲁棒性/G2向外学习/…），对每个模块判断 "
     "what_it_does/omnicompany_delta/action 三要素；portability 按 directly_reusable "
     "(可直接移植) / worth_learning (需改造) / reference_only (仅参考) 三级评定。"
     "产出 JSON 含代码证据引用。"),
]


def probe_once(label: str, desc: str) -> dict:
    print(f"\n[probe] {label}", flush=True)
    t0 = time.time()
    report = run_info_audit_probe_strict(
        format_in=FORMAT_IN,
        format_out=FORMAT_OUT,
        description=desc,
    )
    elapsed = time.time() - t0
    n_missing = len(report.missing_info)
    n_critical = sum(1 for m in report.missing_info if m.critical)
    suff = report.sufficiency.value
    conf = report.confidence_self
    print(f"  sufficiency={suff} conf={conf:.2f} missing={n_missing} critical={n_critical} ({elapsed:.1f}s)")
    for m in report.missing_info[:3]:
        mark = "[!!]" if m.critical else "[..]"
        print(f"    {mark} {m.description[:90]}")
    return {
        "label": label,
        "description_len": len(desc),
        "sufficiency": suff,
        "confidence": conf,
        "n_missing": n_missing,
        "n_critical": n_critical,
        "missing": [{"desc": m.description[:100], "critical": m.critical} for m in report.missing_info],
        "concerns": report.concerns[:3],
        "elapsed_s": round(elapsed, 1),
    }


def main():
    print("=== Exp H: probe baseline 回归检测 ===")
    results = []
    for label, desc in DESCRIPTIONS:
        r = probe_once(label, desc)
        results.append(r)

    print("\n=== 回归对比表 ===")
    hdr = f"{'描述类型':25s} {'suff':12s} {'conf':6s} {'miss':5s} {'crit':5s}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        flag = "⚠" if r["n_critical"] > results[0]["n_critical"] else ("✓" if r["n_critical"] < results[0]["n_critical"] else "=")
        print(f"{r['label'][:25]:25s} {r['sufficiency']:12s} {r['confidence']:.2f}   {r['n_missing']:3d}   {r['n_critical']:3d}  {flag}")

    # 关键判断: BAD 的 missing 是否 > GOOD?
    good = results[0]
    bad = results[1]
    better = results[2]
    regression_detected = bad["n_missing"] > good["n_missing"] or bad["n_critical"] > good["n_critical"]
    improvement_detected = better["n_critical"] < good["n_critical"]
    print(f"\n结论:")
    print(f"  降质后 probe 报的问题增多: {'✓ 是' if regression_detected else '✗ 否'}")
    print(f"    (good: {good['n_missing']} missing/{good['n_critical']} crit → bad: {bad['n_missing']} missing/{bad['n_critical']} crit)")
    print(f"  增强后 probe 报的问题减少: {'✓ 是' if improvement_detected else '✗ 否 / 平行'}")

    out = "e:/WindowsWorkspace/omnicompany/data/domains/absorption/exp_h_probe_regression.json"
    open(out, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
