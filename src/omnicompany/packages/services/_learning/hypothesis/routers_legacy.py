# [OMNI] origin=claude-code domain=services/hypothesis/routers_legacy.py ts=2026-04-20T00:00:00Z type=router status=active
# [OMNI] material_id="material:learning.hypothesis.agent_node_loops.router_definitions.py"
# 2026-07-26 OMNI-040 Stage 3: 从 _archive/ 迁回 hypothesis/ 顶层正式位置 (Diamond 继承源).
"""hypothesis Experimenter — 主探索 agent 节点(v5 决策库收编版仅存部分)。

ExperimenterRouter (AgentNodeLoop):
  主 agent — 自由用 bash/read_file/glob/grep 探索，输出行为轨迹。

旧 markdown 版 ReflectorRouter、双脑 LockstepExperimenterRouter 及其配套工具
(EditRouter/WriteFileRouter/ValidateHypothesisDocRouter/FindSimilarFormatsRouter)
已随决策本体合并清单#1 拆除(khyp 文档体系退役);总结 agent 现为
workers/belief_reflector.py 的 BeliefReflectorRouter(直接维护统一决策库)。
"""

from __future__ import annotations

import json
import logging
import copy
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import (
    CompactConfig,
    LoopConfig,
    PermissionConfig,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    ExtractResultRouter,
    GlobRouter,
    GrepRouter,
    PromptBuilderRouter,
    ReadFileRouter,
    SingleToolRouter,
    ToolExecutionError,
)
from omnicompany.packages.services._core.agent.routers.read_image import ReadImageRouter
from omnicompany.packages.services._learning.hypothesis.shadow_tools import (
    DeclareProbeInventoryRouter,
    InspectProbeRegionRouter,
    ProposeProbeRouter,
    _required_grounding_paths,
)

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# hypothesis 域专用工具 Router（Phase C 迁移新增）
# ════════════════════════════════════════════════════════════════════════════

class BashRouter(SingleToolRouter):
    """bash 命令执行（复用 ToolExecutor.execute('bash', ...)）."""

    TOOL_NAME: ClassVar[str] = "bash"
    DESCRIPTION: ClassVar[str] = (
        "Executes a given bash command and returns its output.\n\n"
        "IMPORTANT: Avoid using this tool to run find, grep, cat, head, tail, sed, awk, or echo commands. "
        "Use the appropriate dedicated tool instead:\n"
        " - File search: Use glob (NOT find or ls)\n"
        " - Content search: Use grep (NOT grep or rg)\n"
        " - Read files: Use read_file (NOT cat/head/tail)\n"
        " - Edit files: Use edit (NOT sed/awk)\n"
        " - Write files: Use write_file (NOT echo >/cat <<EOF)\n\n"
        "Reserve bash for system commands and terminal operations (env checks, Python scripts, HTTP requests, etc.)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "description": {"type": "string", "description": "Clear description of what this command does"},
            "timeout": {"type": "number", "description": "Optional timeout in milliseconds (max 600000)"},
            "run_in_background": {"type": "boolean", "description": "Set to true to run in background"},
        },
        "required": ["command"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        if getattr(ctx, "hypothesis_shadow_mode", False):
            raise ToolExecutionError(
                "hypothesis shadow scene 禁止 shell；只允许读指定截图并提交待审核建议"
            )
        return self._executor.execute("bash", args)


class GroundedReadImageRouter(ReadImageRouter):
    """ReadImageRouter with a shared delivery marker for shadow exploration.

    Queuing an image and actually delivering it to the next model turn are two
    different states.  This router records the queued path; Experimenter moves
    it to ``seen`` only after AgentNodeLoop has appended a real image block.
    """

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        result = super()._execute(args, ctx)
        if result.startswith("[IMAGE_QUEUED]"):
            pending_paths = getattr(ctx, "hypothesis_pending_image_paths", None)
            if isinstance(pending_paths, list):
                resolved = str(Path(str(args.get("path") or "")).resolve())
                if resolved not in pending_paths:
                    pending_paths.append(resolved)
        return result


# ════════════════════════════════════════════════════════════════════════════
# ExperimenterRouter — 主 agent（自由探索）
# ════════════════════════════════════════════════════════════════════════════

_EXPERIMENTER_SYSTEM_PROMPT = """\
你是一个假设探索 agent。通过运行命令和读取文件，探索目标系统的行为规律并验证假设。

全部用简体中文思考和记录。

你拥有以下工具：
- bash: 执行任意 shell 命令。典型用途：列目录、查环境变量、跑 Python 脚本、发 HTTP 请求。
- read_file: 读取文件完整内容。
- glob: 按模式查找文件路径。
- grep: 在文件或目录内搜关键词。
- finish: 结束本轮探索。不需要输出内容。

工作原则：
- 每 2-3 步自问：这条路径是否在逼近 goal？如果连续没进展，主动换方向。
- 不要重复跑完全相同的命令。
- 优先走可能直接触达 goal 的路径，不要只做表面 CLI 探索。
- 可以自由写 Python 脚本、查注册表、读配置、发 HTTP 请求——任何你觉得能推进 goal 的手段。
- 观察到显著现象时，在自己的推理里标注"这可能是一条规律"，但不要硬塞工具调用去"记录"——总结 agent 会从你的行为轨迹里归纳。

当输入含 game-ui shadow scene 时：
- bash 由运行时强制禁用；禁止通过任何途径操作设备或改写游戏状态。
- 第一轮只调用 read_image 查看 scene 的 required_grounding_image_paths，等下一轮真实看到全部图片再提建议。
- scene 若要求 candidate inventory，必须在任何裁片前调用 declare_probe_inventory，一次冻结完整候选、动作家族、控件组和交互支持；不得在看过裁片后补候选。
- scene 若要求 region inspection，先调用 inspect_probe_region 生成候选框裁片，等下一轮真实看到裁片后再用同一框和返回 id 提交。
- 每个建议分别调用 propose_probe；目标名、目标框内直接可见的文字或图标、scene 指定坐标、预期变化和风险必须具体。
- 每次 propose_probe 都必须显式传 action_type；不得依赖默认值或事后补交。
- 只提交画面中直接可见且能用目标框指认的候选；常见游戏布局、记忆或猜测不能代替当前画面。
- scene 若附带 visual_candidate_manifest，它只是一份未确认的几何检查清单。优先复核 recommended 候选；定位器不能证明可点击、控件名称或结果。
- 基于定位候选提交建议时，把使用的候选 id 写入 candidate_ids。正文段落、大片角色立绘和 OCR 碎片不能仅因定位器框出就当成交互。
- 无文字或既有事实支撑的图标只按可见形状与位置命名。排序、筛选、切换、详情、语音、表情等功能只能放进“待验证”的结果问题，不能写进目标名称冒充事实。
- 已选中的页签和当前状态控件仍属于可见交互候选；允许把预期结果写为保持当前状态或无变化，不能因此跳过。
- pinch 与 two_finger_swipe 只有在画面出现明确手势指令或既有验证事实时才能晋升，且必须完整记录中心/路径、第二指偏移、幅度和手势表面。
- 单轮 propose_probe 调用数不得超过 scene 的建议预算。自行计数，达到预算立即 finish，不提交同一候选的重复版本。
- propose_probe 只记账，不执行动作。完成建议后调用 finish。
"""


def _shadow_inspection_budget_exhausted(scene: dict) -> bool:
    inspections = scene.get("_region_inspections") or {}
    if not isinstance(inspections, dict) or not inspections:
        return False
    limit = int(
        scene.get("max_region_inspections")
        or max(8, int(scene.get("max_suggestions") or 1) * 3)
    )
    return len(inspections) >= limit


class _ExperimenterPromptBuilder(PromptBuilderRouter):
    """Experimenter 首轮 prompt 装配。"""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        store = input_data.get("store", {}) or {}
        session = input_data.get("session", {}) or {}

        goal = session.get("goal", "(未指定目标)")
        tools_hint = session.get("tools", [])
        iteration = store.get("iteration", 0)
        entries = store.get("entries", [])
        scene = session.get("scene", {}) or {}

        lines = [
            "## 探索目标",
            goal,
            "",
            "## 建议工具（参考）",
            f"{tools_hint}",
            "",
            f"## 当前假设库（第 {iteration} 轮，共 {len(entries)} 条）",
        ]
        if entries:
            for e in entries:
                label = {"living": "验证中", "stable": "已证实",
                         "deprecated": "已证伪"}.get(e.get("state", ""), "待验证")
                lines.append(
                    f"- [{label}] {e.get('id','')}: {e.get('predicted','') or e.get('trigger','')}"
                )
        else:
            lines.append("（暂无假设）")
        if scene.get("mode") == "shadow" and scene.get("kind") == "game-ui-exploration":
            observation = scene.get("observation") or {}
            visual_manifest = scene.get("visual_candidate_manifest") or {}
            prior_verified_targets = scene.get("prior_verified_targets") or []
            prior_target_reference = scene.get("prior_target_reference") or {}
            required_grounding = [str(path) for path in _required_grounding_paths(scene)]
            lines.extend([
                "",
                "## 影子场景合同",
                "本轮只能观察与建议。运行时没有设备动作入口，bash 也会被拒绝。",
                f"benchmark_run_id: {scene.get('benchmark_run_id', '')}",
                f"完整截图: {observation.get('frame_path', '')}",
                f"坐标参考图: {scene.get('grounding_image_paths') or []}",
                f"本轮必须读取的完整画面: {required_grounding}",
                f"截图 artifact: {observation.get('artifact_id', '')}",
                f"截图 sha256: {observation.get('sha256', '')}",
                f"源画面尺寸: {json.dumps(observation.get('viewport') or {}, ensure_ascii=False)}",
                f"坐标输入空间: {scene.get('coordinate_space') or 'normalized_1000'}",
                f"坐标参考布局: {json.dumps(scene.get('coordinate_reference_layout') or {}, ensure_ascii=False)}",
                f"允许动作: {scene.get('allowed_action_types') or []}",
                f"禁止目标词: {scene.get('forbidden_target_terms') or []}",
                f"禁止目标处理: {scene.get('forbidden_target_policy') or 'record-ineligible'}",
                f"建议预算: {scene.get('max_suggestions', 20)}",
                f"区域检查预算: {scene.get('max_region_inspections') or max(8, int(scene.get('max_suggestions') or 1) * 3)}",
                "第一轮只用 read_image 读取“本轮必须读取的完整画面”；下一轮真实看到全部图片后再逐个调用 propose_probe。",
                "source_pixels 模式必须同时读取带绝对坐标标签的参考图；不能按聊天界面中的缩放尺寸估算源像素。",
                "每次 propose_probe 都必须显式填写 action_type；缺少该字段的候选会被拒绝且不写入账本。",
                "建议预算是本 session 的硬上限：自行计数，达到上限立即 finish；同一候选只能提交一次，不能批量提交修订版。",
                "每条建议的 visible_cue 必须描述目标框内直接可见的文字、图标、颜色和相邻锚点。",
                "visible_cue 只有在完整原图中能逐字辨认时才转写目标主体文字。小角标在本影子阶段一律不转写文字，只描述颜色、形状和可确认的图标类别；即使它看起来像“新”等字符也不能写成文字。",
                "visible_cue 必须区分目标框内特征与框外邻接锚点；引用邻居时写明左/右/上/下和“相邻”或“框外”，不能把邻居的文字、角标、星级或状态归给当前目标。",
                "propose_probe 的 coordinate_space 必须与“坐标输入空间”完全一致；source_pixels 直接按网格 px 标签填写，normalized_1000 才使用 0..999。",
                "tap 的 target_bounds 要紧贴并完整包住目标控件，不能用整列、整块功能区或错位的近似框；点击点由运行时取目标框中心。",
                "swipe 的 target_bounds 是安全手势走廊，不是整张页面或完整列表边界。走廊必须完全位于当前可见的可滚动表面内，并同时包含手势起点与终点（右/下边界为开区间）；排除角色、天空等背景和悬浮返回/导航控件。visible_cue 只能把走廊内像素写成框内内容，走廊外线索必须明确标为相邻或框外锚点。",
                "提交前分别复核 x 与 y：若控件位于最右侧源像素网格线右边，x 必须大于该网格线标注值；其余边缘同理。",
                "坐标文字和网格线直接叠在原图上，没有增加画布边距；源像素原点仍是原截图左上角，不能扣除文字标签宽高。",
                "若坐标参考布局是 source_bands_2_columns：单张参考图按阅读顺序排成左上、右上、左下、右下四段；每段都是原图完整宽度的纵向裁片，Xpx 与 Ypx 都是原图绝对坐标。不能使用拼图画布坐标。",
                "优先提交带文字标签、独立导航箭头、明确只读信息入口等高辨识度候选；低辨识度的连续语义图标组和功能不明按钮排在后面。",
                "risk_flags 只记录具体的资源消耗、购买、账号、删除、不可逆或明确持久状态变更。可返回的页面跳转、只读面板和普通导航使用空数组 []；对结果不确定要写“待验证”的 expected_change，不能把不确定性伪装成风险。",
                "画面中没有直接可见证据的控件一律不提交；不能按常见游戏布局补全。",
                "不要把推测写成已发生事实，不要声称建议已执行。",
            ])
            if scene.get("require_candidate_inventory"):
                lines.extend([
                    "本 scene 要求先冻结候选覆盖计划：在任何 inspect_probe_region 前调用一次 declare_probe_inventory。",
                    "候选清单必须来自完整画面，不得读取 benchmark expected truth；逐项登记 action_family、approximate_bounds、group_id 和 interaction_support_kind。",
                    "declare_probe_inventory 返回的候选只通过 propose_probe.inventory_candidate_id 引用；candidate_ids 专门引用 visual_candidate_manifest，二者不得混用。",
                    "重复、对称、十字或对齐控件组的每个可见成员分别登记；画面明确出现多点手势文字时，把指令及安全手势表面登记为 pinch 或 two_finger_swipe 候选。",
                    "多点手势的 approximate_bounds 与最终 target_bounds 都圈实际操作表面；说明文字只提供交互支持，写在 support_note/rationale，不能把说明文字区域当作手势表面。",
                    "缺少交互支持的可见图形使用 unverified_visual，只留在候选清单；不得消耗裁片预算或写入 actionable ledger。",
                    "每个 actionable 候选先保留一次检查槽；同一候选最多修框一次。只有总预算仍覆盖所有未检查候选时，才允许提前修框。",
                ])
                required_inventory_ids = scene.get("required_inventory_candidate_ids") or []
                if required_inventory_ids:
                    lines.append(
                        "以下候选必须进入首次冻结清单并逐项完成裁片复核，不得缩小范围："
                        + json.dumps(required_inventory_ids, ensure_ascii=False)
                    )
                lines.append(
                    "完整画面上看起来可能是独立按钮或控件组成员、但需要裁片确认的候选，"
                    "使用 pending_visual_review；它可以消耗裁片预算，propose 时再提交 "
                    "isolated_overlay_container、ui_control_group_membership、"
                    "reviewed_visual_manifest_candidate 或 explicit_gesture_instruction。"
                    "只有无需继续复核且明确缺少交互支持的纯图形才使用 unverified_visual。"
                )
                if not (scene.get("visual_candidate_manifest") or {}).get("candidates"):
                    lines.append(
                        "当前 scene 没有已审阅 visual candidate manifest，禁止使用 "
                        "reviewed_visual_manifest_candidate；控件组成员使用 "
                        "ui_control_group_membership，无法证明交互支持的图形使用 unverified_visual。"
                    )
                existing_inventory = scene.get("_probe_candidate_inventory") or {}
                if existing_inventory.get("candidates"):
                    existing_inspections = scene.get("_region_inspections") or {}
                    lines.extend([
                        "本轮从运行时校验重试继续；候选清单已经冻结，禁止再次调用 declare_probe_inventory，也不要重新读取已送达的完整图。",
                        "直接复用以下冻结候选："
                        + json.dumps(
                            existing_inventory.get("candidates") or [],
                            ensure_ascii=False,
                        ),
                        "直接复用以下区域检查："
                        + json.dumps(existing_inspections, ensure_ascii=False),
                        "对仍缺 ledger 的候选调用 propose_probe；inventory_candidate_id 引用上述候选，region_inspection_id 引用上述检查。",
                    ])
            if scene.get("require_interaction_support"):
                lines.extend([
                    "本 scene 启用交互支持门。tap 候选需要既有验证目标、独立浮层容器、同组控件成员或已审阅视觉候选之一；多点手势需要明确手势指令或既有验证事实。",
                    "interaction_support_kind 必须来自冻结候选清单；仅凭图形外观不能晋升为可执行建议。",
                ])
                lines.append(
                    "冻结为 pending_visual_review 的候选允许在裁片复核后晋升为明确支持类型；"
                    "其余候选的 interaction_support_kind 仍须与冻结清单一致。"
                )
            if scene.get("forbidden_target_policy") == "reject":
                lines.extend([
                    "本 scene 对禁止目标采用 reject：命中禁止词的候选不会写入账本，也不消耗建议额度；不要把它们放进最终建议计数。",
                ])
            if scene.get("require_region_inspection"):
                lines.extend([
                    "本 scene 要求目标框复核：完整图送达后，先为每个候选调用 inspect_probe_region。工具会返回 region_inspection_id 并在下一轮附上“上下文红框 + 精确目标像素”图。",
                    "一次只检查一个候选框。inspect_probe_region 返回 IMAGE_QUEUED 后立即结束当前工具批次，下一轮真实看到该裁片后再提交或修正；不得在同一轮排队多个裁片。",
                    "必须真实查看裁片，再用完全相同的 target_bounds 和 region_inspection_id 调用 propose_probe。右侧精确裁片的闭合红框是硬边界；红框外或被边界切断的文字、数值、图标不能补全为框内 visible_cue。修改框后必须重新检查。",
                    "tap 裁片若只出现目标的一部分、边缘或被截断形状，禁止 propose；使用同一 candidate_id 的第二次 inspect 修框。冻结 approximate_bounds 允许一次有限邻域修正。",
                    "inspect_probe_region 还会返回 complete_text_tokens 与 adjacent_text_tokens。二者以完整截图 OCR 的整词多边形为准：整词完全落入红框才算框内，64px 检查上下文内且完全位于红框外才算邻接；裁片 OCR 识别出的截断前后缀没有转写资格。所有逐字 UI 文字和数值都必须在 visible_cue 中加引号，并分别逐项填写到 visible_text_tokens / adjacent_text_tokens。运行时会拒绝未声明或跨区域挪用的 UI 原文。对应清单为空时传 []；只能描述颜色、纹理、分隔线、形状和不含逐字文本的框外锚点。",
                    "若 inspect_probe_region 返回 intersecting_incomplete_text_tokens 与 suggested_target_bounds，说明当前框切断了完整 UI 文字。按 suggested_target_bounds 重新检查原候选；不能转写半词，也不要因此漂移到画面其他区域。",
                    "区域检查达到预算后必须使用已经看过的合法裁片提交候选或结束，不能继续扩散搜索。",
                ])
                required_inspections = int(
                    scene.get("min_region_inspections_per_candidate") or 1
                )
                if required_inspections > 1:
                    lines.extend([
                        f"本 scene 要求每个 actionable 候选至少检查 {required_inspections} 次；第二次可以沿用正确框，也可以依据宽上下文刻度修框。未达到次数时 propose 会被拒绝。",
                    ])
                if scene.get("require_full_target_confirmation"):
                    lines.extend([
                        "每次 propose_probe 必须传 target_fully_enclosed=true；只有右侧精确裁片完整包住目标时才允许为 true。看到局部、边缘或目标越出红框时继续修框。",
                        "多点手势另传 gesture_surface_excludes_instruction=true，确认 target_bounds 只圈实际操作表面，说明文字保留在框外。",
                    ])
                if scene.get("require_expanding_target_confirmation"):
                    expansion = int(scene.get("min_target_expansion_px") or 20)
                    lines.append(
                        "tap 候选的第二次 inspect 必须引用同一 candidate_id，"
                        f"并在第一次框的四边各扩出至少 {expansion}px（画面边缘处扩到边缘）；"
                        "提交时只能引用第二张裁片。这个扩框用于确认白色图标、按钮背景和圆角没有被红框切掉。"
                    )
                if scene.get("require_text_free_gesture_surface"):
                    lines.append(
                        "多点手势 target_bounds 必须位于角色/场景画布的无字区域；"
                        "任何完整或被截断的 OCR 文字都表示仍圈到了说明层或控件层，必须重新选区。"
                    )
                if scene.get("region_visible_cue_scope") == "exact_target_only":
                    lines.extend([
                        "本 scene 的生产线索范围是 exact_target_only：visible_cue 只描述右侧精确红框内像素，不写任何框外或相邻结构；adjacent_text_tokens 必须为 []。上下文图只供你判断框是否合理，不进入最终线索。",
                    ])
            if scene.get("expected_change_mode") == "unverified":
                lines.extend([
                    "本 scene 是未知真实发现：每条 expected_change 必须以“待验证：”开头，只写将由外部设备实证的问题，不能把点击结果写成已知事实。",
                ])
            if scene.get("target_naming_mode") == "visual-neutral":
                lines.extend([
                    "本 scene 启用视觉中性命名：无 OCR 原文或既有事实支撑的图标，target_name 只能写位置、形状、颜色或已核对原文。排序、筛选、切换、导航、详情、语音、表情等功能词会被运行时拒绝；功能猜测只放进以“待验证：”开头的 expected_change。",
                ])
            if prior_verified_targets:
                lines.extend([
                    "",
                    "## 本轮开始前已验证事实（A/B 同条件输入）",
                    "以下目标名称、动作、源像素框、已观察结果和证据在本轮开始前已经存在。逐项在完整原图中复核可见线索，并按原事实提交；不得改写边界或扩展结果含义。",
                    "既有事实: " + json.dumps(prior_verified_targets, ensure_ascii=False),
                ])
                if scene.get("prior_fact_contract_mode") == "exact":
                    lines.extend([
                        "本 scene 启用既有事实精确模式。每次 propose_probe 必须填写对应的 prior_fact_id。",
                        "target_name、action_type、既有 action 坐标、既有 target_bounds 与 expected_change 必须逐字/逐值复制引用事实；运行时会拒绝任何扩写或改动。",
                    ])
            if prior_target_reference:
                lines.extend([
                    "",
                    "## 既有目标放大裁片（内部观察辅助）",
                    f"裁片总览: {prior_target_reference.get('path', '')}",
                    "总览仍以完整原图为最终证据。每个 TARGET 红框只圈出对应事实目标；红框外保留的像素仅作为邻接上下文。先看红框内，再把框外内容明确写成相邻锚点。",
                    "编号映射: " + json.dumps(prior_target_reference.get("items") or [], ensure_ascii=False),
                ])
            if visual_manifest:
                recommended_ids = set(
                    visual_manifest.get("recommended_candidate_ids") or []
                )
                compact_candidates = [
                    {
                        "id": item.get("id"),
                        "source_bounds": item.get("source_bounds"),
                        "center": item.get("center"),
                        "type": item.get("type"),
                        "content": item.get("content"),
                        "structural_flags": item.get("structural_flags"),
                        "aligned_row_count": item.get("aligned_row_count"),
                        "edge_region": item.get("edge_region"),
                        "recommended_for_review": item.get("id") in recommended_ids,
                    }
                    for item in visual_manifest.get("candidates") or []
                ]
                lines.extend([
                    "",
                    "## 视觉定位候选（未确认）",
                    f"定位器: {visual_manifest.get('locator', '')}",
                    f"候选结果 hash: {visual_manifest.get('source_result_sha256', '')}",
                    "这些框只帮助你检查几何位置；定位器不能证明可点击、控件名称或结果。每个可见线索、动作和预期结果仍需从完整截图独立确认。",
                    "先逐项复核 recommended_for_review=true 的候选。若采用，propose_probe 的 candidate_ids 必须写入对应 id。",
                    "candidate_ids 只能引用与本次 target_bounds 位于同一画面区域的框；运行时会拒绝错绑到其他区域的 provenance id。",
                    "已选中的页签和当前状态控件仍要提交；expected_change 可以明确写保持当前状态或无变化。",
                    "recommended=false 的大片区域、正文条目、OCR 碎片和 heuristic-only 候选不得仅凭定位器提交；只有完整截图出现独立控件 affordance 时才可提交。",
                    "候选清单: " + json.dumps(compact_candidates, ensure_ascii=False),
                ])
                if scene.get("visual_candidate_policy") == "require-reference":
                    lines.extend([
                        "本 scene 启用候选引用硬门：每个未知 tap 提案都必须引用与 target_bounds 同一区域的 candidate_ids。未被冻结候选清单定位到的自由补点不得提交。",
                    ])
        lines.append("")
        lines.append("请开始探索。所有工具调用的记录会自动传给总结 agent，你不需要格外记录。")
        lines.append("认为本轮探索已积累足够观察时调用 finish。")
        return [{"role": "user", "content": "\n".join(lines)}]


class _ExperimenterExtractResult(ExtractResultRouter):
    """从 messages 提取 tool_use + tool_result 对，输出 trace。"""

    def __init__(self, *, bus: Any, iteration_ref: dict):
        super().__init__(bus=bus)
        self._iteration_ref = iteration_ref  # 由 Experimenter.run 注入 iteration

    def extract(
        self, *, final_text: str, messages: list[dict], turn_count: int, stop_reason: str,
    ) -> Verdict:
        trace: list[dict] = []
        tool_use_by_id: dict[str, dict] = {}
        for msg in messages:
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "tool_use":
                    tool_use_by_id[block.get("id", "")] = {
                        "tool": block.get("name", ""),
                        "args": block.get("input", {}),
                    }
                elif btype == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    entry = tool_use_by_id.get(tool_use_id, {})
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = "\n".join(
                            c.get("text", "") if isinstance(c, dict) else str(c)
                            for c in result_content
                        )
                    trace.append({
                        "tool": entry.get("tool", ""),
                        "args": entry.get("args", {}),
                        "result": result_content,
                    })
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "iteration": self._iteration_ref.get("iteration", 0),
                "trace": trace,
                "final_text": final_text,
                "turn_count": turn_count,
                "stop_reason": stop_reason,
            },
        )


class ExperimenterRouter(AgentNodeLoop):
    """主 agent：自由探索，输出行为轨迹。

    2026-04-18 Phase C 迁移到 packages.services.agent.AgentNodeLoop。
    """

    DESCRIPTION: ClassVar[str] = "假设探索 AgentNodeLoop：自由探索，输出行为轨迹"
    FORMAT_IN: ClassVar[str] = "hypothesis.store"
    FORMAT_OUT: ClassVar[str] = "hypothesis.factlog"

    NODE_PROMPT: ClassVar[str] = _EXPERIMENTER_SYSTEM_PROMPT
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=200,  # 铁律 B 死循环安全网
        compact=CompactConfig(auto_compact_enabled=False),
        permission=PermissionConfig(mode="default"),
    )
    TOOL_ROUTERS: ClassVar[list[type[SingleToolRouter]]] = [
        BashRouter, ReadFileRouter, GlobRouter, GrepRouter,
        GroundedReadImageRouter, DeclareProbeInventoryRouter,
        InspectProbeRegionRouter, ProposeProbeRouter,
        # FinishRouter 会被基类自动追加
    ]

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("role", "runtime_main")
        # iteration 在 run(input_data) 时从 store 读；先预留可变引用给 ExtractResult 读
        self._iteration_ref: dict = {"iteration": 0}
        self._scene_ref: dict[str, Any] = {}
        self._session_ref: dict[str, str] = {"session_id": ""}
        self._shadow_pending_image_paths: list[str] = []
        self._shadow_seen_image_paths: set[str] = set()
        self._active_shadow_session_id = ""
        super().__init__(**kwargs)

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _ExperimenterPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _ExperimenterExtractResult(bus=bus, iteration_ref=self._iteration_ref)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx["origin"] = input_data.get("origin", "internal-engine")
        ctx["domain"] = input_data.get("domain", "services/hypothesis")
        ctx["agent_name"] = input_data.get("agent_name", "ExperimenterRouter")
        ctx["hyp_session_id"] = self._session_ref.get("session_id") or trace_id
        ctx["hyp_iteration"] = self._iteration_ref.get("iteration", 0)
        scene = self._scene_ref
        if scene.get("mode") == "shadow":
            ctx["hypothesis_shadow_mode"] = True
            ctx["hypothesis_scene"] = scene
            observation = scene.get("observation") or {}
            frame_path = observation.get("frame_path")
            roots = list(scene.get("allowed_image_roots") or [])
            if frame_path:
                roots.append(str(Path(str(frame_path)).resolve().parent))
            for grounding_path in scene.get("grounding_image_paths") or []:
                roots.append(str(Path(str(grounding_path)).resolve().parent))
            ctx["allowed_image_roots"] = tuple(dict.fromkeys(roots))
            ctx["hypothesis_pending_image_paths"] = self._shadow_pending_image_paths
            ctx["hypothesis_seen_image_paths"] = self._shadow_seen_image_paths
        return ctx

    async def on_turn_end_async(
        self, *, turn: int, messages: list[dict], trace_id: str,
    ) -> None:
        """Promote queued image paths only when an image block reached messages."""
        if not self._shadow_pending_image_paths or not messages:
            return
        last_content = messages[-1].get("content", [])
        attached = isinstance(last_content, list) and any(
            isinstance(block, dict) and block.get("type") == "image"
            for block in last_content
        )
        if attached:
            self._shadow_seen_image_paths.update(self._shadow_pending_image_paths)
            self._shadow_pending_image_paths.clear()

    async def run(self, input_data: Any) -> Verdict:
        # 把 iteration 推进 ref，供 ExtractResult 读
        if isinstance(input_data, dict):
            store = input_data.get("store", {}) or {}
            self._iteration_ref["iteration"] = store.get("iteration", 0)
            session = input_data.get("session", {}) or {}
            self._scene_ref = dict(session.get("scene") or {})
            session_id = str(session.get("session_id") or "")
            self._session_ref["session_id"] = session_id
            is_shadow = self._scene_ref.get("mode") == "shadow"
            if not is_shadow or session_id != self._active_shadow_session_id:
                self._shadow_pending_image_paths.clear()
                self._shadow_seen_image_paths.clear()
            self._active_shadow_session_id = session_id if is_shadow else ""
        suggestions_before = self._shadow_ledger_count()
        first = await super().run(input_data)
        is_game_shadow = (
            self._scene_ref.get("mode") == "shadow"
            and self._scene_ref.get("kind") == "game-ui-exploration"
        )
        suggestions_after_first = self._shadow_ledger_count()
        completion_issues_after_first = self._shadow_completion_issues()
        if not is_game_shadow or (
            suggestions_after_first > suggestions_before
            and not completion_issues_after_first
        ):
            if isinstance(first.output, dict):
                first.output["completion_contract_passed"] = True
                first.output["validation_retry_count"] = 0
            return first
        inspection_budget_exhausted = _shadow_inspection_budget_exhausted(
            self._scene_ref
        )
        retry_input = copy.deepcopy(input_data)
        if isinstance(retry_input, dict):
            retry_session = retry_input.setdefault("session", {})
            retry_session["scene"] = copy.deepcopy(self._scene_ref)
            retry_session["goal"] = str(retry_session.get("goal") or "") + (
                "\n\n[RUNTIME_COMPLETION_RETRY] 上一轮 suggestion ledger 仍为空。"
                "最终文字不能代替工具记录。现在必须对已确认的安全候选逐条实际调用 "
                "propose_probe；inventory_candidate_id 只引用 declare_probe_inventory 的候选，"
                "candidate_ids 只引用 visual_candidate_manifest。visible_cue 只写目标框内像素，"
                "交互指令等框外支持放在 rationale。"
                "启用完整目标确认时必须填写 target_fully_enclosed=true；多点手势还要填写 gesture_surface_excludes_instruction=true。"
                + (
                    "tap 候选只能引用最后一次扩框裁片；第二次裁片必须在首框四边保留规定余量。"
                    if self._scene_ref.get("require_expanding_target_confirmation")
                    else ""
                )
                + (
                    "多点手势表面必须完全无 OCR 文字。"
                    if self._scene_ref.get("require_text_free_gesture_surface")
                    else ""
                )
                + (
                    "区域检查预算已经用尽，禁止再次 inspect；直接复用已送达的 region_inspection_id。"
                    if inspection_budget_exhausted
                    else "仍有检查预算；若现有裁片没有目标或只含目标局部，必须用同一 candidate_id 的第二次 inspect 修正，禁止沿用错误裁片。"
                )
                + (
                    "仍未完成的冻结候选："
                    + "；".join(completion_issues_after_first)
                    + "。"
                    if completion_issues_after_first
                    else ""
                )
                + "冻结清单中的每个 actionable 候选都成功写入合格账本后才能 finish。"
            )
        second = await super().run(retry_input)
        suggestions_after_second = self._shadow_ledger_count()
        completion_issues_after_second = self._shadow_completion_issues()
        if isinstance(second.output, dict):
            second.output["finish_validation_failures"] = [
                {
                    "reason": "finish was requested while the shadow suggestion ledger was empty",
                    "trace": (first.output or {}).get("trace", [])
                    if isinstance(first.output, dict)
                    else [],
                    "final_text": (first.output or {}).get("final_text", "")
                    if isinstance(first.output, dict)
                    else "",
                    "turn_count": (first.output or {}).get("turn_count", 0)
                    if isinstance(first.output, dict)
                    else 0,
                    "stop_reason": (first.output or {}).get("stop_reason", "")
                    if isinstance(first.output, dict)
                    else "",
                }
            ]
            second.output["completion_contract_passed"] = (
                suggestions_after_second > suggestions_before
                and not completion_issues_after_second
            )
            second.output["validation_retry_count"] = 1
            second.output["completion_issues"] = completion_issues_after_second
            if (
                suggestions_after_second <= suggestions_before
                or completion_issues_after_second
            ):
                second.output["stop_reason"] = "shadow_completion_contract_failed"
        return second

    def _shadow_ledger_count(self) -> int:
        ledger_raw = self._scene_ref.get("suggestion_ledger")
        session_id = self._session_ref.get("session_id") or ""
        if not ledger_raw or not session_id:
            return 0
        ledger = Path(str(ledger_raw))
        if not ledger.is_file():
            return 0
        count = 0
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("session_id") == session_id:
                count += 1
        return count

    def _shadow_completion_issues(self) -> list[str]:
        if not self._scene_ref.get("require_candidate_inventory"):
            return []
        inventory = self._scene_ref.get("_probe_candidate_inventory") or {}
        actionable_ids = {
            str(item.get("id") or "")
            for item in inventory.get("candidates") or []
            if isinstance(item, dict)
            and item.get("id")
            and item.get("interaction_support_kind") != "unverified_visual"
        }
        if not actionable_ids:
            return ["尚未冻结任何 actionable 候选"]
        ledger_raw = self._scene_ref.get("suggestion_ledger")
        session_id = self._session_ref.get("session_id") or ""
        recorded_ids: set[str] = set()
        if ledger_raw and session_id:
            ledger = Path(str(ledger_raw))
            if ledger.is_file():
                for line in ledger.read_text(encoding="utf-8").splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        record.get("session_id") == session_id
                        and record.get("eligible_for_execution") is True
                    ):
                        candidate_id = str(
                            (record.get("generator") or {}).get(
                                "inventory_candidate_id"
                            )
                            or ""
                        )
                        if candidate_id:
                            recorded_ids.add(candidate_id)
        return [
            f"{candidate_id} 缺少 eligible ledger record"
            for candidate_id in sorted(actionable_ids - recorded_ids)
        ]


