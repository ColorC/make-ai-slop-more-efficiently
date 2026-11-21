# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/scanners ts=2026-05-07T01:30:00Z type=router status=skeleton agent=ai-ide
# [OMNI] summary="客观测试设施扫描器 — 给定 team 路径扫现有 pytest/dogfood/playwright/conftest 输出验证设施清单 + Material 覆盖矩阵"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 6. 元诊断 10 问 #3-#6 (实际有没设施 / 验证什么 / material 覆盖 / 设施做得如何) 需要客观扫描数据, MetaDiagnosticAgent 调本扫描器拿事实"
# [OMNI] tags=scanner,objective,test-facility,coverage-matrix,no-llm
# [OMNI] material_id="material:diagnosis.doctor.scanners.test_facility_scanner.skeleton.py"
"""客观测试设施扫描器.

不用 LLM. 纯 Python 代码扫一个 team 目录:
- pytest test_*.py / *_test.py
- dogfood_*.py / dogfood/*.py
- playwright spec (*.spec.ts / *.spec.js)
- conftest.py (pytest 配置)
- .omni/test_*.yaml (om 框架声明的测试)

输出:
- 验证设施清单 (path / kind / 文件大小 / 测试函数数)
- 覆盖矩阵: team formats.py 输出的 Material × 现有测试是否提及 (按 grep 关键词)
- 缺失项: 列没被任何测试覆盖的 Material

供 MetaDiagnosticAgent 调用 (作工具) 或独立 CLI 跑.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class TestFacility:
    path: str             # 相对项目根
    kind: str             # pytest / dogfood / playwright / conftest / dot_omni / unknown
    size_bytes: int
    test_function_count: int = 0    # 通过 'def test_' 数
    has_red_green_baseline: bool = False  # 含 'red_' / 'green_' / 'baseline' / 'discriminat' 关键词
    has_assert_count: int = 0       # assert 语句数
    notes: str = ""


@dataclass
class CoverageMatrixRow:
    material_id: str
    covered_by: list[str] = field(default_factory=list)   # facility paths


@dataclass
class TestFacilityScanResult:
    team_path: str
    facilities: list[TestFacility] = field(default_factory=list)
    coverage_matrix: list[CoverageMatrixRow] = field(default_factory=list)
    uncovered_material_ids: list[str] = field(default_factory=list)
    overall_health_signals: dict = field(default_factory=dict)  # 简单 metric (不打分, 数事实)


class FacilityScanner:
    """扫一个 team 的现有验证设施.

    用法:
        scanner = FacilityScanner(project_root=Path("e:/.../omnicompany"))
        result = scanner.scan(team_path="src/omnicompany/packages/services/_utility/csv_to_md/")
        # result 是 TestFacilityScanResult dataclass
    """

    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root)

    def scan(self, team_path: str) -> TestFacilityScanResult:
        team_dir = self.project_root / team_path
        result = TestFacilityScanResult(team_path=team_path)

        if not team_dir.is_dir():
            result.overall_health_signals["error"] = f"team_path 不存在: {team_dir}"
            return result

        # ── 1. 扫现有设施 ──
        result.facilities = self._scan_facilities(team_dir)

        # ── 2. 扫 team formats.py 看输出哪些 Material ──
        material_ids = self._scan_material_outputs(team_dir)

        # ── 3. 立覆盖矩阵 ──
        for mid in material_ids:
            covered_by = self._find_coverage(mid, result.facilities)
            result.coverage_matrix.append(CoverageMatrixRow(material_id=mid, covered_by=covered_by))
            if not covered_by:
                result.uncovered_material_ids.append(mid)

        # ── 4. 整体健康信号 (数事实, 不打分) ──
        result.overall_health_signals = {
            "facility_count": len(result.facilities),
            "facility_kinds": list(set(f.kind for f in result.facilities)),
            "total_test_functions": sum(f.test_function_count for f in result.facilities),
            "total_asserts": sum(f.has_assert_count for f in result.facilities),
            "facilities_with_red_green": sum(1 for f in result.facilities if f.has_red_green_baseline),
            "material_count": len(material_ids),
            "uncovered_material_count": len(result.uncovered_material_ids),
            "coverage_ratio_str": (
                f"{len(material_ids) - len(result.uncovered_material_ids)}/{len(material_ids)}"
                if material_ids else "no-materials"
            ),
        }

        return result

    # ── helpers ──

    def _scan_facilities(self, team_dir: Path) -> list[TestFacility]:
        facilities: list[TestFacility] = []

        # pytest test_*.py / *_test.py 在 tests/ 或 团队根
        for pattern in ["tests/test_*.py", "tests/*_test.py", "test_*.py", "*_test.py"]:
            for p in team_dir.glob(pattern):
                if p.is_file():
                    facilities.append(self._analyze_py_facility(p, "pytest"))

        # dogfood_*.py / dogfood/*.py
        for pattern in ["dogfood_*.py", "dogfood/*.py"]:
            for p in team_dir.glob(pattern):
                if p.is_file():
                    facilities.append(self._analyze_py_facility(p, "dogfood"))

        # conftest.py
        for p in team_dir.glob("**/conftest.py"):
            if p.is_file():
                facilities.append(self._analyze_py_facility(p, "conftest"))

        # playwright spec
        for pattern in ["**/*.spec.ts", "**/*.spec.js", "**/*.test.ts", "**/*.test.js"]:
            for p in team_dir.glob(pattern):
                if p.is_file():
                    facilities.append(self._analyze_py_facility(p, "playwright"))

        # .omni/test_*.yaml (om 框架声明)
        omni_dir = team_dir / ".omni"
        if omni_dir.is_dir():
            for p in omni_dir.glob("test_*.yaml"):
                if p.is_file():
                    rel = str(p.relative_to(self.project_root)).replace("\\", "/")
                    facilities.append(TestFacility(
                        path=rel,
                        kind="dot_omni",
                        size_bytes=p.stat().st_size,
                    ))

        return facilities

    def _analyze_py_facility(self, p: Path, kind: str) -> TestFacility:
        """分析一份 .py 测试文件的关键 metric."""
        rel = str(p.relative_to(self.project_root)).replace("\\", "/")
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return TestFacility(path=rel, kind=kind, size_bytes=p.stat().st_size, notes="无法读取")

        # 测试函数数
        test_func_count = len(re.findall(r"^\s*def test_\w+\(", text, re.MULTILINE))
        # assert 数
        assert_count = len(re.findall(r"\bassert\b", text))
        # 红绿基线关键词
        has_rg = bool(re.search(r"red_|green_|baseline|discriminat|red.+green", text, re.IGNORECASE))

        return TestFacility(
            path=rel,
            kind=kind,
            size_bytes=p.stat().st_size,
            test_function_count=test_func_count,
            has_red_green_baseline=has_rg,
            has_assert_count=assert_count,
        )

    def _scan_material_outputs(self, team_dir: Path) -> list[str]:
        """扫 team 的 formats.py 看输出哪些 Material."""
        material_ids: list[str] = []
        for fmt_file in team_dir.glob("**/formats.py"):
            if not fmt_file.is_file():
                continue
            try:
                text = fmt_file.read_text(encoding="utf-8")
            except Exception:
                continue
            # 找 id="..." 类
            for m in re.finditer(r"\bid\s*=\s*[\"\']([\w.\-]+)[\"\']", text):
                mid = m.group(1)
                if "." in mid:  # Material id 含 . 命名空间
                    material_ids.append(mid)
        return list(dict.fromkeys(material_ids))  # 去重保序

    def _find_coverage(self, material_id: str, facilities: list[TestFacility]) -> list[str]:
        """看哪些 facility 提到 material_id (grep)."""
        covered: list[str] = []
        for f in facilities:
            try:
                text = (self.project_root / f.path).read_text(encoding="utf-8")
            except Exception:
                continue
            if material_id in text:
                covered.append(f.path)
        return covered


def scan_team_test_facilities(team_path: str, project_root: Path | str | None = None) -> dict:
    """便捷 API. 返回 dict (可序列化 yaml/json).

    Args:
        team_path: 相对项目根
        project_root: None 走 find-up 自动
    """
    if project_root is None:
        # find-up
        here = Path(__file__).resolve()
        for p in (here, *here.parents):
            if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
                project_root = p
                break
        if project_root is None:
            project_root = here.parents[6]
    scanner = FacilityScanner(project_root=project_root)
    result = scanner.scan(team_path)
    return asdict(result)
