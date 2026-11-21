# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-12T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.naming_discipline.enforcer.py"
"""Guardian 规则 — 命名纪律与文件头规范 (OMNI-030/031/032/033)。

OMNI-030: 文件名含版本/状态标记（_v1 / _old / _final 等）
OMNI-031: Python 测试文件使用 test_*.py 前缀（应使用 *_test.py 后缀）
OMNI-032: 临时文件（temp_*/tmp_*）出现在 scratch 目录之外
OMNI-033: 严格成员文件头使用了 taxonomy.yaml 中禁止的术语别名

注：OMNI-029 已被 observability.py 的 router-bypass-bus 规则占用。

规则设计原则：
  - 纯确定性（certainty="absolute"），无需 LLM 判断
  - 版本通过 git 控制，状态通过 OmniMark status= 字段表达
  - 术语约束确保 AI/人类读文件头时语义唯一精准

参考：docs/taxonomy.yaml / docs/standards/omni-header.md
"""
from __future__ import annotations

import re
from pathlib import Path

from ._base import FileContext, GuardianRule, _is_external, _is_scratch

# ══════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════

# 版本化/状态化文件名检测
# 设计 (2026-04-24 修正): 只匹配 stem 末尾 (stem 结尾或紧邻 `.ext`), 避免
# 中段词边界的合法英语单词被误杀 (如 `emit_as_new_job_checker.py`).
# 设计 (2026-05-08 V0-V26 巡检后扩): stem 末尾原则继续, 但 v\d+ 数字版本号是
# 字面版号铁案 — 中段也非法 (e.g. `v14_full_e2e_rework`, `step_v4_full_synergy`).
# 真 OK 模式 (合法英语单词中段): emit_as_new_job / get_old_value / mark_final_state
# 真 不 OK 模式 (字面版号中段): v14_..., step_v4_..., synergy_v5_..., camppet_v2_...
# 区分: v\d+ 是版号铁案, _new_/_old_/_final_ 中段可能是合法英语 → 末尾才判
_VERSIONED_STEM_TAIL_RE = re.compile(
    r"_(v\d+|old|new|final|backup|bak|copy|revised|updated)$",
    re.IGNORECASE,
)
# v\d+ / step\d+_\d+ 中段也判 — 这两种字面版号不可能是合法英语单词
_VERSIONED_NUM_ANYWHERE_RE = re.compile(
    r"(?:^|_)(v\d+|step\d+_\d+|phase\d+_\d+)(?:_|$)",
    re.IGNORECASE,
)
# 兼容别名
_VERSIONED_STEM_RE = _VERSIONED_STEM_TAIL_RE

# 临时文件前缀（stem 开头）
_TEMP_PREFIX_RE = re.compile(r"^(temp|tmp)_", re.IGNORECASE)

# 测试文件必须用 *_test.py，不用 test_*.py
_TEST_PREFIX_RE = re.compile(r"^test_.*\.py$", re.IGNORECASE)

# taxonomy.yaml 中的 forbidden_aliases（硬编码镜像，避免运行时解析 YAML 增加依赖）
# 与 docs/taxonomy.yaml forbidden_aliases 保持同步
_FORBIDDEN_ALIASES: dict[str, list[str]] = {
    # "worker" removed 2026-04-19: terminology migration repurposes it as canonical (see terminology.md + OMNI-036)
    "router":        ["handler", "processor", "executor"],
    "format":        ["schema", "contract", "dtype", "datatype", "data_format"],
    "pipeline":      ["workflow", "chain", "flow", "dag"],
    "domain":        ["namespace", "area", "region", "module_group"],
    "health_record": ["health_report", "diagnosis_result", "check_result"],
}

# scratch 目录豁免的临时文件规则
_SCRATCH_RE = re.compile(r"[/\\]scratch[/\\]", re.IGNORECASE)

# 仅检查 omnicompany 包内的文件（排除 vendors/外部/迁移期非严格文件）
def _in_omnicompany_pkg(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return "src/omnicompany" in p or p.startswith("omnicompany/")

def _is_graveyard(ctx: FileContext) -> bool:
    return "_graveyard" in ctx.path or "_archive" in ctx.path


# ══════════════════════════════════════════════════════════════════════
# OMNI-029：版本化/状态化文件名
# ══════════════════════════════════════════════════════════════════════

def _check_versioned_filename(ctx: FileContext) -> bool:
    r"""OMNI-030: 文件名含版本标记或状态标记（_v1/_old/_final/v\d+ 中段等）。

    适用范围：所有非外部文件（严格成员和非严格成员均适用）。
    原则：版本通过 git 控制，不通过文件名表达。

    2026-05-08 V0-V26 巡检后扩: 中段 v\d+ / step\d+_\d+ / phase\d+_\d+ 也判
    (这三种是字面版号铁案, 不可能是合法英语单词). _old/_new/_final 中段仍豁免
    (可能是合法英语单词中段).
    """
    if ctx.change_type == "D":
        return False
    if _is_external(ctx) or _is_graveyard(ctx):
        return False
    # scratch 豁免 (2026-05-08 立): scratch 是自由开获区, 字面版号自由
    if _is_scratch(ctx):
        return False

    stem = Path(ctx.path).stem  # 去掉最后一个后缀

    # 版本化/状态化 stem 末尾检测 (合法英语单词中段不判)
    if _VERSIONED_STEM_TAIL_RE.search(stem):
        return True
    # 字面版号 v\d+ / step\d+_\d+ / phase\d+_\d+ 中段也判
    if _VERSIONED_NUM_ANYWHERE_RE.search(stem):
        return True

    return False


def _check_test_prefix(ctx: FileContext) -> bool:
    """OMNI-031: Python 测试文件使用了 test_*.py 前缀（应使用 *_test.py）。

    就近测试文件命名必须统一，确保 pytest 自动发现和手动链接范围一致。

    豁免 (2026-04-24):
      - `tests/` 和 `tests_*/` 顶层目录 — pytest 约定 test_*.py 是规范命名
      - 仅对**就近测试** (如 `packages/**/workers/*_test.py` 旁边若出现 `test_*.py`)
        才判违规, 因为就近测试规则要求 *_test.py 后缀
    """
    if _is_external(ctx) or _is_graveyard(ctx):
        return False
    if not ctx.path.endswith(".py"):
        return False

    # 2026-04-24 收紧 (不再整 tests/ 目录豁免, plan §十二 极端情况审计):
    # - pytest 约定: tests/ 下 `test_*.py` 是规范命名, 合法
    # - src/ 下就近测试应用 `*_test.py` 后缀 → `test_*.py` 前缀才违规
    # - tests/ 下非 `test_*.py` 的文件本规则不命中 (其他命名规则可能继续抓)
    p = ctx.path.replace("\\", "/")
    name = Path(p).name
    is_test_prefix = bool(_TEST_PREFIX_RE.match(name))
    if not is_test_prefix:
        return False
    # pytest 规范位置: tests/ 下的 test_*.py 合规
    if p.startswith("tests/") or "/tests/" in p or p.startswith("tests_") or "/tests_" in p:
        return False
    # src/ 等其他位置的 test_*.py 前缀 → 违规
    return True


# ══════════════════════════════════════════════════════════════════════
# OMNI-052: 同主题多版本并存 (2026-05-08 V0-V26 巡检后立)
# ══════════════════════════════════════════════════════════════════════

# 真案例: challenge_agent_v3_architecture_*.md + challenge_agent_v7_architecture_final_*.md
# 双文件同前缀 (challenge_agent), 一个含 v\d+ 中段, 一个含 _final.
# 检测策略: 当前文件 stem 含 _final 末尾, 扫同目录看有没同前缀 + _v\d+ 中段文件.

# 从 stem 抽"主题前缀" (_v\d+ / _final / _stepN_M / 日期 之前的部分)
_THEME_PREFIX_RE = re.compile(
    r"^(?P<theme>.+?)(?:_v\d+|_final|_step\d+_\d+|_phase\d+_\d+|_old|_new|_backup|_bak|_\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _extract_theme_prefix(stem: str) -> str | None:
    """抽 stem 主题前缀, 用于真匹同主题多版本."""
    m = _THEME_PREFIX_RE.match(stem)
    if m:
        return m.group("theme").lower()
    return None


def _check_multi_version_coexist(ctx: FileContext) -> bool:
    """OMNI-052: 当前文件含 _final 末尾时, 检查同目录是否有同前缀 _v\\d+ 中段文件并存."""
    if _is_external(ctx) or _is_graveyard(ctx) or _is_scratch(ctx):
        return False
    p = ctx.path.replace("\\", "/")
    name = Path(p).name
    stem = Path(p).stem
    # 只对 _final 末尾的文件触发检查 (避免每个 v\d+ 文件都触发, 性能 + 重复报)
    if not re.search(r"_final($|_\d{4}-\d{2}-\d{2}$)", stem, re.IGNORECASE):
        return False
    theme = _extract_theme_prefix(stem)
    if not theme:
        return False
    # 扫同目录看有没同 theme + _v\d+ 中段
    abs_p = Path(ctx.abs_path)
    parent = abs_p.parent
    if not parent.exists():
        return False
    try:
        siblings = [s.name for s in parent.iterdir() if s.is_file() and s.name != name]
    except (PermissionError, OSError):
        return False
    for sib_name in siblings:
        sib_stem = Path(sib_name).stem
        if not re.search(r"_v\d+", sib_stem, re.IGNORECASE):
            continue
        sib_theme = _extract_theme_prefix(sib_stem)
        if sib_theme == theme:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════
# OMNI-053: 顶级散件无 namespace (2026-05-08 V0-V26 巡检后立)
# ══════════════════════════════════════════════════════════════════════

# 真案例: _scratch/r.json, _scratch/r2.json, _scratch/verify_output.log, _scratch/ws_debug.txt
# 特征: 工作目录顶级 + stem 短 (≤ 4 字符) 或 stem 极常见词 (output/log/debug/test 单词)

_SHORT_NAMESPACE_LESS_RE = re.compile(
    r"^(?:r\d*|t\d*|x\d*|out|res|tmp|log|test|debug|run)$",
    re.IGNORECASE,
)


def _check_rootless_stray_no_namespace(ctx: FileContext) -> bool:
    """OMNI-053: 工作目录顶级散件 (stem 短/极常见词)."""
    if _is_external(ctx) or _is_graveyard(ctx):
        return False
    # 只看真"工作目录顶级" — _scratch / data/_scratch / data/_workspaces 顶级直接
    p = ctx.path.replace("\\", "/")
    is_workspace_top = (
        re.match(r"^(?:_scratch|data/_scratch|data/_workspaces)/[^/]+$", p)
    )
    if not is_workspace_top:
        return False
    stem = Path(p).stem
    # stem 极短 (≤ 4 字符) 或 stem 是极常见无 namespace 词
    if len(stem) <= 4 or _SHORT_NAMESPACE_LESS_RE.match(stem):
        return True
    return False


# ══════════════════════════════════════════════════════════════════════
# OMNI-054: 半段编号 (2026-05-08 V0-V26 巡检后立)
# ══════════════════════════════════════════════════════════════════════

# 真案例: V51_V72_部分通过_2026-05-02.md (范围编号), v3.5 (半段版号), step8_4 (子版本)
# OMNI-030 已扫 step\d+_\d+ + phase\d+_\d+, OMNI-054 扫剩余:
#   - V\d+_V\d+ 范围编号
#   - v\d+\.\d+ 半段版号
#   - 字面 X.Y 数字版号

_HALF_STEP_RE = re.compile(
    r"(?:^|_)V\d+_V\d+(?:_|$)|(?:^|_)v\d+\.\d+(?:_|\.|$)|(?:^|_)\d+\.\d+(?:_|\.|$)",
    re.IGNORECASE,
)


def _check_half_step_numbering(ctx: FileContext) -> bool:
    """OMNI-054: 半段/范围编号字面."""
    if _is_external(ctx) or _is_graveyard(ctx) or _is_scratch(ctx):
        return False
    stem = Path(ctx.path).stem
    return bool(_HALF_STEP_RE.search(stem))


def _check_temp_outside_scratch(ctx: FileContext) -> bool:
    """OMNI-032: 临时文件（temp_*/tmp_*）出现在 scratch 目录之外。

    临时脚本只允许在 data/*/scratch/ 目录，其他位置触发警告。
    """
    if _is_external(ctx) or _is_graveyard(ctx):
        return False

    name = Path(ctx.path).name

    if not (_TEMP_PREFIX_RE.match(name) or name.endswith(".tmp")):
        return False

    # scratch 目录豁免
    if _SCRATCH_RE.search(ctx.path):
        return False

    return True


# ══════════════════════════════════════════════════════════════════════
# OMNI-030：OmniMark 头字段使用禁止别名
# ══════════════════════════════════════════════════════════════════════

def _check_forbidden_alias_in_header(ctx: FileContext) -> bool:
    """OMNI-033: OmniMark 头的 type=/domain= 字段使用了 taxonomy.yaml 中的禁止别名。

    适用范围：omnicompany 包内所有有 OmniMark 头的文件。
    设计：保持纯确定性（查字典），不调用 LLM。
    """
    if _is_external(ctx) or _is_graveyard(ctx):
        return False
    if not _in_omnicompany_pkg(ctx):
        return False
    if not ctx.omnimark:
        return False

    # 检查 type= 字段
    type_val = ctx.omnimark.get("type", "").lower().strip()
    if type_val:
        for canonical, forbidden_list in _FORBIDDEN_ALIASES.items():
            if type_val in [f.lower() for f in forbidden_list]:
                return True

    # 检查 domain= 字段的最后一段（如 demogame/table_learning → 只检查 table_learning 部分）
    domain_val = ctx.omnimark.get("domain", "").lower().strip()
    if domain_val:
        # domain 格式是 category/name，检查两部分
        parts = domain_val.replace("\\", "/").split("/")
        for part in parts:
            for canonical, forbidden_list in _FORBIDDEN_ALIASES.items():
                if part in [f.lower() for f in forbidden_list]:
                    return True

    return False


# ══════════════════════════════════════════════════════════════════════
# 规则注册
# ══════════════════════════════════════════════════════════════════════

RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-030",
        name="versioned-filename",
        severity="HIGH",
        description="文件名含版本标记（_v1/_old/_final 等），版本应通过 git 控制",
        check=_check_versioned_filename,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的文件名含版本/状态标记。\n"
            "  版本通过 git history 控制；'旧设计'通过 OmniMark status=deprecated 表达。\n"
            "  请重命名并用 git 管理历史版本。"
        ),
    ),
    GuardianRule(
        id="OMNI-031",
        name="test-prefix-instead-of-suffix",
        severity="MEDIUM",
        description="Python 测试文件使用 test_*.py 前缀，应使用 *_test.py 后缀",
        check=_check_test_prefix,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 使用 test_*.py 命名。\n"
            "  规范：测试文件使用 *_test.py 后缀（与 pytest 就近发现模式对齐）。\n"
            "  请重命名为 {stem}_test.py。"
        ),
    ),
    GuardianRule(
        id="OMNI-032",
        name="temp-file-outside-scratch",
        severity="MEDIUM",
        description="临时文件（temp_*/tmp_*）出现在 data/*/scratch/ 目录之外",
        check=_check_temp_outside_scratch,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 是临时文件，但不在 scratch 目录中。\n"
            "  临时脚本必须放在 data/<domain>/scratch/ 目录。\n"
            "  若是功能性脚本，请去掉 temp_/tmp_ 前缀并补充 OmniMark 头（type=script）。"
        ),
    ),
    GuardianRule(
        id="OMNI-033",
        name="forbidden-alias-in-header",
        severity="MEDIUM",
        description="OmniMark 头的 type=/domain= 字段使用了 taxonomy.yaml 中的禁止别名",
        check=_check_forbidden_alias_in_header,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的 OmniMark 头使用了禁止别名（见 docs/taxonomy.yaml forbidden_aliases）。\n"
            "  请将 type=/domain= 字段替换为规范词（router/format/pipeline/domain 等）。\n"
            "  目标：AI/人类读文件头时语义唯一精准，无歧义。"
        ),
    ),
    GuardianRule(
        id="OMNI-052",
        name="multi-version-coexist",
        severity="HIGH",
        description="同主题多版本并存 (同前缀 stem + _v\\d+ / _final 等不同版号同存)",
        check=_check_multi_version_coexist,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 跟同目录另一文件同主题多版本并存. 单一权威, 旧版进 _archive/ "
            "或重命名删 v 标记. 真案例: challenge_agent_v3 + challenge_agent_v7_final."
        ),
    ),
    GuardianRule(
        id="OMNI-053",
        name="rootless-stray-no-namespace",
        severity="MEDIUM",
        description="工作目录顶级散件无 namespace (stem ≤ 4 字符 + 在 _scratch/data/_workspaces 顶级)",
        check=_check_rootless_stray_no_namespace,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 是工作目录顶级短名散件 (无 namespace). 撞名风险高, 应归子目录 "
            "或加业务前缀 (例 r.json → debug/run_001.json)."
        ),
    ),
    GuardianRule(
        id="OMNI-054",
        name="half-step-numbering",
        severity="MEDIUM",
        description="半段编号字面 (X.Y / Y_X 子版本号 / V51_V72 范围编号)",
        check=_check_half_step_numbering,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 含半段/范围编号 (X.Y / V\\d+_V\\d+ 等). 这是推进过程中临时插档/拼范围信号. "
            "走 git history 不进文件名, 或拆成主题命名."
        ),
    ),
]
