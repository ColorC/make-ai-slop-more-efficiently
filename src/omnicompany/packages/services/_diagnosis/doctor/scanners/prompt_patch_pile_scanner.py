# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/scanners ts=2026-05-07T08:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="prompt patch-pile 扫描器 — 客观代码扫 *_prompt.md 数 AP-024 (patch-pile-antipattern) 3 类信号 (枚举数 / specific 引用 / 补丁标)"
# [OMNI] why="anti_patterns/archetypes.yaml AP-024 typical_fix='立 Guardian 规则扫 prompt 反模式'. V1 留议 — 实施 detection_strategy 客观可测部分. 不裁决好坏, 只数信号给调用方决定阈值"
# [OMNI] tags=scanner,prompt-patch-pile,anomaly-detection,no-llm,AP-024
# [OMNI] material_id="material:diagnosis.doctor.scanners.prompt_patch_pile_scanner.py"
"""Prompt patch-pile 扫描器 (客观代码, 不用 LLM).

跟 work_pattern_scanner.py + facility_scanner.py 同模式 — 客观代码扫 prompt md 文件
数 AP-024 (patch-pile-antipattern) 的 3 类信号:

1. enumeration_count: 反例/枚举类标记 (例 N / 示例 N / 反例 / ❌ / ✗) 出现行数
   AP-024 关联反模式: 笨拙枚举 — prompt 反复加"反例: ..."补丁
2. specific_reference_count: 具体路径/类名引用 (含 .py/.md/PascalCase+Class/Agent/Worker/Router/Tool/Format/Builder)
   AP-024 关联反模式: 过时具体 — prompt 引具体符号容易过时
3. patch_marker_count: 显式补丁标 (TODO / FIXME / HACK / XXX / 补丁 / 修补)
   AP-024 关联反模式: 上下文污染 — TODO/FIXME 未清就堆 prompt 里

V0 不阈值裁决. 输出每份 prompt 的 3 信号 count + line numbers + 前 5 命中 sample,
调用方 (MetaDiagnosticAgent / Guardian / CI) 决定怎么用阈值.

⚠️ V0 已知缺陷 (跟 work_pattern_scanner 同类问题): 信号 ≠ 反模式真存在.
- 高 specific_reference_count 也可能是合理引用 (例 prompt 解释 'SubmitRouter' 怎么用)
- 高 enumeration_count 也可能是合理 few-shot (例 真合规 finding 例)
- 真判 patch-pile 需要看历史 (commit log 看 prompt 文件多次小补丁)
信号本身不裁决, 是给上层 agent 跟历史信号 (work_pattern_scanner) 综合判.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── 信号正则 (字面匹配, 不正则回溯) ─────────────────────────────────

# enumeration: 中文枚举跟反例标
_ENUMERATION_PATTERNS = [
    re.compile(r"例\s*\d+\s*[:：]"),       # "例 1:" "例1：" "例 N:"
    re.compile(r"示例\s*\d+\s*[:：]"),     # "示例 1:" "示例1："
    re.compile(r"反例\s*\d*\s*[:：]"),     # "反例:" "反例 1:"
    re.compile(r"^\s*[❌✗]\s+"),           # 行首 ❌ / ✗
    re.compile(r"反模式\s*\d+\s*[:：]"),   # "反模式 1:"
]

# specific_reference: 具体路径 / PascalCase + 后缀
_SPECIFIC_PATTERNS = [
    re.compile(r"[\w/\\.-]+\.(py|md|yaml|yml|json|toml)\b"),  # 含扩展名的具体路径
    re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:Class|Agent|Worker|Router|Tool|Format|Builder|Scanner)\b"),  # PascalCase + 后缀
]

# patch_marker: 显式补丁标
# XXX 用 (?<![\w-])XXX(?![\w-]) 避开 AP-XXX / -XXX- 等反模式占位符假阳性
# (2026-05-07 dogfood 6 旧元诊断 prompt 时发现 — "引 AP-XXX 反模式" 6 处被 \bXXX\b 误命中)
_PATCH_MARKERS = [
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bHACK\b", re.IGNORECASE),
    re.compile(r"(?<![\w-])XXX(?![\w-])"),  # 独立 XXX, 不命中 AP-XXX / -XXX
    re.compile(r"补丁"),
    re.compile(r"修补"),
]


@dataclass
class SignalHit:
    """一处命中信号."""
    line_number: int   # 1-indexed
    line_text: str     # 命中行原文 (前 200 字符)
    matched_pattern: str  # 命中正则的字面 (debug 用)


@dataclass
class PromptPatchPileSignal:
    """一份 prompt 的 3 类信号 count + 命中样例."""
    prompt_path: str
    line_count: int
    enumeration_count: int = 0
    specific_reference_count: int = 0
    patch_marker_count: int = 0
    enumeration_hits: list[SignalHit] = field(default_factory=list)      # 前 5 命中
    specific_reference_hits: list[SignalHit] = field(default_factory=list)
    patch_marker_hits: list[SignalHit] = field(default_factory=list)

    @property
    def total_signals(self) -> int:
        return self.enumeration_count + self.specific_reference_count + self.patch_marker_count


@dataclass
class PromptPatchPileScanResult:
    scanned_count: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    signals: list[PromptPatchPileSignal] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.signals:
            return f"scanned {self.scanned_count}, no signals"
        avg_enum = sum(s.enumeration_count for s in self.signals) / len(self.signals)
        avg_ref = sum(s.specific_reference_count for s in self.signals) / len(self.signals)
        avg_patch = sum(s.patch_marker_count for s in self.signals) / len(self.signals)
        return (
            f"scanned {self.scanned_count}, skipped {len(self.skipped)} | "
            f"avg enum={avg_enum:.1f} ref={avg_ref:.1f} patch={avg_patch:.1f}"
        )


def _count_pattern_hits(line: str, patterns: list[re.Pattern]) -> tuple[int, str | None]:
    """一行命中 patterns 返 (命中数, 第一个命中正则字面). 没命中返 (0, None)."""
    total = 0
    first_match: str | None = None
    for pat in patterns:
        matches = pat.findall(line)
        if matches:
            total += len(matches)
            if first_match is None:
                first_match = pat.pattern
    return total, first_match


class PromptPatchPileScanner:
    """扫 prompt md 文件数 AP-024 信号."""

    HIT_SAMPLE_CAP = 5  # 每信号类保留前 N 个命中作 sample

    def scan_files(self, prompt_paths: list[str | Path]) -> PromptPatchPileScanResult:
        """扫多份 prompt md."""
        result = PromptPatchPileScanResult()
        for pp in prompt_paths:
            path = Path(pp)
            if not path.exists():
                result.skipped.append((str(path), "文件不存在"))
                continue
            if not path.is_file():
                result.skipped.append((str(path), "非文件"))
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                result.skipped.append((str(path), f"读失败: {e}"))
                continue
            sig = self._scan_content(str(path), content)
            result.signals.append(sig)
            result.scanned_count += 1
        return result

    def _scan_content(self, path: str, content: str) -> PromptPatchPileSignal:
        lines = content.splitlines()
        sig = PromptPatchPileSignal(prompt_path=path, line_count=len(lines))
        for i, line in enumerate(lines, start=1):
            # enumeration
            cnt, pat = _count_pattern_hits(line, _ENUMERATION_PATTERNS)
            if cnt:
                sig.enumeration_count += cnt
                if len(sig.enumeration_hits) < self.HIT_SAMPLE_CAP:
                    sig.enumeration_hits.append(SignalHit(i, line[:200], pat or ""))
            # specific_reference
            cnt, pat = _count_pattern_hits(line, _SPECIFIC_PATTERNS)
            if cnt:
                sig.specific_reference_count += cnt
                if len(sig.specific_reference_hits) < self.HIT_SAMPLE_CAP:
                    sig.specific_reference_hits.append(SignalHit(i, line[:200], pat or ""))
            # patch_marker
            cnt, pat = _count_pattern_hits(line, _PATCH_MARKERS)
            if cnt:
                sig.patch_marker_count += cnt
                if len(sig.patch_marker_hits) < self.HIT_SAMPLE_CAP:
                    sig.patch_marker_hits.append(SignalHit(i, line[:200], pat or ""))
        return sig


def scan_prompt_patch_pile(prompt_paths: list[str | Path]) -> PromptPatchPileScanResult:
    """便捷入口."""
    return PromptPatchPileScanner().scan_files(prompt_paths)
