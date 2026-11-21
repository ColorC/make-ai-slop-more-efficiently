# [OMNI] origin=claude-code domain=omnifactory/guardian ts=2026-04-28T00:00:00Z type=router
# [OMNI] material_id="material:guardian.prompt_antipattern.scanner.worker.py"
"""PromptAntiPatternScanWorker — Guardian AI 指令(prompt)反模式 LLM 巡查 (OMNI-090/091/092).

Worker 协议:
  FORMAT_IN  = guardian.prompt-scan-request
  FORMAT_OUT = guardian.prompt-scan-report

职责 (2026-04-28 plan: PROMPT-ANTIPATTERN-DETECTION):
  扫遍 services/ 下 Worker / Router 文件的 AI 指令字符串常量, 用 LLM 复核
  三条反模式: prompt-context-pollution / prompt-clumsy-enumeration /
  prompt-outdated-specifics. 不同于硬规则, 反模式是语义级的, 必须 LLM 判.

  rules/prompt_quality.py 只是概念注册 (description 给 reviewer prompt 引用),
  规则引擎层不触发 — 真正扫描在本 Worker 主导.

防重跑机制:
  接 GuardianAuditStore (audit_store.py) 五元组缓存:
    (target_path = "<file>::<prompt_name>"
     + rule_id (OMNI-090/091/092)
     + file_sha16 = prompt 文本指纹
     + rule_version = 规则 description 指纹
     + prompt_sha8 = reviewer prompt 自身指纹)
  全匹配 → 复用 verdict, 不调 LLM.

reviewer prompt 自身严守 3 原则 (self-referential):
  本 Worker 实施完后必须扫自己一遍, 若违规则修自己.

输入 (guardian.prompt-scan-request):
  - scope: str | None — 扫描根 (默认 src/omnifactory/packages/services)
  - rule_filter: list[str] | None — 限定 ["OMNI-090"] 等子集
  - force_rescan: bool — 绕过 audit 缓存 (默认 False)

输出 (guardian.prompt-scan-report):
  - findings: list[dict]   — 全部 finding 平铺
  - by_rule: dict[str, int] — 按 rule_id 计数
  - by_verdict: dict[str, int]
  - prompts_scanned: int
  - prompts_cached: int     — audit 命中数
  - audit_records_appended: int
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind

from ..audit_store import (
    AuditRecord,
    GuardianAuditStore,
    compute_prompt_sha8,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# Reviewer SYSTEM prompt — 自身严守三原则
# ══════════════════════════════════════════════════════════════
#
# 设计自检 (plan §3.3):
#   - 不引外部文档/计划 ✓ (概念都在 prompt 内解释)
#   - 不分类穷举 ✓ (写原则让 LLM 判, 不列"这种这样那种那样")
#   - 不举具体过时案例 ✓ (无具体 service 名 / API 名)

_REVIEWER_SYSTEM_PROMPT = """\
你审一段 AI 指令文本(prompt). 判它是否满足三条原则.

# 三条原则

**原则1**: prompt 自洽. 任何概念、引用、思路, 在 prompt 内解释或不写. \
不引用外部文档(它们会变), 不留思考或修改痕迹.

**原则2**: prompt 写原则, 不写枚举. 对开放空间(代码生成 / 设计 / 自然语言), \
给目标和约束让 LLM 自判, 不替 LLM 分类.

**原则3**: prompt 写"它要满足什么", 不写"它该怎么做". 给原则和目标, \
不锁具体方案、案例、API 名.

# 你的判定

读 prompt 全文. 对每条违规给一条 finding:
- rule_id: 违原则 1 → OMNI-090; 违原则 2 → OMNI-091; 违原则 3 → OMNI-092
- severity: HIGH(致命) / MEDIUM(显著) / LOW(轻微)
- evidence: 引 prompt 原文片段(≤ 100 字), 不要复述
- fix_hint: 这条 evidence 该怎么改(一句话)
- confidence: 0.0-1.0

prompt 三原则都满足 → findings 留空, verdict 写 "clean".
有任意违规 → verdict 写 "issues_found".

# 输出格式

只输出一段 JSON, 不要别的文字. 格式:

```json
{
  "verdict": "clean",
  "summary": "一句话总览(≤ 30 字)",
  "findings": [
    {
      "rule_id": "OMNI-090",
      "severity": "MEDIUM",
      "evidence": "原文片段",
      "fix_hint": "怎么改",
      "confidence": 0.85
    }
  ]
}
```

# 边界

- 不分析 prompt 优点 — 只判违规
- 不重复 prompt 原文 — 用 evidence 字段引片段
- 不给一般化建议 — fix_hint 针对具体 evidence
- 不评 prompt 长度 / 排版 / 措辞优雅度 — 这些不是反模式
"""


# ══════════════════════════════════════════════════════════════
# AST 抽 prompt 字符串
# ══════════════════════════════════════════════════════════════

# 名字含任一 token 的模块级 SCREAMING_SNAKE_CASE 常量视作 prompt 候选
_PROMPT_NAME_TOKENS: tuple[str, ...] = (
    "PROMPT", "SYSTEM", "USER", "TEMPLATE",
    "INSTRUCTION", "REVIEWER", "GUIDE", "ROLE",
)

# 长度门槛 — 短于此的不视作 prompt (是 banner / 短 label).
# 30 字符够覆盖真 prompt (LLM 系统指令最少也几十字), 又能挡 banner / divider / 命令字
_MIN_PROMPT_LEN = 30


@dataclass
class PromptCandidate:
    """一段被抽出的 AI 指令文本."""

    file_path: str         # 仓库相对路径 (/ 分隔)
    prompt_name: str       # 常量名或 "<system arg @ line>"
    text: str              # prompt 全文
    lineno: int            # 出现行号
    method: str            # "module_const" | "system_arg"


def _extract_constant_str(node: ast.AST) -> Optional[str]:
    """从 ast 节点抽字符串字面量. 支持 Constant / JoinedStr (f-string) / 简单 BinOp 拼接.

    JoinedStr 抽出未求值的字面量片段拼起来 (含模板槽位 → 占位符), 给 LLM 看结构够用.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                parts.append("{...}")
        return "".join(parts) if parts else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # 简单串拼: "a" + "b" 形式
        left = _extract_constant_str(node.left)
        right = _extract_constant_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def extract_prompts(file_path: Path, repo_root: Path) -> list[PromptCandidate]:
    """从单个 .py 文件抽 prompt 候选清单. 抽不出返回空列表."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug("读文件失败 %s: %s", file_path, e)
        return []

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        logger.debug("AST 解析失败 %s: %s", file_path, e)
        return []

    rel = str(file_path.relative_to(repo_root)).replace("\\", "/")
    seen_texts: set[str] = set()  # 同一段文本只抽一次
    candidates: list[PromptCandidate] = []

    # 模块级常量赋值
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        name = tgt.id
        if not any(tok in name for tok in _PROMPT_NAME_TOKENS):
            continue
        text = _extract_constant_str(node.value)
        if text is None or len(text.strip()) < _MIN_PROMPT_LEN:
            continue
        if text in seen_texts:
            continue
        seen_texts.add(text)
        candidates.append(PromptCandidate(
            file_path=rel,
            prompt_name=name,
            text=text,
            lineno=node.lineno,
            method="module_const",
        ))

    # 兜底: 函数调用关键字参数 system="..." 的字面量
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "system":
                continue
            text = _extract_constant_str(kw.value)
            if text is None or len(text.strip()) < _MIN_PROMPT_LEN:
                continue
            if text in seen_texts:
                continue
            seen_texts.add(text)
            candidates.append(PromptCandidate(
                file_path=rel,
                prompt_name=f"<system arg @ line {node.lineno}>",
                text=text,
                lineno=node.lineno,
                method="system_arg",
            ))

    return candidates


# ══════════════════════════════════════════════════════════════
# 文件遍历 + 路径豁免
# ══════════════════════════════════════════════════════════════

_SCAN_EXCLUDE_DIRS: tuple[str, ...] = (
    "_archive", "_graveyard", "vendors", "__pycache__",
    ".git", "node_modules",
)


def _should_skip_dir(d: Path) -> bool:
    return d.name in _SCAN_EXCLUDE_DIRS or d.name.startswith(".")


def _collect_service_files(service_dir: Path) -> list[Path]:
    """从一个 service 目录里收集 routers.py + workers/**.py."""
    files: list[Path] = []
    rp = service_dir / "routers.py"
    if rp.exists() and rp.is_file():
        files.append(rp)
    wdir = service_dir / "workers"
    if wdir.exists() and wdir.is_dir():
        for p in wdir.rglob("*.py"):
            if p.name == "__init__.py":
                continue
            if any(part in _SCAN_EXCLUDE_DIRS for part in p.parts):
                continue
            files.append(p)
    return files


def _looks_like_service_dir(d: Path) -> bool:
    """判 d 是否本身就是一个 service 目录 (含 workers/ 或 routers.py).

    用于 scope 既能给 services/ 父目录, 也能给单个 service 子目录的鉴别.
    """
    return (d / "workers").is_dir() or (d / "routers.py").is_file()


def iter_target_files(scan_root: Path) -> list[Path]:
    """收集 scan_root 下所有 worker / router .py 文件 (排除归档/外部).

    自适应识别 scan_root 形态:
      - 若 scan_root 本身是 service 目录 (含 workers/ 或 routers.py): 只收它
      - 若 scan_root 是单个 .py 文件: 收它
      - 否则视作 services/ 父目录, 遍历每个子 service

    扫描范围:
      - <service>/routers.py
      - <service>/workers/**.py
    """
    if not scan_root.exists():
        return []

    # 单文件 scope
    if scan_root.is_file() and scan_root.suffix == ".py":
        return [scan_root]

    if not scan_root.is_dir():
        return []

    # 单 service scope
    if _looks_like_service_dir(scan_root):
        return _collect_service_files(scan_root)

    # services/ 父目录: 遍历子 service
    files: list[Path] = []
    for service_dir in sorted(scan_root.iterdir()):
        if not service_dir.is_dir() or _should_skip_dir(service_dir):
            continue
        files.extend(_collect_service_files(service_dir))
    return files


# ══════════════════════════════════════════════════════════════
# audit 缓存键
# ══════════════════════════════════════════════════════════════

def _compute_prompt_text_sha16(text: str) -> str:
    """prompt 文本 sha256 前 16 hex (= audit_store.file_sha16 字段)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _compute_rule_version(rule_description: str) -> str:
    """规则 description 指纹. description 改 → 版本升 → 缓存失效."""
    return "v" + hashlib.sha256(rule_description.encode("utf-8")).hexdigest()[:7]


# ══════════════════════════════════════════════════════════════
# LLM 调用 + JSON 提取
# ══════════════════════════════════════════════════════════════

_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _scan_balanced_json_object(text: str, start: int) -> Optional[str]:
    """从 text[start] 处的 '{' 开始扫到匹配的 '}', 返回完整 JSON 字符串. 简易 brace counting.

    忽略字符串字面量内的 brace, 处理 \\\\ 转义.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _extract_review_json(text: str) -> Optional[dict]:
    """从 LLM 自由文本里抠 ```json {...} ``` 段并解析. 失败返回 None.

    优先 markdown fence (\\`\\`\\`json ... \\`\\`\\`); 失败兜底用 brace counting 找
    含 \"verdict\" + \"findings\" 标志字段的裸 JSON 对象; 都失败放弃 (调用方处理).
    """
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 兜底: 扫每个 '{' 看是否含 verdict 字段, 再 brace counting 取完整对象
    for i, c in enumerate(text):
        if c != "{":
            continue
        cand = _scan_balanced_json_object(text, i)
        if cand is None:
            continue
        if '"verdict"' not in cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _call_reviewer_llm(prompt_text: str, file_path: str, prompt_name: str) -> tuple[Optional[dict], str]:
    """单次 LLM 调用复核一段 prompt. 返回 (parsed_json | None, raw_response_text).

    走 LLMClient(role='runtime_main') — 跟 patrol_worker 同构.
    """
    from omnifactory.runtime.llm.llm import LLMClient

    user_payload = (
        f"待审 prompt 来自: {file_path} 中的 `{prompt_name}` 常量.\n\n"
        f"--- prompt 全文如下 ---\n{prompt_text}\n--- 全文结束 ---\n\n"
        f"按系统指令的 JSON 格式输出判定."
    )

    try:
        client = LLMClient(role="runtime_main")
        response = client.call(
            messages=[{"role": "user", "content": user_payload}],
            system=_REVIEWER_SYSTEM_PROMPT,
        )
    except Exception as e:
        logger.warning("LLM 调用失败 %s::%s: %s", file_path, prompt_name, e)
        return None, f"<LLM error: {type(e).__name__}: {e}>"

    raw = "".join(
        getattr(b, "text", "")
        for b in response.content
        if getattr(b, "type", "text") == "text"
    )
    parsed = _extract_review_json(raw)
    return parsed, raw


# ══════════════════════════════════════════════════════════════
# Worker
# ══════════════════════════════════════════════════════════════

class PromptAntiPatternScanWorker(Worker):
    """扫遍 services/ 下 Worker/Router 文件的 prompt 字符串, LLM 复核三类反模式."""

    DESCRIPTION = (
        "Guardian prompt 反模式 LLM 巡查 Worker (2026-04-28 OMNI-090/091/092). "
        "AST 抽 services/ 下 Worker/Router 文件的 prompt 字符串常量 + system 参数字面量, "
        "对每段 prompt 调 qwen-3.6-plus 复核三条原则 (自洽/写原则/写满足什么), "
        "产 findings 写 audit_store 五元组缓存防重跑. "
        "rules/prompt_quality.py 只是概念注册, 真扫在本 Worker 主导."
    )
    FORMAT_IN = "guardian.prompt-scan-request"
    FORMAT_OUT = "guardian.prompt-scan-report"
    INPUT_KEYS = []  # 全部字段可选

    def run(self, input_data: dict[str, Any]) -> Verdict:
        # dispatcher 传 {FORMAT_IN: payload};直接 worker.run 传 payload 本体. 两形态都支持.
        payload = input_data.get(self.FORMAT_IN) if isinstance(input_data.get(self.FORMAT_IN), dict) else input_data
        scope = payload.get("scope")
        rule_filter = payload.get("rule_filter")  # None | list[str]
        force_rescan = bool(payload.get("force_rescan", False))

        # ── 1. 解析项目根 + 扫描根 ──
        from omnifactory.core.config import _project_root
        project_root = _project_root()

        if scope:
            scan_root = (project_root / scope).resolve() if not Path(scope).is_absolute() else Path(scope)
        else:
            scan_root = project_root / "src" / "omnifactory" / "packages" / "services"

        if not scan_root.exists():
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"scan_root 不存在: {scan_root}")

        # ── 2. 抽 prompt 候选 ──
        target_files = iter_target_files(scan_root)
        candidates: list[PromptCandidate] = []
        for fp in target_files:
            candidates.extend(extract_prompts(fp, project_root))

        if not candidates:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "findings": [],
                    "by_rule": {},
                    "by_verdict": {},
                    "prompts_scanned": 0,
                    "prompts_cached": 0,
                    "audit_records_appended": 0,
                    "scan_root": str(scan_root),
                    "report_path": "",
                    "report_md": "(未发现任何 prompt 候选)",
                },
                diagnosis=f"在 {scan_root} 下未抽出任何 prompt 候选",
            )

        # ── 3. 准备 audit_store + 规则元数据 ──
        store = GuardianAuditStore(project_root)
        reviewer_sha8 = compute_prompt_sha8(_REVIEWER_SYSTEM_PROMPT)

        from ..rules.prompt_quality import RULES as PROMPT_RULES
        rule_by_id = {r.id: r for r in PROMPT_RULES}
        # 三条规则的 description 串起来作 rule_version 锚点 (任何一条 description 改都失效)
        all_descriptions = "|".join(r.description for r in PROMPT_RULES)
        rule_version = _compute_rule_version(all_descriptions)

        active_rule_ids = (
            [r.id for r in PROMPT_RULES if (rule_filter is None or r.id in rule_filter)]
        )
        if not active_rule_ids:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"rule_filter={rule_filter} 与三条规则无交集, 跳过扫描",
            )

        # ── 4. 逐 prompt 缓存查询 / LLM 复核 ──
        all_findings: list[dict] = []
        new_audit_records: list[AuditRecord] = []
        cached_count = 0
        scanned_count = 0
        scan_batch = "prompt-scan-" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        for cand in candidates:
            prompt_sha16 = _compute_prompt_text_sha16(cand.text)
            target_path = f"{cand.file_path}::{cand.prompt_name}"

            # 缓存命中: 三条规则全部命中 → 复用所有 verdict
            if not force_rescan:
                cache_hits = []
                for rid in active_rule_ids:
                    hit = store.lookup_latest(
                        target_path=target_path,
                        rule_id=rid,
                        file_sha16=prompt_sha16,
                        rule_version=rule_version,
                        prompt_sha8=reviewer_sha8,
                    )
                    if hit is not None:
                        cache_hits.append(hit)
                if len(cache_hits) == len(active_rule_ids):
                    cached_count += 1
                    for hit in cache_hits:
                        if hit.verdict == "confirmed":
                            all_findings.append({
                                "file_path": cand.file_path,
                                "prompt_name": cand.prompt_name,
                                "lineno": cand.lineno,
                                "rule_id": hit.rule_id,
                                "severity": "MEDIUM",  # 缓存里不存 severity, 用 rule 默认
                                "evidence": "(从 audit 缓存复用)",
                                "fix_hint": hit.suggestion or "",
                                "confidence": hit.confidence,
                                "from_cache": True,
                            })
                    continue

            # 缓存未命中 → 调 LLM
            scanned_count += 1
            parsed, raw = _call_reviewer_llm(cand.text, cand.file_path, cand.prompt_name)

            if parsed is None:
                # LLM 调用失败 / JSON 解析失败 → 写 uncertain audit, 不让进 finding
                for rid in active_rule_ids:
                    new_audit_records.append(AuditRecord(
                        target_path=target_path,
                        file_sha16=prompt_sha16,
                        rule_id=rid,
                        rule_version=rule_version,
                        prompt_sha8=reviewer_sha8,
                        reviewer="GuardianAgent:qwen3.6-plus:prompt-antipattern-v1",
                        verdict="uncertain",
                        confidence=0.0,
                        reasoning="LLM 调用或 JSON 解析失败",
                        suggestion=raw[:200] if raw else "",
                        source_batch=scan_batch,
                    ))
                continue

            verdict_text = parsed.get("verdict", "issues_found")
            findings_in_resp = parsed.get("findings", []) or []

            # 把 finding 按 rule_id 桶分
            findings_by_rule: dict[str, list[dict]] = {rid: [] for rid in active_rule_ids}
            for f in findings_in_resp:
                rid = f.get("rule_id", "")
                if rid in findings_by_rule:
                    findings_by_rule[rid].append(f)
                # rule_filter 外的 finding 直接丢

            # 每条规则写 audit record (有 finding → confirmed; 无 → dismissed)
            for rid in active_rule_ids:
                rule_findings = findings_by_rule.get(rid, [])
                if rule_findings:
                    # 多条 finding 合并写一条 record (取最高 confidence)
                    top = max(rule_findings, key=lambda f: f.get("confidence", 0.0))
                    new_audit_records.append(AuditRecord(
                        target_path=target_path,
                        file_sha16=prompt_sha16,
                        rule_id=rid,
                        rule_version=rule_version,
                        prompt_sha8=reviewer_sha8,
                        reviewer="GuardianAgent:qwen3.6-plus:prompt-antipattern-v1",
                        verdict="confirmed",
                        confidence=float(top.get("confidence", 0.7)),
                        reasoning=f"{len(rule_findings)} 条 finding · top evidence: {top.get('evidence', '')[:100]}",
                        suggestion=top.get("fix_hint", "")[:200],
                        source_batch=scan_batch,
                    ))
                    for f in rule_findings:
                        all_findings.append({
                            "file_path": cand.file_path,
                            "prompt_name": cand.prompt_name,
                            "lineno": cand.lineno,
                            "rule_id": rid,
                            "severity": f.get("severity", "MEDIUM"),
                            "evidence": f.get("evidence", "")[:200],
                            "fix_hint": f.get("fix_hint", "")[:200],
                            "confidence": float(f.get("confidence", 0.7)),
                            "from_cache": False,
                        })
                else:
                    new_audit_records.append(AuditRecord(
                        target_path=target_path,
                        file_sha16=prompt_sha16,
                        rule_id=rid,
                        rule_version=rule_version,
                        prompt_sha8=reviewer_sha8,
                        reviewer="GuardianAgent:qwen3.6-plus:prompt-antipattern-v1",
                        verdict="dismissed",
                        confidence=0.9,
                        reasoning=f"LLM 判 verdict={verdict_text}, 此规则无违规",
                        suggestion="",
                        source_batch=scan_batch,
                    ))

        # ── 5. audit 批量落盘 ──
        appended = store.append_many(new_audit_records) if new_audit_records else 0

        # ── 6. 汇总输出 ──
        by_rule: dict[str, int] = {}
        by_verdict: dict[str, int] = {}
        for f in all_findings:
            by_rule[f["rule_id"]] = by_rule.get(f["rule_id"], 0) + 1
        for r in new_audit_records:
            by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1

        # ── 7. 落盘 markdown 报告 ──
        from omnifactory.core.config import resolve_service_data_dir
        rep_dir = resolve_service_data_dir("guardian") / "prompt-scan"
        rep_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        rep_path = rep_dir / f"prompt-scan-{ts}.md"
        rep_md = self._render_markdown(
            scan_root=scan_root,
            candidates_count=len(candidates),
            scanned_count=scanned_count,
            cached_count=cached_count,
            findings=all_findings,
            by_rule=by_rule,
            by_verdict=by_verdict,
            audit_records_appended=appended,
        )
        rep_path.write_text(rep_md, encoding="utf-8")
        try:
            from omnifactory.core.omnimark import write_data_sidecar
            write_data_sidecar(
                rep_path,
                written_by=f"{self.__class__.__module__}.{self.__class__.__name__}",
                source_path=__file__,
                ttl_days=90,
            )
        except Exception as e:
            logger.debug("sidecar 写入失败 (非致命): %s", e)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "findings": all_findings,
                "by_rule": by_rule,
                "by_verdict": by_verdict,
                "prompts_scanned": scanned_count,
                "prompts_cached": cached_count,
                "prompts_total": len(candidates),
                "audit_records_appended": appended,
                "scan_root": str(scan_root),
                "report_path": str(rep_path.relative_to(project_root)).replace("\\", "/"),
                "report_md": rep_md,
            },
            diagnosis=(
                f"扫了 {len(candidates)} 段 prompt (新调 LLM {scanned_count} 段, 缓存命中 {cached_count} 段), "
                f"产 {len(all_findings)} 条 finding, audit 写入 {appended} 条."
            ),
        )

    # ── 报告渲染 ──────────────────────────────────────────────

    def _render_markdown(
        self,
        scan_root: Path,
        candidates_count: int,
        scanned_count: int,
        cached_count: int,
        findings: list[dict],
        by_rule: dict[str, int],
        by_verdict: dict[str, int],
        audit_records_appended: int,
    ) -> str:
        ts = datetime.now().isoformat(timespec="seconds")
        lines: list[str] = []
        lines.append(f"# Guardian Prompt Anti-Pattern Scan · {ts}")
        lines.append("")
        lines.append(f"扫描根: `{scan_root}`")
        lines.append("")
        lines.append("## 总览")
        lines.append("")
        lines.append(f"- 抽出 prompt 候选: {candidates_count} 段")
        lines.append(f"- 新调 LLM: {scanned_count} 段")
        lines.append(f"- 命中 audit 缓存: {cached_count} 段")
        lines.append(f"- 产 finding: {len(findings)} 条")
        lines.append(f"- audit 写入: {audit_records_appended} 条")
        lines.append("")

        if by_rule:
            lines.append("### 按规则计数")
            lines.append("")
            for rid in ("OMNI-090", "OMNI-091", "OMNI-092"):
                if rid in by_rule:
                    lines.append(f"- {rid}: {by_rule[rid]}")
            lines.append("")
        if by_verdict:
            lines.append("### 按 verdict 计数 (本轮新写 audit)")
            lines.append("")
            for v in ("confirmed", "dismissed", "uncertain"):
                if v in by_verdict:
                    lines.append(f"- {v}: {by_verdict[v]}")
            lines.append("")

        if not findings:
            lines.append("## Finding 详情")
            lines.append("")
            lines.append("**无违规 finding.** 所有 prompt 均通过三原则审核.")
            return "\n".join(lines)

        lines.append("## Finding 详情")
        lines.append("")
        # 按文件分组
        by_file: dict[str, list[dict]] = {}
        for f in findings:
            by_file.setdefault(f["file_path"], []).append(f)
        for fpath in sorted(by_file.keys()):
            lines.append(f"### `{fpath}`")
            lines.append("")
            for f in by_file[fpath]:
                cache_tag = " *(cached)*" if f.get("from_cache") else ""
                lines.append(
                    f"- **{f['rule_id']}** [{f['severity']}] · "
                    f"`{f['prompt_name']}` (line {f['lineno']}) · "
                    f"confidence={f['confidence']:.2f}{cache_tag}"
                )
                ev = f.get("evidence", "").replace("\n", " ").strip()
                if ev:
                    lines.append(f"  - 证据: {ev}")
                fh = f.get("fix_hint", "").strip()
                if fh:
                    lines.append(f"  - 改: {fh}")
                lines.append("")
        return "\n".join(lines)
