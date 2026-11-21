# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-24T00:00:00Z type=util
# [OMNI] material_id="material:core.guardian.audit_record_store.implementation.py"
"""guardian.audit_store — Guardian 审计留档 (GuardianAuditStore, 2026-04-24).

**动机** (2026-04-24 用户要求):

> "跑了要有痕迹, 健康也要标记, 按哪一版本标准, 谁来判断, 对什么版本的文件标记,
>  如果没有上传要进行一次不诊断上传以留档, 防止重复跑和白跑."

核心原则:
1. **痕迹**: 每次 GuardianAgent 判定都写 record (含 confirmed + dismissed + uncertain)
2. **健康标记**: dismissed (合法) 一视同仁写入, "合法"也是一次凭证
3. **版本标准**: 每 record 带 `rule_version` + `prompt_sha8`
4. **谁判**: `reviewer` 含 agent + 模型 + 版本
5. **文件版本**: `file_sha16` 文件内容指纹 (核心缓存键)
6. **防递归**: store 写盘路径 `data/services/guardian/audit/` 豁免一切规则
7. **防重跑**: 五元组 (path + rule_id + rule_version + prompt_sha8 + file_sha16) 全匹配 → 复用 verdict

schema (records.jsonl, append-only):
```json
{
  "ts": "2026-04-24T08:00:00Z",
  "target_path": "scripts/foo.py",
  "file_sha16": "abc123...",
  "rule_id": "OMNI-073",
  "rule_version": "v1",
  "prompt_sha8": "9ef23a10",
  "reviewer": "GuardianAgent:qwen3.6-plus:v1",
  "verdict": "confirmed",
  "confidence": 0.9,
  "reasoning": "...",
  "source_batch": "073-074-review-2026-04-24T07-38-10"
}
```

查询接口:
- `lookup_latest(target_path, rule_id, file_sha16, rule_version, prompt_sha8) -> Record | None`:
  五元组全匹配 → 返回最新 verdict; 不匹配 → None (调用方决定是否跑 LLM)
- `append_record(record)`: 追加写
- `iter_records()`: 遍历所有
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


_AUDIT_DIR_REL = "data/services/guardian/audit"
_RECORDS_FILENAME = "records.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_file_sha16(path: Path | str) -> str:
    """文件内容 sha256 前 16 hex (8 字节) — 足够辨别内容变化, 字符串短易读."""
    p = Path(path)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def compute_prompt_sha8(prompt: str) -> str:
    """SYSTEM_PROMPT 的 sha256 前 8 hex — prompt 改了缓存就失效."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


def compute_rule_version(rule) -> str:
    """计算规则版本. 基于 rule.check 函数源码 + message_template 的 sha256[:8].

    任何 check 函数修改或 message_template 措辞变动 → 版本自动升级,
    缓存失效, 已判 record 重新送 LLM.
    """
    try:
        import inspect
        src = inspect.getsource(rule.check)
    except Exception:
        src = f"<no-source:{rule.id}>"
    payload = src + "|" + (rule.message_template or "") + "|" + (rule.description or "")
    return "v" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:7]


def current_guardian_agent_prompt_sha8() -> str:
    """抓当前 GuardianAgent 的 SYSTEM_PROMPT 并计算 sha8. 便利函数, 避免各处重复."""
    try:
        from .judge_agent import GuardianAgent
        prompt = getattr(GuardianAgent, "SYSTEM_PROMPT", "")
        return compute_prompt_sha8(prompt)
    except Exception:
        return ""


@dataclass
class AuditRecord:
    """一条 Guardian 审计判定记录."""

    ts: str = field(default_factory=_now_iso)
    target_path: str = ""
    file_sha16: str = ""                           # 被判文件的内容指纹
    rule_id: str = ""                              # OMNI-073 / OMNI-074 / ...
    rule_version: str = "v1"                       # 规则语义版本 (改 check 或 message 时升)
    prompt_sha8: str = ""                          # GuardianAgent SYSTEM_PROMPT 指纹
    reviewer: str = "GuardianAgent:qwen3.6-plus:v1"  # 谁判的
    verdict: str = "uncertain"                     # confirmed / dismissed / uncertain
    confidence: float = 0.0
    reasoning: str = ""
    suggestion: str = ""
    source_batch: str = ""                         # 来自哪轮批量运行 (可追溯)

    def to_jsonl_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "AuditRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class GuardianAuditStore:
    """Append-only JSONL store, 支持五元组缓存查询.

    路径: <project_root>/data/services/guardian/audit/records.jsonl

    用法:
        store = GuardianAuditStore(project_root)
        # 查询
        hit = store.lookup_latest(
            target_path="scripts/foo.py",
            rule_id="OMNI-073",
            file_sha16="abc...",
            rule_version="v1",
            prompt_sha8="9ef23a10",
        )
        if hit:
            # 复用 verdict, 跳过 LLM
            return hit.verdict, hit.confidence
        # 未命中 → 跑 LLM → 写入 record
        store.append_record(AuditRecord(...))
    """

    def __init__(self, project_root: Path | str):
        self._root = Path(project_root)
        self._dir = self._root / _AUDIT_DIR_REL
        self._records_path = self._dir / _RECORDS_FILENAME

    @property
    def records_path(self) -> Path:
        return self._records_path

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def append_record(self, record: AuditRecord) -> None:
        """追加一条 record. 写完后更新 sidecar 指纹."""
        self._ensure_dir()
        line = record.to_jsonl_line() + "\n"
        with self._records_path.open("a", encoding="utf-8") as f:
            f.write(line)
        # data-provenance sidecar (I-20 规范要求)
        try:
            from omnicompany.core.omnimark import write_data_sidecar
            write_data_sidecar(
                self._records_path,
                written_by=f"{__name__}.GuardianAuditStore",
                source_path=__file__,
                # append-only 审计记录无 TTL (归档政策另议)
            )
        except Exception as e:
            logger.debug("sidecar 写入失败 (非致命): %s", e)

    def append_many(self, records: list[AuditRecord]) -> int:
        """批量追加. 返回写入条数."""
        if not records:
            return 0
        self._ensure_dir()
        lines = "".join(r.to_jsonl_line() + "\n" for r in records)
        with self._records_path.open("a", encoding="utf-8") as f:
            f.write(lines)
        try:
            from omnicompany.core.omnimark import write_data_sidecar
            write_data_sidecar(
                self._records_path,
                written_by=f"{__name__}.GuardianAuditStore",
                source_path=__file__,
            )
        except Exception as e:
            logger.debug("sidecar 写入失败: %s", e)
        return len(records)

    def iter_records(self) -> Iterator[AuditRecord]:
        """按写入顺序遍历所有 record. 损坏行跳过."""
        if not self._records_path.exists():
            return
        with self._records_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield AuditRecord.from_dict(json.loads(line))
                except Exception:
                    continue

    def lookup_latest(
        self,
        target_path: str,
        rule_id: str,
        file_sha16: str,
        rule_version: str = "v1",
        prompt_sha8: str = "",
    ) -> Optional[AuditRecord]:
        """五元组全匹配返回最新 record, 不匹配返回 None.

        **所有 5 项必须精确相等** — 任何一项变化 (文件改 / 规则升 / prompt 改) 都视为缓存失效.
        这保证了"100% 语义相同情况下复用, 语义变了重判".

        遍历: 为简化实现, 全量扫 jsonl. 若 record 数量大可用内存索引优化 (后置 TODO).
        """
        latest: Optional[AuditRecord] = None
        for rec in self.iter_records():
            if (
                rec.target_path == target_path
                and rec.rule_id == rule_id
                and rec.file_sha16 == file_sha16
                and rec.rule_version == rule_version
                and rec.prompt_sha8 == prompt_sha8
            ):
                if latest is None or rec.ts > latest.ts:
                    latest = rec
        return latest

    def stats(self) -> dict:
        """汇总 records 统计 (总数 / by verdict / by rule_id)."""
        total = 0
        by_verdict: dict[str, int] = {}
        by_rule: dict[str, int] = {}
        for rec in self.iter_records():
            total += 1
            by_verdict[rec.verdict] = by_verdict.get(rec.verdict, 0) + 1
            by_rule[rec.rule_id] = by_rule.get(rec.rule_id, 0) + 1
        return {"total": total, "by_verdict": by_verdict, "by_rule": by_rule}


__all__ = [
    "AuditRecord",
    "GuardianAuditStore",
    "compute_file_sha16",
    "compute_prompt_sha8",
    "compute_rule_version",
    "current_guardian_agent_prompt_sha8",
]
