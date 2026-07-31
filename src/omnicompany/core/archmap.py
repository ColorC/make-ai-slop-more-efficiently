# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-04-08T04:00:00Z
# [OMNI] material_id="material:omnicompany.core.archmap.drawer_map.engine.py"
"""ArchMap — 结构化 drawer 地图加载 + 校验 + 查询。

docs/archmap.yaml 是 OmniGuardian 的唯一权威 drawer 定义来源。
本模块负责:
  1. 加载 + 缓存（mtime 感知）
  2. 结构校验 (validate)
  3. 查询接口: is_writable(path, writer) / is_forbidden_root_file(path)
  4. 展示接口: list_drawers / render_tree

不在此做的事:
  - 不做 LLM 调用
  - 不修改 archmap.yaml 本身
  - 不判断运行时状态（OmniTow 处置、违规溯源等）

接入方:
  - patrol.py OMNI-014 _check_illegal_drawer → 读 allowed drawers
  - patrol.py OMNI-015 _check_forbidden_root_file → 读 glob patterns
  - (Session 3c) guarded_write.write_file → 生产写入门禁
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── 默认路径 ─────────────────────────────────────────────────

def _find_project_root() -> Path:
    """从本文件位置往上找到含 pyproject.toml 的项目根。"""
    p = Path(__file__).resolve()
    while p.parent != p:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path(__file__).resolve().parents[3]


_PROJECT_ROOT = _find_project_root()
_DEFAULT_ARCHMAP_PATH = _PROJECT_ROOT / "docs" / "archmap.yaml"


# ─── 校验异常 ─────────────────────────────────────────────────

class ArchMapError(Exception):
    """archmap.yaml 结构或语义错误。"""


# ─── 查询结果 ─────────────────────────────────────────────────

@dataclass
class WriteCheck:
    """is_writable() 的返回结构。"""

    allowed: bool
    reason: str
    drawer: str = ""              # 匹配到的 drawer 名（repo_root 或 src_omnicompany 级别）
    drawer_layer: str = ""        # "repo_root" / "src_omnicompany" / "none"
    always_green: bool = False    # 是否命中 always_green=true
    agent_free_fire: bool = False # 是否命中 agent_free_fire=true


# ─── ArchMap ──────────────────────────────────────────────────

@dataclass
class ArchMap:
    """加载后的架构地图。"""

    path: Path
    version: int
    last_reviewed: str
    reviewed_by: str
    enforce_mode: bool
    repo_root: dict[str, dict]
    src_omnicompany: dict[str, dict]
    forbidden_globs: list[str]
    forbidden_root_dirs: list[str]
    forbidden_message: str
    writer_identities: dict[str, str]
    internal_pipeline_origins: list[str]
    auto_comment_pilot_rules: list[str] = field(default_factory=list)
    # S3e.3: protected_key_files 从 list 改成 dict
    # key = 路径, value = {writable_by, require_purpose, audit_label}
    # 兼容 old list[str] 形式 (loader 自动转换为默认 human-only)
    protected_key_files: dict[str, dict] = field(default_factory=dict)

    # 缓存控制
    _mtime: float = 0.0

    # ─── 校验 ─────────────────────────────────────────────

    def validate(self) -> list[str]:
        """结构完整性校验，返回错误列表（空列表 = 通过）。"""
        errors: list[str] = []
        if self.version < 1:
            errors.append(f"version 必须 >= 1，当前 {self.version}")
        if not self.repo_root:
            errors.append("repo_root 为空")
        if not self.src_omnicompany:
            errors.append("src_omnicompany 为空")
        if not self.writer_identities:
            errors.append("writer_identities 为空")

        # 每个 drawer 条目必须有 purpose 和 writable_by
        for layer_name, layer in (("repo_root", self.repo_root),
                                   ("src_omnicompany", self.src_omnicompany)):
            for name, spec in layer.items():
                if not isinstance(spec, dict):
                    errors.append(f"{layer_name}.{name} 不是 dict")
                    continue
                if "purpose" not in spec:
                    errors.append(f"{layer_name}.{name} 缺 purpose")
                if "writable_by" not in spec and not spec.get("deprecated"):
                    errors.append(f"{layer_name}.{name} 缺 writable_by")
                writers = spec.get("writable_by", [])
                if not isinstance(writers, list):
                    errors.append(f"{layer_name}.{name}.writable_by 不是 list")
                    continue
                for w in writers:
                    if w not in self.writer_identities:
                        errors.append(
                            f"{layer_name}.{name}.writable_by 含未知 writer {w!r}"
                        )

        # internal_pipeline_origins 的每个值都应该是 writer_identities 里知道的
        # origin 串（但 internal-pipeline 本身是一个 identity，内部 pipeline
        # 的 origin 是 sw-implement 等具体值）。这里只检查非空。
        if not self.internal_pipeline_origins:
            errors.append("internal_pipeline_origins 为空")

        return errors

    # ─── 查询接口 ─────────────────────────────────────────

    def list_drawers(self) -> dict[str, list[str]]:
        """展示用：返回 {'repo_root': [...], 'src_omnicompany': [...]}"""
        return {
            "repo_root": sorted(self.repo_root.keys()),
            "src_omnicompany": sorted(self.src_omnicompany.keys()),
        }

    def is_legal_repo_root_drawer(self, name: str) -> bool:
        """检查仓库根下的一个顶层目录名是否合法。"""
        return name in self.repo_root

    def is_legal_src_omnicompany_drawer(self, name: str) -> bool:
        """检查 src/omnicompany/ 下的一个顶层目录名是否合法。"""
        return name in self.src_omnicompany

    def is_forbidden_root_file(self, filename: str) -> tuple[bool, str]:
        """检查一个**仓库根层**文件名是否匹配禁区 glob。

        Args:
            filename: 文件名（不含路径，例如 "safe_files.txt"）

        Returns:
            (matched, reason)
        """
        for pat in self.forbidden_globs:
            if fnmatch.fnmatch(filename, pat):
                return True, f"匹配禁区模式 {pat!r}: {self.forbidden_message.strip()}"
        return False, ""

    def is_writable(
        self, rel_path: str, writer: str, *, has_purpose: bool = False,
    ) -> WriteCheck:
        """给一个相对仓库根的路径 + writer 身份，判断能不能写。

        判定顺序 (S3e.3 起):
          0. protected_key_files: 优先按 file-level 配置 (writable_by + require_purpose)
          1. 归一化路径（正斜杠）
          2. 仓库根禁区 glob（只看文件名）→ forbidden
          3. 确定所在 drawer 层级 + drawer 名
          4. drawer 级 always_green → allowed
          5. drawer deprecated → 只警告
          6. writer 在 writable_by 里 → allowed
          7. agent_free_fire=true → allowed
          8. 否则 → denied

        Args:
            has_purpose: 调用方是否提供了非空 purpose,仅 key_file 判定用
        """
        p = rel_path.replace("\\", "/").lstrip("./")
        if not p:
            return WriteCheck(False, "空路径", drawer_layer="none")

        # 0. protected_key_files 压倒 drawer 级判定
        if p in self.protected_key_files:
            spec = self.protected_key_files[p]
            allowed_writers = spec.get("writable_by", ["human"])
            if writer not in allowed_writers:
                return WriteCheck(
                    False,
                    f"{p} 是 Guardian 元配置 key_file,允许的 writer={allowed_writers}, "
                    f"当前 writer={writer!r} 不在列表中。"
                    f"如果是 LLM agent 协助调整 archmap, 请用 origin=claude-code 走 write_file。",
                    drawer="(key_file)", drawer_layer="repo_root",
                )
            if spec.get("require_purpose") and not has_purpose:
                return WriteCheck(
                    False,
                    f"{p} 是 protected_key_file 且 require_purpose=true。"
                    f"必须传非空 purpose 说明这次 meta config 修改的原因(会进 audit log)。"
                    f"例如: write_file(..., purpose='S3f: 加 data/domains/demogame/ 子目录')",
                    drawer="(key_file)", drawer_layer="repo_root",
                )
            return WriteCheck(
                True,
                f"{p} 是 protected_key_file, writer {writer!r} 通过审计 (label="
                f"{spec.get('audit_label', 'META_CONFIG_CHANGE')})",
                drawer="(key_file)", drawer_layer="repo_root",
            )

        # 1. 仓库根禁区（只对直接在根下的文件生效）
        if "/" not in p:
            forbidden, reason = self.is_forbidden_root_file(p)
            if forbidden:
                return WriteCheck(
                    False, reason, drawer="(root forbidden)",
                    drawer_layer="repo_root",
                )

        # 2. 找到所在 drawer
        drawer_layer, drawer_name, drawer_spec = self._locate_drawer(p)

        if drawer_name is None:
            return WriteCheck(
                False, "路径不在任何已声明 drawer 内",
                drawer="(unknown)", drawer_layer="none",
            )

        assert drawer_spec is not None

        # 3. always_green 无条件放行
        if drawer_spec.get("always_green"):
            return WriteCheck(
                True, f"{drawer_layer}.{drawer_name} 是 always_green 核心 drawer",
                drawer=drawer_name, drawer_layer=drawer_layer, always_green=True,
            )

        # 4. deprecated drawer 特殊处理：允许读但标记警告
        if drawer_spec.get("deprecated"):
            return WriteCheck(
                False,
                f"{drawer_layer}.{drawer_name} 标记为 deprecated，"
                f"不允许新写入（等待 Session 3b 迁移）",
                drawer=drawer_name, drawer_layer=drawer_layer,
            )

        # 5. writer 白名单
        writers = drawer_spec.get("writable_by", [])
        if writer in writers:
            return WriteCheck(
                True, f"writer {writer!r} 在 {drawer_layer}.{drawer_name}.writable_by 内",
                drawer=drawer_name, drawer_layer=drawer_layer,
                agent_free_fire=drawer_spec.get("agent_free_fire", False),
            )

        # 6. agent_free_fire：给 claude-code / internal-pipeline 一个兜底
        if drawer_spec.get("agent_free_fire") and writer in (
            "claude-code", "internal-pipeline", "human"
        ):
            return WriteCheck(
                True,
                f"{drawer_layer}.{drawer_name} 开启 agent_free_fire，放行 {writer}",
                drawer=drawer_name, drawer_layer=drawer_layer,
                agent_free_fire=True,
            )

        # 7. 都没命中
        return WriteCheck(
            False,
            f"writer {writer!r} 不在 {drawer_layer}.{drawer_name}.writable_by = {writers}",
            drawer=drawer_name, drawer_layer=drawer_layer,
        )

    def _locate_drawer(
        self, norm_path: str
    ) -> tuple[str, Optional[str], Optional[dict]]:
        """确定路径所在 drawer。

        Returns:
            (layer, drawer_name, drawer_spec) 或 (layer, None, None) 如果未匹配
        """
        # src/omnicompany/... → 先判 src_omnicompany 层
        if norm_path.startswith("src/omnicompany/"):
            rest = norm_path[len("src/omnicompany/"):]
            if "/" not in rest:
                # 根下的文件（__init__.py 等），归 src_omnicompany 级别兜底
                return "src_omnicompany", "__root_files__", {
                    "purpose": "src/omnicompany/ 根下的允许文件",
                    "writable_by": ["human", "claude-code"],
                    "always_green": True,
                }
            drawer = rest.split("/", 1)[0]
            spec = self.src_omnicompany.get(drawer)
            if spec is not None:
                return "src_omnicompany", drawer, spec
            return "src_omnicompany", None, None

        # 否则用 repo_root 层的第一段
        first_seg = norm_path.split("/", 1)[0]
        spec = self.repo_root.get(first_seg)
        if spec is not None:
            return "repo_root", first_seg, spec

        return "repo_root", None, None

    # ─── 渲染 ─────────────────────────────────────────────

    def render_tree(self) -> str:
        """返回一个可读的树形渲染（供 CLI show 用）。"""
        lines = []
        lines.append(f"# ArchMap v{self.version}")
        lines.append(f"  last_reviewed: {self.last_reviewed} by {self.reviewed_by}")
        lines.append(f"  enforce_mode:  {self.enforce_mode}")
        lines.append("")
        lines.append("repo_root/")
        for name in sorted(self.repo_root.keys()):
            spec = self.repo_root[name]
            flags = _fmt_flags(spec)
            deprecated = " [DEPRECATED]" if spec.get("deprecated") else ""
            lines.append(f"  {name}/{deprecated}  {flags}")
            lines.append(f"      purpose: {spec.get('purpose', '-')}")
            w = spec.get("writable_by", [])
            lines.append(f"      writable_by: {', '.join(w) if w else '(read-only)'}")
        lines.append("")
        lines.append("src/omnicompany/")
        for name in sorted(self.src_omnicompany.keys()):
            spec = self.src_omnicompany[name]
            flags = _fmt_flags(spec)
            lines.append(f"  {name}/  {flags}")
            lines.append(f"      purpose: {spec.get('purpose', '-')}")
            w = spec.get("writable_by", [])
            lines.append(f"      writable_by: {', '.join(w) if w else '(read-only)'}")
        lines.append("")
        lines.append(f"forbidden_at_repo_root ({len(self.forbidden_globs)} patterns):")
        for pat in self.forbidden_globs:
            lines.append(f"  - {pat}")
        lines.append("")
        lines.append(f"auto_comment_pilot_rules ({len(self.auto_comment_pilot_rules)}):")
        for rid in self.auto_comment_pilot_rules:
            lines.append(f"  - {rid}")
        return "\n".join(lines)

    # ─── auto_comment 接入 ────────────────────────────────────

    def is_auto_comment_pilot(self, rule_id: str) -> bool:
        """该规则是否在备注化软修复试点集合内。"""
        return rule_id in self.auto_comment_pilot_rules

    def is_internal_pipeline_origin(self, origin: str) -> bool:
        """origin 字段是否对应内部管线（决定 fix-queue vs inline）。"""
        return origin in self.internal_pipeline_origins


def _normalize_key_files(raw) -> dict[str, dict]:
    """支持 list[str] 旧格式 + dict[path,spec] 新格式 (S3e.3)。"""
    if not raw:
        return {}
    if isinstance(raw, list):
        # 老格式: 默认 human-only + require_purpose
        return {
            p: {
                "writable_by": ["human"],
                "require_purpose": True,
                "audit_label": "META_CONFIG_CHANGE",
            }
            for p in raw
        }
    if isinstance(raw, dict):
        out: dict[str, dict] = {}
        for path, spec in raw.items():
            if not isinstance(spec, dict):
                continue
            out[path] = {
                "writable_by": list(spec.get("writable_by", ["human"])),
                "require_purpose": bool(spec.get("require_purpose", True)),
                "audit_label": str(spec.get("audit_label", "META_CONFIG_CHANGE")),
            }
        return out
    return {}


def _fmt_flags(spec: dict) -> str:
    flags = []
    if spec.get("always_green"):
        flags.append("always_green")
    if spec.get("agent_free_fire"):
        flags.append("agent_free_fire")
    if spec.get("forbid_new_subdirs"):
        flags.append("forbid_new_subdirs")
    if spec.get("forbid_impl"):
        flags.append("forbid_impl")
    if spec.get("forbid_live_import"):
        flags.append("forbid_live_import")
    return f"[{', '.join(flags)}]" if flags else ""


# ─── 加载 + 缓存 ──────────────────────────────────────────────

_cached: Optional[ArchMap] = None


def load_archmap(
    path: str | Path | None = None,
    force_reload: bool = False,
) -> ArchMap:
    """加载 archmap.yaml，缓存 + mtime 感知。

    Args:
        path: 默认 docs/archmap.yaml
        force_reload: 忽略缓存强制重读

    Raises:
        ArchMapError: 文件缺失 / YAML 语法错误 / 结构校验失败
    """
    global _cached
    p = Path(path) if path else _DEFAULT_ARCHMAP_PATH

    if not p.exists():
        raise ArchMapError(f"archmap.yaml 不存在: {p}")

    mtime = p.stat().st_mtime
    if (not force_reload) and _cached is not None and _cached.path == p and _cached._mtime == mtime:
        return _cached

    import yaml
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ArchMapError(f"archmap.yaml YAML 解析失败: {e}") from e

    if not isinstance(raw, dict):
        raise ArchMapError("archmap.yaml 顶层不是 dict")

    forbidden_section = raw.get("forbidden_at_repo_root", {}) or {}

    try:
        m = ArchMap(
            path=p,
            version=int(raw.get("version", 0)),
            last_reviewed=str(raw.get("last_reviewed", "")),
            reviewed_by=str(raw.get("reviewed_by", "")),
            enforce_mode=bool(raw.get("enforce_mode", False)),
            repo_root=raw.get("repo_root", {}) or {},
            src_omnicompany=raw.get("src_omnicompany", {}) or {},
            forbidden_globs=list(forbidden_section.get("glob_patterns", []) or []),
            forbidden_root_dirs=list(forbidden_section.get("forbidden_root_dirs", []) or []),
            forbidden_message=str(forbidden_section.get("message", "")),
            writer_identities=raw.get("writer_identities", {}) or {},
            internal_pipeline_origins=list(raw.get("internal_pipeline_origins", []) or []),
            auto_comment_pilot_rules=list(raw.get("auto_comment_pilot_rules", []) or []),
            protected_key_files=_normalize_key_files(raw.get("protected_key_files")),
            _mtime=mtime,
        )
    except Exception as e:
        raise ArchMapError(f"archmap.yaml 结构构造失败: {e}") from e

    errors = m.validate()
    if errors:
        raise ArchMapError(
            "archmap.yaml 校验失败:\n  - " + "\n  - ".join(errors)
        )

    _cached = m
    logger.debug("ArchMap loaded v%d from %s", m.version, p)
    return m
