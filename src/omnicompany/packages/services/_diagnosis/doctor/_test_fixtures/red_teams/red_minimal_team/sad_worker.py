# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/_test_fixtures/red_teams ts=2026-05-07T01:55:00Z type=fixture status=active agent=ai-ide
# [OMNI] summary="红 team fixture 内的 worker — 故意违反多条铁律, 用于 MetaDiagnosticAgent 红绿对比 (跟 csv_to_md 绿 team 对照)"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 9 红绿基线"
# [OMNI] tags=test-fixture,red-sample,team,non-compliant
# [OMNI] material_id="material:diagnosis.doctor.test_fixtures.red_teams.red_minimal_team.sad_worker.py"
"""
红 team fixture worker — 故意违反多条铁律.

⚠️ 不是真 worker, 不挂 dispatcher, 仅供 MetaDiagnosticAgent 红绿对比用.

跟 csv_to_md 绿 team 对照. 故意特征:
- DESCRIPTION 太短 (违反 R-01)
- 没 FORMAT_OUT (违反 R-02)
- 直接 import openai (违反 R-04)
- run() 不是函数式, 写文件副作用直接绕过 meta_io (违反 R-06)
- Verdict.diagnosis 'OK' 空话 (违反 R-14)
- output 嵌套 (违反 R-23)
- run() 100+ 行 (违反 R-10)
- 硬编码 'C:/sad/output.txt' (违反铁律)
- 预防截断 input[:1000] (违反铁律 A)

team 整体故意特征:
- 无 DESIGN.md / formats.py / team.py / .omni/manifest.yaml / __init__.py
- 单个 .py 自己一堆问题
- MetaDiagnosticAgent 应识别这是个 'unhealthy team' 跟 csv_to_md 形成判别力对比
"""
from __future__ import annotations

# R-04 违反: 直接 import openai 不走 LLMClient
import openai  # noqa: F401


class SadWorker:  # 没继承 Worker
    DESCRIPTION = "干事"  # R-01 违反: 太短
    FORMAT_IN = "sad.input"
    # R-02 违反: 没 FORMAT_OUT

    def run(self, input_data):
        _ = openai
        # R-06 违反: 直接 open() 写文件不走 meta_io
        # 硬编码绝对路径
        try:
            with open("C:/sad/output.txt", "w", encoding="utf-8") as f:
                # 铁律 A 违反: 预防截断
                truncated = str(input_data)[:1000]
                f.write(truncated)
        except Exception:
            pass

        # 大量没意义的代码堆塞 (违反 R-10 行数限制 + 副作用堆积)
        x = 1
        for i in range(50):
            x += i
        results = []
        for j in range(20):
            results.append(j * 2)
        # ... 故意堆下去
        useless = [k for k in range(100) if k % 3 == 0]
        more = sum(useless)

        # R-14 违反: diagnosis 空话
        # R-23 违反: output 嵌套
        # 不返 Verdict 用普通 dict 假装
        return {
            "kind": "PASS",
            "diagnosis": "OK",  # 空话
            "output": {  # 嵌套
                "sad.output": {
                    "data": {
                        "value": more,
                    },
                },
            },
        }
